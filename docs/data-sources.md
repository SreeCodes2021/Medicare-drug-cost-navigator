# Data Sources

Exact access methods for datasets used by the Phase 6 Navigator. URLs should be re-verified at ingestion time — government portals are periodically reorganized.

**Tabular data** is loaded into `data/navigator.duckdb`. Sections 4–7 and the Chroma policy corpus below describe **Phase 1–5 sources removed in the Phase 6 pivot** — retained here for roadmap reference only.

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
| **Used by** | `normalize_drug` (internal to `estimate_drug_cost` / `estimate_drug_cost_all_channels`) |

### 1.1 RxNorm offline fallback (`tools/rxnorm_offline.py`)

When live NLM REST calls fail (`httpx.HTTPError`) or return no matches, `normalize_drug` falls back to curated 2026 snapshots for demo/test drugs:

| Field | Value |
|---|---|
| **Trigger** | Live API error or empty match set |
| **Coverage** | Ingredient RXCUIs, strength-specific SCD/SBD concepts, approximate fuzzy match for common demo drugs |
| **Provenance** | Candidates carry `source: "rxnorm_offline"` in the normalization result |
| **Purpose** | Offline tests, degraded-network operation, and local development without changing the cost-pipeline contract |
| **Not a substitute** | Production drug resolution still prefers live RxNorm; offline rows cover only the curated allowlist |

---

## 2. Part D formulary, cost-share, and plan data

| Field | Value |
|---|---|
| **Source** | CMS Prescription Drug Plan Formulary, Pharmacy Network, and Pricing Information Files (PUF/SPUF) |
| **Order page** | https://www.cms.gov/Research-Statistics-Data-and-Systems/Files-for-Order/NonIdentifiableDataFiles/PrescriptionDrugPlanFormularyPharmacyNetworkandPricingInformationFiles |
| **Data portal** | https://data.cms.gov/provider-summary-by-type-of-service/medicare-part-d-prescribers/quarterly-prescription-drug-plan-formulary-pharmacy-network-and-pricing-information |
| **Naming** | `SPUF.YYYY.YYYYMMDD.zip` (quarterly, includes pricing); monthly PUF also available |
| **Key files** | `plan information`, `basic drugs formulary`, `beneficiary cost`, `insulin beneficiary cost`, `pricing` (quarterly only) |
| **Methodology** | https://www.cms.gov/files/document/methodology-spuf-2025.pdf |
| **Format** | ZIP containing tab-delimited text files |
| **Refresh** | Monthly (formulary/network); quarterly (pricing) |
| **Phase 1** | Download latest quarterly zip; filter to `config/ingest_filters.yaml` states (currently AR + TX); load into DuckDB |
| **Used by** | `estimate_drug_cost`, `estimate_drug_cost_all_channels`, `lookup_plan`, `list_plans` |

---

## 3. Part D standard benefit parameters

| Field | Value |
|---|---|
| **Source** | CMS annual Part D redesign program instructions |
| **Reference** | https://www.cms.gov/newsroom/fact-sheets/final-cy-2026-part-d-redesign-program-instructions |
| **Format** | Published reference values → `config/benefit_params.yaml` per contract year |
| **2026 values (example)** | Deductible $615; OOP cap $2,100; 25% initial coverage coinsurance |
| **Refresh** | Annual |
| **Used by** | Benefit-phase math in `estimate_drug_cost` (`tools/part_d_benefit_params.py`) |

---

## 4. Medicare drug spending / cost trends *(Phase 8 — not loaded in v1)*

| Field | Value |
|---|---|
| **Source** | CMS Medicare Part D drug spending datasets |
| **Portal** | https://data.cms.gov |
| **Format** | Structured/tabular bulk CSV files |
| **Refresh** | Periodic (roughly annual/quarterly) |
| **Phase 1** | Bulk CSV → DuckDB, keyed by drug identifier (RxCUI or program drug name) |
| **Used by** | `cost_trend_lookup` tool |

---

## 5. Therapeutic equivalence (alternatives) *(Phase 8 — not loaded in v1)*

| Field | Value |
|---|---|
| **Source** | FDA Orange Book |
| **URL** | https://www.fda.gov/drugs/drug-approvals-and-databases/approved-drug-products-therapeutic-equivalence-evaluations-orange-book |
| **Format** | Periodic bulk download (compressed data files) |
| **Refresh** | Periodic |
| **Phase 1** | Equivalence code → alternatives lookup table in DuckDB |
| **Used by** | `alternatives_finder` tool |

---

## 6. NADAC (pharmacy acquisition cost benchmark) *(Phase 10 — not loaded in v1)*

| Field | Value |
|---|---|
| **Source** | CMS NADAC files |
| **Portal** | https://data.medicaid.gov/ (NADAC dataset) |
| **Format** | Structured/tabular bulk files |
| **Refresh** | Weekly |
| **Phase 1** | Reference table in DuckDB |
| **Used by** | Synthesis agent context (acquisition cost explanations) |

---

## 7. Policy / explanation corpus *(Phase 8 — Chroma removed in Phase 6 pivot)*

| Field | Value |
|---|---|
| **Sources** | CMS Part D redesign fact sheets, PUF/SPUF methodology PDFs, IRA Medicare Drug Price Negotiation program docs, Medicare.gov cost explainer pages |
| **Format** | PDF/HTML → chunked text |
| **Storage** | Chroma vector store under `data/chroma/` |
| **Refresh** | As CMS publishes updates |
| **Used by** | `policy_retrieval` tool, Policy agent |

---

## 8. Program-level negotiated prices (IRA selected drugs) *(Phase 10 — not loaded in v1)*

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
  "benefit_params": {"contract_year": 2026}
}
```

v1 ingest writes `spuf` and `benefit_params` entries. Legacy Phase 1–5 keys (`spending`, `orange_book`, `nadac`, `policy_corpus`) appear only if those pipelines are restored.

Example with all planned sources:

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
