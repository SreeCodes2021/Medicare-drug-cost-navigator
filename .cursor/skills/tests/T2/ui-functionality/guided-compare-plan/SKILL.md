---
name: ui-functionality-guided-compare-plan
description: >-
  Browser and API tests for Guided form Compare plans — drug fields, multiple
  plan combobox rows, comparison results. Use with /ui-functionality/guided-compare-plan.
disable-model-invocation: true
---

# UI functionality — Guided compare plan

Parent: [ui-functionality/SKILL.md](../SKILL.md). Scenarios: [test-scenarios.md](../test-scenarios.md).

## Automated checks

```bash
scripts/build-frontend.sh
medicare-ui-test run --offline --groups guided
medicare-ui-test browser --flow guided-compare-plan --base-url http://localhost:8000
```

API parity:

```bash
medicare-chat-invoke send --message "Compare the cost of metformin 500mg across these Medicare plans: S9999-001, H8888-001. Use a 30-day supply and \$0 year-to-date out-of-pocket spending. Summarize the differences and identify the lowest estimated cost." \
  --filters-json '{"drug":"metformin","dosage":"500mg","days_supply":30,"ytd_oop_spend":0}'
```

## Browser flow steps

1. Open portal → `#mode-tab-guided`.
2. Click `#guided-mode-compareplans`.
3. Fill `#cp-drug` = `metformin`, `#cp-dosage` = `500mg`.
4. Two plan rows exist by default (`#cp-plan-input-1`, `#cp-plan-input-2`).
5. Select `S9999-001` on row 1, `H8888-001` on row 2 via combobox options.
6. Click `#compareplans-submit`.
7. Wait for assistant in `#guided-chat-messages`.
8. Assert `#guided-results-content` shows comparison or multi-estimate cards.

## Validation (negative)

1. Enter drug only, leave one plan empty → submit with single plan → `#guided-error` (need ≥2 plans).

## Quality checklist

| Check | Expected |
|-------|----------|
| Min 2 plan rows | `resetComparePlanRows` creates two rows |
| Add plan | `#compareplans-add-row` up to 4 rows |
| Comparison disclaimer | `comparison-disclaimer-banner` when applicable |

### Channel + comparison prose (`/chat-QA`)

Browser flow only checks DOM. For compare-plans **accuracy**:

1. `medicare-chat-invoke send` with the same compare message (see API parity above).
2. Grade with `/chat-QA` — use `grading.channel_coverage` per plan.
3. Optional deterministic check: `POST /api/compare-plans` with the same `plan_ids`.

Flag when assistant text claims uniform pricing across all channels but tool data shows
`missing_channels`. Fixture plans `S9999-001` / `H8888-001` rarely trigger this; use
`H2802-063` / `H5216-366` with AR data for regression (see [test-scenarios.md](../test-scenarios.md)).

## Failure → fix

| Symptom | Fix |
|---------|-----|
| `submitComparePlans` / `getComparePlanValues` | `app.js` compare row comboboxes |
| Comparison layout | `renderPlanComparisonHtml`, `renderMultiEstimatesStackHtml` |
| Plan hidden inputs | `cp-plan-{n}` wiring |

Report using [report-template.md](../report-template.md).
