---
name: ui-functionality-guided-multi
description: >-
  Browser and API tests for Guided form Multiple drugs — plan combobox, drug
  rows, combined estimate, guided results. Use with /ui-functionality-guided-multi.
disable-model-invocation: true
---

# UI functionality — Guided multi (multiple drugs)

Parent: [ui-functionality/SKILL.md](../ui-functionality/SKILL.md). Scenarios: [test-scenarios.md](../ui-functionality/test-scenarios.md).

## Automated checks

```bash
scripts/build-frontend.sh
medicare-ui-test run --offline --groups guided
medicare-ui-test browser --flow guided-multi --base-url http://localhost:8000
```

API parity:

```bash
medicare-chat-invoke send --message "Estimate costs for metformin 500mg, januvia 100mg on plan S9999-001. Use a 30-day supply and \$0 year-to-date out-of-pocket spending. Summarize each drug and the combined cost."
```

## Browser flow steps

1. Open portal → `#mode-tab-guided`.
2. Click `#guided-mode-multidrug` (Multiple drugs tab).
3. Select plan on `#md-plan-input` → option `(S9999-001)`.
4. Fill first drug row `#md-drug-1` / `#md-dosage-1` = `metformin` / `500mg`.
5. Click `#multidrug-add-row` → fill `#md-drug-2` / `#md-dosage-2` = `januvia` / `100mg`.
6. Click `#multidrug-submit`.
7. Wait for assistant in `#guided-chat-messages`.
8. Assert `#guided-turn-counter` = `1/5 turns`.
9. Assert `#guided-results-content` shows multi-drug / batch estimate content.

## Validation (negative)

1. Clear plan → `#multidrug-submit` → `#guided-error` mentions plan.
2. Clear all drug fields → error mentions drug.

## Quality checklist

| Check | Expected |
|-------|----------|
| Add row | `#multidrug-add-row` adds up to 5 rows |
| Remove row | Cannot remove last row |
| Combined message | User bubble lists both drugs and plan |

## Failure → fix

| Symptom | Fix |
|---------|-----|
| `submitMultiDrugEstimate` missing | `app.js` |
| Batch HTML wrong | `renderMultiEstimatesStackHtml`, `renderGuidedResponse` |
| Row controls broken | `addDrugRow`, `multidrug-rows` |

Report using [report-template.md](../ui-functionality/report-template.md).
