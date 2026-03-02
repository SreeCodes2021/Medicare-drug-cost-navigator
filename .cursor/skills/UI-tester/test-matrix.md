# UI-tester test matrix

Maps **UI surfaces** → **automated checks** and **manual verification** for [`UI-tester`](SKILL.md).

## Automated groups

| Group | CLI flag | Pytest |
|-------|----------|--------|
| Static files + HTML/JS contract | `--groups static` | `tests/test_ui.py` (static tests) |
| API endpoints UI depends on | `--groups api` | `test_ui_api_endpoints` |
| Chat smoke + follow-up | `--groups chat` | `test_chat_smoke_offline` |

Default: all three (`medicare-ui-test run`).

## Surface → test mapping

| UI surface | Element / API | Automated | Manual |
|------------|---------------|-----------|--------|
| Disclaimer banner | `#disclaimer-text`, `GET /api/disclaimer` | `api:disclaimer:*`, `js:fetch:/api/disclaimer` | Text visible, not stuck on "Loading…" |
| Header | `.app-header` | `served:/` | Title visible below banner |
| Filters panel | `#filters-panel`, `#toggle-filters` | `html:id:filters-panel` | Collapse/expand works |
| Drug filter | `#filter-drug` | `html:id:filter-drug`, `js:refs:filter-drug` | Typing updates badge |
| Dosage filter | `#filter-dosage` | same pattern | — |
| Plan select | `#filter-plan`, `GET /api/plans` | `api:plans:*` | Options populated |
| Year select | `#filter-year` | `html:id:filter-year` | 2025/2026 options |
| YTD spend | `#filter-ytd` | `html:id:filter-ytd` | Number accepted in chat payload |
| Include alternatives | `#filter-alternatives` | `html:id:filter-alternatives` | Checkbox toggles |
| Include cost trend | `#filter-trend` | `html:id:filter-trend` | Checkbox toggles |
| Filter badge | `#filter-badge` | `html:id:filter-badge` | Count matches filled filters |
| Chat empty state | `#empty-state`, `.chip` | `html:prompt_chips` | Chips send messages |
| Chat messages | `#chat-messages` | `html:id:chat-messages` | Scroll, user/assistant roles |
| Loading | `#loading`, `#loading-text` | `html:id:loading` | Spinner during request |
| Chat input | `#chat-form`, `#chat-input`, `#send-btn` | `html:id:chat-input` | Submit disabled while loading |
| Turn counter | `#turn-counter` | `html:id:turn-counter` | Shows `N/5 turns` after chat |
| Results placeholder | `#results-content` | `html:id:results-content` | Cards after ok response |
| Formulary card | `renderFormularyCard` | `chat:*:field:formulary` | Tier, copay, phase pill |
| Cost trend card | `renderCostTrendCard` | `chat:*:field:cost_trend` | Bars when data present |
| Alternatives card | `renderAlternativesCard` | `chat:*:field:alternatives` | List when data present |
| Citations card | `renderCitationsCard` | `chat:*:field:citations` | Expandable details |
| Data as of | `#data-as-of` | `chat:*:field:data_as_of` | Badge visible when dates present |
| Tool statuses | footer in `renderBaseline` | `chat:*:field:tool_statuses` | Tool names shown |
| Session follow-up | `session_id` in POST body | `chat:*:follow_up:*` | Turn count increments |
| Clarification state | `status: needs_clarification` | `chat:*:status` | Warning in results panel |
| Limit reached | `status: limit_reached` | — (manual) | Warning; baseline preserved |
| Network error | catch in `sendMessage` | — (manual) | Friendly error bubble |

## Path → required verify (commit-push)

| Staged path | Required commands |
|-------------|-------------------|
| `frontend/**` | `pytest tests/test_ui.py -v` |
| `src/medicare_navigator/api/**` (static mount) | `pytest tests/test_ui.py tests/test_follow_up.py -v` |
| `src/medicare_navigator/ui_test/**` | `pytest tests/test_ui.py -v` |
| `src/medicare_navigator/models/response.py` | `pytest tests/test_ui.py -v` |

## Smoke message catalog

Defined in `SMOKE_MESSAGES` ([`checks.py`](../../../src/medicare_navigator/ui_test/checks.py)):

1. **tier_lookup** — full drug + plan query (expects `ok` or `needs_clarification`)
2. **chip_prompt_alternatives** — lipitor alternatives chip text (expects `ok`, `needs_clarification`, or `not_found`)

Add new smoke cases when adding UI features that depend on new API shapes.
