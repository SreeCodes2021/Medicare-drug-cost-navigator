# Phase 6 Implementation Plan

**Medicare Drug Cost & Benefit-Transparency Navigator**

This document records a scope pivot: the broad conversational assistant built through Phase 5 (`106ad37`) plus three later un-documented commits (`4a60e4a` DuckDB read-only + frontend plan polling, `096b63d` policy corpus ingestion, `968147c` explicit LLM mock mode) was replaced with a single-purpose, deterministic cost estimator per [docs/navigator-implementation-spec.md](./navigator-implementation-spec.md). That spec — not `build-requirements.md` — is now the source of truth for scope.

**Base commit:** `968147c` (HEAD at time of writing). This phase's changes are in the working tree, not yet committed.

---

## 1. Overview

Phases 1–5 built a broad Medicare assistant: policy Q&A over a Chroma-backed RAG corpus, multi-year cost-trend history, therapeutic alternatives, a clarification agent, and a formulary/cost-share calculator, all orchestrated by an LLM tool-calling loop over 7 MCP tools.

Phase 6 discards everything outside a narrower goal: **estimate the out-of-pocket cost of one drug, on one plan's regular formulary, for one fill**, for a non-LIS beneficiary in the pre-deductible or initial-coverage phase — with six specific CMS data-correctness rules ("Bugs" 1–6) handled explicitly rather than glossed over. Insulin, excluded-drug formulary entries, and the catastrophic phase are explicit future work, not silently approximated.

**Phase 6 scope:** new 4-table SPUF schema that persists previously-discarded CMS fields (`PLAN_SUPPRESSED_YN`, `QUANTITY_LIMIT_`*, `PRIOR_AUTHORIZATION_YN`, `STEP_THERAPY_YN`, `DED_APPLIES_YN`); a single consolidated `estimate_drug_cost` tool implementing the spec's 8-step pipeline and all 6 bugs; a corrected `COVERAGE_LEVEL` code mapping discovered via real CMS data; verbatim-caveat guardrail enforcement; a rewritten frontend (chat + guided estimate tabs, Sources-only side panel — see §7), prompt, mock LLM layer, and test suite (79 tests, all passing).

**Deleted in Phase 6:** `agents/` (clarification, policy, synthesis), `intake/`, `orchestrator/pipeline.py` (legacy pipeline mode), `ingestion/policy_corpus.py`, `tools/{policy_retrieval,cost_trend,alternatives,ira_drugs,formulary_benefit,supply_estimate}.py`, `config/policy_corpus.yaml`, `config/benefit_params.yaml`, the Chroma vector store, and the `chromadb`/`instructor` dependencies.

---

## 2. Product boundaries (can / can't)

This section is the user-facing scope contract for Phase 6. The implementation spec ([navigator-implementation-spec.md](./navigator-implementation-spec.md) §1) and system prompt (`agent/prompts.py`) match these boundaries; hard stops are enforced inside `estimate_drug_cost`, not left to the LLM.

### Can (in scope)

