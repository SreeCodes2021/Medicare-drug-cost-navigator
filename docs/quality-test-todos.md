# Quality test backlog

Tracked findings and suggested test additions from `/quality-test` runs.
Do not delete historical entries — mark resolved inline.

## Enhancements

- [ENHANCEMENT] pharmacy-lookup — Geocode NPPES pharmacy street addresses (or use registry lat/lon when present) and compute straight-line distance from the user's ZIP centroid to each pharmacy location, instead of ZIP-centroid-to-ZIP-centroid only. Same-ZIP results would sort by real proximity; different addresses within one ZIP would no longer all read as 0.0 mi internally. **Partial mitigation shipped 2026-08-21:** chat prose omits distance when `distance_miles == 0` and states the fixed search radius in the results header.

## Open fixes

- [RISK, non-QA-run, code review 2026-08-21] pharmacy-lookup — Query-time NPPES enrichment (`PharmacyRepository.enrich_stub_records` in `storage/repository.py` → `npi_enrichment.enrich_npis`) runs a synchronous `httpx.Client` call inside `find_pharmacies`, which `mcp/registry.py`'s `async def call_tool` invokes unawaited. `npi_enrichment.py`'s own module docstring claims this path is "ingest-time only... unlike `tools/normalize_drug.py`'s RxNorm calls, which run live per chat query... and use `httpx.AsyncClient`" — but any ZIP-only stub pharmacy surfaced in a `find_pharmacies` result (including via `resolve_plan_pharmacy_match_question`'s Q4 loop, which can call `find_pharmacies` up to `MAX_COVERED_PLANS_FOR_PHARMACY_CHECK`=10 times per query) triggers a live, blocking NPPES call that stalls the whole event loop for up to a 10s timeout per NPI, serially. `test_find_pharmacies_enriches_cms_stub_records` mocks `enrich_stub_records` entirely, so this blocking behavior is unexercised by any test. Fix: make the query-time enrichment path async (`httpx.AsyncClient`, matching `tools/normalize_drug.py`'s pattern) or drop query-time enrichment and rely on ingest-time-only enrichment (re-ingest to backfill stragglers).
- [DATA GAP, non-code, still open, code review 2026-08-21] pharmacy-lookup — Real CMS Pharmacy Network PPUF file column layout is unconfirmed — `ingestion/spuf.py` says so directly: "CMS Pharmacy Network file column names are unconfirmed from memory." The `PHARMACY_NUMBER` → NPI decode (`"10" + 10-digit NPI`) in `npi_enrichment.decode_cms_pharmacy_number` is also a guess. If the real file diverges, ingestion won't error — it'll silently under-match, producing more ZIP-only stub pharmacies than expected in production, which feeds directly into the blocking-I/O risk above (more stubs → more live per-query NPPES calls). Needs validation against an actual downloaded CMS PPUF pharmacy-network file before this goes live, or at minimum an ingest-time warning when the match rate against real column names comes back suspiciously low.

- ~~[REVISE] pharmacy-lookup/B8-1 — `"I live in 72712. What are the pharmacies available in my zip?"` falls through to `openai/gpt-5.6-luna` instead of `System/NearbyPharmacy`~~ **resolved 2026-08-21** — `_NEARBY_PHARMACY_RE` now matches `"pharmacies … in/available in my zip"` phrasing; B8-1 returns `System/NearbyPharmacy`.
- ~~[REVISE] pharmacy-lookup/B8-2 — `"Give me list of plans which has pharmacies in 72712"` returns `needs_clarification` via LLM~~ **resolved 2026-08-21** — `_NEARBY_PHARMACY_RE` + `_PHARMACY_IN_ZIP_DIGITS_RE` extract bare ZIP from `"pharmacies in {5 digits}"`; B8-2 returns `System/NearbyPharmacy` with oracle rows.

- ~~[REVISE] pharmacy-lookup/B7-2 — Follow-up "can you check within 50 miles instead?" gets `response_source: openai/gpt-5.6-luna` and prose claims "No pharmacies were found within 50 miles" — no radius-widening path exists in chat; LLM must not assert a wider search it did not run.~~ **resolved 2026-08-21** — `resolve_pharmacy_radius_follow_up` in `pharmacy_questions.py` + session `last_tool_calls` from deterministic pharmacy paths; B7-2 follow-up now returns `System/NearbyPharmacy` with honest fixed-radius refusal.
- ~~[REVISE] pharmacy-lookup — `extract_zip` ignores `"I live in 72719"` when the word `zip` is absent anywhere in the message~~ **resolved 2026-08-21** — `_LIVE_IN_ZIP_RE` accepts `"I live in / I'm in / my address is {5 digits}"`; B7-3 oracle updated; new B7-6 scenario added.

