---
name: UI-tester
description: >-
  Diagnose and verify Medicare Drug Cost Navigator UI functionality — static
  assets, API contracts, chat smoke flows, filters, results rendering, and
  session behavior. Use when the user invokes /UI-tester, UI-tester, says the UI
  isn't working, or asks to test, verify, or debug the frontend.
disable-model-invocation: true
---

# UI Tester — Medicare Navigator Frontend Verification

User invoked this skill — find why the UI is broken, run the repo UI test suite,
and report pass/fail per surface area. Fix UI bugs when the root cause is clear;
hand off chat-quality issues to [`/chat-QA`](../chat-QA/SKILL.md).

## Prerequisites

```bash
pip install -e ".[dev]"
```

| Mode | Server needed? | Command |
|------|----------------|---------|
| **Offline** (default for CI) | No | `medicare-ui-test run --offline` or `pytest tests/test_ui.py -v` |
| **Live** | Yes on port 8000 | `medicare-ui-test run --base-url http://localhost:8000` |

Start the server for live checks:

```bash
uvicorn medicare_navigator.api.app:app --reload --host 0.0.0.0 --port 8000
```

Open http://localhost:8000 — UI is served from `frontend/dist/` (there is no separate build step).

## Quick diagnose workflow

1. **Offline contract tests** — fastest signal, no server:

```bash
pytest tests/test_ui.py -v
medicare-ui-test run --offline
```

2. **Live smoke** — confirms real HTTP + latency:

```bash
medicare-ui-test run --base-url http://localhost:8000
```

3. **Inspect failures** — JSON output lists each check by `group` (`static`, `api`, `chat`).

4. **Fix or hand off** — see mapping below.

## CLI reference

```bash
# All groups (static + api + chat)
medicare-ui-test run --offline

# Subset
medicare-ui-test run --offline --groups static,api

# List contracts the UI depends on
medicare-ui-test list
```

Implementation: [`src/medicare_navigator/ui_test/checks.py`](../../../src/medicare_navigator/ui_test/checks.py), CLI: [`cli.py`](../../../src/medicare_navigator/ui_test/cli.py).

## What gets tested

### Static (`static` group)

| Check | Why it matters |
|-------|----------------|
| `frontend/dist/{index.html,app.js,styles.css}` on disk | FastAPI mounts this folder; missing files → blank page |
| Required element IDs in HTML | `app.js` uses `getElementById`; missing id → runtime null errors |
| `app.js` fetch paths `/api/disclaimer`, `/api/plans`, `/api/chat` | Broken paths → disclaimer/plans/chat never load |
| Core functions: `loadDisclaimer`, `loadPlans`, `sendMessage`, `renderResults`, `getFilters` | Deleted/renamed → UI dead on load or submit |
| Prompt chips (≥3) | Empty-state quick prompts |

### API contract (`api` group)

| Endpoint | UI consumer |
|----------|-------------|
| `GET /api/disclaimer` → `{text}` | `#disclaimer-text` banner |
| `GET /api/plans` → `[{plan_key, plan_name, …}]` | `#filter-plan` dropdown |
| `GET /api/health`, `/api/meta/as-of` | Ops / data-as-of (indirect) |

### Chat smoke (`chat` group)

| Flow | UI behavior verified |
|------|---------------------|
| POST `/api/chat` tier lookup | Messages append, `turn_count`, results cards |
| POST follow-up with `session_id` + filters | Session persistence, turn counter `N/5` |
| Response envelope fields | `renderResults()` reads `status`, `formulary`, `cost_trend`, `alternatives`, `citations`, `data_as_of`, `tool_statuses`, `response_source` |

Smoke messages are defined in `SMOKE_MESSAGES` inside `checks.py`.

## UI surface checklist (manual / browser)

Run after automated checks pass or when debugging visual/interaction bugs:

| Area | Steps | Expected |
|------|-------|----------|
| **Disclaimer** | Load page | Yellow banner shows text (not "Loading disclaimer…") |
| **Plans filter** | Open plan dropdown | Demo plans listed with `plan_key` |
| **Filter badge** | Set drug + plan | Badge count increases |
| **Collapse filters** | Click `−` on Filters | Panel hides; button shows `+` |
| **Prompt chips** | Click a chip | User message sent; loading spinner; assistant reply |
| **Chat send** | Type + Send | Empty state removed; user + assistant bubbles |
| **Turn counter** | After send | Shows `1/5 turns` (increments each turn) |
| **Results — ok** | Metformin + H1234-045 query | Formulary card, optional trend/alternatives/citations |
| **Results — clarify** | "metformin copay" (no plan) | Warning in results; chat shows clarification |
| **Results — follow-up** | Second message same session | Results merge (baseline preserved) |
| **Error path** | Stop server, send message | "Sorry, something went wrong" in chat |

Full matrix: [test-matrix.md](test-matrix.md).

## Failure → fix mapping

| Failed check / symptom | Likely cause | Fix location |
|------------------------|--------------|--------------|
| `disk:*` or `served:/` | Missing `frontend/dist` | Restore/build static files |
| `html:id:*` or `js:refs:*` | HTML/JS contract drift | `frontend/dist/index.html`, `app.js` — keep IDs in sync |
| `js:fetch:*` | Wrong API path in JS | `frontend/dist/app.js` |
| `api:disclaimer:*` | Config/disclaimer file | `config/disclaimer.txt`, `config.py` |
| `api:plans:nonempty` | DB not seeded | `medicare-ingest` |
| `chat:*:http` timeout | Server down or LLM slow | Start server; increase `--timeout`; check `.env` keys |
| `chat:*:visible_text` empty | Pipeline returns no explanation | [`/chat-bot-fixer`](../chat-bot-fixer/SKILL.md) |
| `chat:*:field:*` | API response shape changed | `models/response.py` + `app.js` `renderResults` |
| Blank page, 404 on `/app.js` | Static mount missing | `api/app.py` `_frontend` mount |
| CORS errors (dev on other port) | Origin not allowed | `CORS_ORIGINS` in `.env` |

## Output format

Present results as:

```markdown
## UI test summary

**Mode:** offline | live @ http://localhost:8000
**Verdict:** PASS | FAIL ({n} failed)

### By group
| Group | Pass | Fail | Notes |
|-------|------|------|-------|
| static | … | … | … |
| api | … | … | … |
| chat | … | … | … |

### Failures (if any)
- `{check_name}` — {detail} → {suggested fix}

### Manual follow-ups
{only if automated passed but user reports visual bugs}
```

If fixing code, re-run `pytest tests/test_ui.py -v` and `medicare-ui-test run --offline` before reporting done.

## Related skills

| Skill | When |
|-------|------|
| [`/chat-QA`](../chat-QA/SKILL.md) | Assistant text quality, citations, compliance |
| [`/chat-bot-fixer`](../chat-bot-fixer/SKILL.md) | Implement pipeline fixes from chat-QA |
| [`/commit-push`](../commit-push/SKILL.md) | Commit after UI fixes (runs `test_ui.py` for `frontend/**`) |

## Constraints

- **Do not fabricate test results** — run `medicare-ui-test` or `pytest tests/test_ui.py`.
- **Do not grade chat quality here** — UI tester checks wiring and contracts; chat-QA grades explanations.
- **Edit `frontend/dist/` directly** — there is no separate frontend build pipeline in Phase 1.
- **Do not commit** unless the user asks.
