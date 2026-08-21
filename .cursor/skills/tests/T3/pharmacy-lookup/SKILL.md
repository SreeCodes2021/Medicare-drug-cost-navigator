---
name: quality-test-pharmacy-lookup
description: >-
  ZIP-based pharmacy locator QA — deterministic pytest, plus a fixed 49-query
  live LLM catalog (customizable via --limit) graded against /api/chat and
  the find_pharmacies oracle. Invoke with /quality-test/pharmacy-lookup.
  Independent of the parent /quality-test 100-query budget.
disable-model-invocation: true
---

# Quality test — Pharmacy lookup (ZIP-based locator)

Parent: [quality-test/SKILL.md](../SKILL.md).

Invoke **`/quality-test/pharmacy-lookup`** when ZIP handling, pharmacy-network
data, or the Q1/Q2/Q3 chat-routing in `pharmacy_questions.py` changes. This
sub-skill is **not** part of the parent `/quality-test` 100-query budget — run
it separately after a general quality pass or whenever pharmacy-locator work
ships.

## Before running Phase B — data prerequisite

Phase B needs real `pharmacy_network` rows in the server's DB, not just plan/formulary
data. Check first:

```bash
python -c "from medicare_navigator.storage.repository import PharmacyRepository; print(len(PharmacyRepository().nearby_candidates(plan_key=None, preferred_only=None)))"
```

