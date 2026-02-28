# Commit-push test matrix

Maps **staged paths** → **required verify commands** for [commit-push](SKILL.md). Apply every matching row; dedupe commands; run in order listed below.

All pytest runs are offline — [`tests/conftest.py`](../../../tests/conftest.py) clears API keys so tests use deterministic LLM fallbacks.

## Run order

1. Targeted pytest files (from specific path matches)
2. Full suite (`pytest tests/ -v`) when broad `src/**` or config changes match

## Path → tests

| Staged path pattern | Required commands |
|---------------------|-------------------|
| `src/medicare_navigator/tools/**` | `pytest tests/test_tools.py -v` |
| `src/medicare_navigator/intake/**` | `pytest tests/test_intake.py tests/test_tools.py -v` |
| `src/medicare_navigator/orchestrator/**`, `session/**` | `pytest tests/test_follow_up.py -v` |
| `src/medicare_navigator/api/**` | `pytest tests/test_follow_up.py -v` |
| `src/medicare_navigator/agents/**`, `llm/**` | `pytest tests/test_follow_up.py tests/test_intake.py -v` |
| `src/medicare_navigator/ingestion/**`, `storage/**` | `pytest tests/ -v` |
| `src/medicare_navigator/models/**`, `config.py` | `pytest tests/ -v` |
| `src/medicare_navigator/eval/**` | `pytest tests/ -v` (also note `medicare-eval` if eval queries/results changed) |
| `tests/test_tools.py` | `pytest tests/test_tools.py -v` |
| `tests/test_intake.py` | `pytest tests/test_intake.py -v` |
| `tests/test_follow_up.py` | `pytest tests/test_follow_up.py -v` |
| `tests/**` (only test files staged) | `pytest <staged test paths> -v` |
| `pyproject.toml`, `tests/conftest.py` | `pytest tests/ -v` |
| `src/**` (fallback) | `pytest tests/ -v` |

Rows are additive: multiple matches → union of commands, then dedupe.

### Frontend only

If **every** staged path is under `frontend/` → **no automated tests**. Note in overview: manual UI check at http://localhost:8000.

### Docs / config only

If **every** staged path is under `docs/`, `.cursor/skills/`, or is a root `*.md` / `README.md` / `.env.example` with no runtime code → **no tests** (note in overview).
