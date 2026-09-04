---
name: quality-test-compound-questions
description: >-
  Cross-category question routing QA — does the chat bot answer every part of
  a message that spans more than one question type (insulin, non-insulin drug
  cost, OOP/MOOP, pharmacy lookup, date/duration windows) or bundles multiple
  distinct questions in one prompt, instead of a deterministic resolver
  silently answering only the first-matched half. Deterministic pytest +
  19 live LLM scenarios / 20 queries. Invoke with /quality-test/compound-questions.
  Independent of the parent /quality-test 100-query budget.
disable-model-invocation: true
---

# Quality test — Compound / cross-category questions

Parent: [quality-test/SKILL.md](../SKILL.md).

Invoke **`/quality-test/compound-questions`** when `navigator.py`'s deterministic
resolver chain (Tier → OOP → Alternatives → Pharmacy Q1-Q5 → Insulin → MixedBasket →
Dosage) changes, or after any fix in that area, to confirm a message spanning two or
more question categories still gets every part answered — not just the first resolver
that happens to match.

**Canonical references:**

- [llm-scenarios.md](llm-scenarios.md) — 19-scenario / 20-query catalog (CC-A through CC-F)
- [src/medicare_navigator/agent/navigator.py](../../../../../src/medicare_navigator/agent/navigator.py) `_resolve_deterministic` — the resolver chain and its ordering
- [src/medicare_navigator/agent/oop_questions.py](../../../../../src/medicare_navigator/agent/oop_questions.py), [pharmacy_questions.py](../../../../../src/medicare_navigator/agent/pharmacy_questions.py) — the two resolver modules most implicated so far
- Related single-category passes: [insulin/SKILL.md](../insulin/SKILL.md), [mixed-basket/SKILL.md](../mixed-basket/SKILL.md), [pharmacy-lookup/SKILL.md](../pharmacy-lookup/SKILL.md) — each stays inside one question category; this sub-skill is the cross-category complement to all three.

## Why this sub-skill exists

Every deterministic resolver in `_resolve_deterministic` returns immediately on its own
pattern match, with no check for whether the same message also asks something else. The
other three Tier-3 catalogs (insulin, mixed-basket, pharmacy-lookup) each stay inside one
question category by construction, so none of them ever exercised this. The first live run
(2026-09-04, 20 queries) found 7 confirmed drops across 5 distinct root causes; 2 of those
(wrong-plan pharmacy-network extraction, duration-blindness in the plan-scoped pharmacy
resolvers) were fixed the same day. See [llm-scenarios.md](llm-scenarios.md) for full
per-scenario status and [docs/quality-test-todos.md](../../../../../docs/quality-test-todos.md)
for the tracked backlog entry.

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
| **Phase A** | **0** | Pytest only — no dedicated golden-cases.jsonl group (this is a routing-precedence question, not a numeric-oracle one) |
| **Phase B** | **20** | Fixed catalog in [llm-scenarios.md](llm-scenarios.md) (19 scenarios, CC13 is a 2-turn follow-up) |

Not part of the parent `/quality-test` 100-query cap, same as insulin (10) and
mixed-basket (20).

## Phase A — Deterministic (always run first)

Any `[FAIL]` → overall **BLOCK** (skip Phase B).

```bash
pytest tests/test_pharmacy_questions.py tests/test_budget_window.py tests/test_navigator.py -v
```

| Module | Covers |
|--------|--------|
| `test_pharmacy_questions.py` | Plan-key disambiguation when two plan keys are named in one message (nearest to a "network"/"pharmac..." anchor) |
| `test_budget_window.py` | Duration-guard extension to the plan-scoped pharmacy resolvers (Q1/Q2), mirroring the existing MixedBasket duration guard |
| `test_navigator.py` | General resolver-chain routing regressions |

## Phase B — 20 live LLM queries

Follow [llm-scenarios.md](llm-scenarios.md). Rephrase wording each run; scenario intent is
fixed.

**Run the fixed catalog (do not write ad-hoc batch scripts):**

