---
name: ui-functionality-llm-availability
description: >-
  Confirm every model in the LLM catalog (GPT-5.4 Nano, GPT-5.6 Luna, Claude
  Haiku 4.5) is actually invokable end-to-end, not just listed in the
  dropdown. Use with /ui-functionality/llm-availability.
disable-model-invocation: true
---

# UI functionality — All listed LLMs are working

Parent: [ui-functionality/SKILL.md](../SKILL.md).

## Why this exists

`#model-select` / `#guided-model-select` hardcode the same catalog from
[`llm/models.py`](../../../../src/medicare_navigator/llm/models.py)
(`MODEL_CATALOG`). Nothing today actually exercises each model live — a model
can be listed and selectable in the UI while its provider key is missing or
expired, silently falling back or erroring only when a beneficiary happens to
pick it.

## Step 1 — Check the catalog + configured providers

```bash
medicare-chat-invoke health
medicare-chat-invoke models
```

`models` prints each `{id, label, provider, configured}` — `configured: false`
means that provider's API key ([`OPENAI_API_KEY`](../../../../.env.example) /
`ANTHROPIC_API_KEY`) is missing from `.env`. Do not attempt a live call for an
unconfigured model — instead verify the UI handles that gracefully (see Step 3).

## Step 2 — Live smoke call per configured model

For every model with `configured: true`:

```bash
medicare-chat-invoke send --message "What's the cost for metformin 500mg on plan S9999-001?" --model gpt-5.4-nano
medicare-chat-invoke send --message "What's the cost for metformin 500mg on plan S9999-001?" --model gpt-5.6-luna
medicare-chat-invoke send --message "What's the cost for metformin 500mg on plan S9999-001?" --model claude-haiku-4-5-20251001
```

For each, check the returned bundle:

| Field | Expected |
|-------|----------|
| `raw.response.response_source` | Not `"System"`/error fallback — should reflect a real LLM-authored explanation for `status: ok` |
| `raw.response.llm_usage.model` | Echoes back the requested model id |
| `raw.response.llm_usage.provider` | Matches the catalog's provider for that model |
| `grading.explanation` | Non-empty, coherent prose (not a raw error string) |

This requires live API keys (`LLM_MOCK` unset/`0`) — it is **not** an offline/mock check. If keys aren't available in this environment, report which models could not be verified rather than skipping silently.

## Step 3 — Unconfigured-model UX

If any catalog model has `configured: false`:

1. Confirm the UI still lists it (informational) but does not crash when selected.
2. Send one message with that model selected — expect a clear `/api/health`-style error (missing API key), not a silent fallback that pretends to be that model.

## Automated regression (offline, mock-safe)

```bash
pytest tests/test_llm_client.py tests/test_llm_mock.py -v
```

These don't call live providers but lock in the model-resolution/catalog contract (`resolve_model`, `provider_has_credentials`).

## Failure → fix

| Symptom | Fix |
|---------|-----|
| `medicare-chat-invoke models` shows `configured: false` unexpectedly | `.env` missing `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` — not a code bug |
| A specific model always errors, others work | [`llm/client.py`](../../../../src/medicare_navigator/llm/client.py) provider-specific request path for that model, or `openai_reasoning_effort` handling in `llm/models.py` (GPT-5.6 Luna needs `effort: "none"` for tool calls) |
| `llm_usage.model` doesn't match requested `--model` | `api/app.py` not threading `req.model` through to `Navigator.run(llm_model=...)` |
| UI dropdown lists a model with no working credentials and no error surfaced | `GET /api/models` `configured` flag not checked before allowing selection, or health check not surfaced in UI |

Report using [report-template.md](../report-template.md).
