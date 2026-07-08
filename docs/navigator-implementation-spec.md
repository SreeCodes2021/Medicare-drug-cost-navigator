# Medicare Part D Drug Cost Navigator — Implementation Spec (v1, trimmed scope)

## 1. Scope

This version handles: a single standard-tier, orally-administered generic or brand
drug, on a plan's regular formulary, for a beneficiary receiving no low-income
subsidy (LIS), in either the pre-deductible or initial-coverage benefit phase.

Explicitly out of scope for v1 (see Section 6 — Future Work):
- Insulin (separate statutory cap, separate file, no phase logic)
- Excluded-drug formulary entries (supplemental/enhanced-plan-only coverage)
- Indication-based coverage restrictions
- Catastrophic-phase computation
- Automatic phase detection from YTD spend

## 2. Data sources (CMS SPUF, quarterly)

| File | Used for |
|---|---|
| `plan_information` | `FORMULARY_ID`, `DEDUCTIBLE`, `PLAN_SUPPRESSED_YN` |
| `basic_drugs_formulary` | `NDC`, `TIER_LEVEL_VALUE`, `QUANTITY_LIMIT_*`, `PRIOR_AUTHORIZATION_YN`, `STEP_THERAPY_YN` |
| `pricing` | `UNIT_COST` by NDC + days supply |
| `beneficiary_cost` | `COST_TYPE_*`, `COST_AMT_*`, `DED_APPLIES_YN`, `COVERAGE_LEVEL` |

## 3. Pipeline

```
1. Resolve plan       plan_information[CONTRACT_ID, PLAN_ID]
                       -> FORMULARY_ID, DEDUCTIBLE, PLAN_SUPPRESSED_YN
                       -> if PLAN_SUPPRESSED_YN == 'Y': STOP, return suppressed-data warning

2. Resolve drug        drug name + strength -> RXCUI (RxNorm)
                       -> if drug is insulin: STOP, route to future-scope message

3. Formulary lookup    basic_drugs_formulary[FORMULARY_ID, RXCUI]
                       -> may return MULTIPLE NDC rows (see Section 5, Bug 5)
                       -> check QUANTITY_LIMIT_* against requested days supply
                          (see Section 5, Bug 5b)
                       -> if PRIOR_AUTHORIZATION_YN == 'Y' or STEP_THERAPY_YN == 'Y':
                          surface as a hard caveat, do not silently compute cost

4. Days-supply mapping  see Section 4 — required before any join on days supply

5. Pricing lookup       Runs ONLY when step 6 (evaluated first, below)
                        resolves the matched TIER to the pre-deductible
                        phase — during the deductible, the beneficiary pays
                        the plan's full negotiated price, not a copay.
                        Steps 5 and 7 are mutually exclusive per matched
                        TIER: never both, never summed.
                        pricing[CONTRACT_ID, PLAN_ID, NDC, DAYS_SUPPLY]
                       -> UNIT_COST
                       -> apply per-unit -> per-fill conversion (see Section 5, Bug 3)
                       -> if multiple NDCs matched in step 3: compute a cost
                          for EACH NDC, then report low-high range (Bug 5)

6. Phase determination  Evaluate this BEFORE step 5 or step 7 — its result
                        (per matched TIER) decides which of them runs.
                        compare YTD spend to plan_information.DEDUCTIBLE
                       -> phase = 0 (pre-deductible) or 1 (initial coverage)
                       -> per-tier override: check DED_APPLIES_YN for the
                          matched TIER before trusting the YTD-vs-deductible
                          comparison (see Section 5, Bug 2) — an exempt tier
                          routes straight to step 7's copay even while the
                          beneficiary is pre-deductible overall

7. Cost-share lookup    Runs ONLY when step 6 resolves the matched TIER to
                        the initial-coverage phase (deductible met, or the
                        tier is exempt) — the plan's copay/coinsurance
                        applies instead of step 5's full price.
                        beneficiary_cost[CONTRACT_ID, PLAN_ID, TIER,
                        COVERAGE_LEVEL, DAYS_SUPPLY]
                       -> COST_TYPE_PREF / COST_AMT_PREF (and NONPREF, MAIL_*)
                       -> if COST_TYPE_PREF == 2 (coinsurance):
                          # COINSURANCE NOT CALCULATED — CONTACT INSURER.
                          # Base amount coinsurance applies to is not confirmed
                          # from CMS layout; do not compute a dollar figure.
                          return copay-only estimate + explicit disclaimer

8. Output               budget estimate (range if multiple NDCs), all
                        applicable caveats attached (QL, PA, ST, suppressed
                        data, coinsurance-not-computed)
```

## 4. Days-supply code mapping (required lookup table)

`pricing.DAYS_SUPPLY` and `beneficiary_cost.DAYS_SUPPLY` are **not the same
representation**. This is not a bug — it's how CMS defined the two files — but
it requires an explicit mapping layer before any join. Do not join directly on
the raw field.

```
DAYS_SUPPLY_CODE_MAP = {
    30: 1,   # pricing "30" -> beneficiary_cost code 1
    60: 4,   # pricing "60" -> beneficiary_cost code 4
    90: 2,   # pricing "90" -> beneficiary_cost code 2
    # code 3 ("other") has no direct pricing equivalent —
    # only reachable if a plan's DAYS_SUPPLY in pricing is some
    # non-30/60/90 value; handle as an explicit "other" branch,
    # do not silently coerce it to 30/60/90.
}
```

