# UI requirements guide

This document captures UI product requirements for the Medicare Drug Cost Navigator frontend. Use it when changing the guided form, data-release display, or related API wiring.

**Related docs:** [how-to-use-the-app.md](how-to-use-the-app.md) (user-facing), [developer-guide.md](developer-guide.md) (engineering).

---

## Scope

These requirements apply to:

- Guided form → **Single** submode (`#guided-single`)
- Guided form → **Multiple drugs** submode (`#guided-multidrug`)
- Guided form → **Compare plans** submode (`#guided-compareplans`)
- Data release label and how it ties to ingested CMS SPUF data
- Plan loading filtered by the active contract year

They do **not** cover Chat tab behavior, LLM model selection, or estimate math (those live in backend/agent docs).

---

## Data release display

### Problem (why this exists)

The guided form previously showed a **Contract year** dropdown with hardcoded options (`2025`, `2026`). That was wrong because:

1. Only ingested data should be offered — if 2025 SPUF was never loaded, selecting 2025 misled users.
2. Estimates always used the plan record’s `contract_year` from DuckDB, not the dropdown value, so the UI could imply a year that was not actually used.
3. Quarter was always shown as **Q1** because ingest hardcoded `cms_spuf_{year}_q1` in the manifest.

### Requirements

| ID | Requirement |
| --- | --- |
| DR-1 | Show the active CMS data release as a **read-only label**, not a dropdown. There is only one dataset at a time; historic releases are out of scope for now. |
| DR-2 | Label format: **`YYYY-Qn`** (e.g. `2026-Q3`). Use contract year from manifest + calendar quarter from ingest. |
| DR-3 | **Year** comes from manifest `spuf.contract_year` (Medicare contract year, e.g. 2026), not from today’s calendar year. |
| DR-4 | **Quarter** comes from the **ingest/pull date** (`date.today()` at ingest time), stored in manifest as `spuf.quarter` and reflected in `source_id` (`cms_spuf_2026_q3`). |
| DR-5 | On the next ingest in a new calendar quarter, the label updates automatically — no manual UI change. |
| DR-6 | If no data is loaded, show **`No data loaded`** (not a fake year/quarter). |
| DR-7 | Visual style: plain text under the “Data release” caption — **no bordered box** mimicking an input field. |
| DR-8 | `contract_year` from the active release must still be sent in chat/guided `filters` so the agent context matches the dataset. |

### What not to do

- Do not hardcode year options (e.g. 2025 / 2026) in HTML.
- Do not let users pick a release quarter/year when only one ingest exists.
- Do not derive the displayed quarter from “today’s date” on every page load if it disagrees with manifest — the manifest (ingest date) is the source of truth.
- Do not show contract year and calendar year interchangeably in the label.

---

## UI placement and behavior

### Guided form — Single

Location: fill options row alongside **Days supply** and **YTD out-of-pocket**.

```
Data release          Days supply        YTD out-of-pocket ($)
2026-Q3               [30 ▼]             [optional]
```

| Element | ID / API | Behavior |
| --- | --- | --- |
| Label caption | `Data release` | Muted label text (same as other guided fields). |
| Value | `#data-release-label` | Populated on page load from `GET /api/data-release`. |
| Plans | `#filter-plan` + `/api/plans?year={contract_year}` | Plan list filtered to the active contract year after release loads. |

Init order:

1. `loadDataRelease()` — fetch release, set label, store `currentDataRelease` in memory.
2. `pollPlansUntilLoaded()` — load plans for `currentDataRelease.contract_year`.

**Refresh** (plan list button) reloads plans for the same contract year; it does not change the data release.

### Guided form — Multiple drugs and Compare plans

Repeatable rows let users add drugs (multi-drug basket) or plans (plan comparison). Each submode has a hard cap enforced in the UI and backend.

| ID | Requirement |
| --- | --- |
| RR-1 | **Multiple drugs:** up to **5** drug rows (`MAX_BATCH_DRUGS`). **Compare plans:** up to **4** plan rows (`MAX_COMPARE_PLANS`). |
| RR-2 | When the cap is reached, the **+ Add drug** / **+ Add plan** button (`#multidrug-add-row`, `#compareplans-add-row`) must be **disabled** and visually grayed out — same disabled treatment as the primary action buttons (`Get combined estimate`, `Compare plans`): reduced opacity, `not-allowed` cursor. |
| RR-3 | Clicking a disabled add button at the cap must show a **limit message** in `#guided-error` (same alert area used for validation errors): “You can add up to 5 drugs per estimate.” or “You can compare up to 4 plans at a time.” |
| RR-4 | Removing a row so the count drops below the cap must **re-enable** the add button and **clear** any limit message shown for that cap. |
| RR-5 | Compare plans keeps a **minimum of 2** plan rows; multiple drugs keeps a **minimum of 1** drug row. Remove buttons are disabled at those floors. |

### Guided sub-mode tabs (Single / Multiple drugs / Compare plans)

