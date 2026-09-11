# Strengths — Insulin Implementation Review

What was done well in the unstaged insulin-coverage work. These are worth preserving and using as patterns for future benefit-phase or drug-category expansions.

## 1. Real bug found and fixed during implementation

**Hint-collision in SPUF file discovery**

The CMS insulin zip member name (`"insulin beneficiary cost file ..."`) contains the general file's discovery substring (`"beneficiary cost"`). Without `exclude_hints` on `_find_member()`, the general and insulin files could cross-contaminate depending on zip member ordering.

- Fix: `exclude_hints=INSULIN_FILE_HINTS` on general-file lookup; separate `INSULIN_FILE_HINTS` for insulin file.
- Regression: `test_insulin_beneficiary_cost_ingested_separately_from_general_file` in `tests/test_spuf_ingest.py` asserts 12 insulin rows, correct 30-day channel copays, and general `beneficiary_cost` tier-3 rows remain `copay`-type only.
- Validated against real CMS zip (Wyoming-scoped ingest): zero cap violations, no general-table contamination.

This is exactly the kind of subtle ingest bug that only surfaces with real file names — good catch and good test.

## 2. Empirical field resolution instead of assumption

CMS's Insulin Beneficiary Cost File publishes **both** copay and coinsurance columns with no `COST_TYPE_*` selector saying which applies. Rather than guessing, the implementation:

- Joined all 43,140 insulin rows against the general Beneficiary Cost File
- Used general file's `COST_TYPE_*` as ground truth
- Found: `COPAY_AMT_*_INSLN` never exceeds statutory cap; `COIN_AMT_*_INSLN` matches real coinsurance only ~62% of the time

Conclusion documented in [insulin-cost-estimation.md §5](../insulin-cost-estimation.md): copay column is authoritative; coinsurance column discarded at ingest.

This follows the existing project precedent (`BUG4_CAVEAT` — disclose ambiguity rather than guess).

## 3. Clean module boundaries

`insulin_cost.py` is a dedicated module that:

- Takes only primitive/typed arguments (no `_EstimateContext` import)
- Returns its own `InsulinChannelComputation` dataclass
- Never imports from `estimate_drug_cost.py` (avoids circular import)

`estimate_drug_cost.py` imports `insulin_cost` — one-way dependency. Easy to unit test in isolation.

## 4. Narrow, honest status semantics

Replacing blanket "insulin unsupported" with:

- **Priced estimates** when CMS data exists (`ok` + `insulin_cap`)
- **Narrow data-gap message** when formulary says covered but insulin file has no row (`insulin_out_of_scope` with `covered=True`)

The rewritten `INSULIN_OUT_OF_SCOPE_MESSAGE` explains the statutory cap while honestly stating a dollar estimate isn't available — better UX than the old hard stop.

## 5. Formulary checks before insulin branch

Old behavior: insulin hard-stopped **before** formulary lookup, so quantity limits, PA/ST, and not-covered never surfaced for insulin.

New behavior: standard gates run first. An insulin drug with a 90-day quantity limit now correctly returns `quantity_limit_blocked` instead of a misleading cap estimate or generic hard stop.

## 6. Downstream alignment

All user-facing surfaces updated consistently:

| Surface | Change |
|---|---|
| `agent/prompts.py` | Insulin in scope; `insulin_cap` presentation rules; data-gap vs priced distinction |
| `mcp/schemas.py` | Tool description reflects statutory cap path |
| `disclaimers.py` | New `INSULIN_STATUTORY_CAP_CAVEAT`; rewritten out-of-scope message |
| `guardrails/citations.py` | Insulin caveat in `_CARD_ONLY_CAVEATS` (card-only, not LLM paraphrase) |
| `frontend/src/app.js` | `insulin_cap` label; routine caveat byte-sync; About modal scope text |
| `eval/queries.jsonl` | `eval-010` expects priced lantus; tool key updated to `estimate_drug_cost_all_channels` |

No MCP parameter/schema changes needed — insulin reuses existing tool contracts.

## 7. Comprehensive fixture-level test coverage

New and updated tests cover the important behavioral matrix:

| Test | What it guards |
|---|---|
| `test_insulin_returns_real_capped_estimate_not_hard_stop` | $35 cap, `insulin_cap` phase, insulin caveat replaces BUG2 |
| `test_insulin_60_and_90_day_scaling` | $70 / $105 from CMS days-supply codes, not local math |
| `test_insulin_catastrophic_phase_overrides_cap_to_zero` | $0 at YTD ≥ annual cap |
| `test_insulin_no_deductible_phase_and_channel_differentiation` | `ded_applies_yn == "NA"`; mail $30 vs retail $35 |
| `test_insulin_narrow_fallback_when_plan_has_no_cost_share_data` | H8888-001 data gap, no silent general-pipeline fallback |
| `test_insulin_beneficiary_cost_ingested_separately_from_general_file` | Hint-collision regression |
| `test_insulin_beneficiary_cost_repository_scaling_and_narrow_fallback` | Repository 30/60/90 + `has_any` false |
| `test_insulin.py` | Allowlist: Lyumjev, Soliqua, Xultophy, case insensitivity |
| MCP + navigator tests | End-to-end priced insulin and data-gap hard stop |

## 8. Thorough implementation spec

[insulin-cost-estimation.md](../insulin-cost-estimation.md) is unusually complete for an in-repo doc:

- Primary CMS source links (2026 record layout, methodology, real zip verification)
- Full field schema table
- Empirical validation methodology and results table
- Step-by-step calculation pseudocode
- Worked examples from real CMS data
- Known limitations and follow-ups (§10)

This doc should be the canonical reference; the trails folder intentionally does not duplicate it.

## 9. Golden-case integration

`golden-037` added to `insulin_cap` case group in numeric-accuracy skill — enables deterministic `/api/estimate` oracle verification without live ingest.

## 10. Allowlist gap proactively fixed

Lyumjev, Soliqua, and Xultophy added after live formulary audit found them on-formulary but unrecognized. Documented in test docstrings with rationale (GLP-1/insulin combos billed as single capped product).

## Patterns to reuse

1. **Separate CMS file → separate table → separate repository → separate compute module**
2. **Empirical validation before choosing ambiguous CMS fields**
3. **`exclude_hints` for file discovery when CMS naming overlaps**
4. **Narrow hard-stop status for data gaps vs blanket out-of-scope**
5. **Disclose unused fields via caveat rather than silent drop** (`INSULIN_STATUTORY_CAP_CAVEAT`)
