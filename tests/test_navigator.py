import pytest

from medicare_navigator.agent.navigator import (
    _extract_explicit_ytd_from_message,
    _format_last_tool_calls,
    _merge_last_tool_calls,
    navigator,
)
from medicare_navigator.agent.prompts import NAVIGATOR_SYSTEM_PROMPT, build_navigator_system_prompt
from medicare_navigator.config import settings
from medicare_navigator.llm.client import llm_client
from medicare_navigator.llm.errors import LLMNotConfiguredError
from medicare_navigator.tools.disclaimers import INSULIN_OUT_OF_SCOPE_MESSAGE
from medicare_navigator.session.manager import session_manager
from tests.spuf_fixture import (
    PLAN_FL_MAPD,
    PLAN_FL_MAPD_MOOP,
    PLAN_FL_PARTIAL_CHANNELS,
    PLAN_FL_PDP,
    PLAN_FL_SUPPRESSED,
)


@pytest.fixture(autouse=True)
def _spuf(spuf_db):
    pass


@pytest.mark.asyncio
async def test_navigator_metformin_uses_all_channels_tool():
    message = f"What's the cost for metformin 500mg on plan {PLAN_FL_PDP}?"
    response = await navigator.run(message)
    assert response.status == "ok"
    assert "estimate_drug_cost_all_channels" in response.tools_invoked
    assert response.estimate is not None
    assert response.estimate.cost_low is not None
    assert response.estimate.cost_high is not None
    assert response.estimate.cost_low <= response.estimate.cost_high


@pytest.mark.asyncio
async def test_navigator_metformin_cost_estimate():
    message = (
        f"What's the cost for metformin 500mg on plan {PLAN_FL_MAPD}? "
        "I have already spent $1000 this year."
    )
    response = await navigator.run(message)
    assert response.status == "ok"
    assert response.drug_name == "metformin"
    assert response.estimate is not None
    assert response.estimate.benefit_phase == "initial_coverage"
    lower = response.explanation.lower()
    assert "metformin" in lower


@pytest.mark.asyncio
async def test_navigator_needs_plan_clarification():
    response = await navigator.run("metformin cost")
    assert response.status in ("ok", "needs_clarification")
    assert "plan" in response.explanation.lower()


@pytest.mark.asyncio
async def test_navigator_multi_drug_compare_without_strengths_clarifies():
    response = await navigator.run(
        f"Compare metformin and januvia costs on plan {PLAN_FL_PDP}"
    )
    assert response.status == "needs_clarification"
    assert response.response_source == "System/Dosage"
    assert response.tools_invoked == []
    lower = response.explanation.lower()
    assert "metformin" in lower
    assert "januvia" in lower
    assert "not covered" not in lower


@pytest.mark.asyncio
async def test_navigator_refuses_enrollment_without_plan_lookup():
    response = await navigator.run(f"sign me up for plan {PLAN_FL_PDP} please")
    assert response.status == "ok"
    assert response.response_source == "System/Enrollment"
    assert response.tools_invoked == []
    assert "enroll" in response.explanation.lower()


@pytest.mark.asyncio
async def test_navigator_rejects_negative_days_supply_in_message():
    response = await navigator.run(
        f"metformin 500mg on {PLAN_FL_PDP} with -30 day supply"
    )
    assert response.status == "needs_clarification"
    assert response.response_source == "System/InvalidInput"
    assert response.tools_invoked == []
    assert "-30" in response.explanation or "30-" in response.explanation.replace(" ", "")


@pytest.mark.asyncio
async def test_router_uses_navigator():
    from medicare_navigator.orchestrator.router import orchestrator

    response = await orchestrator.run(f"lisinopril 10mg cost plan {PLAN_FL_PDP}")
    assert response.status == "ok"


@pytest.mark.asyncio
async def test_navigator_suppressed_plan_hard_stop():
    response = await navigator.run(f"metformin cost on plan {PLAN_FL_SUPPRESSED}")
    assert "suppressed" in response.explanation.lower() or "contact the plan" in response.explanation.lower()
    assert "$" not in response.explanation.split(response.disclaimer)[0]


@pytest.mark.asyncio
async def test_navigator_insulin_priced_via_statutory_cap():
    """Insulin is no longer a hard stop — it must return a real, dollar-figure estimate
    (fixture: S9999-001 tier 3 insulin copay, capped at/under $35/30-day)."""
    response = await navigator.run(f"lantus cost on plan {PLAN_FL_PDP}")
    assert response.status == "ok"
    assert response.estimate is not None
    assert response.estimate.benefit_phase == "insulin_cap"
    assert response.estimate.cost_low is not None
    assert "$" in response.explanation
    assert "not supported" not in response.explanation.lower()


