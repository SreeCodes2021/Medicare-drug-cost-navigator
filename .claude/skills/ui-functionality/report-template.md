# UI functionality report template

Copy and fill when a sub-skill run completes:

```markdown
## UI functionality — [surface]

**Mode:** browser @ [base-url] | offline contracts
**Verdict:** PASS | FAIL (n issues)

### Issues
| Severity | Symptom | Root cause | Fix |
|----------|---------|------------|-----|
| Critical / Suggestion / Nice | … | … | auto-fixed / needs review |

### Auto-fixes applied
- [file] — [what changed]

### Re-test
- `pytest tests/test_ui.py -v`: …
- `medicare-ui-test run --offline --groups …`: …
- `medicare-ui-test browser --flow …`: …
```

Severity guide:
- **Critical** — broken flow, blank panel, wrong data, or missing required control
- **Suggestion** — confusing UX, slow load, weak error message
- **Nice** — polish only
