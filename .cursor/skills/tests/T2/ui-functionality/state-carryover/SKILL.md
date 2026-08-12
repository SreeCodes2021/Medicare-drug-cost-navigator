---
name: ui-functionality-state-carryover
description: >-
  Verify what must persist vs. reset when switching Chat <-> Guided, or
  between Guided sub-tabs (Single/Multiple drugs/Compare plans) — selected
  State, plan scoping, drug/dosage fields, and chat sessions. Use with
  /ui-functionality/state-carryover.
disable-model-invocation: true
---

# UI functionality — Cross-tab state carryover

Parent: [ui-functionality/SKILL.md](../SKILL.md).

## The persist-vs-reset contract

| Field / concept | Chat tab | Guided → Single | Guided → Multi | Guided → Compare | Notes |
|---|---|---|---|---|---|
| Selected State (location picker) | `chatState` | `guidedState` | `guidedState` | `guidedState` | **One** `guidedState` shared across all three guided submodes — switching submodes keeps the state and its scoped plan list. Chat has its own separate `chatState`; the two must never leak into each other. |
| Plan combobox options | scoped by `chatState` | scoped by `guidedState` | scoped by `guidedState` | scoped by `guidedState` | `guidedPlanComboboxInstances()` fans `onGuidedStateChanged` out to every guided plan combobox at once. |
| Drug / dosage selection | own picker | `singleDrugPicker` | one picker per row | `compareDrugPicker` | Each surface/row owns its own picker instance — never shared, so switching guided submodes does **not** carry a drug/dosage selection over. |
| Chat session (`sessionId`) / turn count | own | `guidedSessionId` (separate) | same `guidedSessionId` | same `guidedSessionId` | Chat and Guided are two independent conversations; switching Guided submodes does **not** reset `guidedSessionId` — only "New chat" / a fresh guided submit does. |
| Days supply / YTD OOP | per-form field, not shared | `filter-days-supply` / `filter-ytd` | `md-days-supply` / `md-ytd` | `cp-days-supply` / `cp-ytd` | Intentionally separate per submode — filling Single does not pre-fill Multi or Compare. |

Source of truth: [`frontend/src/app.js`](../../../../../../frontend/src/app.js) — search `guidedState`, `chatState`, `guidedSessionId`, `sessionId`.

## Automated checks (contract-level)

```bash
pytest tests/test_ui.py -v -k "guided_state or guided_drug_and_dosage"
```

- `test_guided_state_is_shared_across_guided_submodes_but_not_with_chat`
- `test_guided_drug_and_dosage_fields_reset_per_submode_not_shared`

These are string/AST-style contract checks on `app.js` (no JS test runtime in this repo) — they catch someone accidentally merging `chatState`/`guidedState` into one variable, or wiring a single shared drug/dosage picker across submodes.

## Browser flow (manual / Playwright)

1. Guided tab: select State `AR`. Switch **Single → Multi → Compare** — assert the State selector still shows `AR` and every plan combobox is scoped to AR plans (not empty, not unscoped) each time.
2. In Guided **Single**, select drug `metformin` + dosage `500mg`, then switch to **Multi** — assert Multi's drug row is blank, not pre-filled with metformin.
3. Send one guided message in **Single** (turn 1/5). Switch to **Compare**, fill it out, and submit — assert the guided turn counter continues from where it was (does not reset to 0), because `guidedSessionId` is shared across guided submodes, not per-submode.
4. Select a State in the **Chat** tab's location picker, then open **Guided** — assert Guided's State selector is empty (not pre-filled from Chat), confirming `chatState` and `guidedState` are isolated.
5. Send a Chat message, then send a Guided message — assert two different `session_id` values appear in the network requests (`sessionId` vs `guidedSessionId`).

## Failure → fix

| Symptom | Fix |
|---------|-----|
| Plan list resets/empties when switching guided sub-tabs | `guidedState` accidentally scoped per-submode instead of module-level in `app.js` |
| Drug/dosage carries over between guided submodes | Submodes sharing one `createDrugDosagePicker` instance instead of separate `singleDrugPicker` / per-row / `compareDrugPicker` |
| Chat's selected state leaks into Guided plans | `onChatStateChanged` writing to `guidedState` instead of `chatState` |
| Guided turn counter resets when switching submodes | Submode switch calling `resetGuidedConversation()` — should only reset on "New chat" or explicit reset action |

Report using [report-template.md](../report-template.md).
