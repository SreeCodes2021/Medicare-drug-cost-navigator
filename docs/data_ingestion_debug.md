# Data ingestion debug (Render Shell)

Commands for checking **what CMS SPUF data is loaded**, **row counts by table**, and **per-state breakdowns** after ingest.

Use this on **Render Shell** on the web service, or locally with the same commands after swapping the DB path.

| Environment | DuckDB path | Manifest |
|---|---|---|
| Render (production) | `/data/navigator.duckdb` | `/data/manifest.json` |
| Local dev | `./data/navigator.duckdb` | `./data/manifest.json` |

See also [deployment.md](./deployment.md) for ingest schedules, re-ingest commands, and disk recovery.

---

## 1. Quick health check

```bash
curl -s http://localhost:8000/api/health | python -m json.tool
```

Or via the project CLI:

```bash
medicare-chat-invoke health
```

Look for:

| Field | Healthy signal |
|---|---|
| `data_fresh` | `true` |
| `seeded_at` | Today's date (or within 1 day of last ingest) |
| `spuf_version` | Current CMS zip label |
| `spuf_as_of` | CMS file as-of date |
| `spuf_source_id` | e.g. `cms_spuf_2026_q1` |

Alert when `data_fresh` is `false` for more than one check cycle.

---

## 2. Manifest (metadata — no row counts)

```bash
cat /data/manifest.json | python -m json.tool
```

Key fields under `spuf`:

- `states` — states recorded at last ingest (e.g. `["AR", "TX"]`)
- `version` — CMS zip version label
- `as_of` — CMS file date
- `source_id` — internal source identifier
- `contract_year`, `quarter`

`seeded_at` (top level) drives `/api/health` freshness.

---

## 3. Which states are loaded?

**DuckDB** (authoritative — what the API queries):

```bash
python -c "
import duckdb
print(duckdb.connect('/data/navigator.duckdb', read_only=True).execute(
    'SELECT DISTINCT upper(state) AS state FROM plans ORDER BY 1'
).fetchall())
"
```

**Compare manifest vs DuckDB:**

```bash
python -c "
import json, duckdb
from pathlib import Path
m = json.loads(Path('/data/manifest.json').read_text())
db_states = [r[0] for r in duckdb.connect('/data/navigator.duckdb', read_only=True).execute(
    'SELECT DISTINCT upper(state) FROM plans ORDER BY 1').fetchall()]
print('manifest states:', m.get('spuf', {}).get('states'))
print('duckdb states:  ', db_states)
"
```

**List plans** (optional):

```bash
python -c "
import duckdb
for row in duckdb.connect('/data/navigator.duckdb', read_only=True).execute(
    'SELECT plan_key, plan_name, state FROM plans ORDER BY state, plan_name'
).fetchall():
    print(row)
"
```

Or hit the API: `GET /api/plans?state=AR`.

---

## 4. Row counts — all tables

```bash
python -c "
import duckdb
conn = duckdb.connect('/data/navigator.duckdb', read_only=True)
tables = [
    ('plans',                          'Core — plan catalog'),
    ('basic_drugs_formulary',          'Core — formulary'),
    ('beneficiary_cost',               'Core — tier cost shares'),
    ('insulin_beneficiary_cost',       'Core — IRA insulin cap'),
    ('pricing',                        'Core — NDC pricing'),
    ('pharmacy_network',               'Pharmacy — plan↔NPI network'),
    ('pharmacies',                     'Pharmacy — NPPES enrichment'),
    ('usage_hourly',                   'Analytics (not SPUF)'),
    ('query_log',                      'Analytics (not SPUF)'),
]
print(f\"{'Table':<30} {'Rows':>12}  Category\")
print('-' * 70)
for t, cat in tables:
    try:
        n = conn.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]
        print(f'{t:<30} {n:>12,}  {cat}')
    except Exception:
        print(f'{t:<30}      (missing)  {cat}')
conn.close()
"
```

### Table categories

| Table | Ingest mode | Notes |
|---|---|---|
| `plans` | Core (nightly) | Replaced per active state on each core run |
| `basic_drugs_formulary` | Core | Shared across plans via `formulary_id` |
| `beneficiary_cost` | Core | General tier cost shares |
| `insulin_beneficiary_cost` | Core | IRA insulin cap rows |
| `pricing` | Core | Largest table |
| `pharmacy_network` | Weekly / `--with-pharmacy-network` | **Not** updated on nightly `--core-only` |
| `pharmacies` | Weekly / `--with-pharmacy-network` | NPPES enrichment; global, not purged per state |
| `usage_hourly`, `query_log` | Runtime | Analytics; not part of SPUF ingest |

---

## 5. Row counts — per state

Use **separate counts per table** (not one big multi-table JOIN) to avoid inflated row counts.

