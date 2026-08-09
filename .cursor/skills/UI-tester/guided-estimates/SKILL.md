---
name: ui-guided-estimates
description: Test and debug the single-drug, multiple-drug, and compare-plans guided estimate flows in the Medicare Navigator UI.
disable-model-invocation: true
---

# Guided estimate UI testing

Use this subskill when a defect involves the Guided form.

## Test surfaces

- Open and close the guided sheet on desktop and mobile.
- Switch between Single, Multiple drugs, and Compare plans.
- Select plans with mouse, typing, ArrowUp/ArrowDown, Enter, Escape, and blur.
- Add and remove repeatable drug and plan rows, including minimum and maximum counts.
- Submit valid and invalid forms; verify inline errors, loading state, disabled buttons, and result rendering.
- Start a New chat and verify every guided field and dynamic row resets.

## Required checks

1. Run `pytest tests/test_ui.py -v`.
2. Run `medicare-ui-test run --offline`.
3. Validate `frontend/dist/` because FastAPI serves that directory.
4. For a visual defect, manually verify at narrow mobile width and at desktop width.

## Debugging rules

- Check browser console errors before changing layout.
- Confirm the hidden plan input, not the visible combobox text, is used in the request payload.
- Keep the sheet above its backdrop on mobile.
- Preserve the baseline results when a guided request fails.
- Add a regression test to the guided contract when a required control or interaction changes.
