---
name: smoke-test
description: >-
  One-call Tier 1 smoke test for the Medicare Navigator portal — every key,
  field, dropdown, and chatbox exists and tolerates input without crashing.
  Runs UI-tester's static/API/chat contracts, the field/keyboard sweep, and
  responsive-interaction checks in one pass and reports a single verdict. Use
  when the user invokes /smoke-test, smoke-test, or asks for a smoke test of
  the app.
disable-model-invocation: true
---

# Smoke test — Tier 1 (one call, whole tier)

User invoked this skill — run **everything** in the smoke tier end-to-end and
report one consolidated result. Don't ask the user to separately invoke
`/UI-tester` or its sub-skills; this skill does that internally.

**Scope:** does every field/dropdown/chatbox exist, accept input, and not
crash? **Not in scope:** whether the *answer* is right (that's
[`/functional-test`](../T2/SKILL.md)) or whether the *quality*
of an answer is good (that's [`/quality-test`](../T3/SKILL.md)).

## What this runs, in order

1. **Pre-flight** — build the frontend if stale:

```bash
scripts/build-frontend.sh
```

2. **Offline contracts** (static assets, HTML/JS element wiring, API shapes, chat smoke, field sweep):

```bash
pytest tests/test_ui.py tests/test_smoke_fields.py -v
medicare-ui-test run --offline --groups static,api,chat,fields
```

3. **Live smoke** (if a server is running or can be started) — confirms real HTTP, not just in-process:

```bash
uvicorn medicare_navigator.api.app:app --reload --host 0.0.0.0 --port 8000 &
medicare-ui-test run --base-url http://localhost:8000 --groups static,api,chat,fields
```

If no server is available or the user didn't ask for live checks, skip this step and note it as "offline-only run" in the report — don't block on it.

4. **Responsive/keyboard interaction pass** — apply the checklist in [`UI-tester/responsive-interactions/SKILL.md`](UI-tester/responsive-interactions/SKILL.md): mobile/tablet/desktop viewports, Tab order, Escape behavior, combobox keyboard nav, touch targets. Use Playwright (`medicare-ui-test browser --flow chat --base-url ...`) if available; otherwise do a manual/contract-level pass and say so.

5. **Manual UI surface checklist** — apply [`UI-tester/SKILL.md`](UI-tester/SKILL.md)'s "UI surface checklist" table (disclaimer banner, plans filter, filter badge, chips, chat send, turn counter, results states, error path) if you have live browser access; otherwise rely on step 2's automated equivalents.

## Auto-fix policy

Same as `UI-tester`: fix obvious wiring drift (missing element id, wrong fetch path, missing keyboard handler) directly; don't fabricate a PASS. Hand off anything about answer *correctness* or *quality* to the other two tiers instead of trying to fix it here.

## One consolidated report

```markdown
## Smoke test — {date/time}

**Mode:** offline | offline + live @ {base-url}
**Verdict:** PASS | FAIL ({n} issues)

### By group
| Group | Pass | Fail | Notes |
|-------|------|------|-------|
| static | … | … | … |
| api | … | … | … |
| chat | … | … | … |
| fields | … | … | … |
| responsive/keyboard | … | … | … |

### Issues found
| Severity | Symptom | Root cause | Fix |
|----------|---------|------------|-----|
| Critical/Suggestion/Nice | … | … | auto-fixed / needs review |

### Auto-fixes applied
- `{file}` — {what changed}

### Not covered this run
{e.g. "no live server available — offline contracts only"}
```

## Internal building blocks (do not ask the user to call these separately)

- [`UI-tester/SKILL.md`](UI-tester/SKILL.md) — static/api/chat contract engine (`medicare-ui-test`).
- [`UI-tester/responsive-interactions/SKILL.md`](UI-tester/responsive-interactions/SKILL.md) — viewport/keyboard/touch checklist.
- `tests/test_smoke_fields.py` + `check_field_*` functions in [`ui_test/checks.py`](../../../../src/medicare_navigator/ui_test/checks.py) — the `fields` group.

## Constraints

- **Do not fabricate test results** — every verdict must come from an actual `pytest`/`medicare-ui-test` run.
- **Do not commit** unless the user asks (pair with [`/commit-push`](../../commit-push/SKILL.md) if they want that after fixes).
- If something looks like a functional or quality issue (wrong number, bad explanation) rather than broken wiring, note it and point to `/functional-test` or `/quality-test` — don't try to grade it here.