@pytest.mark.asyncio
async def test_navigator_insulin_no_cost_share_data_hard_stop():
    """H8888-001 (PLAN_FL_MAPD) has lantus on formulary but no insulin cost-share row —
    the narrower data-gap hard stop, distinct from the old blanket "unsupported" message.
    Unlike the suppressed-plan hard stop, this message legitimately cites the fixed $35
    statutory figure as explanatory text — so the check here is "message relayed
    verbatim," not "no dollar sign anywhere"."""
    response = await navigator.run(f"lantus cost on plan {PLAN_FL_MAPD}")
    assert "cost-share" in response.explanation.lower()
    assert INSULIN_OUT_OF_SCOPE_MESSAGE in response.explanation
    assert "not supported by this tool" not in response.explanation.lower()


@pytest.mark.asyncio
async def test_navigator_insulin_multi_product_answers_each_product():
    response = await navigator.run(
        f"Lantus and Humalog together are $35 total on plan {PLAN_FL_PDP}, right?"
    )
    assert response.status == "ok"
    assert len(response.channel_estimates) == 2
    assert {estimate.drug_name.lower() for estimate in response.channel_estimates} == {
        "lantus",
        "humalog",
    }
    lower = response.explanation.lower()
    assert "lantus" in lower
    assert "humalog" in lower
    assert "not pooled" in lower


@pytest.mark.asyncio
async def test_navigator_generic_insulin_policy_does_not_lookup_generic_drug():
    response = await navigator.run(
        f"Is insulin always exactly $35 on plan {PLAN_FL_PDP}, or can it be lower?"
    )
    assert response.status == "ok"
    assert response.response_source == "System/InsulinPolicy"
    assert "ceiling" in response.explanation.lower()
    assert "estimate_drug_cost" not in response.tools_invoked


@pytest.mark.asyncio
async def test_navigator_insulin_named_product_policy_ceiling():
    response = await navigator.run(
        f"Is insulin always $35 per month on plan {PLAN_FL_PDP} for Lantus?"
    )
    assert response.status == "ok"
    assert response.response_source == "System/Insulin"
    assert "ceiling" in response.explanation.lower()
    assert "$" in response.explanation


@pytest.mark.asyncio
async def test_navigator_insulin_deductible_policy_answer():
    response = await navigator.run(
        f"Does the Part D deductible apply before I pay for Lantus on plan {PLAN_FL_PDP}?"
    )
    assert "deductible" in response.explanation.lower()
    assert "no" in response.explanation.lower()


@pytest.mark.asyncio
async def test_navigator_insulin_catastrophic_from_oop_max_wording():
    response = await navigator.run(
        f"I'm in catastrophic coverage — what do I pay for Lantus on plan {PLAN_FL_PDP}? "
        f"Assume I've met my $2100 annual OOP max."
    )
    assert response.estimate is not None
    assert response.estimate.benefit_phase == "catastrophic"
    assert response.estimate.cost_low == 0.0
    assert "$0.00" in response.explanation


@pytest.mark.asyncio
async def test_navigator_insulin_tier_lookup_mentions_tier():
    response = await navigator.run(f"What formulary tier is Lantus on plan {PLAN_FL_PDP}?")
    assert "tier 3" in response.explanation.lower()


@pytest.mark.asyncio
async def test_navigator_januvia_tier_lookup_matches_cost_ask_coverage():
    """Tier-only ask must agree with the cost-ask path: januvia is covered, tier 2 on
    PLAN_FL_PDP (S9999-001) — a tier-only question must not report false not-covered."""
    tier_response = await navigator.run(f"What tier is januvia on plan {PLAN_FL_PDP}?")
    assert tier_response.response_source == "System/Tier"
    lower = tier_response.explanation.lower()
    assert "tier 2" in lower
    assert "not covered" not in lower

    cost_response = await navigator.run(f"How much does januvia cost on plan {PLAN_FL_PDP}?")
    assert cost_response.estimate is not None
    assert cost_response.estimate.covered is True