Implement this as a single named lookup (e.g. in the skill's shared logic),
not as an inline conditional repeated at each join site, so a future
correction only needs to change one place.

## 5. Known issues and how v1 handles them

### Bug 1 — Days-supply code mismatch
Not a bug — a required translation layer. Handled by Section 4's mapping
table. Any join between `pricing` and `beneficiary_cost` must pass through
this map first.

### Bug 2 — Deductible phase is not a single global gate
`DED_APPLIES_YN` is per-tier. A beneficiary can be pre-deductible overall
(YTD < plan deductible) while a specific tier (commonly Tier 1 generics) is
still charged at the initial-coverage cost-share, because the plan exempts
that tier from the deductible. **Disclaimer, not a fix:**

> This estimate assumes the deductible-phase determination is based on your
> reported YTD spend and this plan's per-tier deductible rule as published by
> CMS. Some plans exempt certain tiers from the deductible; if your actual
> pharmacy charge differs from this estimate, your plan's tier-specific
> deductible treatment is the most likely reason. Confirm with your plan.

### Bug 3 — Unit cost vs. fill cost
`pricing.UNIT_COST` is documented as "average unit cost (e.g. per pill)."
v1 treats this as requiring a round-up multiplication to the actual fill
quantity rather than assuming it is already a per-fill total:

```
fill_quantity = ceil(days_supply / days_per_dose_unit)   # e.g. 90 days / 1 tab/day = 90
estimated_drug_cost = UNIT_COST * fill_quantity
```

Round up (`ceil`), not down or nearest, on any quantity derived from days
supply — under-estimating a fill quantity is worse than over-estimating by
a fraction of a unit, since the actual pharmacy fill will never dispense a
partial unit. This rounding rule applies anywhere days-supply is converted
to a discrete quantity (fill quantity, and quantity-limit comparisons in
Bug 5b).

### Bug 4 — Coinsurance base is not confirmed
Not computed in v1. When `COST_TYPE_PREF` (or NONPREF/MAIL_*) equals 2
(coinsurance), the output must include, verbatim:

```
# COINSURANCE NOT CALCULATED — CONTACT INSURER.
# CMS record layout does not confirm the dollar base against which the
# published coinsurance percentage is applied. Computing a dollar figure
# here would risk presenting an unverified number as a firm cost estimate.
```

This applies at the code level (a comment at the point of the branch) and
at the output level (a visible caveat to the end user, not just a code
comment) whenever a matched tier's cost-share type is coinsurance.

### Bug 5 — Multiple NDCs per RXCUI
An RXCUI (e.g., "lisinopril 10 MG Oral Tablet") can map to several NDCs in
`basic_drugs_formulary` — different manufacturers, potentially different
tiers or pricing. v1 does not pick one arbitrarily. Instead:

- Compute the estimated fill cost independently for every matched NDC.
- Report **the range**: lowest estimated cost to highest estimated cost
  across all matched NDCs, plus the count of NDCs the range is based on.
- If all matched NDCs land on the same tier, note that explicitly (the
  range is then driven by `pricing.UNIT_COST` variation only, e.g.
  different manufacturers' negotiated prices — not by different
  cost-share rules).
- If matched NDCs span different tiers, state that plainly — this is a
  more significant caveat than a same-tier price spread, since it means
  the beneficiary's actual cost depends on which specific product their
  pharmacy fills.

Example output shape:
```
Estimated cost for a 90-day supply: $8.10 – $14.40
(based on 3 formulary NDCs for this drug, all Tier 1)
```

### Bug 5b — Quantity limits can silently block the requested days supply
Before returning any cost estimate, compare the requested days supply
against `QUANTITY_LIMIT_AMOUNT` / `QUANTITY_LIMIT_DAYS` (from
`basic_drugs_formulary`) when `QUANTITY_LIMIT_YN == 'Y'`. If the requested
supply exceeds what the quantity limit permits in a single fill, do not
compute a cost for the requested duration — state that the plan's quantity
limit does not permit that fill size and give the maximum fill size the
plan does allow instead.

### Bug 6 — Suppressed plan data
Check `PLAN_SUPPRESSED_YN` on `plan_information` before any downstream
lookup. If `'Y'`:

```
# PLAN_SUPPRESSED_YN = 'Y' for this plan/period.
# CMS has suppressed this plan's pharmacy data for data-quality or
# reliability reasons. Do not compute or display a cost estimate from
# this plan's records; direct the user to contact the plan directly.
```

This is a hard stop, not a caveat appended to an otherwise-computed
number — a suppressed-data plan should never reach the pricing or
cost-share steps.

## 6. Future work (deferred, not fixed)

- Insulin cost-share (separate file, separate $35/month cap logic, no
  benefit-phase dependency)
- Excluded-drugs formulary (enhanced/supplemental plan coverage only)
- Indication-based coverage restrictions (requires matching beneficiary
  diagnosis to FDA-approved indication on file)
- Catastrophic-phase computation (requires the annual statutory TrOOP
  threshold, not present in any CMS SPUF file)
- Automatic benefit-phase detection purely from YTD dollar input (v1
  requires the tier-level deductible check in Bug 2 as a manual/explicit
  step, not a fully automated inference)
- Confirmed coinsurance base (once confirmed against an authoritative
  source, replace the Bug 4 disclaimer with an actual computation)

## 7. Disclaimers required in every output (summary)

1. General: this is an estimate based on CMS-published plan data for the
   current quarter, not a guarantee of actual pharmacy charge.
2. Bug 2: deductible-phase / per-tier exemption caveat.
3. Bug 4: coinsurance-not-calculated notice, whenever applicable.
4. Bug 5: NDC-range caveat, whenever more than one NDC is matched.
5. Bug 5b: quantity-limit notice, whenever the requested supply exceeds
   the plan's allowed fill size.
6. Bug 6: suppressed-data hard stop, whenever applicable (replaces all
   other output for that plan).
