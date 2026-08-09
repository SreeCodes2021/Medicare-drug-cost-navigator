---
name: numeric-accuracy
description: >-
  Verify dollar figures shown to a beneficiary match a deterministic ground
  truth — either the no-LLM /api/estimate oracle, or a real, manually
  re-verified CMS SPUF value — rather than trusting the LLM's prose. Use when
  the user invokes /numeric-accuracy, numeric-accuracy, or asks to validate
  cost numbers against real/CMS values.
disable-model-invocation: true
---

# Numeric accuracy — ground-truth cost verification

User invoked this skill — check that dollar figures a beneficiary sees are
*correct*, not just well-formatted or well-cited. This is narrower than
[`/chat-QA`](../chat-QA/SKILL.md) dimension 1 (citation-groundedness): that
checks a claim traces to *some* source; this checks the claim matches the
*right* number.

## Two oracles, in order of preference

1. **Deterministic API oracle (no LLM, always available)** — `POST /api/estimate`, `/api/estimate-batch`, `/api/compare-plans` compute cost directly from ingested CMS data. Any chat/guided prose number must match this exactly. This is the same oracle `chat-QA`'s channel-parity sub-check already uses — reuse it here as the primary check for any numeric-accuracy pass.
2. **Real CMS ground truth (golden cases)** — for cases already manually verified against a live CMS ingest, stored in [`golden-cases.jsonl`](golden-cases.jsonl). Grows over time; see "Adding a new golden case" below.

## Step 1 — Deterministic oracle diff (fast, no ingest needed)

For any chat/guided answer you're checking:

```bash
curl -s -X POST http://localhost:8000/api/estimate \
  -H 'Content-Type: application/json' \
  -d '{"plan_id":"S9999-001","drug":"metformin","dosage":"500mg","days_supply":30,"ytd_oop_spend":0}'
```

Or for a plan comparison:

```bash
curl -s -X POST http://localhost:8000/api/compare-plans \
  -H 'Content-Type: application/json' \
  -d '{"drug":"metformin","dosage":"500mg","plan_ids":["S9999-001","H8888-001"],"days_supply":30,"ytd_oop_spend":0}'
```

Diff every `$` figure in the chat/guided explanation against this response. Any mismatch is a hard fail — file it the same way `chat-QA` dimension 1 would (citation-groundedness = 0), because a mismatch here means the LLM invented or miscalculated a number even though a correct one was available.

## Step 2 — Golden cases (offline fixture + real CMS)

```bash
python scripts/run_golden_cases.py                        # offline fixture cases only
python scripts/run_golden_cases.py --include-live --base-url http://localhost:8000  # + real CMS cases
```

The `--include-live` cases require a real ingest first:

```bash
medicare-ingest spuf --download --states AR --merge-states
```

Golden cases file: [`golden-cases.jsonl`](golden-cases.jsonl). Each row has `requires_live_ingest`, the exact request (`drug`, `dosage`, `plan_id`, `days_supply`, `ytd_oop_spend`), an optional `channel`, and the expected `cost_low`/`cost_high`/`tier`/`phase`.

**Always pin a `channel`** (`preferred_retail`, `standard_retail`, `preferred_mail`, or `standard_mail`) whenever the source figure you verified came from one specific channel — which is almost always, since CMS pricing legitimately differs per channel (e.g. plan `S5921-400` lovastatin 40mg is $5 preferred-retail but $13 standard-retail). Omitting `channel` makes the runner aggregate min/max across all four channels, which will produce false failures (or worse, false passes) whenever channels diverge. Only omit `channel` if you've confirmed all populated channels genuinely share the same price for that case.

### Adding a new golden case

Only add a case after **manually** confirming the number against CMS (a real ingest run, cross-checked with the CMS SPUF files directly, an already-published verified example like [`docs/business-solution.md` §3.3](../../../docs/business-solution.md), or a fresh `/api/estimate` call whose `channels` breakdown you've inspected directly). Never add a golden case from LLM output alone — that defeats the purpose of a ground-truth oracle.

```json
{"id": "golden-00N", "source": "<where you verified this>", "verified_on": "<ingest date/state or 'fixture'>", "requires_live_ingest": true|false, "drug": "...", "dosage": "...", "plan_id": "...", "days_supply": 30, "ytd_oop_spend": 0, "channel": "preferred_retail", "expected_cost_low": 0.0, "expected_cost_high": 0.0, "notes": "..."}
```

## Step 3 — Chat/guided prose vs. oracle (full loop)

```bash
medicare-chat-invoke send --message "What's the cost for metformin 500mg on plan S9999-001?"
```

Compare `grading.explanation`'s dollar figures against Step 1's oracle response for the same inputs. Use [`/chat-QA`](../chat-QA/SKILL.md)'s channel-parity sub-check for the detailed rubric on partial-channel claims.

## Failure → fix

| Symptom | Fix |
|---------|-----|
| Chat number doesn't match `/api/estimate` oracle | LLM math/paraphrase error — see [`/chat-bot-fixer`](../chat-bot-fixer/SKILL.md) dimension 1 |
| `/api/estimate` itself doesn't match a golden CMS case | Real bug in the cost pipeline — [`tools/estimate_drug_cost.py`](../../../src/medicare_navigator/tools/estimate_drug_cost.py), [`tools/part_d_benefit_params.py`](../../../src/medicare_navigator/tools/part_d_benefit_params.py), or [`ingestion/spuf.py`](../../../src/medicare_navigator/ingestion/spuf.py) |
| `--include-live` case fails after a fresh ingest | Either the golden case is stale (CMS data changed quarter-to-quarter — expected, update `verified_on`) or a real regression — re-verify manually against CMS before changing the golden value |

## Constraints

- **Never mark a golden case verified without a real, reproducible ingest or citation.**
- **Do not fabricate ground truth** — if you can't verify a number against CMS or the deterministic API, say so explicitly rather than approximating.
- **Do not commit** golden-case additions unless the user asks.
