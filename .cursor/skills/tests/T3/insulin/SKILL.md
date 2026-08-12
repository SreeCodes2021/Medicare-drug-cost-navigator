---
name: quality-test-insulin
description: >-
  IRA insulin cap billing QA — deterministic golden cases, pytest, ingest
  validation, plus exactly 10 live LLM queries graded against /api/estimate and
  chat-QA. Invoke with /quality-test/insulin. Independent of the parent
  /quality-test 100-query budget. Mixed insulin+regular baskets: /quality-test/mixed-basket.
disable-model-invocation: true
---

# Quality test — Insulin billing (IRA statutory cap)

Parent: [quality-test/SKILL.md](../SKILL.md).

Invoke **`/quality-test/insulin`** when insulin pricing, policy language, or
CMS insulin-file behavior changes. This sub-skill is **not** part of the parent
`/quality-test` 100-query budget — run it separately after a general quality
pass or whenever insulin work ships.

**Canonical references** (link, do not duplicate):

- [docs/insulin-cost-estimation.md](../../../../../docs/insulin-cost-estimation.md) — calculation, CMS sources, worked examples
- [docs/insulin_trails/test-and-doc-gaps.md](../../../../../docs/insulin_trails/test-and-doc-gaps.md) — known gaps
- [llm-scenarios.md](llm-scenarios.md) — 10 live-LLM insulin-only scenarios (B1–B4, B6)
- [mixed-basket/SKILL.md](../mixed-basket/SKILL.md) — 20 LLM queries for insulin + regular same-plan baskets
- [numeric-accuracy/golden-cases.jsonl](../../utils/numeric-accuracy/golden-cases.jsonl) — `insulin_cap` group (`golden-037`–`047`)

## Real LLM mandate

Same as parent `/quality-test` — **never** grade `mock/...` responses.

```bash
medicare-chat-invoke health
medicare-chat-invoke models   # require gpt-5.6-luna configured: true
```

Default model for all Phase B queries: **`gpt-5.6-luna`**.

## Query budget

| Phase | Queries | Notes |
|-------|---------|-------|
| **Phase A** | **0** | Golden runner, pytest, optional ingest validation |
| **Phase B** | **10** | Fixed catalog in [llm-scenarios.md](llm-scenarios.md) — insulin-only; mixed baskets in [mixed-basket](../mixed-basket/SKILL.md) |

## Phase A — Deterministic (always run first)

Any `[FAIL]` → overall **BLOCK** (skip Phase B).

### A1. Golden oracle (`insulin_cap` group)

```bash
python scripts/run_golden_cases.py --case-group insulin_cap --by-group
python scripts/run_golden_cases.py --case-group insulin_cap --include-live --base-url http://localhost:8000 --by-group
```

| Case | Scenario |
|------|----------|
| `golden-037`–`040` | 30/60/90-day cap, catastrophic $0 |
| `golden-041` | Data-gap (`insulin_out_of_scope`) on H8888-001 |
| `golden-042` | All-channels range $30–$35 |
| `golden-043` | Under-cap tier-1 humalog $10 |
| `golden-044` | Pinned preferred_mail $30 |
| `golden-045` | Unmapped 45-day, no `$` |
| `golden-046`–`047` | Live AR S5884-198 spot-checks (requires ingest) |

Min cases: **7 offline + 2 live** (`requires_live_ingest: true`).

### A2. Pytest

```bash
pytest tests/test_insulin.py \
       tests/test_estimate_drug_cost.py -k insulin \
       tests/test_spuf_ingest.py -k insulin \
       tests/test_navigator.py -k insulin \
       tests/test_insulin_golden_contract.py -v
```

### A3. Post-ingest validation (when `--include-live`)

```bash
medicare-ingest spuf --download --states AR --merge-states
python scripts/validate_insulin_cost_data.py
```

Required before live golden cases `golden-046`–`047` when running `--include-live`.

## Phase B — 10 live LLM queries (insulin-only)

Follow [llm-scenarios.md](llm-scenarios.md). Rephrase wording each run; categories are fixed.
For **mixed insulin + regular** same-plan baskets, run [mixed-basket/llm-scenarios.md](../mixed-basket/llm-scenarios.md) (20 queries).

**Run the fixed catalog (do not write ad-hoc batch scripts):**

