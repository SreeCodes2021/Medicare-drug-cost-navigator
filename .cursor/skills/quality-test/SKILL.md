---
name: quality-test
description: >-
  One-call Tier 3 quality test for the Medicare Navigator portal — real (never
  mocked) LLM calls, up to 50 real test queries per invocation, covering
  numeric accuracy against the deterministic/real-CMS oracle, mandatory OOP/MOOP
  scope checks (Part D cap vs medical MOOP, generic vs filtered plan), on-the-fly
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
| **OOP / MOOP scope** | **4 queries (required)** | **Always run** — see [§ OOP / MOOP scope](#oop--moop-scope-mandatory) below; counts toward the 50-query total (carve from happy-path + exploratory if needed) |
| On-the-fly exploratory questioning | 26 queries | Fresh questions per [`exploratory-qa`](../exploratory-qa/SKILL.md) — malformed input, out-of-scope asks, meaningful/meaningless follow-ups, prompt injection (OOP/MOOP cases live in the dedicated section above, not here) |
| **Total** | **≤ 50** | If the user asks for a smaller/faster pass, scale each row down proportionally and say so in the report header — **never skip the OOP/MOOP block entirely** |

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

### 2b. OOP / MOOP scope (mandatory — 4 queries every run)

These questions are **always** part of `/quality-test`. They catch a common
failure mode: conflating **Part D statutory annual OOP cap** (same across plans,
grounded via `get_part_d_benefit_params`) with **Medicare Advantage medical-network
MOOP** (plan-specific, not in CMS SPUF formulary data), or spuriously naming a
plan when the user asked generically (especially with a UI plan filter set).

Use a **real plan_key** from `GET /api/plans` for the medical-MOOP case. Run at
least one case on **gpt-5.6-luna** (default production model). Invent fresh
wording each run — the scenarios are fixed, the literal phrasing is not.

| # | Scenario | Example shape (rephrase each run) | Pass criteria |
|---|----------|----------------------------------|---------------|
| 1 | **Generic “any plan” OOP** | "for any plan, what is my max OOP according to CMS?" | Explains **both** Part D drug cap and medical MOOP limits; cites **$2,100** (2026) for Part D; **does not** call `lookup_plan` or name a specific plan |
| 2 | **Generic + UI filter** | Same as #1 with `--filters-json '{"plan_id":"<real plan_key>"}'` | Same as #1 — filter must **not** leak into the answer (no unprompted plan name) |
| 3 | **Part D annual cap only** | "What is the CMS Part D annual out-of-pocket maximum for 2026?" | States **$2,100.00**; tool is `get_part_d_benefit_params` (or `System/OOP` early return); dim 1 = grounded |
| 4 | **Medical MOOP with plan** | "Compare max OOP in and out of network for \<real plan_key\>" | `lookup_plan` ok; refuses medical MOOP from SPUF honestly; offers drug-cost estimate; **no** fabricated in/out-of-network dollar figures |

**Oracle for the Part D cap (free, no LLM):**

```bash
python -c "from medicare_navigator.tools.part_d_benefit_lookup import get_part_d_benefit_params; r=get_part_d_benefit_params(2026); print(r.data['annual_oop_cap'])"
```

Any `$` figure for the Part D cap in chat prose must match this exactly.

**Also grade (malformed, counts toward exploratory budget if not already covered):**
repeated drug tokens (e.g. `"metformin "` × 200 + `"500mg on <real plan>"`) must **not**
return a false `not_covered` when the drug is on the formulary — `needs_clarification`
or a correct `$` estimate is acceptable.

**Behavior anchor:** [`src/medicare_navigator/agent/oop_questions.py`](../../../src/medicare_navigator/agent/oop_questions.py)

### 3. On-the-fly exploratory questioning (budget: 26 real queries)

Follow [`exploratory-qa/SKILL.md`](../exploratory-qa/SKILL.md) — invent fresh questions each run across all its categories (malformed input, out-of-scope asks, meaningful vs. meaningless follow-ups, prompt injection) and grade with the same rubric plus the "did not break" check. Distribute across categories roughly evenly (e.g. ~7 malformed, ~7 out-of-scope, ~12 follow-up pairs). **OOP/MOOP scope cases are not duplicated here** — they are mandatory in [§ 2b](#2b-oop--moop-scope-mandatory--4-queries-every-run).

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

### OOP / MOOP scope (mandatory)
| Scenario | Model | Expected | Actual | Verdict | Notes |
|----------|-------|----------|--------|---------|-------|
| Generic any-plan OOP | … | Part D $2,100 + medical MOOP N/A in SPUF; no plan named | … | PASS/FAIL | |
| Generic + UI filter | … | Same; filter ignored | … | PASS/FAIL | |
| Part D annual cap 2026 | … | $2,100.00 grounded | … | PASS/FAIL | |
| Medical MOOP + plan_key | … | lookup + honest refusal | … | PASS/FAIL | |

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