| Boundary | What the tool does |
| -------- | ------------------ |
| **Medicare Part D** | Estimates drug cost for standalone PDPs and MAPD plans with a Part D benefit, using CMS SPUF quarterly data |
| **Florida (FL)** | Real CMS data is ingested and verified for FL only (572 plans in the current 2026 zip). Offline fixtures also include TX for tests |
| **Non-insulin drugs** | Generic or brand drugs on a plan's **regular** (`basic_drugs_formulary`) tier — e.g. lovastatin, metformin, lisinopril |
| **Orally administered** | Tablets, capsules, and other standard oral formulations (the v1 spec's "standard-tier, orally-administered" scope) |
| **Non-LIS beneficiaries** | Assumes no Low-Income Subsidy (Extra Help); published copay rows are used as-is |
| **Pre-deductible or initial-coverage phase** | User supplies year-to-date out-of-pocket spend; tool compares against plan deductible and applies per-tier `DED_APPLIES_YN` overrides (Bug 2) |
| **One drug, one plan, one fill** | Single cost estimate per request — not a year-long projection or multi-drug comparison |
| **30 / 60 / 90-day fills** | Standard CMS days-supply codes via the named `DAYS_SUPPLY_CODE_MAP` (Bug 1) |
| **Copay cost-sharing** | Returns a dollar estimate when the matched tier's cost-share type is copay |
| **Prior auth / step therapy** | Surfaces PA or ST as a verbatim caveat; cost is still computed (soft caveat, not a hard stop) |
| **Multiple NDCs per drug** | Reports a low–high range across all matched manufacturer NDCs (Bug 5) |
| **Plan lookup** | Resolve a plan by contract–plan ID (`S5921-383`) or list plans in the ingested state(s) |

### Can't (out of scope — hard stop or not supported)

| Boundary | What happens instead |
| -------- | -------------------- |
| **Other states (beyond ingested data)** | No plan/drug rows unless that state was ingested (`medicare-ingest spuf --states FL`). National multi-state coverage is deferred future work (§13) |
| **Insulin** | **Hard stop** — `is_insulin()` routes before any formulary lookup. Separate $35/month statutory cap and CMS file (see Q2 in §9) |
| **Medicaid** | Not supported — tool only reads Medicare Part D SPUF files; no Medicaid formulary or cost-share data |
| **LIS / Extra Help** | Not supported — no reduced copay/coinsurance tables for subsidized beneficiaries |
| **Catastrophic coverage phase** | Not computed — TrOOP threshold is not in SPUF; v1 only handles pre-deductible and initial-coverage phases |
| **Excluded-drug formulary** | Not supported — drugs covered only on enhanced/supplemental formularies are out of scope |
| **Indication-based restrictions** | Not supported — no matching of beneficiary diagnosis to FDA-approved indication |
| **Coinsurance dollar amounts** | **Not computed** — when cost-share type is coinsurance, Bug 4 caveat is returned instead of a dollar figure |
| **Suppressed plans** | **Hard stop** — `PLAN_SUPPRESSED_YN=Y` blocks all downstream lookups (Bug 6) |
| **Quantity-limit violations** | **Hard stop** — requested days supply exceeds the plan's max single-fill limit (Bug 5b) |
| **Non-standard fill sizes** | Partial estimate only (ingredient cost, no reliable cost-share) when days supply is not 30/60/90 |
| **Policy Q&A, alternatives, cost trends** | Removed in Phase 6 — no Chroma RAG corpus, no therapeutic-alternative or multi-year trend tools |
| **Plan switching / enrollment advice** | Never recommended — informational estimates only |
| **Medical or financial advice** | Disclaimed in every response |
| **Real-time pharmacy pricing** | CMS quarterly reference data only; actual pharmacy charge may differ |

---

## 3. Decisions locked for Phase 6


| Decision                  | Choice                                                                                        | Rationale                                                                                                                                                         |
| ------------------------- | --------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Tool granularity          | **One consolidated `estimate_drug_cost` tool**, plus small `lookup_plan`/`list_plans` helpers | Hard-stop and ordering rules (suppressed-plan check first, days-supply mapping before any join) must not depend on an LLM correctly sequencing several tool calls |
| Drug resolution           | `**normalize_drug()` called internally**, not LLM-visible                                     | The insulin hard-stop lives inside drug resolution and must never be a skippable step                                                                             |
| Suppressed plans (Bug 6)  | **Persist `plan_suppressed`, do not filter at ingest**                                        | The old ingest-time exclusion meant a suppressed plan could never even be selected, so the mandated hard-stop could never fire                                    |
| Coinsurance (Bug 4)       | **Excluded entirely from the cost range**, never estimated                                    | CMS layout doesn't confirm coinsurance's dollar base; showing a computed figure risks presenting an unverified number as firm                                     |
| PA / step therapy         | **Soft caveat, cost still computed**                                                          | Spec's "surface as a hard caveat, do not silently compute" contrasts with Bug 6's explicit "not a caveat appended... a hard stop"                                 |
| `COVERAGE_LEVEL` codes    | **0 = deductible, 1 = initial coverage, 3 = catastrophic** (2 unused)                         | Verified against real 2026 CMS FL data — the initial 1/2 assumption was wrong and would have silently returned incorrect low-ball costs (see §8)                  |
| Disclaimer enforcement    | **Guardrail force-appends**, not just a prompt instruction                                    | Bug 6 / Bug 4 text must never be droppable by LLM paraphrase — these are safety-critical, not stylistic                                                           |
| Days-supply mapping       | **Single named lookup** (`tools/days_supply.py`)                                              | Spec Section 4 explicitly forbids inlining/repeating this translation at each join site                                                                           |
| Multi-NDC pricing (Bug 5) | **Independent per-NDC computation, report low–high range**                                    | Different manufacturers' NDCs can carry different prices or even different tiers; picking one arbitrarily hides that spread                                       |


---

## 4. New DB schema

Rebuilt from scratch — `data/navigator.duckdb` and `data/chroma/` were deleted, not migrated.

```
plans                    plan_key PK, contract_id, plan_id, plan_name, plan_type, state,
                          contract_year, formulary_id, deductible, plan_suppressed

basic_drugs_formulary     formulary_id, ndc, rxcui, tier, quantity_limit_yn,
   (was: formulary)        quantity_limit_amount, quantity_limit_days,
                           prior_authorization_yn, step_therapy_yn, as_of_date

pricing                   plan_key, ndc, days_supply (raw day count), unit_cost

beneficiary_cost          plan_key, tier, coverage_level, days_supply_code (CMS 1-4
                          CODE, not a day count), pharmacy_channel, cost_type, copay,
                          coinsurance_pct, ded_applies_yn, as_of_date
```

Removed: `cost_trends`, `alternatives`, `policy_passages` (served only deleted features).

Ingestion (`ingestion/spuf.py`) changes: `PLAN_SUPPRESSED_YN` exclusion removed from `_plan_in_filter`; `basic_drugs_formulary` dedupes to the max `FORMULARY_VERSION` per `(formulary_id, contract_year)`; `_extract_cost_shares` no longer discards every `days_supply`/`coverage_level` value except 1 — every CMS code now survives ingestion.

---

## 5. `estimate_drug_cost` — the consolidated tool

`src/medicare_navigator/tools/estimate_drug_cost.py` runs spec Section 3's 8 steps as one deterministic async function:

1. Resolve plan → hard stop if `plan_suppressed` (Bug 6, verbatim message)
2. Resolve drug via `normalize_drug()` → hard stop if insulin (Section 6, future work)
3. Formulary lookup → screen every matched NDC against `QUANTITY_LIMIT_*` (Bug 5b); surface PA/step-therapy as a caveat
4. Map requested days-supply to its CMS code via the single named `DAYS_SUPPLY_CODE_MAP` (Bug 1) — an unmapped value (not 30/60/90) takes the explicit "other" branch, never silently coerced
5. Price each surviving NDC: `unit_cost × ceil(days_supply / 1)` (Bug 3 — ceil, never floor/round)
6. Determine benefit phase from YTD spend vs. plan deductible; override per-tier via `DED_APPLIES_YN` (Bug 2, disclaimer always attached)
7. Cost-share lookup; coinsurance-typed NDCs are excluded from the range, not estimated (Bug 4)
8. Assemble `DrugCostEstimate`: low–high range across surviving NDCs (Bug 5), with every triggered caveat attached verbatim

New supporting modules: `tools/days_supply.py`, `tools/insulin.py` (hardcoded name/ingredient allowlist), `tools/disclaimers.py` (verbatim caveat strings as named constants — single source of truth for tool code and tests).

---

## 6. LLM, prompt, and guardrail changes

- `agent/prompts.py` — narrow system prompt describing the exact v1 scope, instructing verbatim caveat relay and a hard stop on `suppressed`/`insulin_out_of_scope`/`quantity_limit_blocked` statuses.
- `llm/mock.py` — simplified to the single-tool flow; a dropped drug-name fallback branch (length-based token guess when the local RxNorm cache has no match) was restored after real-data testing exposed the regression.
- `guardrails/citations.py` — `apply_guardrails` now force-appends any tool-returned caveat or hard-stop message not verbatim-present in the LLM's explanation. Two false positives were found and fixed during this phase:
  - `_has_formulary_evidence` required `data` to be truthy, so a legitimate `not_covered` result (which has `data=None` by design) was mistaken for an unbacked "tier/copay" claim and triggered a garbled retry.
  - The dollar-amount traceability check ran against hard-stop messages too, so insulin's own disclosed "$35/month cap" text was flagged as an untraceable figure.
- `llm/client.py` — `structured_completion`/`instructor` removed (only served the deleted legacy pipeline).

---

## 7. Frontend rewrite (chat + guided estimate)

The Phase 6 UI is a second rewrite on top of the Phase 5 committed static UI (`frontend/src/`). It aligns the interface with the narrower cost-estimator scope: cost and caveats live in the **chat transcript** (LLM explanation); the right panel is **Sources only** (citations + data-as-of), not structured formulary/trend/alternative cards.

### Layout

| Before (Phase 5 / early Phase 6) | After (current) |
| -------------------------------- | --------------- |
| 3-column grid: filters sidebar · chat · results | 2-column grid: **main panel** · **Sources** panel |
| Filters always visible in left sidebar | Filters moved into **Guided estimate** tab |
| Results: formulary card + cost-trend bars + alternatives list + citations | Results: **citations only** (+ data-as-of badge, tool-status footer) |
| `filter-alternatives` / `filter-trend` checkboxes | Removed — those backend tools were deleted |
| Collapsible filter panel with badge count | Removed — replaced by tabbed input modes |

### Dual input modes (`switchMode`)

The main panel has two ARIA tabbed modes:

1. **Ask in chat** (default) — free-form textarea; optional filters from the guided form are still sent via `getFilters()` when populated.
2. **Guided estimate** — structured form: drug, dosage, plan (with refresh + poll), contract year, **days supply** (30/60/90), YTD out-of-pocket. **Get estimate** validates drug + plan, composes a natural-language prompt (`composeGuidedMessage`), calls the same `/api/chat` path, then **switches to the chat tab** so the user sees the full assistant reply.

Loading copy changed from "Looking up formulary…" to **"Estimating cost…"**.

### Cost display model

The UI does **not** render a dedicated cost-range card from `response.estimate` (`DrugCostEstimate`). Dollar figures and verbatim caveats appear in the assistant message body (`explanation`), with inline citation links. The API still returns `estimate` for citations/guardrails/tests (`CHAT_RESPONSE_UI_FIELDS` documents it), but the frontend treats chat text as the primary cost surface.

### Copy and prompt chips

- Subtitle: *"Estimate what a single drug fill will cost on a Medicare plan"*
- Empty-state chips retargeted to cost estimates (metformin cost, 90-day lisinopril, YTD spend scenario) — alternatives/trend chips removed
- Results placeholder: *"Source citations will appear here after you get an estimate."*

### Files touched

```
frontend/src/
├── index.html    # main-panel, mode tabs, guided form, Sources header
├── app.js        # switchMode, submitGuidedEstimate, composeGuidedMessage;
│                 # removed renderFormularyCard/renderCostTrendCard/renderAlternativesCard
└── styles.css    # 2-col layout, mode-tab/guided styles; removed phase-pill/trend-bar CSS

src/medicare_navigator/ui_test/checks.py   # element-ID contract, smoke messages, JS fn checks
tests/test_ui.py                           # dist contract tests (unchanged structure)
```

### UI test contract updates

`ui_test/checks.py` now requires:

- New IDs: `main-panel`, `mode-tab-chat`, `mode-tab-guided`, `mode-chat`, `mode-guided`, `guided-body`, `guided-error`, `guided-submit`, `filter-days-supply`
- Removed IDs: `filters-panel`, `filter-badge`, `toggle-filters`, `filters-body`, `filter-alternatives`, `filter-trend`
- New JS functions: `switchMode`, `submitGuidedEstimate`
- `CHAT_RESPONSE_UI_FIELDS`: `estimate` replaces `formulary` / `cost_trend` / `alternatives`
- Smoke messages retargeted to cost-estimate queries (including a 90-day quantity-limit scenario)

Run UI checks:

```bash
medicare-ui-test run --offline
# or
pytest tests/test_ui.py -v
```

---

## 8. Real-data findings (FL-only ingest)

Per the approved plan, the already-cached real CMS SPUF zip (`data/raw/SPUF_2026_20260408.zip`) was re-ingested restricted to FL only (`medicare-ingest spuf --source ... --states FL`): 572 plans, 188,841 formulary rows, 5,726,853 pricing rows, 60,314 beneficiary-cost rows.

This surfaced one substantive correctness bug that the synthetic fixtures didn't catch: real `COVERAGE_LEVEL` values are **0** (deductible), **1** (initial coverage), and **3** (catastrophic — ~$0 copay/coinsurance in 99%+ of real rows, consistent with the IRA's post-2025 catastrophic-phase redesign). Code **2** never appears. The implementation had assumed 1/2; left uncorrected, it would have silently matched the wrong phase's cost-share row (in one traced case, a real coinsurance-typed drug would have wrongly resolved to a $0 catastrophic-phase copay row and returned a confidently wrong "free" estimate instead of the correct "coinsurance not calculated" caveat). Fixed in `estimate_drug_cost.py`; fixtures updated to match.