| ID | Requirement |
| --- | --- |
| ST-1 | The three sub-mode tabs (`#guided-mode-single`, `#guided-mode-multidrug`, `#guided-mode-compareplans`) must be **equally divided** across the full guided-form width — three equal columns, not content-sized flex items. |
| ST-2 | Tab labels must be **centered** within each column (`text-align: center`). |
| ST-3 | Layout uses `.guided-submode-tabs` with `display: grid` and `grid-template-columns: repeat(3, 1fr)`; do not revert to horizontal scroll or uneven flex sizing. |

| Element | ID | Behavior |
| --- | --- | --- |
| Add drug | `#multidrug-add-row` | Adds a row until 5; disabled + grayed at cap. |
| Drug rows | `#multidrug-rows` | Dynamic drug/dosage picker rows. |
| Combined estimate | `#multidrug-submit` | Disabled until plan + at least one drug with dosage are valid. |
| Add plan | `#compareplans-add-row` | Adds a row until 4; disabled + grayed at cap. |
| Plan rows | `#compareplans-rows` | Dynamic plan combobox rows (default 2 on load). |
| Compare plans | `#compareplans-submit` | Disabled until drug, dosage, and ≥2 plans are valid. |
| Limit / validation alert | `#guided-error` | Shows cap or field-validation messages. |

Implementation notes:

- Disabled add buttons use `pointer-events: none`; parent click handlers (`initGuidedAddRowButtons`) detect clicks on the grayed button area and surface the limit message (same pattern as disabled primary submit buttons in `.guided-action-row`).
- Row controls: `updateDrugRowControls()`, `updateComparePlanRowControls()` in `frontend/src/app.js`.

### Chat tab

Chat does not show the data release label. Guided filters still pass `contract_year` when the user uses guided form fields or plan picker values that overlap with chat.

---

## Backend and manifest contract

### Ingest (`medicare-ingest spuf`)

On each ingest:

```text
ingest_quarter = calendar_quarter(date.today())
source_id      = f"cms_spuf_{contract_year}_q{ingest_quarter}"
```

Manifest `spuf` block must include:

- `contract_year`
- `quarter`
- `source_id`
- `as_of`, `version`, `states`

### API

| Endpoint | Purpose |
| --- | --- |
| `GET /api/data-release` | Primary UI source. Returns `{ "release": { id, label, contract_year, quarter, source_id, as_of, seeded_at, ... } }` or `{ "release": null }`. |
| `GET /api/data-releases` | Backward-compatible; returns 0 or 1 item in `releases[]`. |
| `GET /api/plans?year=` | Plans filtered by `contract_year`. |

### Quarter resolution (read path)

When building the release label server-side (`get_data_release()`), resolve quarter in this order:

1. `spuf.quarter` (explicit, set at ingest)
2. Parse `spuf.source_id` (`cms_spuf_2026_q3` → Q3)
3. Derive from `seeded_at` or `spuf.as_of` date
4. Fallback: Q1 when only DB plan years exist without manifest detail

---

## Key files

| Area | Path |
| --- | --- |
| HTML | `frontend/src/index.html` — `#data-release-label`, `#multidrug-add-row`, `#compareplans-add-row` |
| JS | `frontend/src/app.js` — `loadDataRelease()`, `currentDataRelease`, `getFilters()`, `updateDrugRowControls()`, `updateComparePlanRowControls()`, `initGuidedAddRowButtons()` |
| CSS | `frontend/src/styles.css` — `.data-release-label`, `.btn-secondary:disabled`, `.guided-submode-tabs` |
| Manifest helpers | `src/medicare_navigator/ingestion/manifest.py` |
| Ingest | `src/medicare_navigator/ingestion/spuf.py` |
| API | `src/medicare_navigator/api/app.py` |
| UI smoke IDs | `src/medicare_navigator/ui_test/checks.py` — `data-release-label` in `REQUIRED_ELEMENT_IDS` |

---

## After deploying code changes

If an environment still shows the wrong quarter (e.g. **2026-Q1** in August), the manifest was ingested before quarter-aware ingest shipped. **Re-run ingest** so `quarter` and `source_id` reflect the pull date.

---

## Future (not implemented)

When multiple SPUF releases are stored side by side:

- Consider a real selector (dropdown or tabs) populated from `/api/data-releases`.
- Filter plans, formulary, and estimates by selected `source_id` / release, not only `contract_year`.
- Until then, keep a single static label per the requirements above.

---

## Changelog

| Date | Change |
| --- | --- |
| 2026-08-09 | Guided sub-mode tabs: equal 3-column grid, centered labels (Single / Multiple drugs / Compare plans). |
| 2026-08-09 | Repeatable-row caps: gray out add buttons at limit, show `#guided-error` limit message (Multiple drugs ≤5, Compare plans ≤4). |
| 2026-08-09 | Initial guide: static `YYYY-Qn` label from ingest date; removed hardcoded 2025/2026 dropdown; plain-text styling (no box). |
