---
name: quality-test-no-false-signals
description: >-
  Quality-tier check that the portal UI never shows misleading pre-estimate
  signals (e.g. formulary labels in drug/dosage pickers). Deterministic only.
  Use with /quality-test-no-false-signals or as part of /quality-test § 1c-A.
disable-model-invocation: true
---

# Quality test — No false signals (misleading UI)

Parent: [quality-test/SKILL.md](../quality-test/SKILL.md).

## Why this exists

Beneficiaries trust what they see **before** they submit an estimate. The anchor
bug: drug/dosage pickers showed "On formulary" for every common drug because
[`drug_on_formulary`](../../../src/medicare_navigator/tools/drug_lookup.py)
matches any strength RXCUI, while the UI mapped `on_formulary: true` to visible
labels in [`frontend/src/app.js`](../../../frontend/src/app.js).

Coverage must apply to all **three guided submodes**: Single drug, Compare
drugs (multi), and Compare plans — each uses `createDrugDosagePicker`.

## Automated checks (0 queries — always run)

```bash
pytest tests/test_no_false_signals.py -v
```

| Check | Pass criteria |
|-------|---------------|
| No formulary picker labels | `frontend/src/app.js` must not contain `"On formulary"`, `"Not on formulary"`, or assign `picker-meta--on-formulary` / `picker-meta--off-formulary` from `on_formulary` in the picker path |
| No picker-as-source copy | Estimate blocked notes must not say coverage was "shown in the picker" |
| API may still return `on_formulary` | `GET /api/drugs?plan_id=…` may include the field; UI must not surface it |

CSS classes for `.picker-meta--on-formulary` in [`styles.css`](../../../frontend/src/styles.css) may remain (unused is fine).

## Optional browser spot-check

```bash
medicare-ui-test browser --flow guided-single --base-url http://localhost:8000
```

Open drug picker with a plan selected → list items show **name only**, no
`.picker-meta` formulary text. Repeat spot-check on guided-multi and
guided-compare-plan when time allows.

## Failure → fix

| Symptom | Fix |
|---------|-----|
| "On formulary" reappears in picker | Remove `on_formulary` → meta mapping in `normalizeComboboxOption`; do not pass `on_formulary` into picker options from `fetchDrugs` / `fetchDrugDosages` |
| Dosage picker shows coverage hints | Same — only drug/dosage label in combobox options |
| Copy references picker for coverage | Update `estimate-note--blocked` strings in `app.js` |

## Verdict

Any `[FAIL]` → overall `/quality-test` **BLOCK**.