Other real-data checks: quantity-limit blocking (Bug 5b) and coinsurance exclusion (Bug 4) both confirmed against real rows. No suppressed plans or multi-NDC-per-formulary drugs happened to appear in this FL slice — both remain covered by the fixture-based unit tests per the plan's contingency.

---

## 9. Example queries and responses

**These run against a real plan and real Sonnet** (`claude-sonnet-4-6` via the live Anthropic API, `response_source: anthropic/claude-sonnet-4-6`), not the mock LLM or synthetic fixture data. The plan is `S5921-383` — **AARP Medicare Rx Preferred from UHC (PDP)**, a real Florida PDP from the CMS SPUF zip ingested restricted to FL (§8). The drug in Q1 is lovastatin 40mg, RXCUI `197905` (its real RxNorm clinical-drug identifier), on the plan's real tier-1/NDC/pricing/cost-share rows.

(Earlier drafts of this section used `H8888-001` / `S9999-001` — those are **not real plans**; they're synthetic fixture plans defined in `tests/fixtures/spuf/plan information.txt` for offline/mock testing. This section now reflects a genuine live run instead.)

### Q1 — a normal, single-NDC cost estimate, with a real per-tier deductible exemption

> **User:** "How much will lovastatin 40mg cost me on Medicare plan S5921-383? I have not spent anything out of pocket yet this year."

**Bot (real Sonnet):**

> Great news — here are theS5921-383 details for your lovastatin 40mg on **AARP Medicare Rx Preferred from UHC (PDP)** (plan S5921-383, Florida, 2026):
>
> **Estimated cost for a 30-day fill: $5.00**
>
> Lovastatin is covered on **Tier 1** of this plan's formulary, which is typically the lowest-cost generic tier. With $0 in year-to-date out-of-pocket spending, you are currently in the **pre-deductible phase** (this plan has a $130 deductible). The $5.00 estimate reflects your cost-share in that phase.
>
> ---
>
> This estimate assumes the deductible-phase determination is based on your reported YTD spend and this plan's per-tier deductible rule as published by CMS. Some plans exempt certain tiers from the deductible; if your actual pharmacy charge differs from this estimate, your plan's tier-specific deductible treatment is the most likely reason. Confirm with your plan.
>
> ---
>
> **General Disclaimer:** These figures are government reference data for the current quarter and are not real-time pharmacy pricing. Your actual cost at the pharmacy may differ based on your specific circumstances, pharmacy network, benefit phase, and plan rules. Always confirm your cost-share with your plan or pharmacist before filling a prescription.
>
> *Disclaimer: This tool is for informational purposes only. The model can make mistakes. This is not medical advice, financial advice, or insurance enrollment guidance. Costs shown are an estimate based on CMS-published plan data for the current quarter, not a guarantee of actual pharmacy charge or real-time pricing. Confirm any information with your doctor, pharmacist, or Medicare plan before making decisions.*

Under the hood: `$0` YTD spend is below the plan's real $130 deductible, so the raw phase is `pre_deductible` — but tier 1 on this real plan has `DED_APPLIES_YN=N` (deductible-exempt), so Bug 2's per-tier override applies the real initial-coverage $5.00 copay row instead of full price. The tool returns `benefit_phase: pre_deductible` (the *raw* phase) while the actual $5.00 figure already reflects the override — the Bug 2 caveat is what discloses this to the user, and Sonnet correctly relayed it verbatim rather than paraphrasing it away.

### Q2 — an out-of-scope drug, routed to a hard stop

> **User:** "How much will Lantus insulin cost me on Medicare plan S5921-383?"

**Bot (real Sonnet):**

> Insulin cost estimates are not supported by this tool. Insulin has a separate statutory $35/month cap that does not depend on deductible or benefit-phase status, and CMS publishes it under a different file than the one this estimator uses. Please check your plan's insulin-specific pricing directly.
>
> ---
>
> *Disclaimer: This tool provides government reference data for the current quarter and is not a guarantee of actual pharmacy pricing. Always confirm costs directly with your plan or pharmacist before making healthcare decisions.*
>
> Disclaimer: This tool is for informational purposes only. The model can make mistakes. This is not medical advice, financial advice, or insurance enrollment guidance. Costs shown are an estimate based on CMS-published plan data for the current quarter, not a guarantee of actual pharmacy charge or real-time pricing. Confirm any information with your doctor, pharmacist, or Medicare plan before making decisions.

No formulary or pricing lookup is attempted — `is_insulin()` routes the request to the future-scope message before step 3 of the pipeline ever runs. Sonnet relayed the tool's message essentially verbatim and added its own short disclaimer on top of it, and the guardrail's own force-appended general disclaimer still ran regardless, giving the double disclaimer seen above.

---

## 10. Test coverage


| File                               | Covers                                                                                                                                                           |
| ---------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `tests/test_estimate_drug_cost.py` | Dedicated coverage of Bugs 1–6, insulin routing, suppressed-plan hard stop, PA/ST soft caveat                                                                    |
| `tests/test_spuf_ingest.py`        | New schema, suppressed-plan persistence, `FORMULARY_VERSION` dedup, QL/PA/ST/DED_APPLIES_YN ingestion, all days-supply codes/coverage levels surviving ingestion |
| `tests/test_mcp_registry.py`       | `estimate_drug_cost`/`lookup_plan`/`list_plans` tool dispatch, suppressed/insulin routing at the MCP layer                                                       |
| `tests/test_navigator.py`          | End-to-end agent loop: cost estimate, plan clarification, suppressed hard stop, insulin routing                                                                  |
| `tests/test_citations.py`          | Citation building against the new artifact shape; guardrail force-append for hard-stop messages and caveats                                                      |
| `tests/test_ui.py`                 | Frontend dist contract: element IDs, JS↔HTML parity, API smoke chat; `CHAT_RESPONSE_UI_FIELDS` includes `estimate` (not formulary/trend/alternatives) |
| `ui_test/checks.py`                | Guided-estimate element IDs, `switchMode`/`submitGuidedEstimate` JS contract, cost-focused smoke messages |


**79 tests pass.** Run:

```bash
scripts/build-frontend.sh   # optional; conftest runs it if needed
pytest tests/ -v
```

`medicare-eval` was also updated (`eval/queries.jsonl`, `eval/run_eval.py`) and passes 11/11 cases against the offline fixture.

---

## 11. Repo layout (Phase 6 changes)

```
src/medicare_navigator/
├── tools/
│   ├── estimate_drug_cost.py      # new — the consolidated 8-step tool
│   ├── days_supply.py             # new — single named DAYS_SUPPLY_CODE_MAP
│   ├── insulin.py                 # new — is_insulin() allowlist
│   └── disclaimers.py             # new — verbatim caveat/message constants
├── ingestion/
│   ├── schema.py                  # new 4-table shape
│   └── spuf.py                    # persists plan_suppressed/QL/PA/ST/DED_APPLIES_YN
├── storage/repository.py          # BasicDrugsFormularyRepository, BeneficiaryCostRepository
├── models/response.py             # DrugCostEstimate replaces FormularyResult/SupplyEstimate/...
├── mcp/{schemas,registry}.py      # 3 tools instead of 7
├── agent/{prompts,navigator}.py   # narrow prompt; DrugCostEstimate-based response assembly
├── llm/mock.py                    # single-tool mock flow
└── guardrails/citations.py        # verbatim-caveat enforcement guardrail

frontend/src/                      # 2-col layout: chat/guided tabs + Sources panel (§7)
│   ├── index.html                 # mode tabs, guided form, days-supply select
│   ├── app.js                     # cost in chat explanation; citations in Sources panel
│   └── styles.css

src/medicare_navigator/ui_test/    # medicare-ui-test CLI; updated Phase 6 contracts

(deleted) agents/, intake/, orchestrator/pipeline.py,
          ingestion/policy_corpus.py,
          tools/{policy_retrieval,cost_trend,alternatives,ira_drugs,
                 formulary_benefit,supply_estimate}.py,
          config/{policy_corpus,benefit_params}.yaml
```

---

## 12. How to run

```bash
# Local — offline fixture
medicare-ingest spuf --source tests/fixtures/spuf
uvicorn medicare_navigator.api.app:app --reload --port 8000

# Local — real CMS data, FL only
medicare-ingest spuf --source data/raw/SPUF_2026_20260408.zip --states FL

# UI contract checks
medicare-ui-test run --offline

# Tests
pytest tests/ -v

# Eval
LLM_MOCK=true python -m medicare_navigator.eval.run_eval
```

---

## 13. Phase 6 → future work (deferred, per spec Section 6)

- Insulin cost-share (separate $35/month statutory cap, separate CMS file, no benefit-phase dependency)
- Excluded-drugs formulary (enhanced/supplemental plan coverage only)
- Indication-based coverage restrictions
- Catastrophic-phase computation (annual statutory TrOOP threshold, not present in any SPUF file)
- Fully automatic benefit-phase detection purely from YTD dollar input (v1 still requires the explicit per-tier deductible check)
- A confirmed coinsurance base, once available from an authoritative CMS source, to replace the Bug 4 disclaimer with an actual computation
- National (beyond FL) real-data ingest and multi-state verification of Bug 5/Bug 6 real-world examples, which did not appear in the FL-only slice

