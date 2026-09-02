# Insulin Trails — Implementation Review

**Review date:** 2026-08-11  
**Scope:** Unstaged insulin-coverage changes (20 modified files + 3 untracked files)  
**Status:** Feedback only — no code changes in this folder

## Executive summary

Insulin cost estimation moves from a **blanket v1 hard stop** ("insulin cost estimates are not supported") to **real, CMS-sourced dollar estimates** using the dedicated Insulin Beneficiary Cost File, capped at the IRA statutory **$35 per 30-day supply** (scaled for 60/90-day fills), with no deductible phase and $0 in catastrophic coverage. A narrower `insulin_out_of_scope` status is retained only when CMS publishes no insulin cost-share row for the plan's tier and fill size — a genuine data gap, not "insulin unsupported."

The implementation is architecturally sound, well-tested at the fixture level, and documented in depth in [Insulin Cost Estimation](../insulin-cost-estimation.md). This trails folder complements that spec with **review judgment**: strengths, bugs, risks, test gaps, and prioritized follow-ups.

## Change magnitude

| Metric | Value |
|---|---|
| Modified files (unstaged) | 20 |
| Lines added / removed (approx.) | +484 / −66 |
| **Untracked files (must stage before merge)** | 3 |
| New DuckDB table | `insulin_beneficiary_cost` |
| New Python module | `tools/insulin_cost.py` |
| New benefit phase label | `insulin_cap` |

### Untracked files (P0)

These files are required for the feature to work but are **not yet in git**:

| File | Role |
|---|---|
| `src/medicare_navigator/tools/insulin_cost.py` | Core insulin pricing logic |
| `tests/test_insulin.py` | Allowlist unit tests |
| `tests/fixtures/spuf/insulin beneficiary cost file.txt` | SPUF fixture for ingest + estimate tests |

## How to read this folder

| Document | Contents |
|---|---|
| [architecture.md](./architecture.md) | End-to-end data flow, ingest, estimate pipeline, status semantics |
| [strengths.md](./strengths.md) | What was done well — design wins and test themes |
| [bugs-and-risks.md](./bugs-and-risks.md) | Concrete bugs, edge cases, policy/regulatory and deployment risks |
| [action-items.md](./action-items.md) | Prioritized checklist (P0 / P1 / P2) |
| [test-and-doc-gaps.md](./test-and-doc-gaps.md) | Missing tests, golden-case gaps, stale docs, eval quirks |

## Primary reference

For CMS source documents, field-resolution evidence, calculation methodology, and worked examples, see **[Insulin Cost Estimation](../insulin-cost-estimation.md)**. The trails docs do not duplicate that material.

## Related upstream docs (currently stale — see action-items)

- [Navigator Implementation Spec](../navigator-implementation-spec.md) — still lists insulin as out of scope (§1, §3 step 2, §6)
- [Business Solution](../business-solution.md) — §7.1 still reads as unshipped future work
- [Data Sources](../data-sources.md) — omits insulin beneficiary cost file from key SPUF files list
