---
name: quality-test
description: >-
  One-call Tier 3 quality test for the Medicare Navigator portal — real (never
  mocked) LLM calls, up to 50 real test queries per invocation, covering
  numeric accuracy against the deterministic/real-CMS oracle, on-the-fly
  edge-case/adversarial/follow-up questioning, and the chat-QA
  safety/compliance rubric. Use when the user invokes /quality-test,
  quality-test, or asks for a quality test of the app's answers.
disable-model-invocation: true
---

# Quality test — Tier 3 (one call, whole tier, real LLM, up to 50 queries)

User invoked this skill — run **everything** in the quality tier end-to-end
against the **real, live LLM providers** (never the mock) and report one
consolidated result. Don't ask the user to separately invoke `/chat-QA`,
`/numeric-accuracy`, or `/exploratory-qa`; this skill runs all three itself.

**Scope:** are the *numbers* right and are the *explanations* safe,
compliant, and well-handled — including on inputs nobody expects? **Not in
scope:** raw wiring (`/smoke-test`) or business-logic correctness like
dosage-scoping/state-carryover/disclaimer-presence (`/functional-test`).

This tier is read-only/grading by default — it does not auto-fix. If the user
wants fixes applied, hand off to [`/chat-bot-fixer`](../chat-bot-fixer/SKILL.md)
after this report, or say so and ask first.

## Real LLM mandate — no mock, ever, in this skill

This tier exists specifically to catch what a mocked LLM can't: real
reasoning mistakes, real prompt-injection susceptibility, real per-provider
quirks. **Never run with `LLM_MOCK=1`** or against `tests/` fixtures for the
LLM-facing parts. Confirm before starting:

```bash
medicare-chat-invoke health
medicare-chat-invoke models
```

- `medicare-chat-invoke health` → `llm_configured: true` and no `LLM_MOCK`-related warning.
- `medicare-chat-invoke models` → check `configured: true` for every model you intend to test. The user has confirmed they're okay spending real API cost — don't hold back on that basis, but don't run more than the query budget below without asking.
- Every `response_source` in a graded bundle should read like `openai/<model>` or `anthropic/<model>` — if you ever see a `mock/...` source, stop and tell the user the server is in mock mode before grading anything.
- If any model is `configured: false` (missing API key), skip that model's live calls, note it in the report, and don't count it toward the query budget below.

## Query budget — up to 50 real queries per invocation

Every call to `medicare-chat-invoke send` (or a follow-up in the same
session) counts as one query against this budget. Default allocation —
adjust the split if the user asks for a different emphasis, but stay at or
under 50 total unless they explicitly raise the cap:

| Section | Default budget | Notes |
|---------|-----------------|-------|
| Numeric accuracy — live oracle diffs | 5 queries | Real chat/guided questions whose `$` figures get diffed against `/api/estimate`/`/api/compare-plans` (no LLM needed for the oracle side, but the chat side is a real query) |
| Happy-path quality baseline | 10 queries | Representative normal questions + follow-ups across chat, guided single/multi/compare, spread across at least 2 of the 3 catalog models |
| On-the-fly exploratory questioning | 30 queries | Fresh questions invented per [`exploratory-qa`](../exploratory-qa/SKILL.md)'s categories — malformed input, out-of-scope asks, meaningful/meaningless follow-ups, prompt injection |
| **Total** | **≤ 50** | If the user asks for a smaller/faster pass, scale each row down proportionally and say so in the report header |

Use real plan/drug combinations from the live data actually loaded on this
server (check `GET /api/plans`, `GET /api/drugs`, `GET /api/states` first) —
don't assume fixture keys like `S9999-001` exist outside an offline/fixture
environment.

## Prerequisites

```bash
medicare-chat-invoke health
```
If it fails, ask the user to start the server (`uvicorn medicare_navigator.api.app:app --reload --host 0.0.0.0 --port 8000`) before proceeding — quality checks need a live pipeline, not just offline contracts.

## What this runs, in order

### 1. Numeric accuracy (budget: 5 real queries + oracle diffs)

