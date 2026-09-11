---
name: ui-functionality-chat
description: >-
  Browser and API-parity tests for the Medicare Navigator chat tab — prompt chips,
  send, results panel, follow-ups, turn counter. Use with /ui-functionality-chat
  or when testing chat portal functionality.
disable-model-invocation: true
---

# UI functionality — Chat

Parent: [ui-functionality/SKILL.md](../ui-functionality/SKILL.md). Scenarios: [test-scenarios.md](../ui-functionality/test-scenarios.md).

## Automated checks

```bash
scripts/build-frontend.sh
pytest tests/test_ui.py -v
medicare-ui-test run --offline --groups static,api,chat
medicare-ui-test browser --flow chat --base-url http://localhost:8000
```

API parity (same payload as UI):

```bash
medicare-chat-invoke send --message "What's the cost for metformin 500mg on plan S9999-001?"
```

## Browser flow steps

1. Open `http://localhost:8000` — Chat tab active (`#mode-tab-chat`).
2. Confirm `#disclaimer-text` is not stuck on "Loading…".
3. Type in `#chat-input`: `What's the cost for metformin 500mg on plan S9999-001?`
4. Submit `#chat-form` / `#send-btn`.
5. Wait for `.message.assistant` in `#chat-messages`; `#loading` hidden.
6. Assert `#turn-counter` shows `1/5 turns`.
7. Assert `#results-content` has no `.placeholder` (estimate cards or clarification).
8. Optional follow-up: type in `#chat-input`, submit; turn counter increments.

## Quality checklist

| Check | Expected |
|-------|----------|
| Empty state | `#empty-state` with ≥3 `.chip` buttons |
| Send disabled while loading | `#send-btn` disabled during request |
| User bubble | `.message.user` with submitted text |
| Assistant bubble | `.message.assistant` with non-empty body |
| Results | Formulary/cost content or clarification warning |
| Error path | Stop server → friendly "Sorry" assistant message |

### Explanation quality (hand off to `/chat-qa`)

After a successful send, run `medicare-chat-invoke send` with the same message and grade.
For cost or compare answers, always inspect:

- `grading.channel_coverage` — which channels have numeric estimates
- `grading.channel_warnings` — non-empty ⇒ **REVISE/BLOCK** on dimension 1 unless prose
  qualifies missing channels

Compare-plans chat must not claim "all CMS pharmacy channels" when `missing_channels` is
non-empty. Fixture-only tests will not catch this — use the AR regression scenario in
[test-scenarios.md](../ui-functionality/test-scenarios.md).

## Failure → fix

| Symptom | Fix |
|---------|-----|
| Blank page | `frontend/dist/`, `api/app.py` static mount |
| `chat:*:field:*` failures | `models/response.py`, `app.js` `renderResults` |
| No visible text | [`/chat-bot-fixer`](../chat-bot-fixer/SKILL.md) |
| Chip click no-op | `app.js` chip listener, `html:prompt_chips` |

Report using [report-template.md](../ui-functionality/report-template.md).
