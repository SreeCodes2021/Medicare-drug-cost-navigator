---
name: commit-push
description: >-
  Inspect staged changes, derive required pytest from path matrix, show overview
  and commit message, get approval, fetch/pull, run tests, commit, and push.
  Use when the user invokes /commit-push, commit-push, or asks to commit and push.
disable-model-invocation: true
---

# Commit and push

User invoked this skill — committing and pushing is allowed after explicit approval.

**Default:** only commit **already staged** files; do not `git add` unless the user asks.

## 1. Inspect (run in parallel)

```bash
git status
git diff --staged
git log -5 --oneline
git diff --staged --name-only
```

If nothing is staged but the user wants to commit working-tree changes, run `git diff` (unstaged) and list untracked paths — then ask whether to `git add` specific paths before continuing.

## 2. Gate

| Condition | Action |
|-----------|--------|
| Nothing staged | Stop. Show unstaged/untracked overview; tell user to stage (`git add …`) or ask you to stage, then re-run. |
| Staged paths look like secrets (`.env`, `credentials`, `*.pem`, `*secret*`) | Warn in overview; do not commit unless user explicitly confirms in permission step. |

## 3. Derive test plan

From staged paths (`git diff --staged --name-only`), apply [test-matrix.md](test-matrix.md):

1. Match all path patterns; union required commands; dedupe.
2. Respect **run order** in the matrix.
3. If docs-only (matrix § docs/config), list "No tests required".
4. If frontend-only (matrix § frontend), list manual UI check note.

**Test environment:** [`tests/conftest.py`](../../../tests/conftest.py) forces deterministic LLM fallback (no API keys) — pytest is fully offline.

## 4. Change overview (required — stop here)

Post this to the user **before** any `git fetch`, `git pull`, tests, `git commit`, or `git push`. Do not sync, test, commit, or push in the same turn unless the user already gave clear approval for that exact message, scope, and test plan.

### Overview template

```markdown
## Commit overview

**Branch:** `{branch}` ({ahead/behind vs upstream if any})

### Staged files
| Path | Change |
|------|--------|
| `path/to/file` | {brief: what changed and why it matters} |

### Required tests (from staged paths)
| Command | Why |
|---------|-----|
| `{command}` | {path match or rule from test-matrix} |

### Summary
{2–4 sentences: what this commit does as a whole, in plain language}

### Proposed commit message
```
{one-line message, ≤72 chars, imperative}
```

### Not included (if any)
- Unstaged: …
- Untracked: …

**After you approve:** fetch/pull latest, run required tests, then commit and push to `{remote}/{branch}`. If tests fail, report failures and offer to fix before commit.

**Awaiting your approval** — reply **yes** / **commit and push**, or edit the message / file list / test plan first.
```

Fill the tables from `git diff --staged` and [test-matrix.md](test-matrix.md). Group related paths when helpful; skip line-by-line diffs unless the user asked for them.

### Commit message rules

- **One short line** (aim ≤72 characters): imperative, no body, no ticket fluff.
- Match recent repo tone from `git log` (e.g. `Add follow-up session tests` not `This commit adds…`).
- Summarize the **why** of the staged change in plain language.

## 5. Permission (required)

| User says | Action |
|-----------|--------|
| Approves message + commit + push (`yes`, `commit and push`, `lgtm`, etc.) | Proceed to §6 → §7 → §8 → §9 |
| Approves commit only, not push | Sync (§6), tests (§7), commit (§8); stop and ask again before push |
| Wants different message, files, or test plan | Update overview; do not sync, test, commit, or push until re-approved |
| No reply / unclear | Stop — no fetch, pull, test, commit, or push |

## 6. Sync with remote (required before tests/commit)

Run only after §5 approval. Goal: reduce push rejections and avoid committing on a stale branch.

```bash
git fetch origin
git status
```

| Situation | Action |
|-----------|--------|
| No upstream / no tracking branch | Skip pull; note in §10 report. Push step may use `git push -u origin HEAD`. |
| Up to date with upstream | Continue to §7. |
| Behind upstream | `git pull` (default merge). **Never** `git pull --rebase` unless the user explicitly asks. |
| Ahead only | Continue to §7 (nothing to pull). |
| Diverged (ahead and behind) | `git pull` (merge). If merge conflicts, **stop** — report files in conflict; do not commit until resolved. |
| `git pull` fails (dirty tree, unrelated histories, etc.) | **Stop** — report error output; ask user how to proceed (stash, rebase, resolve conflicts). Do not commit. |

After a successful pull:

1. Re-run `git status`, `git diff --staged`, and `git diff --staged --name-only`.
2. Confirm staged files are still what the user approved. If pull changed staged content or dropped staging, **stop** and show an updated overview (re-derive tests) for re-approval.
3. If new commits landed from remote, mention them briefly in the final report (e.g. "Integrated 2 remote commits via merge").

**Never:** `git pull --force`, force-fetch, or rewrite remote history.

## 7. Run required tests (required before commit)

Run only after §6 completes (or is skipped with no upstream). Execute every command from the approved test plan in matrix run order.

**Environment:** activate `.venv` (`source .venv/bin/activate`) or ensure `pip install -e ".[dev]"` so `medicare_navigator` is importable.

```bash
# Examples — use the exact commands from the overview, not this list
pytest tests/test_tools.py -v
pytest tests/test_intake.py -v
pytest tests/test_follow_up.py -v
pytest tests/ -v
```

| Result | Action |
|--------|--------|
| All pass | Continue to §8. |
| Any fail | **Stop** — do not commit. Go to §7.1. |

### 7.1 Test failure

1. Post a short **failure summary**: command, failing test name(s), last error lines.
2. Offer to diagnose and fix in chat.
3. After fix, re-run the failed command(s) and any related commands from the test plan.
4. Loop until all required tests pass or the user stops.
5. Only then continue to §8.

**Do not commit** while required tests are failing.

## 8. Commit

Run only after §7 passes (or §7 skipped because test plan was "none"):

```bash
git commit -m "$(cat <<'EOF'
<approved one-line message>
EOF
)"
```

**Never:** `git config` changes, `--no-verify`, amend (unless user rule amend conditions all apply), commit unstaged/untracked files without explicit user request to stage them.

**Hook failed:** fix the issue, then create a **new** commit — do not amend a failed commit.

## 9. Push

Run only after §5 approval for push and §8 succeeded:

```bash
git push
```

If no upstream: `git push -u origin HEAD`.

**Never** force-push to `main` or `master`. If push is rejected, report why; do not force unless the user explicitly asks.

## 10. Report

Reply briefly with: sync result (fetch/pull/skipped), test results (pass/fail + commands run), commit hash, message, branch, and push result (remote updated or error).
