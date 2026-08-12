---
name: quality-test-mixed-basket
description: >-
  Same-plan multi-drug baskets mixing insulin (IRA cap) and regular Part D
  drugs — deterministic golden batch oracle, pytest, plus 20 live LLM queries.
  Invoke with /quality-test/mixed-basket. Independent of parent /quality-test
  100-query cap and insulin-only /quality-test/insulin budget.
disable-model-invocation: true
---

# Quality test — Mixed insulin + regular basket

Parent: [quality-test/SKILL.md](../SKILL.md).

Invoke **`/quality-test/mixed-basket`** when chat or batch pricing must handle
**multiple drugs on one plan** where at least one product uses the insulin cap
path and at least one uses ordinary Part D tier/deductible/phase logic.

**Canonical references:**

- [llm-scenarios.md](llm-scenarios.md) — 20 live-LLM scenario catalog (M1–M5)
- [numeric-accuracy/golden-cases.jsonl](../../utils/numeric-accuracy/golden-cases.jsonl) — `mixed_basket` group (`golden-048`–`050`)
- [docs/insulin-cost-estimation.md](../../../../../docs/insulin-cost-estimation.md) — insulin cap rules
- Related insulin-only pass: [insulin/SKILL.md](../insulin/SKILL.md)

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
| **Phase A** | **0** | Golden `mixed_basket`, batch pytest, navigator routing pytest |
| **Phase B** | **20** | Fixed catalog in [llm-scenarios.md](llm-scenarios.md) |

Parent `/quality-test` also runs **2 mandatory §2g queries** (subset of this catalog) within its 100-query cap.

## Phase A — Deterministic (always run first)

Any `[FAIL]` → overall **BLOCK** (skip Phase B).

### A1. Golden oracle (`mixed_basket` group)

```bash
python scripts/run_golden_cases.py --case-group mixed_basket --by-group
```

| Case | Scenario |
|------|----------|
| `golden-048` | metformin 500mg + lantus on S9999-001 — mixed phases; combined $33–$50 |
| `golden-049` | lantus + humalog — dual insulin; combined $40–$45 |
| `golden-050` | lantus data-gap + metformin on H8888-001 — partial basket + caveat |

### A2. Pytest

```bash
pytest tests/test_batch_estimate.py -k "insulin_plus_regular or insulin_data_gap" \
       tests/test_mixed_basket.py \
       tests/test_dosage_questions.py -k mixed -v
```

Also run parent §1c-A (`tests/test_dosage_questions.py`) when invoked from `/quality-test`.

## Phase B — 20 live LLM queries

Follow [llm-scenarios.md](llm-scenarios.md). Rephrase wording each run; scenario intent is fixed.

**Run the fixed catalog (do not write ad-hoc batch scripts):**

```bash
python scripts/run_llm_scenarios.py --suite mixed-basket
python scripts/run_llm_scenarios.py --suite mixed-basket --failures-only
python scripts/run_llm_scenarios.py --suite mixed-basket --output json > /tmp/mixed-basket-llm.json
```

Suite data: `scripts/llm_scenario_suites/mixed_basket.json`. Grade with `--failures-only` for a compact pass, or `--output json` for full rubric grading. Optional single scenario: `--scenario M3-2`.

| Block | Queries | Focus |
|-------|---------|-------|
| M1 | 6 | Core oracle — per-drug `$`/phase, combined total, channel pin |
| M2 | 4 | Routing & dosage — missing oral strength + insulin; three-drug basket |
| M3 | 4 | Partial basket — not-covered regular, insulin data-gap, caveat on total |
| M4 | 3 | Phase contrast — YTD catastrophic mix; pre-deductible regular + insulin_cap |
| M5 | 3 | Adversarial — pooled $35 bait, deductible-on-whole-basket, injection |

**Oracle workflow per query** (or use batch runner above for all 20):

```bash
curl -s -X POST http://localhost:8000/api/estimate-batch \
  -H 'Content-Type: application/json' \
  -d '{"plan_id":"S9999-001","items":[{"drug":"metformin","dosage":"500mg"},{"drug":"lantus"}],"days_supply":30,"ytd_oop_spend":0}'
medicare-chat-invoke send --message "..." --model gpt-5.6-luna
```

Use real `plan_id` / drugs from `GET /api/plans` on the running server when not using fixture anchors.

### Grading rules (chat-QA dimension 1 + insulin policy)

| Violation | dim 1 |
|-----------|-------|
| Drops a named drug from the answer | 0 |
| Pools insulin cap into one $35 for the whole basket (insulin + regular) | 0 |
| Applies insulin_cap / no-deductible language to non-insulin drugs | 0 |
| Applies pre-deductible / tier copay language to insulin cap path | 0 |
| Fabricates `$` on `insulin_out_of_scope` | 0 |
| Combined total when oracle excludes partial items and no caveat | 0 |

Pass: address **every** named product; honest partial outcomes; combined total only when user asks and oracle allows summing.

## Consolidated report

```markdown
## Mixed-basket quality test — {date/time}

**Mode:** real LLM (models used: {list}) — {N}/20 real queries spent
**Overall verdict:** BLOCK | REVISE | PASS

### Phase A — Golden (`mixed_basket`)
| Cases | Passed | Notes |
|-------|--------|-------|

### Phase A — Pytest
| Module | Result | Notes |

### Phase B — M1 Core oracle (6)
| # | Scenario | Oracle | Prose | Verdict | Notes |

### Phase B — M2 Routing & dosage (4)
| # | Scenario | Expected | Actual | Verdict | Notes |

### Phase B — M3 Partial basket (4)
| # | Scenario | Expected | Actual | Verdict | Notes |

### Phase B — M4 Phase contrast (3)
| # | Scenario | Expected | Actual | Verdict | Notes |

### Phase B — M5 Adversarial (3)
| # | Scenario | Expected | Actual | Verdict | Notes |

### Priority fixes needed
1. …

### Backlog updated
{path to docs/quality-test-todos.md — items appended, or "none (clean PASS)"}

### Not covered this run
- multi-model testing skipped (gpt-5.6-luna only, not requested)
- …
```

## Post-run backlog

Same rules as parent [quality-test/SKILL.md](../SKILL.md#post-run-backlog) — append to
[docs/quality-test-todos.md](../../../../../docs/quality-test-todos.md) on non-PASS.

## Failure → fix

| Symptom | Route |
|---------|-------|
| Golden/pytest fail | Batch pipeline or routing — `navigator.py`, `batch_estimate.py`, `estimate_drug_cost.py` |
| LLM prose ≠ batch oracle | [`/chat-bot-fixer`](../../../../chat-bot-fixer/SKILL.md) |
| Deterministic insulin path drops regular drugs | Routing gate in `navigator.py` + `insulin_requests.message_names_non_insulin_cost_drugs` |
