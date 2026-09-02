# Bugs and Risks — Insulin Implementation Review

Findings from review of unstaged insulin-coverage changes. Severity: **P0** (merge/deploy blocker), **P1** (correctness/ops), **P2** (maintainability/drift). No code fixes in this document.

---

## P0 — Merge blockers

### P0-1: Core files untracked in git

**Files not in git (still `??` in `git status`):**

| File | Impact if missing at commit |
|---|---|
| `src/medicare_navigator/tools/insulin_cost.py` | **ImportError** — `estimate_drug_cost.py` imports this module |
| `tests/test_insulin.py` | Allowlist regression coverage lost |
| `tests/fixtures/spuf/insulin beneficiary cost file.txt` | Ingest + estimate tests fail; no insulin rows in fixture DB |

The 20 modified files alone do not constitute a shippable feature without these three.

---

## P1 — Correctness and operations

### P1-1: Re-ingest required after deploy

The `insulin_beneficiary_cost` table is created by `CREATE TABLE IF NOT EXISTS` in `schema.py` but **populated only by updated `ingest_spuf()`**.

**Risk:** Existing production/persistent DuckDB on Render (or local `data/navigator.duckdb`) that was ingested before this code ships will have an **empty** insulin table. Every insulin query will either:

- Hit `insulin_out_of_scope` (existence gate fails), or
- Return no costs if gate is skipped (unmapped days supply)

**Mitigation:** Full SPUF re-ingest must run as part of deploy. Document in deployment runbook. See [action-items.md](./action-items.md).

`ensure_schema()` creates the empty table on startup but does not backfill data.

### P1-2: `segment_id` stored but never used in queries

`insulin_beneficiary_cost` stores `segment_id` per row, but `InsulinBeneficiaryCostRepository` queries only on `(plan_key, tier, days_supply_code, pharmacy_channel)` — same convention as `beneficiary_cost`.

Per [insulin-cost-estimation.md §10](../insulin-cost-estimation.md): **1,850** real `(contract, plan, tier, days_supply)` combinations in CY2026 Q1 have multiple distinct `SEGMENT_ID` values.

**Risk:** When multiple segment rows exist for the same lookup key, DuckDB `fetchone()` returns an **arbitrary** row — wrong copay for MA-PD local segment plans.

**Severity:** Inherited project-wide limitation, not introduced by insulin work alone — but insulin table inherits it without improvement.

### P1-3: 2026 "lesser of" rule — delegated to CMS file

