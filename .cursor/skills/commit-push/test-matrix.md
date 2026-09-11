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
| `src/medicare_navigator/agent/datetime_context.py`, `agent/mediator.py` | `pytest tests/test_budget_window.py tests/test_mediator.py tests/test_datetime_context.py -v` |
| `tests/test_budget_window.py`, `tests/test_mediator.py`, `tests/test_datetime_context.py` | `pytest tests/test_budget_window.py tests/test_mediator.py tests/test_datetime_context.py -v` |
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
| `.cursor/skills/tests/T3/**` | `pytest tests/test_no_false_signals.py tests/test_answer_consistency.py tests/test_disclaimer_coverage.py tests/test_dosage_questions.py tests/test_alternatives_questions.py tests/test_budget_window.py tests/test_mediator.py tests/test_datetime_context.py -v` |
| `tests/test_smoke_fields.py` | `pytest tests/test_smoke_fields.py -v` |
| `tests/**` (only test files staged) | `pytest <staged test paths> -v` |
| `pyproject.toml`, `tests/conftest.py` | `pytest tests/ -v` |
| `src/**` (fallback) | `pytest tests/ -v` |

Rows are additive: multiple matches → union of commands, then dedupe.

| `frontend/**` | `pytest tests/test_ui.py tests/test_smoke_fields.py tests/test_disclaimer_coverage.py tests/test_no_false_signals.py tests/test_answer_consistency.py -v` |
| `.cursor/skills/tests/utils/numeric-accuracy/golden-cases.jsonl`, `scripts/run_golden_cases.py` | `python scripts/run_golden_cases.py` |

### Frontend only

If **every** staged path is under `frontend/` → `pytest tests/test_ui.py -v`. Note in overview: optional live check `medicare-ui-test run` at http://localhost:8000.

### Docs / config only

If **every** staged path is under `docs/`, `.cursor/skills/`, or is a root `*.md` / `README.md` / `.env.example` with no runtime code → **no tests** (note in overview).

### Budget / date-window (calendar-sensitive)

When staged paths touch `budget_start_date`, mediator date extraction, or remaining-year fill math (`agent/datetime_context.py`, `agent/mediator.py`, `agent/insulin_requests.py`, `tools/estimate_drug_cost.py`, or `tests/test_budget_window.py`):

| Layer | Command / agent | Why |
|-------|-----------------|-----|
| **Offline (required on commit)** | `pytest tests/test_budget_window.py tests/test_mediator.py tests/test_datetime_context.py -v` | Catches calendar roll-forward bugs — e.g. `"starting September 1"` after Sep 1 rolls to next year via `resolve_explicit_start_date`, zeroing the remaining-year window and falling back to a single 30-day fill. `test_mediator_extracted_start_date_flows_into_deterministic_insulin_response` freezes time to **2026-08-03** so September 1 stays in the current contract year. |
| **Live complement (note in overview; not blocking commit)** | [`/quality-test` §2h](../tests/T3/SKILL.md#2h-budget--date-window-questions-mandatory--4-queries-every-run) | Exercises mediator LLM date extraction against a real model (`mediator_enabled` required). Use a month/day **still in the future** relative to the run date, or include an explicit year — do not reuse a fixed anchor like "September 1" year-round. |
