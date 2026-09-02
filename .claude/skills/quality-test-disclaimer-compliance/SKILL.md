---
name: quality-test-disclaimer-compliance
description: >-
  Quality-tier disclaimer sweep — every chat status, compare-plans surface, and
  static banner. Deterministic pytest plus chat-qa dimension 4 on live LLM turns.
  Use with /quality-test-disclaimer-compliance or as part of /quality-test § 1c-A.
disable-model-invocation: true
---

# Quality test — Disclaimer compliance

Parent: [quality-test/SKILL.md](../quality-test/SKILL.md).

Tier 2 [`ui-functionality/disclaimer-everywhere`](../ui-functionality-disclaimer-everywhere/SKILL.md)
owns the same pytest; this sub-skill adds the **beneficiary-trust** lens for
`/quality-test` and grades **chat-qa dimension 4** on live LLM bundles.

## Three independent layers

1. **`QueryResponse.disclaimer` field** — set on every chat status in
   [`navigator.py`](../../../src/medicare_navigator/agent/navigator.py).
2. **Inline append into `explanation`** — [`guardrails/citations.py`](../../../src/medicare_navigator/guardrails/citations.py)
   `apply_guardrails` on the `ok` path; off-topic path inlines in navigator.
3. **Static banner** — `#disclaimer-banner` via `GET /api/disclaimer`, outside tab panels.
4. **Compare-plans caveat** — `PlanComparisonApiResponse.disclaimer` on every
   `/api/compare-plans` response.

## Automated checks (0 queries — always run)

```bash
pytest tests/test_disclaimer_coverage.py -v
```

## Live LLM grading (0 extra queries)

Score [`chat-qa`](../chat-qa/SKILL.md) **dimension 4** (Disclaimer &
data-currency) on **every** live LLM bundle graded anywhere in this run —
happy-path, § 1c-B answer-consistency, § 2b–2f mandatory scenarios, and
exploratory findings alike. Presence of the field itself is guaranteed
deterministically (`apply_guardrails`, `test_disclaimer_coverage.py`), but
this dimension also checks it's *unmissable*, which is worth verifying on the
highest-stakes turns (OOP/MOOP, dosage clarification), not just the happy
path:

| Score | Criteria |
|-------|----------|
| 0 | Missing informational-only disclaimer and/or "as of" data currency |
| 2 | Both present and unmissable |

Missing disclaimer on any graded live turn → **BLOCK** for disclaimer-compliance.

## Browser spot-check (optional)

1. Load portal — banner shows real text (not "Loading disclaimer…").
2. Chat → normal cost question — explanation contains canonical disclaimer.
3. Guided → Compare plans — comparison caveat distinct from general disclaimer.
4. 5-turn limit message — banner still visible.

Canonical text: [`config/disclaimer.txt`](../../../config/disclaimer.txt).

## Failure → fix

See [ui-functionality/disclaimer-everywhere](../ui-functionality-disclaimer-everywhere/SKILL.md) failure table.

## Verdict

Any deterministic `[FAIL]` → overall `/quality-test` **BLOCK**.