```bash
python scripts/run_llm_scenarios.py --suite compound-questions
python scripts/run_llm_scenarios.py --suite compound-questions --failures-only
python scripts/run_llm_scenarios.py --suite compound-questions --output json > /tmp/compound-questions-llm.json
```

Suite data: `scripts/llm_scenario_suites/compound_questions.json`. Optional single
scenario: `--scenario CC12`.

| Block | Queries | Focus |
|-------|---------|-------|
| CC-A | 5 | OOP resolver starves the rest of the message |
| CC-B | 4 | Plan-scoped pharmacy answers: wrong plan / duration-blind — **fixed 2026-09-04** |
| CC-C | 3 | Pharmacy resolvers drop an accompanying insulin/policy question |
| CC-D | 2 | Missing-dosage compounds (control — expected PASS) |
| CC-E | 3 | Multi-plan / tier / duration compounds |
| CC-F | 3 | Follow-up and adversarial compounds |

**Per-query workflow** (or use the batch runner above for all 20):

```bash
medicare-chat-invoke send --message "..." --model gpt-5.6-luna
curl -s -X POST http://localhost:8000/api/estimate \
  -H 'Content-Type: application/json' \
  -d '{"plan_id":"...","drug":"...","days_supply":30,"ytd_oop_spend":0}'
```

Use real `plan_id` / drugs from `GET /api/plans` / `GET /api/drugs` when not using the FL
fixture anchors (`S9999-001`, `H8888-001`, ZIP `32801`).

### Grading rule (specific to this sub-skill)

A response that fully and correctly answers **half** of a two- or three-part compound
question is still a finding — not a pass — for the half it dropped. Apply
[chat-QA](../../utils/chat-QA/SKILL.md) dimension 1 to whichever half *was* answered, and
separately note any half that was silently dropped as its own BLOCK/REVISE line, even when
the answered half is perfectly grounded.

## Consolidated report

```markdown
## Compound-questions quality test — {date/time}

**Mode:** real LLM (models used: {list}) — {N}/20 real queries spent
**Overall verdict:** BLOCK | REVISE | PASS

### Phase A — Pytest
| Module | Result | Notes |
|--------|--------|-------|

### Phase B — CC-A OOP starves the message (5)
| # | Scenario | Expected | Actual | Verdict | Notes |
|---|----------|----------|--------|---------|-------|

### Phase B — CC-B Plan-scoped pharmacy: wrong plan / duration-blind (4)
| # | Scenario | Expected | Actual | Verdict | Notes |
|---|----------|----------|--------|---------|-------|

### Phase B — CC-C Pharmacy drops accompanying question (3)
| # | Scenario | Expected | Actual | Verdict | Notes |
|---|----------|----------|--------|---------|-------|

### Phase B — CC-D Missing-dosage compounds, control (2)
| # | Scenario | Expected | Actual | Verdict | Notes |
|---|----------|----------|--------|---------|-------|

### Phase B — CC-E Multi-plan / tier / duration (3)
| # | Scenario | Expected | Actual | Verdict | Notes |
|---|----------|----------|--------|---------|-------|

### Phase B — CC-F Follow-up and adversarial (3 queries)
| # | Scenario | Expected | Actual | Verdict | Notes |
|---|----------|----------|--------|---------|-------|

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
| A deterministic resolver answers only the first-matched half of a compound message | Add a defer-on-signal guard at that resolver's call site in `navigator.py::_resolve_deterministic`, mirroring the `has_unhandled_date_window` pattern already used for MixedBasket/Q1/Q2 |
| Wrong plan/drug picked when a message names more than one | Check whether the relevant extractor uses `extract_plan_key`/first-match semantics; consider an anchor-based disambiguator like `pharmacy_questions._extract_plan_key_for_pharmacy` |
| Agent-loop fallback drops one clause of a compound follow-up | [`/chat-bot-fixer`](../../../chat-bot-fixer/SKILL.md) — likely a system-prompt/tool-use-strategy issue, not a routing one |
