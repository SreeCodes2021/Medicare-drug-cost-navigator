---
name: chat-bot-fixer
description: >-
  Implements pipeline fixes from /chat-QA grading feedback, then re-invokes the
  chat API and re-grades until PASS or iteration limit. Use when the user invokes
  /chat-bot-fixer, chat-bot-fixer, or asks to fix chat quality issues identified
  by chat-QA.
disable-model-invocation: true
---

# Chat Bot Fixer — Implement QA Feedback Loop

User invoked this skill — fix chat quality issues surfaced by `/chat-QA`, then re-grade.
This skill **implements** changes; `/chat-QA` **grades only**. Always pair them.

## Workflow

```
┌─────────────┐     BLOCK/REVISE      ┌──────────────────┐
│  /chat-QA   │ ────────────────────► │ /chat-bot-fixer  │
│  (grade)    │                       │ (implement fix)  │
└─────────────┘                       └────────┬─────────┘
       ▲                                       │
       │              re-invoke + re-grade      │
       └───────────────────────────────────────┘
                         until PASS or limit
```

### Step 0 — Inputs

| Starting point | Action |
|----------------|--------|
| User gives `/chat-bot-fixer` + a test question | Run `/chat-QA` first (invoke + grade) |
| User pastes a prior `/chat-QA` grade | Use that grade as baseline |
| User says "fix the issues from above" | Use the most recent `/chat-QA` output in the thread |

Capture and keep for every iteration:
- `user_message` — the test question
- `session_id` — only if doing multi-turn; otherwise omit
- `filters_json` — if the original invoke used filters
- Per-dimension scores and **"What would need to change"** from the grade

### Step 1 — Triage (BLOCK first)

| Verdict | Action |
|---------|--------|
| **PASS** | Stop. Report no changes needed. |
| **REVISE** | Fix dimensions scored 1 (and any "cannot verify" on D1). |
| **BLOCK** | Fix dimensions scored **0** on D1, D2, or D3 **before** anything else. |

Work dimensions in the priority order listed in the grade's "What would need to change" section.

### Step 2 — Map dimension → code

| Dim | Issue type | Primary fix locations |
|-----|------------|----------------------|
| **1** Citation-groundedness | Invented numbers, claims without source | [`synthesis.py`](../../../src/medicare_navigator/agents/synthesis.py) (`SYNTHESIS_SYSTEM_PROMPT`, `_deterministic_explanation`, citation validation in `run_synthesis_agent`); upstream tool artifacts in [`orchestrator/pipeline.py`](../../../src/medicare_navigator/orchestrator/pipeline.py) |
| **2** Marketing boundary | Plan-switch nudges, enrollment steering | `SYNTHESIS_SYSTEM_PROMPT`; deterministic strings in `synthesis.py`; [`policy.py`](../../../src/medicare_navigator/agents/policy.py) |
| **3** Medical advice | Start/stop/change drug, clinical judgment | Same as D2; alternatives phrasing in `_deterministic_explanation` and `_follow_up_alternatives_answer` |
| **4** Disclaimer & data-currency | Missing disclaimer or "as of" date | [`config/disclaimer.txt`](../../../config/disclaimer.txt); disclaimer append in `run_synthesis_agent`; citation `as_of_date` fields |
| **5** Rebate opacity | Net-cost stated without rebate caveat | `SYNTHESIS_SYSTEM_PROMPT`; policy claims in `policy.py` |
| **6** Plain language | Jargon, buried answer | `SYNTHESIS_SYSTEM_PROMPT`; deterministic template sentences in `synthesis.py` |
| **7** Tone | Alarmist or falsely reassuring | Same as D6 |

**Canonical anchors** (read before editing):
- Synthesis agent: [`src/medicare_navigator/agents/synthesis.py`](../../../src/medicare_navigator/agents/synthesis.py)
- Policy agent: [`src/medicare_navigator/agents/policy.py`](../../../src/medicare_navigator/agents/policy.py)
- Disclaimer: [`config/disclaimer.txt`](../../../config/disclaimer.txt)
- Intake (clarification messages): [`src/medicare_navigator/intake/agent.py`](../../../src/medicare_navigator/intake/agent.py)

### Step 3 — Implement

- **Minimal diff** — fix only what the grade flagged; match existing style.
- **Prompt vs deterministic** — if the failure is in deterministic fallback text (`_deterministic_explanation`, `_explain_cost_change_answer`), fix the template, not just the LLM prompt.
- **Do not weaken safety** — never remove disclaimer append, citation validation, or marketing-boundary guards to pass a stylistic dimension.
- **Tests** — after code changes, run targeted tests:

| Changed path | Command |
|--------------|---------|
| `agents/synthesis.py` | `pytest tests/test_synthesis.py tests/test_explain_cost_change.py -v` |
| `agents/policy.py` | `pytest tests/test_follow_up.py -v` |
| `intake/agent.py` | `pytest tests/test_intake.py -v` |
| `orchestrator/**` | `pytest tests/test_follow_up.py -v` |
| `config/disclaimer.txt` | `pytest tests/test_synthesis.py -v` |

Add or update a test when the fix is behavioral and testable offline.

### Step 4 — Re-invoke and re-grade

Prerequisites: API on port 8000 (`pip install -e ".[dev]"`).

```bash
medicare-chat-invoke health
medicare-chat-invoke send --message "<same user_message as baseline>"
# Reuse --filters-json and --session-id if the original invoke used them
```

If health fails, tell the user to restart the server (code changes with `--reload` usually pick up automatically):

```bash
uvicorn medicare_navigator.api.app:app --reload --host 0.0.0.0 --port 8000
```

**Re-grade** using the full `/chat-QA` rubric (all seven dimensions + verdict). Apply the grading bundle fields exactly as chat-QA specifies — do not grade from memory.

### Step 5 — Loop or stop

| Outcome | Action |
|---------|--------|
| **PASS** | Stop. Summarize what changed and show before/after verdicts. |
| **REVISE** or **BLOCK** | If iteration < **3**, go to Step 1 with the new grade. |
| Iteration ≥ **3** | Stop. Report remaining issues and what was tried. Do not loop further unless the user asks. |

## Output format

Present each iteration as:

```markdown
## Fix iteration {n}

### Changes made
- `{file}` — {one-line what and why, tied to dimension}

### Tests
- `{command}` — {pass/fail}

### Re-grade (same question: "{user_message}")
**Verdict:** {BLOCK|REVISE|PASS}
| Dim | Before | After | Notes |
|-----|--------|-------|-------|
| 1 … | … | … | … |

### Remaining (if not PASS)
{priority-ordered list from latest grade}
```

After a **PASS**, end with a short summary of the full loop (starting verdict → final verdict, files touched).

## Constraints

- **Never fabricate grades** — only score real `medicare-chat-invoke send` output.
- **Never skip re-grade** — implementing without verification is not done.
- **Do not commit or push** unless the user explicitly asks (use `/commit-push` if they want that).
- **chat-QA owns the rubric** — when grading, follow [chat-QA/SKILL.md](../chat-QA/SKILL.md) exactly; this skill owns implementation and the loop.
