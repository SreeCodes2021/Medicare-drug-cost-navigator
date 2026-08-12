---
name: ui-functionality
description: >-
  End-to-end Medicare Navigator portal testing — browser flows (Playwright) plus
  offline API/contract checks for chat, guided single-drug, multi-drug, and
  compare-plans surfaces. Reports functional and quality issues and auto-fixes
  clear frontend wiring bugs. Use when the user invokes /ui-functionality,
  ui-functionality, or asks to test portal functionality, guided form, or chat UI flows.
disable-model-invocation: true
---

# UI Functionality — Portal E2E Testing

User invoked this skill — exercise the live portal like a beneficiary, report issues, auto-fix when root cause is clear.

## Sub-skills

| Invoke | Surface | Skill |
|--------|---------|-------|
| `/ui-functionality/chat` | Chat tab | [chat/SKILL.md](chat/SKILL.md) |
| `/ui-functionality/guided-single` | Guided → Single | [guided-single/SKILL.md](guided-single/SKILL.md) |
| `/ui-functionality/guided-multi` | Guided → Multiple drugs | [guided-multi/SKILL.md](guided-multi/SKILL.md) |
| `/ui-functionality/guided-compare-plan` | Guided → Compare plans | [guided-compare-plan/SKILL.md](guided-compare-plan/SKILL.md) |
| `/ui-functionality/dosage-dependency` | Dosage list follows drug name | [dosage-dependency/SKILL.md](dosage-dependency/SKILL.md) |
| `/ui-functionality/state-carryover` | Cross-tab/sub-tab state persist-vs-reset | [state-carryover/SKILL.md](state-carryover/SKILL.md) |
| `/ui-functionality/disclaimer-everywhere` | Disclaimer on every status/surface | [disclaimer-everywhere/SKILL.md](disclaimer-everywhere/SKILL.md) |
| `/ui-functionality/llm-availability` | Every catalog LLM actually responds | [llm-availability/SKILL.md](llm-availability/SKILL.md) |
| `/ui-functionality/multi-turn-limit` | 5-turn conversation cap — counter, session state, graceful 6th-turn refusal | [multi-turn-limit/SKILL.md](multi-turn-limit/SKILL.md) |

For a single call that runs this skill plus all nine sub-skills together, use [`/functional-test`](../SKILL.md).

If the user names a surface, read and follow that sub-skill. Otherwise run offline contracts for all groups, then browser flows for each surface.

## Prerequisites

```bash
pip install -e ".[dev,browser]"
playwright install chromium
```

| Step | Command |
|------|---------|
| Build static UI | `scripts/build-frontend.sh` (required when `frontend/src/` is newer than `frontend/dist/`) |
| Offline contracts | `pytest tests/test_ui.py -v` and `medicare-ui-test run --offline --groups static,api,chat,guided` |
| Live server | `uvicorn medicare_navigator.api.app:app --reload --host 0.0.0.0 --port 8000` |
| Browser flow | `medicare-ui-test browser --flow <name> --base-url http://localhost:8000` |

Set `LLM_MOCK=1` in `.env` or rely on test autouse mock for offline runs. For live browser tests without API keys, use mock LLM.

Shared scenarios: [test-scenarios.md](test-scenarios.md). Report format: [report-template.md](report-template.md).

## Hybrid workflow

1. **Pre-flight** — run `scripts/build-frontend.sh` if dist missing or stale; confirm `frontend/dist/` exists.
2. **Offline contracts** — fastest signal, no browser:

```bash
pytest tests/test_ui.py -v
medicare-ui-test run --offline --groups static,api,chat,guided
```

3. **Browser flow** (per surface) — real clicks and typing:

```bash
medicare-ui-test browser --flow chat --base-url http://localhost:8000
medicare-ui-test browser --flow guided-single --base-url http://localhost:8000
medicare-ui-test browser --flow guided-multi --base-url http://localhost:8000
medicare-ui-test browser --flow guided-compare-plan --base-url http://localhost:8000
```

4. **Inspect JSON** — browser command prints `FlowResult` JSON (`ok`, `checks`, `console_errors`, `detail`).
5. **Fix or hand off** — see auto-fix policy below; re-run step 2–3 before reporting done.

## Auto-fix policy

| Issue type | Action |
|------------|--------|
| Missing HTML id / JS ref drift | Fix `frontend/src/`, rebuild dist |
| Wrong fetch path or response field | `app.js` + `models/response.py` |
| Guided contract drift | `ui_test/checks.py` `GUIDED_ELEMENT_IDS` |
| Explanation quality only | Report issue; use [`/chat-QA`](../../utils/chat-QA/SKILL.md) — check `channel_warnings` for compare/cost answers |
| Pipeline / tool bugs | [`/chat-bot-fixer`](../../../chat-bot-fixer/SKILL.md) |

**Do not fabricate test results.** **Do not commit** unless the user asks.

## Related skills

| Skill | When |
|-------|------|
| [`/UI-tester`](../../T1/UI-tester/SKILL.md) | Static/API contract smoke (lighter than E2E) |
| [`/chat-QA`](../../utils/chat-QA/SKILL.md) | Grade assistant explanation quality |
| [`/chat-bot-fixer`](../../../chat-bot-fixer/SKILL.md) | Fix pipeline from chat-QA findings |