## Suggested test cases

### Run 2026-08-21T03:57:00Z (pharmacy-lookup full catalog)

#### Unaddressed fixes
- ~~[REVISE] pharmacy-lookup/B8-1 — see Open fixes above~~ **resolved 2026-08-21**
- ~~[REVISE] pharmacy-lookup/B8-2 — see Open fixes above~~ **resolved 2026-08-21**

#### Suggested test cases
- **Type:** pytest
- **Scenario:** `is_nearby_pharmacy_question("I live in 72712. What are the pharmacies available in my zip?")` is true; `resolve_nearby_pharmacy_question` returns `System/NearbyPharmacy` with oracle rows.
- **Why:** B8-1 is the exact user-reported 72712 phrasing; B7-6 covers `"near me"` variant only — `"in my zip"` is a distinct common phrasing gap.
- **Suggested home:** `tests/test_pharmacy_questions.py`
- **Draft inputs:** parametrize `"available in my zip"`, `"pharmacies in my zip"`, `"in my zip code"` vs negative preferred-only asks.

### Run 2026-08-21T02:59:00Z

#### Unaddressed fixes
- [REVISE] pharmacy-lookup/B7-2 — see Open fixes above (50-mile follow-up hallucination).
- [REVISE] pharmacy-lookup — bare `"I live in {ZIP}"` not extracted without `zip` keyword; user-reported phrasing `"I live in 72719. What are the pharmacies available near me?"` asks for ZIP instead of honest no-match/locator result. Not covered by current skill scenarios (B7-3 tests trailing phrasing only and expects clarification).

#### Suggested test cases
- **Type:** mandatory-llm
- **Scenario:** Nearby-pharmacy ask with `"I live in {5 digits}"` at the **start** of the message (no word `zip`) — e.g. `"I live in 72719. What are the pharmacies available near me?"`
- **Why:** Real user phrasing gap; skill B7-3 only covers ZIP-at-end variant and currently expects clarification, not locator honesty.
- **Suggested home:** `.cursor/skills/tests/T3/pharmacy-lookup/llm-scenarios.md` B7 block + `scripts/llm_scenario_suites/pharmacy_lookup.json`
- **Draft inputs:** message above; oracle after fix: `System/NearbyPharmacy`, honest no-match for 72719 (no fabricated pharmacy). Until fixed, document as REVISE/BLOCK if bot asks for ZIP.
- **Type:** pytest
- **Scenario:** `extract_zip("I live in 72719. What are the pharmacies available near me?")` returns `"72719"` when anchored by `I live in` pattern (without requiring `\bzip\b` elsewhere).
- **Why:** Prevents regression once resolver accepts common location phrasing; keeps bare `#12345` order-number guard for unrelated digits.
- **Suggested home:** `tests/test_pharmacy_questions.py`
- **Draft inputs:** parametrize `"I live in 72719"`, `"I'm in 32801"`, `"my address is 90210"` vs negative `"order #12345"`.

### Run 2026-08-21T00:41:42Z

