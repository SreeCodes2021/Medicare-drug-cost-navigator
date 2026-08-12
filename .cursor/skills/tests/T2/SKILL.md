---
name: functional-test
description: >-
  One-call Tier 2 functional test for the Medicare Navigator portal — every
  E2E surface flow (chat, guided single/multi/compare), dosage-follows-drug,
  cross-tab state carryover, disclaimer-on-every-response, and all-LLMs-work
  checks, run together with a single consolidated report. Use when the user
  invokes /functional-test, functional-test, or asks for a functional test of
  the app.
disable-model-invocation: true
---

# Functional test — Tier 2 (one call, whole tier)

User invoked this skill — run **everything** in the functional tier
end-to-end and report one consolidated result. Don't ask the user to
separately invoke `/ui-functionality` or any of its sub-skills; this skill
walks all of them itself.

**Scope:** does the app do the *right thing* — correct business logic,
correct state handling, correct compliance behavior (disclaimer, all models
working)? **Not in scope:** raw wiring (that's
[`/smoke-test`](../T1/SKILL.md)) or explanation quality / misleading
surfaces / oracle prose consistency (that's
[`/quality-test`](../T3/SKILL.md) § 1c).

## What this runs, in order

### 1. Pre-flight

```bash
scripts/build-frontend.sh
uvicorn medicare_navigator.api.app:app --reload --host 0.0.0.0 --port 8000 &
```

If a server can't be started (no keys, sandboxed), fall back to offline/mock mode (`LLM_MOCK=1`) for everything except the LLM-availability check, and say so clearly in the report.

### 2. E2E surface flows (all four)

Follow [`ui-functionality/SKILL.md`](ui-functionality/SKILL.md) for each surface — offline contracts, then browser flow:

```bash
pytest tests/test_ui.py::test_guided_estimate_ui_contract -v
medicare-ui-test run --offline --groups static,api,chat,guided
medicare-ui-test browser --flow chat --base-url http://localhost:8000
medicare-ui-test browser --flow guided-single --base-url http://localhost:8000
medicare-ui-test browser --flow guided-multi --base-url http://localhost:8000
medicare-ui-test browser --flow guided-compare-plan --base-url http://localhost:8000
```

### 3. Dosage follows drug name

Follow [`ui-functionality/dosage-dependency/SKILL.md`](ui-functionality/dosage-dependency/SKILL.md):

```bash
pytest tests/test_drug_lookup.py -v -k dosage
```
Plus the manual/browser drug-change-clears-dosage flow across Single/Multi/Compare.

### 4. Cross-tab / sub-tab state carryover

Follow [`ui-functionality/state-carryover/SKILL.md`](ui-functionality/state-carryover/SKILL.md):

```bash
pytest tests/test_ui.py -v -k "guided_state or guided_drug_and_dosage"
```
Plus the manual/browser persist-vs-reset matrix walkthrough (State persists across guided sub-tabs; drug/dosage and Chat-vs-Guided state do not leak).

### 5. Disclaimer on every response

Follow [`ui-functionality/disclaimer-everywhere/SKILL.md`](ui-functionality/disclaimer-everywhere/SKILL.md):

```bash
pytest tests/test_disclaimer_coverage.py -v
```

### 6. Multi-turn conversation limit (5-turn cap)

Follow [`ui-functionality/multi-turn-limit/SKILL.md`](ui-functionality/multi-turn-limit/SKILL.md):

```bash
pytest tests/test_disclaimer_coverage.py -v -k limit_reached
```
Plus the manual/browser 5-turn walkthrough (turn counter accuracy 1/5→5/5, graceful 6th-turn `limit_reached`, session/history intact) for both Chat and Guided.

### 7. All listed LLMs are working

Follow [`ui-functionality/llm-availability/SKILL.md`](ui-functionality/llm-availability/SKILL.md):

```bash
medicare-chat-invoke health
medicare-chat-invoke models
# For each model with configured:true:
medicare-chat-invoke send --message "What's the cost for metformin 500mg on plan S9999-001?" --model <id>
```

If live API keys aren't available in this environment, run the offline regression instead (`pytest tests/test_llm_client.py tests/test_llm_mock.py -v`) and clearly flag in the report that live per-model verification was not possible.

## Auto-fix policy

Same as `ui-functionality`: fix clear frontend/backend wiring bugs (missing dosage-clear call, wrong state variable, dropped disclaimer append, model routing bug) directly; re-run the relevant command from above before reporting done. Hand off explanation-quality-only findings to [`/quality-test`](../T3/SKILL.md) — don't try to grade prose quality here.

## One consolidated report

```markdown
## Functional test — {date/time}

**Mode:** offline | offline + live @ {base-url}
**Verdict:** PASS | FAIL ({n} issues)

### By check
| Check | Result | Notes |
|-------|--------|-------|
| Chat E2E | PASS/FAIL | |
| Guided single E2E | PASS/FAIL | |
| Guided multi E2E | PASS/FAIL | |
| Guided compare E2E | PASS/FAIL | |
| Dosage follows drug name | PASS/FAIL | |
| Cross-tab state carryover | PASS/FAIL | |
| Disclaimer everywhere | PASS/FAIL | |
| Multi-turn limit (5-turn cap) | PASS/FAIL | turn counter accuracy + graceful 6th-turn refusal |
| All LLMs working | PASS/FAIL/NOT-VERIFIED (no keys) | per-model breakdown |

### Issues found
| Severity | Symptom | Root cause | Fix |
|----------|---------|------------|-----|

### Auto-fixes applied
- `{file}` — {what changed}

### Not covered this run
{e.g. "no live API keys — LLM-availability checked via offline regression only"}
```

## Internal building blocks (do not ask the user to call these separately)

- [`ui-functionality/SKILL.md`](ui-functionality/SKILL.md) + its `chat`, `guided-single`, `guided-multi`, `guided-compare-plan` sub-skills.
- [`ui-functionality/dosage-dependency/SKILL.md`](ui-functionality/dosage-dependency/SKILL.md)
- [`ui-functionality/state-carryover/SKILL.md`](ui-functionality/state-carryover/SKILL.md)
- [`ui-functionality/disclaimer-everywhere/SKILL.md`](ui-functionality/disclaimer-everywhere/SKILL.md)
- [`ui-functionality/multi-turn-limit/SKILL.md`](ui-functionality/multi-turn-limit/SKILL.md)
- [`ui-functionality/llm-availability/SKILL.md`](ui-functionality/llm-availability/SKILL.md)
- [`quality-test`](../T3/SKILL.md) § 1c — misleading UI, disclaimer dim 4, answer-oracle consistency (Tier 3; runs same disclaimer pytest plus live LLM checks)

## Constraints

- **Do not fabricate test results.**
- **Do not commit** unless the user asks.
- Explanation-text quality (tone, plain language, safety rubric) and real-CMS numeric accuracy belong to `/quality-test` — note them if you notice something, but don't grade them here.
