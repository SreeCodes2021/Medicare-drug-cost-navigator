# Documentation index

Developer-facing documentation for the **Medicare Drug Cost & Benefit-Transparency Navigator** (Phase 6).

| Document | Audience | Contents |
|---|---|---|
| **[Developer Guide](./developer-guide.md)** | All developers | **Start here.** Stack, architecture, setup, run, test, deploy, API, troubleshooting |
| [Business Solution](./business-solution.md) | Product / stakeholders | Executive summary, problem statement, v1 capabilities, architecture, compliance, roadmap |
| [Navigator Implementation Spec](./navigator-implementation-spec.md) | Product / backend | Oral-drug v1 scope, 8-step cost pipeline, Bugs 1–6, days-supply mapping (insulin and catastrophic phase now shipped — see Insulin Cost Estimation) |
| [Insulin Cost Estimation](./insulin-cost-estimation.md) | Product / backend | IRA $35/30-day statutory cap: CMS source docs, calculation methodology, empirical field-resolution evidence, implementation, worked examples |
| [Insulin Trails (review)](./insulin_trails/README.md) | Maintainers / reviewers | Implementation review of insulin coverage: architecture summary, strengths, bugs/risks, action items, test gaps (feedback only) |
| [Deployment](./deployment.md) | DevOps | Render, cron ingest, persistent disk, monitoring |
| [Usage Analytics](./usage-analytics.md) | Ops / backend | Privacy-safe aggregate telemetry, admin API, dashboard, configuration |
| [Data Sources](./data-sources.md) | Data / backend | CMS SPUF, RxNorm, NPPES, ZIP centroids, manifest fields (note: some Phase 1–5 sources are removed) |
| [UI Requirements](./ui-requirements.md) | Product / frontend | Guided-form data-release display, repeatable-row caps, sub-mode tabs |
| [Quality Test Backlog](./quality-test-todos.md) | Maintainers / QA | Tracked findings and suggested test additions from `/quality-test` runs |
| [Build Requirements](../build-requirements.md) | Product | Long-term vision (broader than current v1 scope) |

## Quick commands

```bash
# One-time setup
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env

# Offline data + server
medicare-ingest spuf --source tests/fixtures/spuf
LLM_MOCK=1 uvicorn medicare_navigator.api.app:app --reload --port 8000

# Tests
pytest tests/ -v
LLM_MOCK=1 medicare-eval
```

See [Developer Guide → Local development](./developer-guide.md#local-development) for full instructions.