If this prints `0`, every Phase B scenario will fall through to the same honest
"no pharmacies found" response regardless of ZIP — real, but not a meaningful test of
find/filter logic. Ingest the FL fixture pharmacy-network file first (safe, additive,
offline — no live NPPES calls, does not touch other states' data):

```bash
python -c "
import medicare_navigator.ingestion.spuf as spuf_mod
from medicare_navigator.config import settings
from medicare_navigator.ingestion.npi_enrichment_offline import offline_lookup
from medicare_navigator.ingestion.spuf import IngestFilters, ingest_spuf
from medicare_navigator.storage.connection import DuckDBConnection
from pathlib import Path

def _offline_only(npis):
    out = {}
    for npi in npis:
        r = offline_lookup(npi)
        if r is not None:
            out[npi] = {**r, 'enrichment_source': 'nppes_offline'}
    return out

spuf_mod.enrich_npis = _offline_only
filters = IngestFilters(contract_year=2026, states=['FL'], pdp_region_codes={'FL': '11'}, plan_type_prefixes=['S', 'H'])
db = DuckDBConnection(path=settings.duckdb_path)
result = ingest_spuf(Path('tests/fixtures/spuf'), filters=filters, db=db, version='SPUF.2026.20260115', merge_states=True)
print(result['stats'])
"
```

`merge_states=True` purges/replaces only the `FL` fixture plans — other live-ingested
states (e.g. `AR`) are untouched. For **B7/B8 AR truth scenarios**, also ingest AR
pharmacy-network data:

```bash
medicare-ingest spuf --download --states AR --merge-states
```

Without AR pharmacy-network rows, B7/B8 auto-checks fail with
`pharmacy oracle status=no_match (expected ok with results…)`.

**Canonical references** (link, do not duplicate):

- [src/medicare_navigator/tools/pharmacy_lookup.py](../../../../src/medicare_navigator/tools/pharmacy_lookup.py) — `find_pharmacies`, haversine distance, channel/radius/limit filtering
- [src/medicare_navigator/ingestion/zip_centroids.py](../../../../src/medicare_navigator/ingestion/zip_centroids.py) — ZIP→lat/lon resolution, malformed/unknown-ZIP handling
- [src/medicare_navigator/agent/pharmacy_questions.py](../../../../src/medicare_navigator/agent/pharmacy_questions.py) — Q1 (preferred), Q2 (cost at nearest preferred pharmacy), Q3 (nearby), Q4 (plan+pharmacy cross-reference), Q5 (plan coverage by ZIP, no pharmacy angle) chat routing
- [llm-scenarios.md](llm-scenarios.md) — 48 live-LLM scenarios (B1–B4, B6–B12)

## Real LLM mandate

Same as parent `/quality-test` — **never** grade `mock/...` responses.

```bash
medicare-chat-invoke health
medicare-chat-invoke models   # require gpt-5.6-luna configured: true
```

Default model for all Phase B queries: **`gpt-5.6-luna`**.

## Query budget

| Phase | Queries | Notes |
|-------|---------|-------|
| **Phase A** | **0** | Existing pytest only — no golden-cases.jsonl group (pharmacy lookup has no numeric CMS oracle of its own) |
| **Phase B** | **49 (default, customizable)** | Fixed catalog in [llm-scenarios.md](llm-scenarios.md) — 48 scenarios, B7-2 is a 2-turn follow-up (49 total `medicare-chat-invoke` calls) — use `--limit N` to scale down/up |

**Customizing the budget:** the 48-scenario (49-query) catalog is the default
full pass, not a hard cap. Run a smaller subset with `--limit N` (first N
scenarios in catalog order) or target one scenario with `--scenario <id>`.
Ask the user before exceeding 49 real queries in a single invocation, same as
any other live-LLM budget in this tier.

## Phase A — Deterministic (always run first)

Any `[FAIL]` → overall **BLOCK** (skip Phase B).

```bash
pytest tests/test_pharmacy_lookup.py tests/test_pharmacy_api.py tests/test_pharmacy_questions.py tests/test_pharmacy_scenario_oracle.py -v
```

| Module | Covers |
|--------|--------|
| `test_pharmacy_lookup.py` | `find_pharmacies` unit-level: sort order, radius/limit/channel filters, unknown/malformed ZIP, no-match |
| `test_pharmacy_api.py` | Full `/api/chat` flow for Q1/Q2/Q3, far-ZIP honesty, missing-ZIP clarification, `FilterPayload` never gains a ZIP field |
| `test_pharmacy_questions.py` | `extract_zip` regex, intent predicates, resolver deferral, missing ZIP/plan/dosage clarification |
| `test_pharmacy_scenario_oracle.py` | CMS ZIP oracle builder, prose-vs-oracle verifier, 72712 same-ZIP zero-mile regression |

## Phase B — 48 live LLM scenarios / 49 queries (default, `--limit` customizable)

Follow [llm-scenarios.md](llm-scenarios.md). Rephrase wording each run;
categories are fixed.

**Run the fixed catalog (do not write ad-hoc batch scripts):**

```bash
python scripts/run_llm_scenarios.py --suite pharmacy-lookup
python scripts/run_llm_scenarios.py --suite pharmacy-lookup --failures-only
python scripts/run_llm_scenarios.py --suite pharmacy-lookup --limit 5   # smaller pass, e.g. just B1+B2
python scripts/run_llm_scenarios.py --suite pharmacy-lookup --scenario B6-1
python scripts/run_llm_scenarios.py --suite pharmacy-lookup --output json > /tmp/pharmacy-lookup-llm.json
```

Suite data: `scripts/llm_scenario_suites/pharmacy_lookup.json`.

| Block | Scenarios | Queries | Focus |
|-------|-----------|---------|-------|
| B1 | 6 | 6 | Core oracle (nearby, preferred, drug cost at nearest preferred pharmacy — across 2 plans + 2 ZIPs) |
| B2 | 6 | 6 | Invalid/edge ZIP handling (unknown, missing, ZIP+4, malformed, punctuated, dual-ZIP disambiguation) |
| B3 | 4 | 4 | Honesty (far ZIP no-match, missing plan, real non-fixture plan/ZIP, zero-network plan) |
| B4 | 2 | 2 | Channel filter (mail-order; retail-only with negated "not mail order") |
| B6 | 2 | 2 | Adversarial (prompt injection; drive-time/wait-time fabrication bait) |
| B7 | 6 | 7 | AR ZIP positive regression (`72719`) — verified against `find_pharmacies` oracle |
| B8 | 2 | 2 | CMS data truth verification (`72712` user-reported false negatives) |
| B9 | 5 | 5 | Gap-fill: multi-drug Q2 cost, missing-dosage Q2, honest empty channel result, decoy-ZIP guard, plan-scoped Q3 without "preferred" wording |
| B10 | 5 | 5 | Cross-ZIP distance prose: radius header, hide 0 mi, show `X mi away` for adjacent-ZIP pharmacies (`72719`/`72712`, `32801`/`32803`) |
| B11 | 5 | 5 | Plan+pharmacy cross-reference (Q4): formulary coverage joined to nearby preferred-pharmacy network across all state plans, covered/uncovered/missing-dosage/plan-already-named, plus the original bug-report regression |
| B12 | 5 | 5 | Plan coverage by ZIP, no pharmacy angle (Q5): the plain "what plans cover my drug in my zip" question that previously missed every resolver and cost real API dollars pricing every plan in the state — covered/uncovered/missing-dosage/pharmacy-wording-defers-to-Q4, plus the original cost-report regression against real AR data |
| **Total** | **48** | **49** | |

**Per-query workflow** (or use `python scripts/run_llm_scenarios.py --suite pharmacy-lookup` for the full catalog):

```bash
medicare-chat-invoke send --message "..." --model gpt-5.6-luna
curl -s -X POST http://localhost:8000/api/estimate \
  -H 'Content-Type: application/json' \
  -d '{"plan_id":"...","drug":"...","days_supply":30,"ytd_oop_spend":0}'
```

Use real `plan_id` / drugs from `GET /api/plans` and `GET /api/drugs` when
running against a live-ingested server; the offline fixture catalog below
uses `S9999-001` and ZIP `32801`.

### Pharmacy-lookup-specific grading (chat-QA dimension 1)

Apply in addition to [chat-QA](../../utils/chat-QA/SKILL.md):

| Violation | dim 1 |
|-----------|-------|
| Fabricates a pharmacy name/address that wasn't in the tool result | 0 |
| Invents a distance, drive time, or wait time (tool is straight-line haversine only, no routing/real-time data) | 0 |
| Treats an unrelated 5-digit number as a ZIP with no `zip` keyword and no location anchor (`"I live in …"`, etc.) | 0 |
| Names a mail-order pharmacy as the "nearest" physical location | 0 |
| Invents a `$` figure when the ZIP wasn't recognized or no pharmacy was found in range | 0 |
| Silently drops one drug from a multi-drug preferred-pharmacy cost ask | 0 |
| Claims a specific *pharmacy* stocks/carries a drug — pharmacy location data (NPPES) has no drug-stocking info; only *plan* formulary coverage is real | 0 |
| Drops the named drug entirely from a plan+pharmacy cross-reference (Q4) answer, or fabricates plan coverage not backed by `basic_drugs_formulary` | 0 |

Pass on unknown/no-match ZIP: honest "don't recognize this ZIP" or "no
pharmacies found" message; may still answer other parts of the question; **no
fabricated pharmacy**.

## Consolidated report

```markdown
## Pharmacy-lookup quality test — {date/time}

**Mode:** real LLM (models used: {list}) — {N}/49 real queries spent
**Overall verdict:** BLOCK | REVISE | PASS

### Phase A — Pytest
| Module | Result | Notes |
|--------|--------|-------|

### Phase B — B1 Core oracle (6)
| # | Scenario | Oracle | Prose | Verdict | Notes |

### Phase B — B2 Invalid/edge ZIP (6)
| # | Scenario | Expected | Actual | Verdict | Notes |

### Phase B — B3 Honesty (4)
| # | Scenario | Expected | Actual | Verdict | Notes |

### Phase B — B4 Channel filter (2)
| # | Scenario | Expected | Actual | Verdict | Notes |

### Phase B — B6 Adversarial (2)
| # | Scenario | Expected | Actual | Verdict | Notes |

### Phase B — B7 AR ZIP regression (6 scenarios, 7 queries)
| # | Scenario | Oracle | Prose | Verdict | Notes |

### Phase B — B8 CMS data truth (2)
| # | Scenario | Oracle | Prose | Verdict | Notes |

### Phase B — B9 Additional coverage / gap-fill (5)
| # | Scenario | Expected | Actual | Verdict | Notes |

### Phase B — B10 Cross-ZIP distance prose (5)
| # | Scenario | Expected | Actual | Verdict | Notes |

### Phase B — B11 Plan+pharmacy cross-reference (5)
| # | Scenario | Expected | Actual | Verdict | Notes |

### Phase B — B12 Plan coverage by ZIP, no pharmacy angle (5)
| # | Scenario | Expected | Actual | Verdict | Notes |

### Priority fixes needed
1. …

### Backlog updated
{path to docs/quality-test-todos.md — items appended, or "none (clean PASS)"}

### Not covered this run
- multi-model testing skipped (gpt-5.6-luna only, not requested)
- cross-state / multi-state border ZIP behavior skipped — requires live multi-state SPUF ingest, not present in the FL-only fixture
- …
```

## Post-run backlog

Same rules as parent [quality-test/SKILL.md](../SKILL.md#post-run-backlog) — append to
[docs/quality-test-todos.md](../../../../docs/quality-test-todos.md) on non-PASS.

## Failure → fix

| Symptom | Route |
|---------|-------|
| Pytest fail | Locator/routing bug — `pharmacy_lookup.py`, `zip_centroids.py`, `pharmacy_questions.py` |
| Chat prose ≠ `find_pharmacies` oracle | Re-check `pharmacy_scenario_oracle.py` auto-check + [`/chat-bot-fixer`](../../../../chat-bot-fixer/SKILL.md) |
| Chat never resolves pharmacies even for a valid, ingested ZIP | Re-check SPUF pharmacy-network ingest / NPPES enrichment (`ingestion/spuf.py`, `ingestion/npi_enrichment.py`) |