#### Unaddressed fixes
- ~~[BLOCK] pharmacy-lookup/B7-4 — `_NEARBY_PHARMACY_RE` requires `\bnear\b` as a whole word, but "nearby" (one token) never satisfies that boundary (no transition between 'near' and 'by'), so "I live in zip 32801, any pharmacies nearby?" silently skipped `resolve_nearby_pharmacy_question` entirely and fell through to the general LLM agent loop, which asked a redundant clarifying question instead of just answering~~ **resolved 2026-08-21** — added `nearby` explicitly to `_NEARBY_PHARMACY_RE` in `pharmacy_questions.py`. Verified fixed for $0 (deterministic path, no LLM call needed) via direct `/api/chat` curl — now returns `System/NearbyPharmacy` with the real fixture pharmacy list. Regression test added: `test_is_nearby_pharmacy_question_matches_nearby_as_one_word`. Found by the new B7 live-LLM scenario block (B7-4), not by pytest — a good example of why the live-LLM catalog exists alongside deterministic tests.
- [DATA GAP, non-code, still open] Real (non-fixture) states still have zero `pharmacy_network` rows — only the FL fixture was ingested this run (see [SKILL.md's "Before running Phase B"](.cursor/skills/tests/T3/pharmacy-lookup/SKILL.md#before-running-phase-b-data-prerequisite)). B3-3, B7-1, B7-2, B7-5 all correctly demonstrate honest no-fabrication behavior for a real AR ZIP/plan (72719 / H2802-060), but this is *because* no AR pharmacy-network data exists yet, not because the locator was tested against real AR pharmacy data. Ingest a second state's pharmacy-network SPUF file (live or fixture) to get a true positive-match test for a non-FL state.

#### Suggested test cases
- **Type:** pytest
- **Scenario:** Regex robustness sweep for `is_nearby_pharmacy_question`/`is_preferred_pharmacy_question` against other common one-word phrasings that might share the "nearby" bug's root cause (e.g. "close by" as "closeby", "around here" contractions) — the "nearby" gap was only caught because a live LLM scenario happened to use that exact phrasing; deterministic pytest never would have caught it since all existing pytest messages happened to use "near" as a standalone word.
- **Why:** B7-4 showed the fixed 10-scenario catalog's phrasing coverage was too narrow to catch a real, very-common phrasing gap; broader pytest-level fuzzing of the intent regexes would catch this class of bug without spending live LLM budget.
- **Suggested home:** `tests/test_pharmacy_questions.py`
- **Draft inputs:** parametrize over phrasing variants of "pharmacy"/"pharmacies" + "near"/"close"/"around" as both separate words and single compound words.

### Run 2026-08-14T21:22:00Z

#### Unaddressed fixes
- ~~[REVISE] insulin/B3-2 T2 — Follow-up YTD $2200 says "$0.00 depending on pharmacy channel" when oracle is $0 on all channels~~ **resolved 2026-08-14** — deterministic insulin session follow-up + uniform-channel prose repair

#### Suggested test cases
- **Type:** mandatory-llm
- **Scenario:** Insulin YTD catastrophic follow-up via LLM path must not hedge $0 with channel variance when all channels are $0
- **Why:** B3-2 T2 passed auto-checks but prose is imprecise on gpt-5.4-nano
- **Suggested home:** `.cursor/skills/tests/T3/insulin/llm-scenarios.md` B3-2 follow-up grading
- **Draft inputs:** opener lantus S9999-001; follow-up "what if I've spent $2200 YTD?"; oracle all channels $0

### Run 2026-08-21T00:04:39Z

#### Unaddressed fixes
- ~~[BLOCK] pharmacy-lookup/B4-1 — `resolve_nearby_pharmacy_question` (Q3, `pharmacy_questions.py`) never extracts `plan_key` or `channel` — a "mail-order pharmacies near zip X for plan Y" ask falls through to the fully generic, plan-agnostic and channel-agnostic nearby search, silently dropping both the plan scope and the mail/retail intent~~ **resolved 2026-08-21** — `resolve_nearby_pharmacy_question` now takes an optional `filter_plan_id`, extracts `plan_key` from the message via `extract_plan_key`/UI filter fallback, and parses mail-order-vs-retail wording via new `_extract_channel_scope` (filters `PharmacyResult.channel` by suffix post-hoc, since `find_pharmacies`' own `channel=` param needs the full preferred/standard string the message doesn't supply). Covered by 4 new pytest cases in `test_pharmacy_questions.py` (plan scoping without "preferred" wording, `filter_plan_id` fallback, mail-order filtering, honest no-mail-match). Full pharmacy suite: 49/49 passing.
- [DATA GAP, non-code, still open] Local dev DB backing `http://localhost:8000` has zero `pharmacy_network` rows (`PharmacyRepository().nearby_candidates()` → `[]`) — the SPUF pharmacy-network file + NPPES enrichment for this branch's feature has never been ingested into this server's DB. This blocked meaningful live-LLM validation of B1-1, B1-2, B1-3, B2-3, B3-1, B4-1 (6/10 Phase B scenarios in the 2026-08-21 run) — the bot behaved honestly (no fabricated pharmacy) in every case, but "does it actually find/list a real pharmacy" end-to-end remains unverified against gpt-5.6-luna pending ingest. Not a code defect; re-run `/quality-test/pharmacy-lookup` Phase B once the fixture/live pharmacy-network SPUF file is loaded into this server's DB.

#### Suggested test cases
- **Type:** mandatory-llm
- **Scenario:** Re-run Phase B (10-query catalog) against a DB with real `pharmacy_network` rows ingested, to confirm B1/B2-3/B3-1 list correct fixture pharmacies and B4-1's new plan+channel scoping holds up against a live LLM (pytest already covers it deterministically).
- **Why:** the 2026-08-21 run's Phase B queries were only honesty/routing checks (no fabrication) — the "found the right pharmacy" half of the oracle was never exercised due to the empty DB.
- **Suggested home:** `.cursor/skills/tests/T3/pharmacy-lookup/SKILL.md` Phase B, next invocation after ingest.
- **Draft inputs:** same 10-scenario catalog in `scripts/llm_scenario_suites/pharmacy_lookup.json`, no changes needed — just needs data present.