Follow [`numeric-accuracy/SKILL.md`](../numeric-accuracy/SKILL.md):

```bash
python scripts/run_golden_cases.py                                          # offline fixture golden cases (no LLM, free)
python scripts/run_golden_cases.py --include-live --base-url http://localhost:8000  # + real CMS golden cases (no LLM, free)
```

These two commands are free (no LLM) — they don't count against the 50-query budget. Then spend up to 5 real queries on live oracle diffs: ask the chat/guided pipeline the same question the deterministic oracle just answered, and diff the `$` figures in the real LLM's prose against it.

### 2. Happy-path quality baseline (budget: 10 real queries)

Send representative "normal" questions (tier lookup, a follow-up changing YTD or days supply, a plan comparison) using **real plan/drug data from this server**, and grade each with the full [`chat-QA`](../chat-QA/SKILL.md) 7-dimension rubric. Spread these across at least two of the three catalog models (`gpt-5.4-nano`, `gpt-5.6-luna`, `claude-haiku-4-5-20251001`) so a model-specific regression doesn't hide behind the default model.

```bash
medicare-chat-invoke send --message "What's the cost for <real drug> <dosage> on plan <real plan_id>?" --model gpt-5.4-nano
medicare-chat-invoke send --message "what if I've spent $800 YTD?" --session-id "<session_id>" --model gpt-5.4-nano
medicare-chat-invoke send --message "Compare <real drug> across <plan A> and <plan B>" --model claude-haiku-4-5-20251001
```

### 3. On-the-fly exploratory questioning (budget: 30 real queries)

Follow [`exploratory-qa/SKILL.md`](../exploratory-qa/SKILL.md) — invent fresh questions each run across all its categories (malformed input, out-of-scope asks, meaningful vs. meaningless follow-ups, prompt injection) and grade with the same rubric plus the "did not break" check. Distribute across categories roughly evenly (e.g. ~8 malformed, ~8 out-of-scope, ~14 follow-up pairs) rather than spending the whole budget on one category.

## One consolidated report

```markdown
## Quality test — {date/time}

**Mode:** real LLM (models used: {list}) — {N}/50 real queries spent
**Overall verdict:** BLOCK | REVISE | PASS (worst verdict across all graded turns)

### Numeric accuracy
| Case | Expected | Actual | Result |
|------|----------|--------|--------|
| golden-00N / live oracle diff | … | … | PASS/FAIL |

### Happy-path quality baseline
| Question | Model | Verdict | Weakest dimension |
|----------|-------|---------|--------------------|

### Exploratory findings
| Category | Question tried | Model | Did-not-break | Verdict | Notes |
|----------|-----------------|-------|----------------|---------|-------|

### Priority fixes needed (if not a clean PASS)
1. …

### Not covered this run
{e.g. "claude-haiku-4-5-20251001 not configured — skipped that model's queries"}
```

## Internal building blocks (do not ask the user to call these separately)

- [`numeric-accuracy/SKILL.md`](../numeric-accuracy/SKILL.md) + [`golden-cases.jsonl`](../numeric-accuracy/golden-cases.jsonl) + `scripts/run_golden_cases.py`
- [`chat-QA/SKILL.md`](../chat-QA/SKILL.md) — the rubric itself, applied to both the happy-path baseline and exploratory findings
- [`exploratory-qa/SKILL.md`](../exploratory-qa/SKILL.md) — the on-the-fly question categories

## Constraints

- **Real LLM only** — never grade a `mock/...` response in this skill; that's what `/functional-test`'s offline regression is for.
- **Never fabricate a grade or a golden value** — every number and verdict must come from a real `medicare-chat-invoke` call, `/api/estimate` call, or a manually re-verified CMS figure.
- **Stay at or under the 50-query budget** per invocation unless the user explicitly asks for more — real API cost is being spent.
- **Read-only by default** — do not edit code to fix findings; report them and offer `/chat-bot-fixer` if the user wants the loop closed.
- **Do not commit** unless the user asks.
