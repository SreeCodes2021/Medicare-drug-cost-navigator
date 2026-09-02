# Insulin Cost Estimation

## 1. Summary

Insulin (Lantus, Humalog, NovoLog, Toujeo, Levemir, Tresiba, Apidra, Fiasp, Basaglar,
Semglee, Admelog, Humulin, Lyumjev, and the GLP-1/insulin combination products Soliqua
and Xultophy) now returns a real, CMS-sourced cost estimate — capped at the Inflation
Reduction Act's statutory **$35 per 30-day supply** (scaled for 60/90-day fills), with
no deductible phase and $0 once catastrophic coverage is reached — instead of the v1
hard-stop message ("insulin cost estimates are not supported by this tool").

A narrower hard stop is retained (`ToolStatus.insulin_out_of_scope`) for the genuine
case where a specific plan has no published CMS cost-share record for that drug's tier
and fill size. That is now a data-gap message, not a blanket "insulin unsupported"
message.

This document supersedes the "insulin is out of scope for v1" language in
[`navigator-implementation-spec.md`](./navigator-implementation-spec.md) §1/§6 and the
unshipped roadmap entry in [`business-solution.md`](./business-solution.md) §7.1 — both
still describe the pre-implementation state and are candidates for a follow-up edit to
point here (not done as part of this document).

## 2. Why insulin needed separate handling

Since January 1, 2023 (Inflation Reduction Act, enacted August 2022), Medicare Part D
beneficiary cost-sharing for each covered insulin product is governed by rules that
don't fit the general tiered/deductible pipeline every other drug uses:

- **Flat statutory cap, not tiered cost-sharing.** Capped at $35 for a 30-day supply,
  scaled for longer fills (up to $70 for 60 days, $105 for 90 days) — regardless of the
  drug's formulary tier or whether that tier is normally copay- or coinsurance-based.
- **No deductible, ever.** Unlike every other formulary drug, insulin is statutorily
  exempt from the deductible phase in every contract year.
- **Still $0 in the catastrophic phase.** Once a beneficiary's year-to-date
  out-of-pocket spend reaches the annual Part D OOP cap ($2,100 for 2026), *all*
  covered Part D drugs — insulin included — drop to $0 cost-sharing. The $35 figure is
  a pre-catastrophic ceiling, not a fixed price that always applies.
- **Per-product, not pooled.** A beneficiary on two different insulin products (e.g., a
  long-acting basal + a rapid-acting mealtime insulin) can be charged up to $35 for
  *each* — up to $70/month combined, not $35 total.
- **Published in a separate CMS file.** CMS's quarterly Prescription Drug Plan
  Formulary, Pharmacy Network, and Pricing Information release (the "SPUF") ships
  insulin's capped pricing in a dedicated **Insulin Beneficiary Cost File**, structurally
  different from the general **Beneficiary Cost File** every other drug's estimate is
  priced from.

## 3. Government-issued source documents

### Primary — CMS technical documentation and data

These sources define the actual schema and were used directly to build the ingest and
calculation logic.

