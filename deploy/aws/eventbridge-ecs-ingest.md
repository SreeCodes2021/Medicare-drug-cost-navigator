# AWS: EventBridge → ECS scheduled SPUF ingest (3:00 AM)

Run `medicare-ingest spuf --download` as a **one-off ECS Fargate task** on a schedule, separate from the API service.

## Schedule

EventBridge rule (UTC — adjust for your timezone):

```
cron(0 3 * * ? *)
```

For 3:00 AM US Eastern (EST, UTC-5), use `cron(0 8 * * ? *)` during standard time.

## Task command

```bash
medicare-ingest spuf --download
```

Or use the repo script:

```bash
/bin/bash scripts/run-daily-ingest.sh
```

## Shared storage

Mount the same EFS volume (or EBS snapshot workflow) on:

- API ECS service → `DATA_DIR=/data`
- Ingest task → `DATA_DIR=/data`

Required paths under `DATA_DIR`:

- `navigator.duckdb`
- `manifest.json`
- `raw/` (CMS zip cache)

## First deploy

Run the ingest task once manually before starting the API so production is not on demo seed:

```bash
medicare-ingest spuf --download
```

## Failure monitoring

Poll `GET /api/health` and alert when `data_fresh` is `false` or `seeded_at` is older than one calendar day.

Example health fields:

```json
{
  "status": "ok",
  "seeded_at": "2026-07-05",
  "data_fresh": true,
  "spuf_source_id": "cms_spuf_2026_q1",
  "spuf_as_of": "2026-01-15"
}
```
