# Action Items — Insulin Implementation Review

Prioritized follow-ups from the insulin trails review. **No code changes were made** as part of this review — these are for the implementer to approve and execute.

---

## P0 — Before merge

| # | Action | Owner hint | Rationale | Ref |
|---|---|---|---|---|
| P0-1 | **Stage and commit the 3 untracked files** (`insulin_cost.py`, `test_insulin.py`, insulin SPUF fixture) with the other 20 modified files | Dev | Feature breaks without them — `estimate_drug_cost.py` imports `insulin_cost` | [bugs-and-risks.md P0-1](./bugs-and-risks.md#p0-1-core-files-untracked-in-git) |
| P0-2 | **Run full test suite** after staging (`pytest tests/ -v`) and confirm pass count | Dev | Spec claims 275 passed at completion; re-verify with all files present | [test-and-doc-gaps.md](./test-and-doc-gaps.md) |
| P0-3 | **Plan SPUF re-ingest on deploy** — add to deployment checklist or runbook | DevOps | Empty `insulin_beneficiary_cost` table without re-ingest | [bugs-and-risks.md P1-1](./bugs-and-risks.md#p1-1-re-ingest-required-after-deploy) |

---

## P1 — Correctness and ops (soon after merge)

| # | Action | Owner hint | Rationale | Ref |
|---|---|---|---|---|
| P1-1 | **Update stale docs:** mark `business-solution.md` §7.1 as SHIPPED (mirror §7.2 pattern); update `navigator-implementation-spec.md` §1/§3/§6; add insulin file to `data-sources.md` §2 key files | Docs / Dev | Prevents developers reading wrong scope | [bugs-and-risks.md P2-1](./bugs-and-risks.md#p2-1-stale-upstream-documentation) |
| P1-2 | **Add quarterly re-validation checklist** (manual or script): (a) no `COPAY_AMT_*_INSLN` above statutory cap per fill size; (b) copay-vs-coin cross-check sample against general file | Data / Dev | §5 empirical finding expires each CMS quarter | [insulin-cost-estimation.md §10](../insulin-cost-estimation.md) |
| P1-3 | **Add `requires_live_ingest: true` golden case** after manual spot-check on real ingest (e.g. AR plan with known insulin copay) | QA / Dev | Only `golden-037` exists today (fixture-only) | [test-and-doc-gaps.md](./test-and-doc-gaps.md) |
| P1-4 | **Execute SPUF re-ingest on Render** (or target environment) immediately after deploy with insulin ingest code | DevOps | Populate `insulin_beneficiary_cost` in production DuckDB | [bugs-and-risks.md P1-1](./bugs-and-risks.md#p1-1-re-ingest-required-after-deploy) |
| P1-5 | **Run `medicare-eval`** and confirm eval-001–012 pass with updated `queries.jsonl` | QA | Collateral eval changes (tool keys, expected costs) bundled in diff | [bugs-and-risks.md P2-5](./bugs-and-risks.md#p2-5-collateral-eval-query-changes) |
| P1-6 | **Run `scripts/run_golden_cases.py --by-group`** and confirm `insulin_cap` group passes | QA | Validates golden-037 oracle | [strengths.md §9](./strengths.md#9-golden-case-integration) |

---

## P2 — Maintainability and hardening

| # | Action | Owner hint | Rationale | Ref |
|---|---|---|---|---|
| P2-1 | **Decide segment_id strategy** — document as accepted limitation, or key queries on segment_id for MA-PD plans | Product / Data | 1,850 multi-segment combos in real data | [bugs-and-risks.md P1-2](./bugs-and-risks.md#p1-2-segment_id-stored-but-never-used-in-queries) |
| P2-2 | **Audit insulin allowlist** against live CMS formulary RxCUIs for AR/TX (and future states) — add missing brands (e.g. Rezvoglar) | Data | Hardcoded list drifts as new products launch | [bugs-and-risks.md P2-3](./bugs-and-risks.md#p2-3-allowlist-fragility) |
| P2-3 | **Fix eval runner** — citation hard-stop check should use `estimate_drug_cost_all_channels` (or check both tool keys) | Dev | Tool-key mismatch in `run_eval.py` | [bugs-and-risks.md P2-4](./bugs-and-risks.md#p2-4-eval-runner-tool-key-mismatch) |
| P2-4 | **Update stale code comments** in `navigator.py` (~L501) and `citations.py` (~L675) | Dev | Comments still describe insulin as hard-stop | [bugs-and-risks.md P2-2](./bugs-and-risks.md#p2-2-stale-code-comments) |
| P2-5 | **Add missing tests** — see [test-and-doc-gaps.md](./test-and-doc-gaps.md) for full list | Dev | Golden cases, unmapped supply, multi-NDC, NULL-tier fallback |
| P2-6 | **Obtain CMS primary source** for Soliqua/Xultophy combo-product cap treatment (Part D insulin FAQ or final rule text) | Product / Legal | Currently manufacturer secondary source only | [bugs-and-risks.md P1-5](./bugs-and-risks.md#p1-5-combination-products-soliqua--xultophy--secondary-source-only) |
| P2-7 | **Consider user-facing caveat** about real-world 30-day-multiple proration variance (~25% overcharged in 2023 per Johns Hopkins) | Product | Manages expectation vs CMS-published cap | [bugs-and-risks.md P1-7](./bugs-and-risks.md#p1-7-real-world-proration-inconsistencies-user-expectation-risk) |
| P2-8 | **Deduplicate `_unique_or_none()`** — move to shared util or keep inline with comment | Dev | Minor DRY | [bugs-and-risks.md P2-6](./bugs-and-risks.md#p2-6-duplicate-helper-code) |

---

## Suggested merge checklist

Use this when approving the insulin PR:

- [ ] All 23 files staged (20 modified + 3 previously untracked)
- [ ] `pytest tests/ -v` — all pass
- [ ] `LLM_MOCK=1 medicare-eval` — eval-010 shows `insulin_cap` + cost $30 (all-channels min)
- [ ] `scripts/run_golden_cases.py` — `golden-037` passes
- [ ] Deployment plan includes SPUF re-ingest
- [ ] P1 doc updates scheduled (or included in same PR if desired)

---

## Deferred / out of scope (no action unless product expands)

- LIS/Extra Help insulin caps
- Part B insulin pricing
- Independent recomputation of 2026 "lesser of" from `pricing` + MFP tables
- Storing `COIN_AMT_*_INSLN` for audit (one-way door at ingest)