@pytest.mark.asyncio
async def test_navigator_insulin_channel_contrast_lists_both_channels():
    response = await navigator.run(
        f"For plan {PLAN_FL_PDP}, how does mail-order cost compare to retail for a 30-day Lantus fill?"
    )
    lower = response.explanation.lower()
    assert "$30.00" in response.explanation
    assert "$35.00" in response.explanation
    assert "mail" in lower
    assert "retail" in lower


@pytest.mark.asyncio
async def test_navigator_metformin_mail_retail_contrast_lists_both_channel_types():
    response = await navigator.run(
        f"How does mail order compare to retail for metformin 500mg on plan {PLAN_FL_PDP}?"
    )
    lower = response.explanation.lower()
    assert "mail" in lower
    assert "retail" in lower
    assert "$3.00" in response.explanation or "$5.00" in response.explanation


@pytest.mark.asyncio
async def test_navigator_insulin_under_cap_policy_ceiling():
    response = await navigator.run(
        f"On plan {PLAN_FL_PDP}, is Humalog always capped at $35 or could it be lower?"
    )
    assert response.status == "ok"
    lower = response.explanation.lower()
    assert "ceiling" in lower
    assert "$10.00" in response.explanation
    assert "below the federal $35 insulin ceiling" in lower


@pytest.mark.asyncio
async def test_navigator_insulin_multi_plan_compare_mentions_both_plans():
    response = await navigator.run(
        f"Please compare Lantus costs between plans {PLAN_FL_PDP} and {PLAN_FL_MAPD} "
        f"for a 30-day supply."
    )
    lower = response.explanation.lower()
    assert PLAN_FL_PDP.lower() in lower
    assert PLAN_FL_MAPD.lower() in lower
    assert "cost-share" in lower or "$" in response.explanation


def test_navigator_prompt_describes_scope():
    assert "insulin" in NAVIGATOR_SYSTEM_PROMPT.lower()
    assert "estimate_drug_cost_all_channels" in NAVIGATOR_SYSTEM_PROMPT
    assert "null channel" in NAVIGATOR_SYSTEM_PROMPT.lower()
    assert "all cms pharmacy channels" in NAVIGATOR_SYSTEM_PROMPT.lower()
    assert "moop" in NAVIGATOR_SYSTEM_PROMPT.lower()
    assert "never ask" in NAVIGATOR_SYSTEM_PROMPT.lower()
    assert "what today's date is" in NAVIGATOR_SYSTEM_PROMPT.lower()
    assert "remaining_year_budget_cost_low" in NAVIGATOR_SYSTEM_PROMPT
    assert "do not append the general disclaimer" in NAVIGATOR_SYSTEM_PROMPT.lower()
    assert "exception — insulin" in NAVIGATOR_SYSTEM_PROMPT.lower()
    assert "omit dosage" in NAVIGATOR_SYSTEM_PROMPT.lower()


def test_navigator_system_prompt_includes_runtime_datetime():
    prompt = build_navigator_system_prompt()
    assert "Current date and time" in prompt
    assert "Never ask the user to confirm today's date" in prompt


@pytest.mark.asyncio
async def test_navigator_moop_question_scope_refusal():
    message = (
        f"I want to know the comparison of Max OOP for in and out of network for "
        f"{PLAN_FL_MAPD_MOOP}."
    )
    response = await navigator.run(message)
    lower = response.explanation.lower()

    assert "lookup_plan" in response.tools_invoked
    assert "estimate_drug_cost" not in response.tools_invoked
    assert "estimate_drug_cost_all_channels" not in response.tools_invoked
    assert "which drug" not in lower
    assert "pharmacy channel" not in lower
    assert "moop" in lower or "maximum out-of-pocket" in lower
    assert response.status == "ok"


@pytest.mark.asyncio
async def test_navigator_generic_any_plan_oop_question():
    response = await navigator.run("for any plan, what is my max oop according to the cms?")
    lower = response.explanation.lower()
    assert response.status == "ok"
    assert "lookup_plan" not in response.tools_invoked
    assert "get_part_d_benefit_params" in response.tools_invoked
    assert "2,100" in response.explanation or "2100" in response.explanation
    assert "h1889" not in lower


@pytest.mark.asyncio
async def test_navigator_part_d_annual_cap_question():
    response = await navigator.run(
        "what is the CMS Part D annual out-of-pocket maximum for 2026?"
    )
    assert response.status == "ok"
    assert "get_part_d_benefit_params" in response.tools_invoked
    assert "2,100" in response.explanation or "2100" in response.explanation


