# Data Sources

Exact access methods for all datasets used in Phase 1. URLs should be re-verified at ingestion time — government portals are periodically reorganized.

**Tabular data** is loaded into `data/navigator.duckdb`. **Policy corpus** is embedded in `data/chroma/`.

---

## 1. Drug name normalization

| Field | Value |
|---|---|
| **Source** | RxNorm REST API (NLM) |
| **Docs** | https://lhncbc.nlm.nih.gov/RxNav/APIs/RxNormAPIs.html |
| **Base URL** | `https://rxnav.nlm.nih.gov/REST/` |
| **Key endpoints** | `rxcui.json?name={name}&search=2` (exact/normalized), `approximateTerm.json?term={term}` (fuzzy), `drugs.json?name={name}`, `rxcui/{rxcui}/ndcs.json` |
| **Format** | JSON API |
| **Refresh** | Continuous (on-demand + local DuckDB cache table) |
| **Auth** | None required |
| **Used by** | `normalize_drug` tool, Intake agent |

---

## 2. Part D formulary, cost-share, and plan data

| Field | Value |
|---|---|
| **Source** | CMS Prescription Drug Plan Formulary, Pharmacy Network, and Pricing Information Files (PUF/SPUF) |
| **Order page** | https://www.cms.gov/Research-Statistics-Data-and-Systems/Files-for-Order/NonIdentifiableDataFiles/PrescriptionDrugPlanFormularyPharmacyNetworkandPricingInformationFiles |
| **Data portal** | https://data.cms.gov/provider-summary-by-type-of-service/medicare-part-d-prescribers/quarterly-prescription-drug-plan-formulary-pharmacy-network-and-pricing-information |
| **Naming** | `SPUF.YYYY.YYYYMMDD.zip` (quarterly, includes pricing); monthly PUF also available |
| **Key files** | `plan information`, `basic drugs formulary`, `beneficiary cost`, `pricing` (quarterly only) |
| **Methodology** | https://www.cms.gov/files/document/methodology-spuf-2025.pdf |
| **Format** | ZIP containing tab-delimited text files |
| **Refresh** | Monthly (formulary/network); quarterly (pricing) |
| **Phase 1** | Download latest quarterly zip; filter to `config/demo_plans.yaml` allowlist; load into DuckDB |
| **Used by** | `formulary_benefit_lookup` tool |

---

## 3. Part D standard benefit parameters

| Field | Value |
|---|---|
| **Source** | CMS annual Part D redesign program instructions |
| **Reference** | https://www.cms.gov/newsroom/fact-sheets/final-cy-2026-part-d-redesign-program-instructions |
| **Format** | Published reference values → `config/benefit_params.yaml` per contract year |
| **2026 values (example)** | Deductible $615; OOP cap $2,100; 25% initial coverage coinsurance |
| **Refresh** | Annual |
| **Used by** | `formulary_benefit_lookup` tool (benefit-phase math) |

---

## 4. Medicare drug spending / cost trends

| Field | Value |
|---|---|
| **Source** | CMS Medicare Part D drug spending datasets |
| **Portal** | https://data.cms.gov |
| **Format** | Structured/tabular bulk CSV files |
| **Refresh** | Periodic (roughly annual/quarterly) |
| **Phase 1** | Bulk CSV → DuckDB, keyed by drug identifier (RxCUI or program drug name) |
| **Used by** | `cost_trend_lookup` tool |

---

## 5. Therapeutic equivalence (alternatives)

| Field | Value |
|---|---|
| **Source** | FDA Orange Book |
| **URL** | https://www.fda.gov/drugs/drug-approvals-and-databases/approved-drug-products-therapeutic-equivalence-evaluations-orange-book |
| **Format** | Periodic bulk download (compressed data files) |
| **Refresh** | Periodic |
| **Phase 1** | Equivalence code → alternatives lookup table in DuckDB |
| **Used by** | `alternatives_finder` tool |

---

## 6. NADAC (pharmacy acquisition cost benchmark)

| Field | Value |
|---|---|
| **Source** | CMS NADAC files |
| **Portal** | https://data.medicaid.gov/ (NADAC dataset) |
| **Format** | Structured/tabular bulk files |
| **Refresh** | Weekly |
| **Phase 1** | Reference table in DuckDB |
| **Used by** | Synthesis agent context (acquisition cost explanations) |

---

## 7. Policy / explanation corpus

| Field | Value |
|---|---|
| **Sources** | CMS Part D redesign fact sheets, PUF/SPUF methodology PDFs, IRA Medicare Drug Price Negotiation program docs, Medicare.gov cost explainer pages |
| **Format** | PDF/HTML → chunked text |
| **Storage** | Chroma vector store under `data/chroma/` |
| **Refresh** | As CMS publishes updates |
| **Used by** | `policy_retrieval` tool, Policy agent |

---

## 8. Program-level negotiated prices (IRA selected drugs)

| Field | Value |
|---|---|
| **Source** | CMS selected-drug / Maximum Fair Price (MFP) publications |
| **Portal** | https://www.cms.gov/medicare/medicare-drug-price-negotiation |
| **Format** | Published reference lists |
| **Refresh** | Annual, per negotiation cycle |
| **Phase 1** | Annual reference table in DuckDB |
| **Used by** | Policy agent, Synthesis agent (FR4 cost-change explanations) |

---

## 9. Data manifest

Ingestion jobs write `data/manifest.json` recording:

```json
{
  "spuf": {"version": "SPUF.2026.20260115", "as_of": "2026-01-15"},
  "spending": {"as_of": "2025-12-01"},
  "orange_book": {"as_of": "2026-01-01"},
  "nadac": {"as_of": "2026-01-10"},
  "benefit_params": {"contract_year": 2026},
  "policy_corpus": {"as_of": "2026-01-01"}
}
```

Cache TTLs and UI "Data as of" badges read from this manifest.
