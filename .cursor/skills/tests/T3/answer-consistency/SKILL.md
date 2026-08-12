---
name: quality-test-answer-consistency
description: >-
  Quality-tier check that guided UI and live chat prose agree with the
  deterministic /api/estimate and /api/compare-plans oracle. Phase A is pytest
  (free); Phase B is 5 mandatory live-LLM spot-checks plus up to 5 escalation
  queries on failure. Use with /quality-test/answer-consistency.
disable-model-invocation: true
---

# Quality test — Answer consistency (oracle agreement)

Parent: [quality-test/SKILL.md](../SKILL.md).

Catches **wrong or confused results**: the deterministic pipeline already knows
the answer but UI or chat prose disagrees.

## Phase A — Deterministic (0 queries, always run)

```bash
pytest tests/test_answer_consistency.py -v
```

| Surface | Oracle | Fields |
|---------|--------|--------|
| Single estimate | `POST /api/estimate` | `covered`, `tier`, `benefit_phase`, `effective_phase`, channel costs |
| Compare plans | `POST /api/compare-plans` | Per-plan `covered`, tier, costs match individual estimates |
| Guided UI contract | `app.js` | `covered === false` → "Not covered" blocked state (not a dollar cost) |

Any Phase A `[FAIL]` → **BLOCK** (no escalation).

## Phase B — Live LLM oracle diffs (mandatory when models configured)

**5 baseline scenarios** — each = one `medicare-chat-invoke send` (1 query).
Use real `plan_id` + drug from `GET /api/plans` on this server.

| # | Scenario | Oracle | Pass |
|---|----------|--------|------|
| B1 | Covered drug cost | `POST /api/estimate` | Prose `$` and tier match oracle |
| B2 | Not-covered drug on plan | `POST /api/estimate` (`covered: false`) | Says not covered; no fabricated `$` |
| B3 | YTD follow-up (reuse `session_id`) | Second `POST /api/estimate` | Phase and `$` match second oracle |
| B4 | Tier lookup | `POST /api/estimate` → `data.tier` | States correct tier; dim 1 grounded |
| B5 | Same drug, second plan (tier or covered differs) | Oracle for *that* plan | Correct for second plan, not first |

```bash
medicare-chat-invoke send --message "What's the cost for <drug> <dosage> on plan <plan_key>?" --model gpt-5.6-luna
medicare-chat-invoke send --message "what if I've spent $<ytd> YTD?" --session-id "<session_id>" --model gpt-5.6-luna
```

Default model is **`gpt-5.6-luna`** (same as parent `/quality-test`). Use other models only when the user explicitly requests multi-model testing.

If **`gpt-5.6-luna`** is not `configured: true` → Phase B is **INCOMPLETE** (cannot be overall PASS).

## Escalation (when any B1–B5 fails)

1. Mark answer-consistency **BLOCK**.
2. Spend up to **5 more queries** from the reserved pool:

| Escalation step | Purpose |
|-----------------|---------|
| **Rephrase** same intent | Prompt fragility? |
| **Isolate field** | Tier vs `covered` vs `$` mismatch? |
| Add **compare-plans prose** check | If failure mode is multi-plan |
| Re-run failed scenario on **2nd model** | Model-specific bug? — **only when parent run is in multi-model mode** |

3. Classify: **model-specific**, **systematic guardrail**, or **inconclusive**.
4. If still inconclusive after 5 escalation queries → report **Escalation exhausted**; recommend standalone run with raised cap or [`/chat-bot-fixer`](../../../../chat-bot-fixer/SKILL.md).

**Typical PASS path:** 5 queries. **Worst-case FAIL path:** 10 queries (5 baseline + 5 escalation).

## Standalone invocation

When invoked outside `/quality-test`, may use up to **20 queries** (5 baseline + up to 15 escalation) if the user explicitly wants deeper diagnosis.

## Verdict

| Outcome | Overall |
|---------|---------|
| Phase A fail | BLOCK |
| Any baseline B1–B5 fail | BLOCK (+ escalation) |
| Phase B INCOMPLETE (no keys) | Cannot PASS overall |
| All pass | PASS for this sub-skill |
