---
name: ui-functionality-guided-single
description: >-
  Browser and API tests for Guided form Single sub-mode — drug, dosage, plan
  combobox, Get estimate, guided conversation and results. Use with
  /ui-functionality/guided-single.
disable-model-invocation: true
---

# UI functionality — Guided single

Parent: [ui-functionality/SKILL.md](../SKILL.md). Scenarios: [test-scenarios.md](../test-scenarios.md).

## Automated checks

```bash
scripts/build-frontend.sh
pytest tests/test_ui.py::test_guided_estimate_ui_contract -v
medicare-ui-test run --offline --groups guided
medicare-ui-test browser --flow guided-single --base-url http://localhost:8000
```

API parity:

```bash
medicare-chat-invoke send --message "What's the cost for metformin 500mg on plan S9999-001?" \
  --filters-json '{"drug":"metformin","dosage":"500mg","plan_id":"S9999-001","days_supply":30}'
```

## Browser flow steps

1. Open portal → click `#mode-tab-guided`.
2. Confirm Single sub-mode active (`#guided-mode-single.active`, `#guided-single` visible).
3. Fill `#filter-drug` = `metformin`, `#filter-dosage` = `500mg`.
4. Select plan via `#filter-plan-input` combobox → pick option containing `(S9999-001)`; hidden `#filter-plan` must be set.
5. Click `#guided-submit`.
6. Wait for assistant message in `#guided-chat-messages`; `#guided-loading` hidden.
7. Assert `#guided-turn-counter` = `1/5 turns`.
8. Assert `#guided-results-content` has estimate HTML (not placeholder).
9. Assert `#guided-chat-input` and `#guided-send-btn` enabled for follow-up.

## Validation (negative)

1. Clear drug/plan → click `#guided-submit`.
2. Expect `#guided-error` visible with validation text.

## Quality checklist

| Check | Expected |
|-------|----------|
| Plan list loads | `#filter-plan-listbox` options after plans API |
| Combobox uses hidden value | Request uses `plan_id` from `#filter-plan` |
| Fresh session | Each submit resets guided conversation |
| Five-turn limit | `#guided-turn-counter` stops at `5/5` |

## Failure → fix

| Symptom | Fix |
|---------|-----|
| `guided:html:id:*` | `frontend/src/index.html` |
| Plan not in payload | `createPlanCombobox`, hidden input wiring |
| Empty guided results | `renderGuidedResponse`, API `channel_estimate` |
| Validation not shown | `showGuidedError`, `guided-error` CSS |

Report using [report-template.md](../report-template.md).