```bash
python -c "
import duckdb

DB = '/data/navigator.duckdb'
conn = duckdb.connect(DB, read_only=True)

states = [r[0] for r in conn.execute(
    'SELECT DISTINCT upper(state) FROM plans ORDER BY 1'
).fetchall()]
if not states:
    print('No plans loaded.')
    raise SystemExit(0)

def by_state(label, sql):
    print(f'\n=== {label} ===')
    print(f\"{'State':<6} {'Rows':>12}\")
    print('-' * 20)
    total = 0
    for state, n in conn.execute(sql).fetchall():
        print(f'{state:<6} {n:>12,}')
        total += n
    print('-' * 20)
    print(f\"{'TOTAL':<6} {total:>12,}\")

by_state('plans', '''
    SELECT upper(state), COUNT(*)
    FROM plans
    GROUP BY 1 ORDER BY 1
''')

for table in [
    'beneficiary_cost',
    'insulin_beneficiary_cost',
    'pricing',
    'pharmacy_network',
]:
    by_state(table, f'''
        SELECT upper(p.state), COUNT(*)
        FROM {table} t
        JOIN plans p ON t.plan_key = p.plan_key
        GROUP BY 1 ORDER BY 1
    ''')

# Formulary rows reachable from plans in each state.
# Shared formulary_ids may appear in more than one state.
by_state('basic_drugs_formulary (via plan formulary_id)', '''
    SELECT upper(p.state), COUNT(*)
    FROM basic_drugs_formulary f
    JOIN plans p ON p.formulary_id = f.formulary_id
    GROUP BY 1 ORDER BY 1
''')

print('\n=== distinct formulary_ids per state ===')
print(f\"{'State':<6} {'Formularies':>12}\")
print('-' * 20)
for state, n in conn.execute('''
    SELECT upper(state), COUNT(DISTINCT formulary_id)
    FROM plans
    WHERE formulary_id IS NOT NULL AND formulary_id != ''
    GROUP BY 1 ORDER BY 1
''').fetchall():
    print(f'{state:<6} {n:>12,}')

by_state('pharmacies (linked via pharmacy_network)', '''
    SELECT upper(p.state), COUNT(DISTINCT ph.npi)
    FROM pharmacy_network pn
    JOIN plans p ON pn.plan_key = p.plan_key
    JOIN pharmacies ph ON ph.npi = pn.npi
    GROUP BY 1 ORDER BY 1
''')

conn.close()
"
```

---

## 6. Row counts — single state

Change `STATE` to `AR`, `TX`, or any loaded state code:

```bash
python -c "
import duckdb
STATE = 'AR'
conn = duckdb.connect('/data/navigator.duckdb', read_only=True)
pk = f\"plan_key IN (SELECT plan_key FROM plans WHERE upper(state)='{STATE}')\"
fid = f\"formulary_id IN (SELECT DISTINCT formulary_id FROM plans WHERE upper(state)='{STATE}')\"
for label, sql in [
    ('plans', f\"SELECT COUNT(*) FROM plans WHERE upper(state)='{STATE}'\"),
    ('formulary', f'SELECT COUNT(*) FROM basic_drugs_formulary WHERE {fid}'),
    ('beneficiary_cost', f'SELECT COUNT(*) FROM beneficiary_cost WHERE {pk}'),
    ('insulin', f'SELECT COUNT(*) FROM insulin_beneficiary_cost WHERE {pk}'),
    ('pricing', f'SELECT COUNT(*) FROM pricing WHERE {pk}'),
    ('pharmacy_network', f'SELECT COUNT(*) FROM pharmacy_network WHERE {pk}'),
]:
    print(f'{label:22} {conn.execute(sql).fetchone()[0]:>12,}')
conn.close()
"
```

---

## 7. Expected baselines (2026 AR+TX)

Reference volumes from a full AR+TX ingest. Your counts should be in the same ballpark.

| Table | AR+TX (approx) | AR only (approx) |
|---|---|---|
| `plans` | 462 | 85 |
| `basic_drugs_formulary` | ~196,000 | ~97,000 |
| `pricing` | ~4,800,000 | ~856,000 |
| `beneficiary_cost` | ~53,500 | ~9,200 |
| `insulin_beneficiary_cost` | varies | check with validator below |
| `pharmacy_network` | varies | depends on weekly pharmacy ingest |

### Red flags

| Symptom | Likely cause |
|---|---|
| `plans = 0` | No data loaded; run ingest |
| `pricing` or `beneficiary_cost` near zero with plans present | Partial or failed ingest |
| `pharmacy_network = 0` | Pharmacy ingest not run yet (normal right after core-only nightly) |
| `pharmacies = 0` but `pharmacy_network > 0` | NPPES enrichment missing or failed; network rows exist but pharmacy names/addresses may be incomplete |

---

## 8. Insulin data validation

After any real (non-fixture) ingest:

```bash
python scripts/validate_insulin_cost_data.py --db /data/navigator.duckdb
```

Prints insulin row count plus statutory-cap and conflict checks. See [insulin-cost-estimation.md](./insulin-cost-estimation.md).

---

## 9. What `medicare-ingest` prints on completion

When ingest finishes, stdout includes a summary like:

```
SPUF ingestion complete: 85 plans loaded (462 total in DB, 196416 formulary rows).
Manifest as_of: 2026-04-08 (source_id=cms_spuf_2026_q2, states=['AR', 'TX'])
```

Internal stats (not persisted to manifest):

| Key | Meaning |
|---|---|
| `plans` | Plans loaded this run |
| `plans_purged` | Plans removed before reload |
| `formulary_ids` | Distinct formulary IDs touched |
| `formulary_rows` | Total `basic_drugs_formulary` rows after ingest |
| `beneficiary_cost_rows` | Total beneficiary cost rows |
| `insulin_beneficiary_cost_rows` | Total insulin cap rows |
| `pharmacy_network_rows` | Total pharmacy network rows |
| `pharmacies` | Total NPPES pharmacy rows |
| `total_plans` | All plans in DB after ingest |

There is no separate “show last ingest stats” command — use the queries above, or re-run ingest and watch stdout.

---

## 10. Re-ingest (when counts look wrong)

| Goal | Command |
|---|---|
| Add or refresh one state | `medicare-ingest spuf --download --states AR --merge-states` |
| Nightly core equivalent | `medicare-ingest spuf --download --preserve-other --core-only` |
| Full core + pharmacy recovery | `medicare-ingest spuf --download --preserve-other --with-pharmacy-network --force` |

After re-ingest, re-run the row-count queries in sections 4–6 and confirm `GET /api/health` → `data_fresh: true`.

Full ingest ops: [deployment.md](./deployment.md).