CMS [2026 PDE reporting guidance](https://www.hhs.gov/guidance/sites/default/files/hhs-guidance-documents/CMS/prescription_drug_event_record_reporting_instructions_g.pdf) states that for covered insulin products, applicable cost-sharing is the **lesser of**:

- $35 (one-month supply),
- 25% of maximum fair price (if selected drug under negotiation), or
- 25% of negotiated price.

**Implementation:** Trusts CMS pre-published `COPAY_AMT_*_INSLN` values. Does **not** independently compute `min($35, 0.25 × pricing.UNIT_COST)` or apply MFP.

**Empirical safety (CY2026 Q1):** 0 rows exceeded statutory cap; under-cap cases (e.g. $10 tier copay unchanged) confirmed.

**Risk:** Point-in-time assumption. If CMS file semantics change in a future quarterly release, or if copay column stops reflecting the lesser-of computation, estimates could drift without code changes.

**Re-validation required:** After each real quarterly ingest (documented in spec §10; no automated check yet).

### P1-4: Unmapped days supply skips existence gate

In `_resolve_estimate_context` (`estimate_drug_cost.py` ~L465):

```python
if is_insulin_drug and days_supply_code is not None:
    if not has_insulin_cost_data(...):
        return ToolResult.failure(ToolStatus.insulin_out_of_scope, ...)
```

When `days_supply_code is None` (fill size outside 30/60/90):

- Existence gate is **skipped**
- Context is built with `is_insulin=True`
- `compute_insulin_channel_costs` returns no costs (repo returns `None`)
- Result: `ok` status with **no dollar estimate** + `unmapped_days_supply_caveat`

**Not wrong necessarily** — matches general-pipeline behavior for unmapped supply — but differs from data-gap semantics (`insulin_out_of_scope`). Product decision: is "unmapped supply" vs "no CMS row" distinction clear enough to users?

### P1-5: Combination products (Soliqua / Xultophy) — secondary source only

GLP-1/insulin combination products are on the insulin allowlist and priced via the insulin cap path.

**Source for cap treatment:** Manufacturer savings-program page (Sanofi Soliqua), not CMS primary regulatory text. CMS Part D insulin FAQ returned HTTP 403 during implementation.

**Policy nuance:** GLP-1 receptor agonists alone are **not** insulin ([KFF IRA summary](https://www.kff.org/medicare/the-facts-about-the-35-insulin-copay-cap-in-medicare/)). Combo products may have different billing treatment — directionally likely correct but not byte-for-byte verified against CMS.

### P1-6: `COIN_AMT_*_INSLN` discarded at ingest — one-way door

Coinsurance columns are never stored. If a future dispute investigation needs to audit the unused field, re-ingest is required. Deliberate given §5 reliability finding; documented in spec §10.

### P1-7: Real-world proration inconsistencies (user expectation risk)

[Johns Hopkins research](https://publichealth.jhu.edu/2026/medicare-patients-out-of-pocket-costs-for-insulin-decrease-under-mandated-caps) (cited in spec §3): ~25% of beneficiaries were overcharged relative to the cap in 2023 due to inconsistent 30-day-multiple proration by plans/pharmacies.

**Risk:** Tool shows CMS-published cap; actual pharmacy charge may differ. No user-facing caveat about real-world proration variance (only statutory-cap methodology caveat).

---

## P2 — Maintainability and documentation drift

### P2-1: Stale upstream documentation

| Document | Stale content |
|---|---|
| `docs/navigator-implementation-spec.md` §1 | "Insulin … out of scope for v1" |
| `docs/navigator-implementation-spec.md` §3 step 2 | "if drug is insulin: STOP" |
| `docs/navigator-implementation-spec.md` §6 | Insulin listed under "Future work (deferred)" |
| `docs/business-solution.md` §7.1 | Reads as unshipped; says "$35/month regardless of benefit phase" (catastrophic $0 contradicts); references separate file "to be identified" |
| `docs/data-sources.md` §2 | Key files list omits `insulin beneficiary cost` file |

`docs/README.md` already links `insulin-cost-estimation.md` but not this trails folder (addressed separately).

### P2-2: Stale code comments

| Location | Stale text |
|---|---|
| `src/medicare_navigator/agent/navigator.py` ~L501 | "insulin hard-stops run before needs_dosage" — insulin is now priced, not hard-stopped (except data gap) |
| `src/medicare_navigator/guardrails/citations.py` ~L675 | "Hard-stop messages (e.g. insulin's statutory $35/month cap)" — normal insulin returns priced estimate, not hard stop |

### P2-3: Allowlist fragility

- **Hardcoded** brand/ingredient set — no CMS-driven detection.
- **Substring matching** (`any(name in lowered for name in _INSULIN_NAMES)`) is broad; `"insulin"` as a substring could theoretically match unexpected strings (low practical risk on Medicare formulary).
- **Likely gaps:** Biosimilars/brands not yet on list (e.g. Rezvoglar / insulin glargine-yfgn may match via ingredient substring if RxNorm returns `insulin glargine`, but brand-only queries might miss).
- **Maintenance:** Each new insulin launch requires manual allowlist audit against live formulary.

### P2-4: Eval runner tool-key mismatch

`src/medicare_navigator/eval/run_eval.py` ~L70:

```python
estimate_status = resp.tool_statuses.get("estimate_drug_cost")
```

But `eval/queries.jsonl` now expects `estimate_drug_cost_all_channels` in `expected_tool_status` for most cases (eval-001 through eval-012).

**Risk:** Citation hard-stop check may not fire correctly for cases where only `estimate_drug_cost_all_channels` is invoked — potential false pass/fail in eval harness.

### P2-5: Collateral eval query changes

`eval-002`: `expected_cost` changed 5.0 → 3.0 when tool key switched to `estimate_drug_cost_all_channels` (reflects cheapest channel — preferred_mail $3 on fixture). **Not insulin-related** but bundled in same diff; should be validated when eval is run.

`eval-012`: drug name simplified `januvia 100mg` → `januvia` — verify mock navigator still resolves correctly.

### P2-6: Duplicate helper code

`_unique_or_none()` exists in both `estimate_drug_cost.py` and `insulin_cost.py`. Minor DRY violation; not a bug.

### P2-7: No schema migration for existing DBs beyond `CREATE TABLE IF NOT EXISTS`

New table is created on `ensure_schema()` but **no additive migration entry** in `SCHEMA_MIGRATIONS` (only column-level migrations). Acceptable because it's a new table, not a column add — but operators must still re-ingest to populate.

---

## Out of scope (documented, not bugs)

| Item | Notes |
|---|---|
| LIS / Extra Help lower insulin caps | App does not model LIS beneficiaries at all |
| Part B insulin (pump, etc.) | Not in SPUF Part D pipeline |
| Part B $35 cap (July 2023+) | Separate benefit; not implemented |
| Inhaled insulin (Afrezza) | Not on allowlist unless brand/ingredient added |
| Insulin not on plan formulary | Correctly returns `not_covered`, not insulin path |

---

## Risk summary matrix

| ID | Risk | Likelihood | Impact | Mitigation status |
|---|---|---|---|---|
| P0-1 | Missing untracked files at commit | Certain if not staged | Build break | Action required |
| P1-1 | Empty insulin table post-deploy | High without re-ingest | All insulin queries wrong | Document + run ingest |
| P1-2 | Wrong segment copay | Medium (MA-PD segments) | Wrong dollar figure | Inherited; no fix yet |
| P1-3 | CMS file semantics change | Low per quarter | Systematic mispricing | Quarterly re-validation |
| P1-4 | Unmapped supply UX confusion | Low | User confusion | Product decision |
| P1-5 | Combo product classification | Low | Wrong cap path | CMS primary source needed |
| P2-1 | Doc drift misleads developers | Certain today | Wrong assumptions | Update docs |
| P2-3 | Missed insulin brand | Medium over time | Falls through as non-insulin general path | Allowlist audit |
