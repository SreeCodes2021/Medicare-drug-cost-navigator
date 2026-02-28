# Build Requirements: Medicare Drug Cost & Benefit-Transparency Navigator

**Audience:** this document is written to be handed directly to a build agent (human or AI) as the primary specification. Where a specific technology, model, or hosting choice is not mandated, that is intentional — capabilities and constraints are specified; implementation choices are left to the builder. See Section 12 for the explicit list of deferred decisions.

---

## 1. Overview

Build a system that, given a drug (and dosage) and, optionally, a specific Medicare Part D / Medicare Advantage (MA) plan, explains what a beneficiary is charged for that drug under that plan and why — grounded entirely in publicly available government and regulatory data. The system must combine deterministic data lookups with language-model-driven explanation, and must never state a claim it cannot trace back to a specific retrieved source.

---

## 2. Scope

### 2.1 In Scope
- Formulary tier and cost-share lookup for a given drug + plan.
- Benefit-phase determination (deductible / initial coverage / annual out-of-pocket cap).
- Multi-year cost/spending trend for a given drug.
- Identification of therapeutically equivalent alternatives.
- Plain-language, citation-grounded explanation synthesizing the above.

### 2.2 Out of Scope / Non-Goals
- Must **not** recommend switching Medicare plans under any circumstance.
- Must **not** provide medical advice, diagnosis, or treatment recommendations.
- Must **not** claim real-time, point-of-sale pricing accuracy.
- Must **not** ingest, store, or process any individual's protected health information (PHI).
- Must **not** replace a financial advisor, licensed insurance agent, or benefits counselor — informational support only.

---

## 3. Functional Requirements

| ID | Requirement |
|---|---|
| FR1 | Given a drug + dosage + (optional) plan identifier, the system MUST return the current formulary tier and cost-sharing amount for that plan. |
| FR2 | Given a drug, the system MUST return its historical spending or price trend across available years. |
| FR3 | Given a drug, the system MUST surface therapeutically equivalent alternatives where they exist. |
| FR4 | The system MUST explain cost changes in plain language (e.g., price change, formulary tier change, benefit-phase transition, program-level negotiated-price effective date). |
| FR5 | The system MUST display an explicit "data as of [date]" indicator on every returned figure. |
| FR6 | The system MUST NOT recommend switching plans — it explains the current plan only. |
| FR7 | Every generated explanation MUST cite the specific source dataset/document it draws from; the system MUST NOT generate claims unsupported by retrieved data. |
| FR8 | Every deterministic lookup MUST return an explicit, structured failure state (e.g., not-found, not-covered, stale) rather than a silent empty result, so downstream language-model components can report a failure honestly instead of generating a plausible-sounding but unsupported answer. See Section 5.5. |

---

## 4. Data Requirements

All data must be (a) 100% publicly available, (b) free of PHI, (c) traceable to an official government or regulatory source. Exact file paths/endpoints should be verified at build time, as government data portals are periodically reorganized.

| Dataset | Purpose | Expected format | Refresh cadence |
|---|---|---|---|
| Medicare drug spending data (program-level, by drug) | Multi-year cost/spend trend | Structured/tabular bulk files | Periodic (roughly annual/quarterly) |
| Part D formulary, pharmacy network & pricing data (plan-level) | Tier and cost-share lookup per drug per plan | Structured/tabular bulk files | Monthly/quarterly |
| Part D standard benefit parameters (annual) | Deductible, initial coverage limit, catastrophic threshold, annual out-of-pocket cap | Structured/tabular or published reference values | Annual |
| Drug label / therapeutic equivalence data | Generic/brand mapping, equivalence classing | API or bulk download | Continuous |
| National average drug acquisition cost benchmark | Pharmacy-level acquisition cost reference | Structured/tabular bulk files | Weekly |
| Drug name normalization / identifier reference | Free-text drug name → standardized identifier mapping | API or reference table | Continuous |
| Program-level negotiated-price list (where applicable) | Explains negotiated-price effects on cost | Published reference list | Annual, per cycle |

---

## 5. System Architecture Requirements

### 5.1 Terminology (required, not optional — use precisely throughout the build)
- **Tool** — a deterministic, stateless function. Same input always produces the same output. No language model involved, no judgment exercised.
- **Agent** — a language-model-driven component that interprets, reasons, or exercises judgment, and may invoke one or more tools itself.

Do not label a deterministic lookup an "agent." Do not have an "agent" perform a deterministic lookup without going through a tool interface.

### 5.2 Required Tools (deterministic, no language model)
- Drug-name normalization lookup (raw text → standardized identifier).
- Formulary & benefit-phase lookup (identifier + plan → tier, cost-share, phase).
- Cost-trend lookup (identifier → multi-year trend).
- Alternative-finder / equivalence matching (identifier or drug class → equivalents).
- Retrieval over the explanatory/policy text corpus (query → relevant passages) — implemented as a tool callable by an agent, not as a standalone agent itself.

Each tool MUST implement the failure contract in Section 5.5.

### 5.3 Required Agents (language-model-driven, may invoke tools)
- **Intake/parsing agent** — normalizes free-text input into a structured query; may call the normalization tool.
- **Policy/explanation agent** — retrieves and interprets relevant policy/program text; may call the retrieval tool.
- **Synthesis agent** — combines all upstream tool and agent outputs into one final, plain-language answer. This agent MUST refuse to state anything not traceable to a specific upstream output, and MUST attach a citation to every factual claim it produces.

