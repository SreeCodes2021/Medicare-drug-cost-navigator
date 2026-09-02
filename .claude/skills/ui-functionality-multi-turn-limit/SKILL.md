---
name: ui-functionality-multi-turn-limit
description: >-
  Verify a session correctly counts up through all 5 allowed chat turns
  (`settings.max_chat_turns`), the turn counter/UI state track it accurately at
  each step, and the 6th message is refused gracefully via `limit_reached`
  without losing prior conversation state. Use with
  /ui-functionality-multi-turn-limit.
disable-model-invocation: true
---

# UI functionality — Multi-turn conversation limit (up to 5 back-and-forth turns)

Parent: [ui-functionality/SKILL.md](../ui-functionality/SKILL.md).

**Scope:** mechanical/deterministic — does the turn counter, session state, and
limit-reached gate wire up correctly across a full 5-turn conversation? Runs
offline with `LLM_MOCK=1`, no real API cost. **Not in scope:** whether the
assistant's *answers* stay coherent/accurate through 5 real turns of
conversation — that's live-LLM depth testing, covered by
[`/quality-test` § 2i](../quality-test/SKILL.md#2i-multi-turn-conversation-depth-mandatory--30-queries-every-run).

## Why this exists

`settings.max_chat_turns = 5` ([`config.py`](../../../src/medicare_navigator/config.py))
is a hard product limit enforced in
[`session/manager.py`](../../../src/medicare_navigator/session/manager.py)
(`session["turn_count"] < settings.max_chat_turns`) and
[`agent/navigator.py`](../../../src/medicare_navigator/agent/navigator.py)
(`limit_reached` early return around line 896). Before this sub-skill existed, the
only automated coverage was
[`tests/test_disclaimer_coverage.py::test_limit_reached_status_still_carries_disclaimer`](../../../tests/test_disclaimer_coverage.py)
— which drives 6 turns but only asserts the disclaimer is present on the 6th,
not that turns 1–5 each behaved correctly or that the UI reflects the count.
T1's `test-matrix.md` explicitly lists "Limit reached" as `(manual)` — never
automated. This closes that gap.

## Automated checks (API-level, mock-friendly)

```bash
pytest tests/test_disclaimer_coverage.py -v -k limit_reached
pytest tests/test_session_manager.py -v 2>/dev/null || true  # if present
```

Add/confirm a contract test that walks all 5 turns and inspects each response,
not just the 6th (extend `test_disclaimer_coverage.py` or add
`tests/test_multi_turn_limit.py` if the assertions below aren't already covered):

| Turn | `status` | `session_id` | Notes |
|------|----------|--------------|-------|
| 1 | `ok` / `needs_clarification` | assigned, then reused turns 2–6 | First turn always allowed |
| 2–5 | `ok` / `needs_clarification` | same as turn 1 | `turn_count` increments each turn; still under `max_chat_turns` |
| 6 | `limit_reached` | same as turn 1 | Disclaimer still present (existing test); explanation names the limit; no tool call attempted |

Pass criteria beyond the existing disclaimer check:
- `session_id` is identical across all 6 requests (session isn't silently reset).
- Turns 1–5 never return `limit_reached` early (the gate doesn't trip early or late).
- Turn 6's response does not fabricate a `$` figure or tool result — `grading`/`tools_invoked` empty on the `limit_reached` turn.

## Browser flow (manual / Playwright)

1. Open Chat tab, send **5** distinct real questions in the same session (vary drug/plan
   each turn so it's not just prompt repetition, e.g. metformin → januvia → a follow-up
   YTD tweak → a different plan → a plan comparison).
2. After each send, assert `#turn-counter` reads `N/5 turns` matching the turn just
   completed (`1/5` → `5/5`), never skipping or double-incrementing.
3. Assert `#send-btn` / `#chat-form` remain usable through turn 5 (not disabled early).
4. Send a **6th** message. Assert:
   - The UI surfaces a clear "conversation limit reached" message (not a silent failure,
     not a raw error, not a blank assistant bubble).
   - `#turn-counter` does not exceed `5/5` (or shows the limit state, per current
     `app.js` behavior — confirm against source, don't assume).
   - Prior 5 turns' messages remain visible in `#chat-messages` (limit reached does not
     clear history).
5. Repeat steps 1–4 for the **Guided** flow's shared `guidedSessionId` (see
   [state-carryover/SKILL.md](../ui-functionality-state-carryover/SKILL.md) step 3 for how the guided
   turn counter is shared across Single/Multi/Compare) — confirm the same 5-turn cap
   and graceful 6th-turn behavior apply there too, not just Chat.
6. Confirm "New chat" (if present) resets the counter to `0/5` and issues a fresh
   `session_id`, unblocking further turns.

## Failure → fix

| Symptom | Fix |
|---------|-----|
| Turn counter skips/lags the actual turn count | `app.js` turn-counter update not tied 1:1 to each `/api/chat` response |
| 6th turn returns a raw error / blank bubble instead of a clear limit message | `app.js` doesn't special-case `status: "limit_reached"` in `renderResults`/message rendering |
| Session resets (new `session_id`) mid-conversation before turn 5 | Client dropping/regenerating `sessionId` instead of reusing it across sends |
| Prior messages disappear after hitting the limit | Chat history array cleared instead of appended-to on the `limit_reached` response |
| Guided flow doesn't enforce the same cap as Chat | `guidedSessionId` bypassing the shared `session/manager.py` turn-count gate |

Report using [report-template.md](../ui-functionality/report-template.md).