@pytest.mark.asyncio
async def test_navigator_multi_drug_basket_produces_two_channel_estimates():
    message = f"What's the cost for metformin 500mg and lisinopril 10mg on plan {PLAN_FL_PDP}?"
    response = await navigator.run(message)
    assert response.status == "ok"
    assert len(response.channel_estimates) == 2
    drug_names = {e.drug_name for e in response.channel_estimates}
    assert drug_names == {"metformin", "lisinopril"}
    assert response.tools_invoked.count("estimate_drug_cost_all_channels") == 1
    lower = response.explanation.lower()
    assert "metformin" in lower
    assert "lisinopril" in lower


@pytest.mark.asyncio
async def test_navigator_multi_drug_turn_retains_all_drugs_in_session_last_tool_calls():
    """Regression: a two-drug turn used to leave session["last_tool_call"] pointing at
    only whichever drug's tool call ran last, so a follow-up question about "the
    calculation" only had context for one of the two drugs. It must now retain both."""
    session_id = "test-multi-drug-session"
    message = f"What's the cost for metformin 500mg and lisinopril 10mg on plan {PLAN_FL_PDP}?"
    await navigator.run(message, session_id=session_id)

    session = session_manager.get_or_create(session_id)
    last_calls = session.get("last_tool_calls") or []
    assert len(last_calls) == 2
    drug_names = {c["arguments"].get("drug_name") for c in last_calls}
    assert drug_names == {"metformin", "lisinopril"}

    context = _format_last_tool_calls(last_calls)
    assert "metformin" in context.lower()
    assert "lisinopril" in context.lower()


def test_extract_explicit_ytd_from_message_parses_natural_language_follow_up():
    assert _extract_explicit_ytd_from_message(
        "what if I've already spent $800 on prescriptions this year?"
    ) == 800.0
    assert _extract_explicit_ytd_from_message("how much for metformin") is None


@pytest.mark.asyncio
async def test_navigator_ytd_follow_up_without_drug_name_updates_recalculation():
    """Regression: a drug-less follow-up stating an explicit YTD dollar amount used to be
    silently ignored (ytd_oop_spend stayed 0.0) because only the insulin/mixed-basket
    resolvers ran deterministic YTD extraction. It must now flow into filter_slots for
    every turn, including the general single-drug agent-loop path — verified here via the
    mixed-basket resolver, which reads filter_slots.ytd_oop_spend downstream of the merge."""
    session_id = "test-ytd-follow-up-session"
    opener = f"What's the cost for metformin 500mg and lantus on plan {PLAN_FL_PDP}?"
    await navigator.run(opener, session_id=session_id)

    from medicare_navigator.models.query import QuerySlots

    follow_up = (
        f"what if I've already spent $800 on prescriptions this year? "
        f"metformin 500mg and lantus on plan {PLAN_FL_PDP}"
    )
    response = await navigator.run(
        follow_up,
        filter_slots=QuerySlots(raw_message=follow_up),
        session_id=session_id,
    )
    assert response.status == "ok"
    assert response.channel_estimates
    assert all(e.ytd_oop_spend == 800.0 for e in response.channel_estimates)


def test_format_last_tool_calls_single_call_uses_singular_phrasing():
    calls = [
        {
            "name": "estimate_drug_cost_all_channels",
            "arguments": {"plan_key": "S1234-001", "drug_name": "metformin"},
        }
    ]
    context = _format_last_tool_calls(calls)
    assert context.startswith("Last cost estimate call:")
    assert "metformin" in context
    assert "days_supply you used" in context


def test_format_last_tool_calls_multiple_calls_lists_every_drug():
    calls = [
        {
            "name": "estimate_drug_cost_all_channels",
            "arguments": {"plan_key": "S1234-001", "drug_name": "metformin"},
        },
        {
            "name": "estimate_drug_cost_all_channels",
            "arguments": {"plan_key": "S1234-001", "drug_name": "lovastatin"},
        },
    ]
    context = _format_last_tool_calls(calls)
    assert "metformin" in context
    assert "lovastatin" in context
    assert "not just one" in context.lower()


