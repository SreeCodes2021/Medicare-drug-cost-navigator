# Documentation index

Developer-facing documentation for the **Medicare Drug Cost & Benefit-Transparency Navigator** (Phase 6).

| Document | Audience | Contents |
|---|---|---|
| **[Developer Guide](./developer-guide.md)** | All developers | **Start here.** Stack, architecture, setup, run, test, deploy, API, troubleshooting |
| [Business Solution](./business-solution.md) | Product / stakeholders | Executive summary, problem statement, v1 capabilities, architecture, compliance, roadmap |
| [Navigator Implementation Spec](./navigator-implementation-spec.md) | Product / backend | v1 scope, 8-step cost pipeline, Bugs 1–6, days-supply mapping |
| [Phase 6 Implementation Plan](./phase-6-implementation-plan.md) | Maintainers | What shipped in Phase 6, decisions, commit history |
| [Deployment](./deployment.md) | DevOps | Render, cron ingest, persistent disk, monitoring |
| [Data Sources](./data-sources.md) | Data / backend | CMS SPUF, RxNorm, manifest fields (note: some Phase 1–5 sources are removed) |
| [Build Requirements](../build-requirements.md) | Product | Long-term vision (broader than current v1 scope) |
| Phase 1–5 plans | History | `phase-1-implementation-plan.md` … `phase-5-implementation-plan.md` |

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