- **[CMS SPUF Record Layout, Contract Year 2026](https://data.cms.gov/sites/default/files/2025-10/83da019f-fa24-483e-87de-cc089780a6a5/SPUFRecordLayout-2026.pdf)**
  — the authoritative, current field-by-field layout for all files in the quarterly
  release, including the Insulin Beneficiary Cost File's exact schema (§4 below).
  Located via CMS's own dataset metadata (`describedBy` field on the dataset entry in
  [`data.cms.gov/data.json`](https://data.cms.gov/data.json)) — CMS's public "Files for
  Order" web page only linked archived 2017/2020 layouts, not the current one.
- **[SPUF Public Use File Methodology (2024)](https://data.cms.gov/sites/default/files/2023-10/98c7b019-7e9c-4c6d-a77c-f49f6c5b87e6/Methodology-SPUF-2024.pdf)**
  — confirms the file inventory (the quarterly release is 10 files, including the
  Insulin Beneficiary Cost File as its own numbered entry) and general SPUF conventions.
- **[CMS 2022 Quarterly SPUF Record Layout](https://www.cms.gov/files/document/record-layout-2022-quarterly-file.pdf)**
  — the previous-generation layout, useful for historical contrast: it documents the
  old, voluntary **"Senior Savings Model" file** (a single flat `COPAY` field, no
  ambiguity) that predates the IRA's mandatory $35 cap. Confirms the current file's
  dual copay/coinsurance-field design (§5) is a genuinely new, post-2023 CMS schema
  choice, not a carryover with a settled precedent.
- **The actual CMS data**, already present in this repo:
  `data/raw/SPUF_2026_20260408.zip` → `insulin beneficiary cost file  PPUF_2026Q1.zip`
  (43,140 real rows). Every schema and calculation claim in this document was verified
  directly against this file, not assumed from the layout PDF alone.

CMS's `www.cms.gov` domain blocks common non-browser HTTP clients (verified: returns
403 to a plain fetch, but succeeds with a standard browser `User-Agent` header);
`data.cms.gov` has no such restriction.

### Secondary — policy/business context

These informed the business understanding (why the cap exists, how it's meant to work)
but are journalism/vendor sources, not primary regulatory text — treat as directionally
reliable, not byte-for-byte authoritative:

- [KFF — Insulin Costs and Coverage in Medicare Part D](https://www.kff.org/medicare/insulin-costs-and-coverage-in-medicare-part-d/)
- [CoveredUSA — Does Medicare Cover Insulin in 2026? ($35 Cap Explained)](https://coveredusa.org/en/qa/does-medicare-cover-insulin)
- [Johns Hopkins Bloomberg School of Public Health — Medicare Patients' Out-of-Pocket Costs for Insulin Decrease Under Mandated Caps](https://publichealth.jhu.edu/2026/medicare-patients-out-of-pocket-costs-for-insulin-decrease-under-mandated-caps)
  — documents that ~25% of beneficiaries were overcharged relative to the cap in 2023
  due to inconsistent 30-day-multiple proration by plans/pharmacies in the real world.
- [CMS — Part D Senior Savings Model (Insulin Savings) Common Questions & Answers](https://www.cms.gov/sites/default/files/2021-10/Insulin%20Common%20Questions%20Sept%202021_Clean_abridged_DCD_CMMI_clean_508.pdf)
  — background on the pre-IRA voluntary program the current mandatory cap superseded.
- [Sanofi — SOLIQUA 100/33 Medicare Part D Savings Program](https://www.soliqua100-33.com/medicare-part-d-senior-savings)
  — used to confirm GLP-1/insulin combination products (Soliqua, and by the same
  mechanism Xultophy) are billed as a single capped insulin product under Part D. This
  is the one calculation-relevant fact sourced only from a secondary/vendor page, not
  CMS's own primary regulatory text — see §10.

## 4. Data source: the CMS Insulin Beneficiary Cost File

Confirmed schema (from the CY2026 record layout, cross-checked against the real file):

| Field | Type | Description (CMS's own wording) |
|---|---|---|
| `CONTRACT_ID` | Char(5) | Organization contract number assigned by CMS |
| `PLAN_ID` | Char(3) | Plan identifier assigned by CMS |
| `SEGMENT_ID` | Char(3) | Segment ID for local MA-PD plans (zero for all other) |
| `TIER` | 9(2) | Cost share tier value. **This field is missing for defined standard plans.** |
| `DAYS_SUPPLY` | 9(1) | Length of days supply: `1`=30 days, `2`=90 days, `3`=other, `4`=60 days |
| `COPAY_AMT_PREF_INSLN` | 9(12.2) | Amount of copay for insulin at preferred retail pharmacies (`$$$$cc`, e.g. `2.65` → $2.65) |
| `COPAY_AMT_NONPREF_INSLN` | 9(12.2) | Same, standard retail pharmacies |
| `COPAY_AMT_MAIL_PREF_INSLN` | 9(12.2) | Same, preferred mail-order pharmacies |
| `COPAY_AMT_MAIL_NONPREF_INSLN` | 9(12.2) | Same, standard mail-order pharmacies |
| `COIN_AMT_PREF_INSLN` | 9(12.2) | Coinsurance for insulin at preferred retail pharmacies (2-decimal fraction, e.g. `.25` → 25%) |
| `COIN_AMT_NONPREF_INSLN` | 9(12.2) | Same, standard retail pharmacies |
| `COIN_AMT_MAIL_PREF_INSLN` | 9(12.2) | Same, preferred mail-order pharmacies |
| `COIN_AMT_MAIL_NONPREF_INSLN` | 9(12.2) | Same, standard mail-order pharmacies |

Two structural differences from the general Beneficiary Cost File matter for
implementation:

1. **No `COVERAGE_LEVEL` column at all.** The general file has separate rows per
   benefit phase (`0`=pre-deductible, `1`=initial coverage, `3`=catastrophic). The
   insulin file has none — consistent with insulin's flat, phase-independent-except-
   catastrophic pricing (§6).
2. **No `COST_TYPE_*` selector.** The general file explicitly flags, per channel,
   whether that channel is `0`=not offered, `1`=copay, or `2`=coinsurance. The insulin
   file has no equivalent field — both a copay amount *and* a coinsurance amount are
   always present on every row, with nothing in the schema saying which one a
   beneficiary is actually charged. This is the subject of §5.

Also confirmed directly against the real file (not documented by CMS): the raw release
ships this file's copay/coinsurance column names in **lowercase**
(`copay_amt_pref_insln`, etc.), unlike every other SPUF file, which uses uppercase
column names throughout. This repo's ingest pipeline (`ingestion/spuf.py`) already
upper-cases every column name on read (`_normalize_header`) regardless of source casing,
so this is transparent to the rest of the code — noted here only because it was a real,
easy-to-miss bug during implementation (see §7).

## 5. Resolving the copay-vs-coinsurance ambiguity

CMS's own record layout documents what each field individually means but — unlike the
general Beneficiary Cost File — does not state which field (`COPAY_AMT_*_INSLN` or
`COIN_AMT_*_INSLN`) is the amount a beneficiary is actually charged. No other CMS
document (methodology PDF, Senior Savings Model FAQ) resolves this either.

This was settled empirically instead of by assumption: every row in the real,
currently-ingested Insulin Beneficiary Cost File (43,140 rows) was joined against the
matching row in the general Beneficiary Cost File — same `CONTRACT_ID` / `PLAN_ID` /
`SEGMENT_ID` / `TIER` / `DAYS_SUPPLY` — using the general file's own `COST_TYPE_PREF` /
`COST_TYPE_NONPREF` / `COST_TYPE_MAIL_PREF` / `COST_TYPE_MAIL_NONPREF` fields as ground
truth for whether that plan/tier/channel is copay- or coinsurance-based.

**Result, across 122,472 checked (row × channel) instances:**

| Check | Result |
|---|---|
| `COPAY_AMT_*_INSLN` exceeds the statutory cap ($35 / $70 / $105 for 30 / 60 / 90-day) | **0 exceptions**, in any row, of any type |
| `COPAY_AMT_*_INSLN` correctly caps *down* where the general file's real tier copay is higher (e.g. a $47 general copay shows as $35 insulin copay) | Confirmed directly, multiple examples |
| Channel offered/not-offered alignment (blank insulin fields exactly where the general file's `COST_TYPE`=`0`) | 100% clean, 0 mismatches either direction |
| `COIN_AMT_*_INSLN` matches the general file's real coinsurance rate | Only ~62% of coinsurance-type rows — the rest show a value (often a flat `0.25`) that doesn't track the plan's actual rate |

**Conclusion:** `COPAY_AMT_*_INSLN` is the sole authoritative charge. `COIN_AMT_*_INSLN`
is never used to compute a dollar figure — its existence and non-use are disclosed to
the end user via `INSULIN_STATUTORY_CAP_CAVEAT` (§7) rather than silently dropped,
consistent with this codebase's existing practice of disclosing rather than guessing on
ambiguous CMS fields (the precedent is `BUG4_CAVEAT`, which does the same for regular
drugs' coinsurance-base ambiguity — see
[`navigator-implementation-spec.md` §5, Bug 4](./navigator-implementation-spec.md#bug-4--coinsurance-base-is-not-confirmed)).

This is a **point-in-time empirical finding** against the CY2026 Q1 SPUF release. CMS
could in principle change this file's field semantics in a future quarterly release
without changing the schema. The same cross-validation should be re-run after each
future real ingest before trusting a new quarter's numbers unquestioned — see §10.

## 6. Calculation methodology

```
1. Detect insulin          is_insulin(drug_name, ingredient) — hardcoded name/ingredient
                            allowlist (tools/insulin.py). No CMS field marks a drug as
                            insulin, so this can't be a data-driven check. Checked twice:
                            once on the canonical name (pre-RxNorm), once on the
                            RxNorm-resolved name + ingredient (post-RxNorm) — independent
                            detection signals, belt-and-suspenders.

2. Formulary lookup        Same as any other drug: basic_drugs_formulary[FORMULARY_ID,
                            RXCUI] -> matched NDC(s) + TIER. Quantity-limit and
                            not-covered checks run BEFORE the insulin-specific branch,
                            so an insulin drug can surface those statuses like any other
                            drug (this is a deliberate behavior change from the old
                            hard-stop, which always short-circuited past them).

3. Existence check          For the matched tier(s) and requested days-supply CODE (see
                            step 4), does insulin_beneficiary_cost have ANY row (any
                            channel) for this plan? If not: narrow insulin_out_of_scope
                            hard stop (data gap) — do not fall through to the general
                            pricing pipeline, and do not guess $0.

4. Days-supply code         Reuses the SAME days_supply.py mapping already used by every
                            other drug (30->1, 60->4, 90->2) — CMS pre-bakes the 30-day
                            multiple into the insulin file's own dollar figures per code
                            (verified: code 1 = $35, code 4 = $70 = 2x35, code 2 = $105
                            = 3x35), so no local ceil(days_supply/30) computation is
                            needed. A days_supply outside {30, 60, 90} is unmapped, same
                            as the general pipeline — no dollar estimate is fabricated.

5. Copay lookup             insulin_beneficiary_cost[plan_key, TIER, days_supply_code,
                            pharmacy_channel] -> COPAY_AMT (already the capped figure,
                            per §5). No deductible/coverage-level branching at all — the
                            only phase-sensitivity is step 6.
                            A channel with no row (not offered) stays unpriced in every
                            phase, including catastrophic — never fabricated as $0.

6. Catastrophic override    compute_benefit_phase(ytd_oop_spend, ...) — the SAME
                            function used for every other drug. If the beneficiary's
                            YTD out-of-pocket spend has reached the annual Part D cap:
                            applied cost = $0 (benefit_phase reads "catastrophic",
                            reusing CATASTROPHIC_PHASE_NOTE verbatim). Otherwise:
                            applied cost = the capped copay from step 5, and
                            benefit_phase reads "insulin_cap".

7. Output                   Same DrugCostEstimate / MultiChannelDrugCostEstimate shape
                            as any other drug. ded_applies_yn is forced to "NA" (no
                            deductible phase exists for insulin, so the general file's
                            per-tier flag — which describes an unrelated drug at that
                            tier number — is never looked up).
                            Caveat: INSULIN_STATUTORY_CAP_CAVEAT replaces BUG2_CAVEAT
                            (the deductible-phase caveat, inapplicable to insulin).
                            Bug 5 (multi-NDC range), Bug 5b (quantity limit), and PA/ST
                            caveats are orthogonal to the cap and apply unchanged.
```

Per-product independence (a beneficiary on two insulins can be charged up to $35 for
each) requires no special code: the estimator is already called once per single
resolved drug, so nothing aggregates cost across separate calls.

## 7. Implementation

### Data layer

| File | Change |
|---|---|
| [`ingestion/schema.py`](../src/medicare_navigator/ingestion/schema.py) | New `insulin_beneficiary_cost` table — one row per `(plan_key, segment_id, tier, days_supply_code, pharmacy_channel)`, mirroring the general `beneficiary_cost` table's row-per-channel convention (not CMS's wide, one-row-per-plan/tier layout) so the two repositories share the same query shape. `coin_amt_*` columns are omitted entirely — never stored, so nothing tempts a future read of the unreliable field. |
| [`ingestion/spuf.py`](../src/medicare_navigator/ingestion/spuf.py) | New `INSULIN_FILE_HINTS`, `INSULIN_PHARMACY_CHANNEL_COLUMNS`, `_extract_insulin_cost_share()`. **Real bug found and fixed during implementation:** the insulin zip member's real name (`"insulin beneficiary cost file ..."`) contains the general file's own discovery hint (`"beneficiary cost"`) as a substring — `_find_member()` needed a new `exclude_hints` parameter so the general-file lookup skips the insulin file, and vice versa. Without this, file discovery worked only by accident of zip-member ordering. Also added `insulin_beneficiary_cost` to the two hardcoded SPUF table lists in `_purge_states()` and the `preserve_non_spuf_tables` branch of `ingest_spuf()`, so incremental re-ingests don't leak stale insulin rows. |
| [`storage/repository.py`](../src/medicare_navigator/storage/repository.py) | New `InsulinBeneficiaryCostRepository.get_cost_share()` (exact tier match, falling back to `TIER IS NULL` for defined-standard plans per §4) and `.has_any()` (existence check for the narrow fallback). |

### Business logic

| File | Change |
|---|---|
| [`tools/insulin.py`](../src/medicare_navigator/tools/insulin.py) | Allowlist widened: `lyumjev`, `soliqua`, `xultophy` added — found missing against live CMS formulary data (Lyumjev and Soliqua were on-formulary under those exact brand names but not recognized). |
| [`tools/insulin_cost.py`](../src/medicare_navigator/tools/insulin_cost.py) | New module: `has_insulin_cost_data()` (§6 step 3) and `compute_insulin_channel_costs()` (§6 steps 5–6). Deliberately takes only primitive/typed arguments and returns its own dataclass — `estimate_drug_cost.py` imports this module, so this module must never import back from it (circular import). |
| [`tools/disclaimers.py`](../src/medicare_navigator/tools/disclaimers.py) | New `INSULIN_STATUTORY_CAP_CAVEAT`. `INSULIN_OUT_OF_SCOPE_MESSAGE` rewritten to describe the narrow data-gap case instead of "not supported." |
| [`tools/estimate_drug_cost.py`](../src/medicare_navigator/tools/estimate_drug_cost.py) | `_EstimateContext.is_insulin` flag (both `is_insulin()` checks changed from hard-stop-and-return to flag-and-continue). `_compute_channel_costs()` branches to `compute_insulin_channel_costs()`. `_build_caveats()` swaps in the insulin caveat. New `_display_phase()` helper produces `"insulin_cap"` / `"catastrophic"` for the user-facing `benefit_phase` field. `_resolve_tier_metadata()` skips the per-tier `DED_APPLIES_YN` lookup for insulin. |

### Status semantics

`ToolStatus.insulin_out_of_scope` still exists but now means "this plan has no CMS
insulin cost-share record for this drug's tier and fill size" — a narrower, genuine data
gap — rather than "insulin is unsupported." It fires only when §6 step 3's existence
check fails.

### Downstream

`agent/prompts.py` (LLM system prompt scope statement and hard-stop instructions),
`mcp/schemas.py` (tool description), `guardrails/citations.py` (the new caveat added to
`_CARD_ONLY_CAVEATS` so it renders on the estimate card without LLM paraphrasing, same
treatment as the deductible-phase caveat), and `frontend/src/app.js` (About-modal copy,
`BENEFIT_PHASE_LABELS["insulin_cap"]`, `ROUTINE_CAVEAT_TEXTS` styling) were all updated
to match. No MCP tool schema / parameter changes were needed — insulin flows through the
exact same `estimate_drug_cost` / `estimate_drug_cost_all_channels` tool contract as any
other drug.

## 8. Worked examples

Real CMS data (from the currently-ingested SPUF release), except the catastrophic-phase
example, which is a phase transition rather than a static published value:

| Scenario | Detail |
|---|---|
| Current live PDP spot-check | Plan `S5884-198` in AR, Tier 3, 30-day: the insulin file shows $35.00 for both standard mail and preferred retail. The current-quarter replacement keeps the live golden anchors on an active plan. |
| Copay-type tier, already under the cap | The fixture plan `S9999-001`, Tier 3, preferred mail, has a $30.00 insulin cost below $35. The cap is a ceiling, not a fixed price. |
| Coinsurance-type tier, capped to a flat dollar amount | The live AR plan `S5884-198`, Tier 3, has a flat $35.00 insulin cost. Without the cap, an expensive insulin at this tier could cost hundreds of dollars. |
| High copay tier, capped down | Plan `H0028-014`, Tier 3, 30-day, non-preferred: general file's real (uncapped) copay is $47.00 — insulin file shows $35.00. |
| 90-day fill, scaled cap | Same plan/tier, 90-day: general file $141.00 (uncapped) — insulin file $105.00 (exactly 3 × $35). |
| Catastrophic override | Once YTD OOP crosses the annual cap, the same insulin/plan/tier estimate is $0, `benefit_phase` reads `"catastrophic"` instead of `"insulin_cap"`. |
| Not-offered channel | A channel blank in the CMS file stays unpriced (NA) in every phase, including catastrophic — never fabricated as $0. |
| No cost-share data for this plan | An insulin drug on a plan's formulary with no matching `insulin_beneficiary_cost` row returns the narrow `insulin_out_of_scope` message, not a silent fallback to the (wrong) general pricing path. |
| Defined-standard plan | CMS's `"."` tier sentinel (767 real rows) parses to `NULL`; the repository falls back to a `TIER IS NULL` lookup. |
| GLP-1/insulin combination product | Soliqua / Xultophy are billed as a single insulin product under the same cap (§3, secondary source) — no combo-splitting logic needed. |

## 9. Testing and verification performed

- **Unit**: [`tests/test_insulin.py`](../tests/test_insulin.py) — `is_insulin()` allowlist, including the Lyumjev/Soliqua/Xultophy fix.
- **Integration**: [`tests/test_estimate_drug_cost.py`](../tests/test_estimate_drug_cost.py) — real capped estimate, 60/90-day scaling, catastrophic $0 override, `ded_applies_yn == "NA"`, the narrow no-data fallback.
- **Ingest**: [`tests/test_spuf_ingest.py`](../tests/test_spuf_ingest.py) — a dedicated regression test for the hint-collision bug (§7), asserting the general and insulin tables never cross-contaminate.
- **Agent / MCP**: [`tests/test_navigator.py`](../tests/test_navigator.py), [`tests/test_mcp_registry.py`](../tests/test_mcp_registry.py).
- **Golden case**: [`.cursor/skills/tests/utils/numeric-accuracy/golden-cases.jsonl`](../.cursor/skills/tests/utils/numeric-accuracy/golden-cases.jsonl) `golden-037` (new `insulin_cap` case group), verified via `scripts/run_golden_cases.py` against the deterministic `/api/estimate` oracle.
- **Real-data spot-check**: the actual ingest code (not the test fixture) was run against the real cached CMS zip (`data/raw/SPUF_2026_20260408.zip`), scoped to Wyoming for speed, into an isolated scratch database. Result: 408 real insulin cost-share rows ingested; **zero** exceeded the statutory cap for their fill size; the general `beneficiary_cost` table showed no contamination (`coverage_level` values `{0, 1, 3}` only, zero `NULL`s) — confirming the hint-collision fix holds against real CMS data, not only the synthetic test fixture.
- Full project test suite: 275 passed, 0 failed, at the time this work was completed.

## 10. Known limitations and follow-ups

- **§5's field resolution is a point-in-time empirical finding**, not a documented CMS
  guarantee. Re-run the same cross-validation after the next real quarterly ingest
  before trusting new-quarter numbers unquestioned. This is why `golden-037` is scoped
  `requires_live_ingest: false` — a `requires_live_ingest: true` case should only be
  added once someone manually re-verifies a real ingest's numbers.
- **Combination-product cap treatment (Soliqua/Xultophy) is confirmed via a
  manufacturer savings-program page (§3), not CMS's own primary regulatory text.**
  Directionally very likely correct, but CMS's own Part D insulin FAQ PDF — which would
  be the primary source — returned HTTP 403 to every fetch attempt during this work and
  was never directly read.
- **`COIN_AMT_*_INSLN` is discarded at ingest, not stored.** Deliberate, given §5's
  reliability finding, but a one-way door — revisit if a future need arises to
  audit/display it (e.g. a disputed-charge investigation).
- **Segment-ID collapsing**: the new table follows the existing project-wide convention
  of keying on `plan_key` (`CONTRACT_ID-PLAN_ID`) without `SEGMENT_ID` — inherited from
  `beneficiary_cost`/`pricing`, not a new limitation introduced here. 1,850 real
  `(contract, plan, tier, days_supply)` combinations in the current release have
  multiple distinct `SEGMENT_ID` values.
- **Low-Income Subsidy (LIS/"Extra Help") beneficiaries** have a different, lower
  insulin cap per secondary sources. Out of scope here because the app doesn't support
  LIS beneficiaries at all yet — an existing, unrelated limitation, not something this
  work silently handles.
- **`navigator-implementation-spec.md` §1/§6 and `business-solution.md` §7.1** still
  describe insulin as unshipped/out-of-scope. Both are candidates for a follow-up edit
  (e.g. marking §7.1 "SHIPPED", matching the existing pattern used for §7.2's
  catastrophic-phase entry) — not done as part of this document.