def test_merge_last_tool_calls_retains_prior_drugs_on_partial_reestimate():
    prior = [
        {
            "name": "estimate_drug_cost_all_channels",
            "arguments": {
                "plan_key": "S5921-400",
                "drug_name": "lovastatin",
                "dosage": "40mg",
                "ytd_oop_spend": 0,
            },
        },
        {
            "name": "estimate_drug_cost_all_channels",
            "arguments": {
                "plan_key": "S5921-400",
                "drug_name": "metformin",
                "dosage": "500mg",
                "ytd_oop_spend": 0,
            },
        },
    ]
    new_calls = [
        {
            "name": "estimate_drug_cost_all_channels",
            "arguments": {
                "plan_key": "S5921-400",
                "drug_name": "metformin",
                "dosage": "500mg",
                "ytd_oop_spend": 0,
            },
        }
    ]
    merged = _merge_last_tool_calls(prior, new_calls)
    drug_names = {(c["arguments"].get("drug_name")) for c in merged}
    assert drug_names == {"lovastatin", "metformin"}
    metformin_call = next(c for c in merged if c["arguments"]["drug_name"] == "metformin")
    assert metformin_call is new_calls[0]


@pytest.mark.asyncio
async def test_navigator_plan_comparison_produces_two_entries_no_recommendation_language():
    message = (
        f"Compare lovastatin 40mg cost between plan {PLAN_FL_PDP} and plan {PLAN_FL_MAPD}."
    )
    response = await navigator.run(message)
    assert response.status == "ok"
    assert len(response.channel_estimates) == 2
    plan_keys = {e.plan_key for e in response.channel_estimates}
    assert plan_keys == {PLAN_FL_PDP, PLAN_FL_MAPD}

    lower = response.explanation.lower()
    for banned in ("best plan", "cheapest overall", "you should switch", "recommend switching"):
        assert banned not in lower
    assert "not a recommendation to switch plans" in lower


@pytest.mark.asyncio
async def test_navigator_partial_channel_plan_explanation_qualifies_missing_channels():
    message = f"What's the cost for metformin 500mg on plan {PLAN_FL_PARTIAL_CHANNELS}?"
    response = await navigator.run(message)
    assert response.status == "ok"
    assert response.channel_estimates or response.channel_estimate
    estimates = response.channel_estimates or (
        [response.channel_estimate] if response.channel_estimate else []
    )
    est = estimates[0]
    missing = [name for name, ch in est.channels.items() if ch.cost_low is None]
    assert missing, "fixture plan should have partial channel coverage"

    lower = response.explanation.lower()
    for phrase in (
        "all cms pharmacy channel",
        "all four channel",
        "all pharmacy channel",
        "every channel",
        "all channels",
    ):
        assert phrase not in lower
    assert "standard retail" in lower or "no matching estimate" in lower


@pytest.mark.asyncio
async def test_navigator_plan_comparison_partial_vs_full_channels_no_overclaim():
    message = (
        f"Compare metformin 500mg cost between plan {PLAN_FL_PARTIAL_CHANNELS} "
        f"and plan {PLAN_FL_PDP}."
    )
    response = await navigator.run(message)
    assert response.status == "ok"
    assert len(response.channel_estimates) == 2

    lower = response.explanation.lower()
    for phrase in ("all cms pharmacy channel", "all four channel", "all channels"):
        assert phrase not in lower
    assert "not a recommendation to switch plans" in lower


def test_llm_requires_configuration(monkeypatch):
    monkeypatch.setattr(settings, "llm_mock_mode", False)
    monkeypatch.setattr(settings, "anthropic_api_key", "")
    monkeypatch.setattr(settings, "openai_api_key", "")
    with pytest.raises(LLMNotConfiguredError):
        llm_client.require_available()


@pytest.mark.asyncio
async def test_navigator_declines_pure_off_topic_joke_request():
    response = await navigator.run("tell me a joke")
    assert "joke" not in response.explanation.lower() or "medicare" in response.explanation.lower()
    assert "medicare" in response.explanation.lower()
    assert response.response_source == "System"
    assert settings.disclaimer_text in response.explanation


@pytest.mark.asyncio
async def test_navigator_off_topic_weather_includes_disclaimer_inline():
    response = await navigator.run("What's the weather in Miami today?")
    assert response.response_source == "System"
    assert settings.disclaimer_text in response.explanation


def test_format_history_includes_all_max_chat_turns():
    from medicare_navigator.agent.navigator import _format_history

    history = []
    for turn in range(1, 6):
        history.append({"role": "user", "content": f"user turn {turn}"})
        history.append({"role": "assistant", "content": f"assistant turn {turn}"})
    formatted = _format_history(history)
    assert "user turn 1" in formatted
    assert "user turn 5" in formatted
