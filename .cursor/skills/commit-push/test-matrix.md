# Commit-push test matrix

Maps **staged paths** → **required verify commands** for [commit-push](SKILL.md). Apply every matching row; dedupe commands; run in order listed below.

All pytest runs are offline — [`tests/conftest.py`](../../../tests/conftest.py) clears API keys so tests use deterministic LLM fallbacks.

## Run order

1. Targeted pytest files (from specific path matches)
2. Full suite (`pytest tests/ -v`) when broad `src/**` or config changes match

## Path → tests

| Staged path pattern | Required commands |
|---------------------|-------------------|
| `src/medicare_navigator/tools/**` | `pytest tests/test_estimate_drug_cost.py tests/test_estimate_all_channels.py tests/test_drug_lookup.py tests/test_ndc.py tests/test_zip_lookup.py tests/test_benefit_phase.py tests/test_part_d_benefit_params.py -v` |
| `src/medicare_navigator/agent/**` | `pytest tests/test_navigator.py tests/test_citations.py tests/test_dosage_questions.py tests/test_alternatives_questions.py -v` |
| `src/medicare_navigator/orchestrator/**`, `session/**` | `pytest tests/test_navigator.py -v` |
| `src/medicare_navigator/api/**` | `pytest tests/test_ui.py tests/test_disclaimer_coverage.py tests/test_compare_plans.py tests/test_batch_estimate.py -v` |
| `src/medicare_navigator/agents/**`, `llm/**` | `pytest tests/test_navigator.py tests/test_llm_client.py tests/test_llm_mock.py -v` |
| `src/medicare_navigator/guardrails/**` | `pytest tests/test_citations.py tests/test_channel_parity.py tests/test_navigator.py -v` |
| `src/medicare_navigator/ingestion/**`, `storage/**` | `pytest tests/ -v` |
| `src/medicare_navigator/models/**`, `config.py` | `pytest tests/ -v` |
| `src/medicare_navigator/eval/**` | `pytest tests/ -v` (also note `medicare-eval` if eval queries/results changed) |
| `src/medicare_navigator/qa/**` | `pytest tests/test_chat_qa.py -v` |
| `src/medicare_navigator/ui_test/**` | `pytest tests/test_ui.py tests/test_smoke_fields.py -v` |
| `tests/test_estimate_drug_cost.py`, `tests/test_estimate_all_channels.py` | `pytest tests/test_estimate_drug_cost.py tests/test_estimate_all_channels.py -v` |
| `tests/test_drug_lookup.py` | `pytest tests/test_drug_lookup.py -v` |
| `tests/test_disclaimer_coverage.py` | `pytest tests/test_disclaimer_coverage.py -v` |
| `tests/test_no_false_signals.py` | `pytest tests/test_no_false_signals.py -v` |
| `tests/test_answer_consistency.py` | `pytest tests/test_answer_consistency.py -v` |
| `tests/test_dosage_questions.py` | `pytest tests/test_dosage_questions.py -v` |
| `tests/test_alternatives_questions.py` | `pytest tests/test_alternatives_questions.py -v` |
| `.cursor/skills/quality-test/**` | `pytest tests/test_no_false_signals.py tests/test_answer_consistency.py tests/test_disclaimer_coverage.py tests/test_dosage_questions.py tests/test_alternatives_questions.py -v` |
| `tests/test_smoke_fields.py` | `pytest tests/test_smoke_fields.py -v` |
| `tests/**` (only test files staged) | `pytest <staged test paths> -v` |
| `pyproject.toml`, `tests/conftest.py` | `pytest tests/ -v` |
| `src/**` (fallback) | `pytest tests/ -v` |

Rows are additive: multiple matches → union of commands, then dedupe.

| `frontend/**` | `pytest tests/test_ui.py tests/test_smoke_fields.py tests/test_disclaimer_coverage.py tests/test_no_false_signals.py tests/test_answer_consistency.py -v` |
| `.cursor/skills/numeric-accuracy/golden-cases.jsonl`, `scripts/run_golden_cases.py` | `python scripts/run_golden_cases.py` |

### Frontend only

If **every** staged path is under `frontend/` → `pytest tests/test_ui.py -v`. Note in overview: optional live check `medicare-ui-test run` at http://localhost:8000.

### Docs / config only

If **every** staged path is under `docs/`, `.cursor/skills/`, or is a root `*.md` / `README.md` / `.env.example` with no runtime code → **no tests** (note in overview).
