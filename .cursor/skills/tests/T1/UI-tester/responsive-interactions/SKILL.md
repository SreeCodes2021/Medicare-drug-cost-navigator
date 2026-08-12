---
name: ui-responsive-interactions
description: Verify responsive layout, focus, keyboard access, overlays, and touch interactions for the Medicare Navigator frontend.
disable-model-invocation: true
---

# Responsive interaction UI testing

Use this subskill for bugs that appear only at a particular viewport or input method.

## Viewport matrix

- Mobile: 320–639 CSS pixels.
- Tablet: 640–1279 CSS pixels.
- Desktop: 1280 CSS pixels and wider.

## Interaction checklist

- The mobile guided sheet is clickable above its backdrop.
- Escape closes the active modal, menu, or guided sheet in that order.
- Focus moves into the guided form when it opens.
- Combobox options are visible, scrollable, keyboard-selectable, and correctly announce expanded state.
- Content does not create unintended horizontal page scrolling; wide estimate tables may scroll inside their wrapper.
- Buttons remain reachable above the safe-area inset and have usable touch targets.
- Reduced-motion users do not receive required information only through animation.

## Evidence

Record viewport, input method, expected behavior, observed behavior, console errors, and the smallest reproducible steps. Add a regression contract or browser test for every fixed interaction.