```bash
python scripts/run_llm_scenarios.py --suite insulin
python scripts/run_llm_scenarios.py --suite insulin --failures-only
python scripts/run_llm_scenarios.py --suite insulin --output json > /tmp/insulin-llm.json
```

Suite data: `scripts/llm_scenario_suites/insulin.json`.

| Block | Queries | Focus |
|-------|---------|-------|
| B1 | 3 | Core oracle ($, catastrophic, channel pin) |
| B2 | 3 | Policy language (ceiling, deductible, data-gap) |
| B3 | 2 | Dual insulin products, YTD session |
| B4 | 1 | Unmapped days supply |
| B6 | 1 | Prompt injection |

**Per-query workflow** (or use `python scripts/run_llm_scenarios.py --suite insulin` for the full catalog):

```bash
medicare-chat-invoke send --message "..." --model gpt-5.6-luna
curl -s -X POST http://localhost:8000/api/estimate \
  -H 'Content-Type: application/json' \
  -d '{"plan_id":"...","drug":"...","days_supply":30,"ytd_oop_spend":0}'
```

Use real `plan_id` / drugs from `GET /api/plans` and `GET /api/drugs`.

Transport prompts without shell interpolation: use a quoted heredoc, JSON/Python
message list, or otherwise verify that literal values such as `$2200`, `$35`,
`$105`, and `$1` arrive unchanged. Numeric scenarios must name the plan and
insulin product explicitly. For multi-product prompts, grade every requested
product, including partial-coverage or data-gap outcomes.

### Insulin-specific grading (chat-QA dimension 1)

Apply in addition to [chat-QA](../../utils/chat-QA/SKILL.md):

| Violation | dim 1 |
|-----------|-------|
| Claims deductible phase for insulin | 0 |
| States $35 is always the price (ignores catastrophic or under-cap) | 0 |
| Legacy "insulin … not supported by this tool" copy | 0 |
| Named substitute without `alternatives_finder` | 0 (Phase 8) |
| Data-gap: invents `$` when `insulin_out_of_scope` | 0 |
| Pools two insulin products into one $35/month total | 0 |

Pass on data-gap: honest CMS cost-share gap message; may note drug is on formulary; **no fabricated `$`**.

For **mixed insulin + regular** baskets, run `/quality-test/mixed-basket` — not covered in this 10-query insulin-only pass.

## Consolidated report

```markdown
## Insulin quality test — {date/time}

**Mode:** real LLM (models used: {list}) — {N}/10 real queries spent
**Overall verdict:** BLOCK | REVISE | PASS

### Phase A — Golden (`insulin_cap`)
| Cases | Passed | Notes |
|-------|--------|-------|
| offline (037–045) | N/9 | |
| live (046–047) | N/2 or INCOMPLETE | |

### Phase A — Pytest
| Module | Result | Notes |
|--------|--------|-------|

### Phase A — Ingest validation
| Check | Result | Notes |
|-------|--------|-------|

### Phase B — B1 Core oracle (3)
| # | Scenario | Oracle | Prose | Verdict | Notes |

### Phase B — B2 Policy language (3)
| # | Scenario | Expected | Actual | Verdict | Notes |

### Phase B — B3 Multi-product & session (2)
| # | Scenario | Expected | Actual | Verdict | Notes |

### Phase B — B4 Edge routing (1)
| # | Scenario | Expected | Actual | Verdict | Notes |

### Phase B — B6 Adversarial (1)
| # | Scenario | Expected | Actual | Verdict | Notes |

### Priority fixes needed
1. …

### Backlog updated
{path to docs/quality-test-todos.md — items appended, or "none (clean PASS)"}

### Not covered this run
- multi-model testing skipped (gpt-5.6-luna only, not requested)
- mixed insulin + regular baskets — run `/quality-test/mixed-basket`
- …
```

## Post-run backlog

Same rules as parent [quality-test/SKILL.md](../SKILL.md#post-run-backlog) — append to
[docs/quality-test-todos.md](../../../../../docs/quality-test-todos.md) on non-PASS.

## Failure → fix

| Symptom | Route |
|---------|-------|
| Golden/pytest fail | Cost pipeline bug — `insulin_cost.py`, ingest, fixtures |
| LLM prose ≠ oracle | [`/chat-bot-fixer`](../../../../chat-bot-fixer/SKILL.md) |
| Empty insulin table on deploy | Re-ingest SPUF per [insulin-cost-estimation.md](../../../../../docs/insulin-cost-estimation.md) §10 |
