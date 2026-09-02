# Test and Documentation Gaps — Insulin Implementation Review

What is covered today, what is missing, and which upstream docs are stale.

---

## Existing test coverage (positive)

### Unit tests

| File | Coverage |
|---|---|
| `tests/test_insulin.py` | `is_insulin()` allowlist: Lyumjev, Soliqua, Xultophy, case/whitespace, ingredient-only, non-insulin negative |
| `tests/test_spuf_ingest.py` | Hint-collision regression; repository 30/60/90 scaling; `has_any` false for H8888-001 |

### Integration tests (`tests/test_estimate_drug_cost.py`)

| Test | Scenario |
|---|---|
| `test_insulin_returns_real_capped_estimate_not_hard_stop` | S9999-001, 30-day, preferred_retail → $35, `insulin_cap`, insulin caveat |
| `test_insulin_60_and_90_day_scaling` | 60-day $70, 90-day $105 |
| `test_insulin_catastrophic_phase_overrides_cap_to_zero` | YTD 2200 → $0, `catastrophic` |
| `test_insulin_no_deductible_phase_and_channel_differentiation` | `ded_applies_yn == "NA"`; mail $30 vs retail $35 |
| `test_insulin_narrow_fallback_when_plan_has_no_cost_share_data` | H8888-001 → `insulin_out_of_scope`, `covered=True` |

### Agent / MCP tests

| File | Coverage |
|---|---|
| `tests/test_mcp_registry.py` | MCP priced insulin (S9999-001); MCP data-gap (H8888-001) |
| `tests/test_navigator.py` | Navigator priced insulin; navigator data-gap verbatim message |

### Golden cases

| ID | Group | Scenario |
|---|---|---|
| `golden-037` | `insulin_cap` | Lantus, S9999-001, 30-day, preferred_retail, $35, `insulin_cap` |

`insulin_cap` group documented in `.cursor/skills/tests/utils/numeric-accuracy/SKILL.md`.

### Eval harness

| ID | Message | Expected |
|---|---|---|
| `eval-010` | `cost for lantus on plan S9999-001` | `ok`, `insulin_cap`, cost $30 (all-channels min), `estimate_drug_cost_all_channels: ok` |

---

## Test gaps — recommended additions

### High priority

| Gap | Suggested test | Why |
|---|---|---|
| **Under-cap copay** | Plan/tier where general copay is $10, insulin file shows $10 (cap is ceiling, not fixed price) | Spec §8 worked example; only $35-at-cap case tested in fixtures |
| **Unmapped days supply + insulin** | `days_supply=45` (or other non-30/60/90) on insulin drug | Gate skipped; behavior is `ok` with no cost + caveat — undocumented in tests |
| **Defined-standard `TIER IS NULL` fallback** | Fixture row with `TIER="."` in insulin file + formulary tier 3 | Spec §8 claims support; no fixture test |
| **`insulin_out_of_scope` golden case** | `golden-038`: H8888-001 lantus, expect status not `ok` | Oracle for data-gap path |

### Medium priority

| Gap | Suggested test | Why |
|---|---|---|
| **60/90-day golden cases** | `golden-039` / `golden-040` for 60- and 90-day insulin | Only 30-day in golden suite |
| **Catastrophic insulin golden case** | YTD ≥ $2100, expect $0 + `catastrophic` | Phase transition not in golden suite |
| **Multi-channel range golden case** | All-channels lantus, expect range $30–$35 | Channel differentiation tested in pytest but not golden oracle |
| **Multi-tier / multi-NDC insulin (Bug 5)** | Two NDCs at different tiers on same plan | `compute_insulin_channel_costs` loops all tiers — range behavior untested |
| **Quantity limit on insulin** | Insulin drug with QL blocking 90-day fill | Behavior change vs old hard-stop — should return `quantity_limit_blocked` |
| **PA/ST on insulin** | Insulin with `prior_authorization_yn=Y` | Caveat should attach; cost still computed |

### Lower priority

| Gap | Suggested test | Why |
|---|---|---|
| **Insulin detected by ingredient only** | Brand not on list but RxNorm ingredient `insulin glargine` | Partially covered in `test_insulin.py`; no estimate integration test |
| **Non-insulin drug with "insulin" substring** | Edge case for false positive | Low practical risk |
| **MCP `estimate_drug_cost_all_channels` insulin** | Direct MCP all-channels call | MCP test uses single-channel tool |
| **Exploratory QA cases** | Insulin cost questions in quality-test skill | No insulin scenarios in exploratory backlog |

---

## Golden case expansion proposal

| Proposed ID | Group | Key fields | Notes |
|---|---|---|---|
| `golden-038` | `insulin_cap` | H8888-001 lantus → expect failure / no cost | Data-gap oracle; may need `expect_status` extension in runner |
| `golden-039` | `insulin_cap` | lantus, 60-day, $70 | Scaling |
| `golden-040` | `insulin_cap` | lantus, 90-day, $105 | Scaling |
| `golden-041` | `insulin_cap` | lantus, YTD 2200, $0, `catastrophic` | Phase override |
| `golden-042` | `insulin_cap` | lantus, all-channels, `expected_cost_low: 30`, `expected_cost_high: 35` | Channel range |
| `golden-043` | `insulin_cap` | `requires_live_ingest: true`, real AR plan | Post manual quarterly verification |

