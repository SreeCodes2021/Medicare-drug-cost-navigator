---
name: ui-functionality-disclaimer-everywhere
description: >-
  Sweep every response status (ok, needs_clarification, not_found,
  limit_reached) and every surface (chat, guided single/multi/compare,
  compare-plans API, static banner) to confirm the disclaimer is never
  missing. Use with /ui-functionality/disclaimer-everywhere.
disable-model-invocation: true
---

# UI functionality — Disclaimer everywhere

Parent: [ui-functionality/SKILL.md](../SKILL.md).

## Mechanism (read before "fixing" anything)

There are **three independent layers** — a gap in any one of them is a real bug:

1. **`QueryResponse.disclaimer` field** — [`agent/navigator.py`](../../../../src/medicare_navigator/agent/navigator.py) sets `disclaimer=settings.disclaimer_text` on **every** return path (`ok`, `needs_clarification`, `not_found`, `limit_reached`). The frontend does not render this field for chat/guided — it exists for API consumers and future UI use.
2. **Inline force-append into `explanation`** — [`guardrails/citations.py`](../../../../src/medicare_navigator/guardrails/citations.py) `apply_guardrails` appends `settings.disclaimer_text` to the visible `explanation` text if the LLM didn't already include it. This only runs on the `ok`/explanation-generating path.
3. **Static banner** — `#disclaimer-banner` loads once via `GET /api/disclaimer` and is positioned outside both `#mode-chat` and `#mode-guided` panels, so it is visible regardless of which tab is active.
4. **Compare-plans specific caveat** — `PlanComparisonApiResponse.disclaimer` ([`models/response.py`](../../../../src/medicare_navigator/models/response.py)) always carries the "premiums not included / not a switch recommendation" text on every `/api/compare-plans` response.

## Automated checks

```bash
pytest tests/test_disclaimer_coverage.py -v
```

Sweeps: ok / needs_clarification / not_found / limit_reached chat statuses, `/api/compare-plans`, `/api/disclaimer`, and banner DOM placement.

## Browser flow (manual / Playwright)

1. Load the portal — banner shows real text immediately (not stuck on "Loading disclaimer…").
2. Switch Chat → Guided → Chat — banner remains visible and unchanged the whole time.
3. Ask a normal cost question in Chat — assistant explanation text ends with (or contains) the disclaimer sentence.
4. Ask a vague question with no drug named — clarification message still shown with the banner present (banner never depends on chat status).
5. Guided → Compare plans → submit — comparison result includes the "not a recommendation to switch plans" caveat, distinct from the general disclaimer.
6. Hit the 5-turn guided limit — the "session has reached the maximum" message still displays alongside the persistent banner.

## Failure → fix

| Symptom | Fix |
|---------|-----|
| `disclaimer` field empty on any status | `agent/navigator.py` — check every `QueryResponse(...)` construction sets `disclaimer=settings.disclaimer_text` |
| Explanation text missing disclaimer on `ok` | `guardrails/citations.py` `apply_guardrails` — the `if settings.disclaimer_text and ... not in out` branch |
| Banner blank or "Loading…" stuck | `GET /api/disclaimer` failing, or `config/disclaimer.txt` missing — see `config.py` `disclaimer_text` property |
| Banner disappears when switching tabs | Banner element accidentally nested inside `#mode-chat` or `#mode-guided` instead of `#main-panel` sibling |
| Compare-plans missing the comparison caveat | `PlanComparisonApiResponse.disclaimer` default removed or overwritten in `api/app.py` |

Report using [report-template.md](../report-template.md).