### 5.4 Orchestration Requirements
- An orchestration layer MUST sequence calls: dispatching directly to tools for pure data retrieval, and to agents wherever interpretation or synthesis is required.
- The orchestration layer MUST support conditional routing (e.g., skip the alternative-finder tool if the query doesn't require it) and MUST support retries on transient failures.
- The orchestration layer MUST log which tools/agents were invoked per query, for evaluation and debugging purposes.

### 5.5 Tool Failure Contract
Every tool MUST return one of the following structured statuses on failure, rather than a silent empty or null result:

| Status | Meaning | Required downstream behavior |
|---|---|---|
| `not_found` | Input could not be matched (e.g., unrecognized drug name or plan identifier) | Ask the user to confirm the input; do not guess |
| `not_covered` | The item exists but is not present in the relevant plan/dataset | State this explicitly as a valid answer, not an error |
| `stale` | Requested data is not yet available for the current period | Surface the most recent available period and label it as such |
| `no_match` | No result found in the relevant corpus (e.g., no equivalent drug, no relevant passage) | State plainly that none were found; do not fabricate one |

---

## 6. Non-Functional Requirements

- **Citation-groundedness:** the system MUST support automated evaluation of what fraction of generated claims are traceable to a specific retrieved/looked-up source. A minimum acceptable threshold must be defined and measured before the system is considered complete (see Section 9).
- **Caching:** the system MUST cache repeated language-model calls and repeated retrieval results, with a cache lifetime aligned to the underlying data's refresh cadence (Section 4). Cached results MUST NOT be served past the point where the underlying data has been refreshed.
- **Data freshness display:** every user-facing figure MUST show the period/date of the underlying data it was computed from.
- **Latency:** response times must be reasonable for an interactive query/response use case; this is not a real-time trading-grade or emergency-response system, and no sub-second guarantee is required.
- **Availability:** demo/pilot-grade availability is sufficient; production-grade SLA guarantees are not required at this stage.

---

## 7. Interface Requirements

### 7.1 Backend / API
- MUST expose a query interface accepting a drug (+ dosage) and an optional plan identifier, returning a structured response containing: tier/cost-share, benefit phase, cost trend, alternatives (if applicable), and the synthesized explanation with citations.
- MUST expose a way to retrieve the "as of" date for the underlying data used in any given response.
- Specific API framework/technology is left to the implementer.

### 7.2 Frontend / User Interface
- MUST provide a way for a user to submit a query (drug, dosage, optional plan) and view the resulting explanation with visible, clickable/expandable citations.
- MUST display a persistent, visible disclaimer: informational only, not medical or financial advice — confirm with a doctor, pharmacist, or plan.
- MUST display the "as of" data-freshness indicator prominently, not buried in fine print.
- Specific frontend framework/technology is left to the implementer.

---

## 8. Regulatory & Compliance Requirements

- No PHI ingestion, storage, or processing — public/aggregate data only.
- MUST NOT function as, or resemble, Medicare plan marketing, enrollment, or plan-switching advice.
- MUST carry the disclaimer specified in Section 7.2 on every user-facing response.
- MUST NOT persist any user-submitted identifying information beyond the active session, if deployed for live use.

---

## 9. Evaluation & Acceptance Criteria

The build is considered complete only when all of the following are demonstrated, not merely implemented:

- [ ] A curated evaluation set of drug/plan/query combinations exists, including deliberately unmatched/edge cases (misspelled drugs, unknown plan IDs, drugs absent from a formulary).
- [ ] Citation-groundedness rate on the evaluation set meets or exceeds a defined threshold, measured against a defined baseline (e.g., the same synthesis step without retrieval).
- [ ] Every failure case in the evaluation set produces the correct structured status (Section 5.5) rather than a fabricated or empty answer.
- [ ] Formulary tier/cost-share lookups match ground truth in the underlying data for 100% of the evaluation set's well-formed queries (this is a deterministic lookup — it should not have a tolerance band).
- [ ] A retrospective check exists: the system correctly explains at least a handful of real, independently documented drug cost-change events.
- [ ] All disclaimers and data-freshness indicators are visibly present in the delivered interface.

---

## 10. Deployment & Portability Requirements

- The system MUST be packaged in a way that is portable across hosting environments — no hard dependency on a specific cloud provider or hosting platform.
- The system MUST support environment-based configuration (e.g., API keys, data-refresh schedules) rather than hard-coded values.
- Specific hosting platform, container orchestration, and CI/CD tooling are left to the implementer.

---

## 11. Deliverables Checklist

- [ ] Source code for all tools and agents described in Section 5.
- [ ] Ingestion scripts/jobs for all datasets in Section 4.
- [ ] A backend service satisfying Section 7.1.
- [ ] A frontend interface satisfying Section 7.2.
- [ ] The evaluation set and evaluation results described in Section 9.
- [ ] A short README describing setup, configuration, and how to re-run ingestion and evaluation.
- [ ] A list of every data source's exact access method (URL/API/file path) as actually used, since exact paths were not hard-verified at spec time.

---

## 12. Assumptions & Open Questions (deliberately deferred to the implementer)

- **Language model choice:** any model capable of structured/schema-constrained output and of refusing to assert unsupported claims is acceptable. No specific provider or model is mandated.
- **Vector/retrieval store choice:** any store supporting semantic similarity search over text is acceptable.
- **Structured data store choice:** any relational or tabular data store capable of the lookups in Section 5.2 is acceptable.
- **Caching layer choice:** any key-value or similar caching mechanism satisfying Section 6's cache-lifetime requirement is acceptable.
- **Hosting/deployment platform:** left fully open per Section 10.
- **Orchestration mechanism:** any framework or hand-rolled control flow satisfying Section 5.4 is acceptable.
- **Scope of initial plan coverage:** whether the first build covers a full national set of plans or a representative demo subset is left open — flag this decision explicitly rather than assuming.
