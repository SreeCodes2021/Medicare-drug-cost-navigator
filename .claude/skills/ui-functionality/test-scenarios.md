# UI functionality — shared test scenarios

Fixture plans and drugs from [`tests/fixtures/spuf/`](../../../tests/fixtures/spuf/) (offline-safe with `LLM_MOCK=1`).

## Fixture data

| Field | Values |
|-------|--------|
| Plans | `S9999-001`, `H8888-001`, `S9999-004` (partial channel coverage) |
| Drugs | `metformin` 500mg, `januvia` 100mg |
| Days supply | 30 (default), 90 |
| YTD | 0, 800 |

Constants from [`tests/spuf_fixture.py`](../../../tests/spuf_fixture.py): `PLAN_FL_PDP`, `PLAN_FL_MAPD`.

## Composed messages (mirror `frontend/src/app.js`)

### Chat

- Tier lookup: `What's the cost for metformin 500mg on plan S9999-001?`
- Prompt chip (first chip in HTML): lovastatin / S5921-400 scenario (may `not_found` on fixture data)

### Guided single

- Message: `What's the cost for metformin 500mg on plan S9999-001?`
- Filters: `{ "drug": "metformin", "dosage": "500mg", "plan_id": "S9999-001", "days_supply": 30 }`

### Guided multi

- Message: `Estimate costs for metformin 500mg, januvia 100mg on plan S9999-001. Use a 30-day supply and $0 year-to-date out-of-pocket spending. Summarize each drug and the combined cost.`
- No extra filters (plan embedded in message)

### Guided compare plan

- Message: `Compare the cost of metformin 500mg across these Medicare plans: S9999-001, H8888-001. Use a 30-day supply and $0 year-to-date out-of-pocket spending. Summarize the differences and identify the lowest estimated cost.`
- Filters: `{ "drug": "metformin", "dosage": "500mg", "days_supply": 30, "ytd_oop_spend": 0 }`

### Channel-coverage regression (live AR data — not offline fixtures)

Fixture plans (`S9999-001`, `H8888-001`) usually have estimates for all four CMS channels.
Use this scenario to catch **partial-channel** overclaims (e.g., prose says "all channels"
when `preferred_retail` is `null`):

- Message: same compare template as above with plans `H2802-063`, `H5216-366`
- Requires ingested AR data (`medicare-ingest spuf --download --states AR --merge-states`)
- Offline equivalent: compare `S9999-004` (partial channels) vs `S9999-001` (full channels)
- Grade with `/chat-qa` — check `grading.channel_warnings` and `grading.channel_coverage`
- Deterministic ground truth: `POST /api/compare-plans` with the same `plan_ids`

## Expected UI outcomes (happy path)

| Surface | DOM signals |
|---------|-------------|
| Chat | `.message.assistant` in `#chat-messages`; `#turn-counter` shows `1/5`; `#results-content` not placeholder |
| Guided single | `#guided-turn-counter` = `1/5`; assistant in `#guided-chat-messages`; `#guided-results-content` has estimate HTML |
| Guided multi | Same guided conversation + results with batch/multi content |
| Guided compare | Comparison cards or multi-estimate stack in `#guided-results-content` |

## Validation errors (negative paths)

| Surface | Trigger | Expected |
|---------|---------|----------|
| Guided single | Submit with empty drug/plan | `#guided-error` visible |
| Guided multi | No plan or no drugs | `#guided-error` text |
| Guided compare | &lt;2 plans | `#guided-error` text |
