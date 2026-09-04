# Quality test backlog

Tracked findings and suggested test additions from `/quality-test` runs.
Do not delete historical entries — mark resolved inline.

## Enhancements

- [ENHANCEMENT] pharmacy-lookup — Geocode NPPES pharmacy street addresses (or use registry lat/lon when present) and compute straight-line distance from the user's ZIP centroid to each pharmacy location, instead of ZIP-centroid-to-ZIP-centroid only. Same-ZIP results would sort by real proximity; different addresses within one ZIP would no longer all read as 0.0 mi internally. **Partial mitigation shipped 2026-08-21:** chat prose omits distance when `distance_miles == 0` and states the fixed search radius in the results header.

## Open fixes

- [RISK, non-QA-run, code review 2026-08-21] pharmacy-lookup — Query-time NPPES enrichment (`PharmacyRepository.enrich_stub_records` in `storage/repository.py` → `npi_enrichment.enrich_npis`) runs a synchronous `httpx.Client` call inside `find_pharmacies`, which `mcp/registry.py`'s `async def call_tool` invokes unawaited. Any ZIP-only stub pharmacy surfaced in a `find_pharmacies` result (including via `resolve_plan_pharmacy_match_question`'s Q4 loop, up to `MAX_COVERED_PLANS_FOR_PHARMACY_CHECK`=10 calls per query) triggers a live, blocking NPPES call that stalls the event loop for up to a 10s timeout per NPI, serially. Fix: make the query-time enrichment path async (`httpx.AsyncClient`, matching `tools/normalize_drug.py`) or drop query-time enrichment and rely on ingest-time-only enrichment (re-ingest to backfill stragglers).
- [DATA GAP, non-code, still open, code review 2026-08-21] pharmacy-lookup — Real CMS Pharmacy Network PPUF file column layout is unconfirmed (`ingestion/spuf.py`). The `PHARMACY_NUMBER` → NPI decode in `npi_enrichment.decode_cms_pharmacy_number` is also a guess. If the real file diverges, ingestion won't error — it'll silently under-match, producing more ZIP-only stub pharmacies than expected. Needs validation against an actual downloaded CMS PPUF pharmacy-network file, or at minimum an ingest-time warning when the match rate comes back suspiciously low.

## Resolved (pharmacy-lookup)

- ~~[REVISE] pharmacy-lookup/B8-1 — `"I live in 72712. What are the pharmacies available in my zip?"` fell through to LLM instead of `System/NearbyPharmacy`~~ **resolved 2026-08-21**
- ~~[REVISE] pharmacy-lookup/B8-2 — plan-list ask returned `needs_clarification` via LLM~~ **resolved 2026-08-21**
- ~~[REVISE] pharmacy-lookup/B7-2 — 50-mile follow-up hallucination~~ **resolved 2026-08-21**
- ~~[REVISE] pharmacy-lookup — `extract_zip` ignored `"I live in 72719"` without the word `zip`~~ **resolved 2026-08-21**
- ~~[BLOCK] pharmacy-lookup/B7-4 — `"nearby"` did not match `_NEARBY_PHARMACY_RE`~~ **resolved 2026-08-21**
- ~~[BLOCK] pharmacy-lookup/B4-1 — Q3 dropped plan scope and mail/retail channel intent~~ **resolved 2026-08-21**

## Resolved (compound-questions)

First catalog: [compound-questions skill](.cursor/skills/tests/T3/compound-questions/SKILL.md) + `scripts/llm_scenario_suites/compound_questions.json`.

**Fix pass 2026-09-04** — `compound_questions.py` defer guards on all deterministic resolvers, agent-loop enrichment/stitching for dropped halves, CC14 OOP+injection stitch in `invalid_input_questions.py`, and `find_pharmacies` missing-zip hardening in `mcp/registry.py`.

- ~~[BLOCK] CC12 — wrong plan for pharmacy-network half when two plan keys named~~ **resolved 2026-09-04**
- ~~[BLOCK] CC4, CC5, CC6 — Q2 duration-blind single-fill pricing~~ **resolved 2026-09-04**
- ~~[BLOCK] CC-A (CC1, CC2, CC7, CC16, CC19) — OOP resolver dropped companion questions~~ **resolved 2026-09-04**
- ~~[BLOCK] CC-C (CC9, CC15, CC17) — pharmacy resolvers dropped insulin/policy companions~~ **resolved 2026-09-04**
- ~~[BLOCK] CC8 — agent loop dropped lovastatin plan-coverage half~~ **resolved 2026-09-04**
- ~~[REVISE] CC3, CC18 — insulin path dropped remaining-year duration math~~ **resolved 2026-09-04**
- ~~[REVISE] CC14 — injection refusal dropped legitimate OOP half~~ **resolved 2026-09-04**
- ~~[REVISE] CC5, CC6, CC13 follow-up — agent loop multi-month / mail-order gaps~~ **resolved 2026-09-04**

**Re-verified 2026-09-04** — T1–T3 full tier + insulin + mixed-basket + pharmacy-lookup + compound-questions: **0 BLOCK, 0 REVISE** (80 T3 queries + all sub-skill catalogs pass sequentially).

## Resolved (insulin)

- ~~[REVISE] insulin/B3-2 T2 — Follow-up YTD $2200 hedged $0 with channel variance~~ **resolved 2026-08-14**

## Suggested test cases

- **Type:** pytest — Regex robustness sweep for `is_nearby_pharmacy_question` / `is_preferred_pharmacy_question` (e.g. `"closeby"`, `"around here"` contractions). **Home:** `tests/test_pharmacy_questions.py`
- **Type:** mandatory-llm — Re-run pharmacy-lookup Phase B after ingesting pharmacy-network data for additional non-FL states to get true positive-match coverage beyond the FL fixture. **Home:** `.cursor/skills/tests/T3/pharmacy-lookup/SKILL.md` Phase B
