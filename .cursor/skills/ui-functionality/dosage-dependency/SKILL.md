---
name: ui-functionality-dosage-dependency
description: >-
  Verify the dosage list always follows the selected drug name — never global,
  never stale from a previous drug — across Single, Multi-drug, and
  Compare-plans. Use with /ui-functionality/dosage-dependency.
disable-model-invocation: true
---

# UI functionality — Dosage follows drug name

Parent: [ui-functionality/SKILL.md](../SKILL.md).

## Why this exists

`GET /api/drug-dosages?drug=X` is already scoped server-side (RxNorm lookup
per drug name — see [`tools/drug_lookup.py`](../../../../src/medicare_navigator/tools/drug_lookup.py)).
The failure mode this skill guards against is on the **frontend**: the dosage
combobox silently keeping a stale value/option list from the previously
selected drug after the user changes the drug field.

## Automated checks

```bash
pytest tests/test_drug_lookup.py -v -k dosage
```

Covers:
- `test_dosage_endpoint_is_scoped_to_the_named_drug_not_global` — backend: two different drugs never return the same dosage list.
- `test_dosage_combobox_clears_and_reloads_when_drug_changes` — frontend contract: `onSelect` clears the dosage combobox *before* `loadDosagesForDrug` runs, and the loader itself clears any dosage no longer valid for the new drug.

## Browser flow (manual / Playwright)

Repeat in **Single**, **Multiple drugs** (each row), and **Compare plans**:

1. Select drug `metformin` → dosage combobox populates (e.g. `500mg`, `850mg`).
2. Select dosage `850mg`.
3. Change drug to `lisinopril` (without touching dosage).
4. Assert: dosage combobox is now empty/disabled until re-opened, and its option list no longer contains `850mg`-style metformin strengths — it shows lisinopril strengths only.
5. Clear the drug field entirely → dosage combobox is disabled with placeholder "Select a drug first".

## Failure → fix

| Symptom | Fix |
|---------|-----|
| Old dosage value survives a drug change | `createDrugDosagePicker` → `onSelect` in [`app.js`](../../../../frontend/src/app.js) must call `dosageCombobox.clear()` before `loadDosagesForDrug` |
| Dosage list shows options for the wrong drug | Check `fetchDrugDosages` uses the just-changed `drug`, not a stale closure variable |
| Dosage combobox stays enabled with no drug selected | `loadDosagesForDrug` must call `dosageCombobox.setDisabled(!hasDrug, ...)` |
| Backend returns same list for different drugs | [`tools/drug_lookup.py`](../../../../src/medicare_navigator/tools/drug_lookup.py) `list_drug_dosages` — RxNorm query not using `drug` param |

Report using [report-template.md](../report-template.md).