Note: golden runner may need extension to support `insulin_out_of_scope` status assertions for `golden-038`.

---

## Eval harness gaps

### Tool-key mismatch

`run_eval.py` checks hard-stop citation logic against `estimate_drug_cost`:

```python
estimate_status = resp.tool_statuses.get("estimate_drug_cost")
```

But `queries.jsonl` now uses `estimate_drug_cost_all_channels` in `expected_tool_status` for eval-001–012.

**Action:** Update eval runner to check the tool that was actually invoked (see [action-items.md P2-3](./action-items.md)).

### Collateral changes in `queries.jsonl`

| ID | Change | Validate |
|---|---|---|
| eval-001, 002, 004, 010, 011, 012 | Tool key `estimate_drug_cost` → `estimate_drug_cost_all_channels` | Mock navigator invokes correct tool |
| eval-002 | `expected_cost` 5.0 → 3.0 | All-channels min (preferred_mail $3) |
| eval-010 | Hard stop → `ok`, `insulin_cap`, cost $30 | Insulin now priced |
| eval-012 | `januvia 100mg` → `januvia` | Dosage resolution still works |

### Missing eval cases

| Suggested ID | Scenario |
|---|---|
| eval-013 | Lantus on H8888-001 → data-gap message (navigator end-to-end) |
| eval-014 | Lantus 90-day on S9999-001 → `insulin_cap`, cost $105 |
| eval-015 | Lantus with YTD $2200 → catastrophic $0 |

---

## Mixed insulin + regular basket (addressed 2026-08-11)

Same-plan multi-drug baskets mixing IRA insulin cap pricing and ordinary Part D
drugs are now covered end-to-end:

| Layer | Location |
|---|---|
| Routing fix | `insulin_requests.message_names_non_insulin_cost_drugs`, `navigator.py` gate |
| Dosage clarification | `dosage_questions.py` — mixed oral + insulin without strengths |
| Batch API pytest | `tests/test_batch_estimate.py` — insulin + regular, partial basket |
| Navigator pytest | `tests/test_mixed_basket.py` |
| Golden oracle | `golden-048`–`050`, `case_group: mixed_basket` |
| Quality skills | `/quality-test/mixed-basket` (20 LLM), parent §2g (2 LLM), insulin trimmed to 10 insulin-only LLM |

---

## Documentation gaps

### Stale docs (must update)

| Document | Section | Current state | Should say |
|---|---|---|---|
| `navigator-implementation-spec.md` | §1 Scope | Insulin out of scope | Insulin in scope via separate file/pipeline; link to insulin-cost-estimation.md |
| `navigator-implementation-spec.md` | §3 step 2 | "if insulin: STOP" | Route to insulin_cost module |
| `navigator-implementation-spec.md` | §6 Future work | Insulin listed as deferred | Remove or mark shipped |
| `business-solution.md` | §7.1 | Unshipped future work; "$35/month regardless of benefit phase" | Mark SHIPPED; clarify catastrophic $0; link to spec |
| `data-sources.md` | §2 Key files | 4 files listed | Add `insulin beneficiary cost` file |

### Adequate docs (no change needed)

| Document | Notes |
|---|---|
| `insulin-cost-estimation.md` | Comprehensive — canonical reference |
| `docs/README.md` | Already links insulin-cost-estimation.md |
| `docs/insulin_trails/` | This review folder (new) |

### Optional doc additions

| Item | Suggestion |
|---|---|
| `deployment.md` | Add note: post-deploy SPUF re-ingest required for insulin table |
| `developer-guide.md` | Mention insulin fixture file in test data section |
| `quality-test-todos.md` | Add insulin exploratory scenarios |

---

## Verification commands

Run after staging all files:

```bash
# Full unit + integration
pytest tests/ -v

# Insulin-focused subset
pytest tests/test_insulin.py tests/test_estimate_drug_cost.py -k insulin -v
pytest tests/test_spuf_ingest.py -k insulin -v

# Eval harness
LLM_MOCK=1 medicare-eval

# Golden oracle
python scripts/run_golden_cases.py --by-group
```

Re-record pass count in `insulin-cost-estimation.md` §9 if it differs from "275 passed."

---

## Coverage summary

| Area | Fixture tests | Golden cases | Live ingest | Eval |
|---|---|---|---|---|
| Basic cap (30-day) | Yes | golden-037 | No | eval-010 |
| 60/90-day scaling | Yes | No | No | No |
| Catastrophic $0 | Yes | No | No | No |
| Channel differentiation | Yes | No | No | Partial (eval-010 uses min) |
| Data-gap fallback | Yes | No | No | No |
| Hint-collision ingest | Yes | N/A | Spot-checked WY | N/A |
| Under-cap copay | No | No | Spec example only | No |
| NULL-tier fallback | No | No | Spec claim only | No |
| QL/PA/ST on insulin | No | No | No | No |

**Overall:** Strong fixture-level coverage for the happy path and main edge cases. Golden suite and eval harness are thin for insulin. Live-ingest oracle intentionally deferred per spec §10.
