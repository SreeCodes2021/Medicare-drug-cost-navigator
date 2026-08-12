"""JSON Schema definitions for MCP / LLM tool calling."""

TOOL_SCHEMAS: list[dict] = [
    {
        "name": "estimate_drug_cost",
        "description": (
            "Estimate the out-of-pocket cost of a single drug fill on a Medicare plan's regular "
            "formulary. Runs the full resolve-plan -> resolve-drug -> formulary -> pricing -> "
            "cost-share pipeline server-side and returns a cost range plus any required caveats "
            "(quantity limits, prior authorization/step therapy, multi-NDC pricing spread, "
            "unconfirmed coinsurance base). Insulin is priced via its separate statutory "
            "$35-per-30-day-supply cap (benefit_phase reads insulin_cap) rather than the tiered/"
            "deductible pipeline. Also used to route suppressed-plan requests, and the narrower "
            "case of an insulin drug with no published CMS cost-share record for this plan/tier, "
            "to their required hard-stop messages — call this whenever the user asks what a "
            "single drug will cost on a plan, even before you know if it's covered. "
            "Multi-product insulin requests are resolved by the application layer and produce "
            "one estimate per named product."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "plan_key": {"type": "string", "description": "Exact plan key, e.g. S5678-012"},
                "drug_name": {"type": "string", "description": "Drug name, e.g. lisinopril"},
                "dosage": {
                    "type": "string",
                    "description": "Optional strength, e.g. 10mg (not quantity like '10 pieces')",
                },
                "days_supply": {
                    "type": "integer",
                    "description": "Requested days supply per fill (default 30)",
                    "default": 30,
                },
                "ytd_oop_spend": {
                    "type": "number",
                    "description": "Beneficiary's year-to-date out-of-pocket spend (default 0)",
                    "default": 0,
                },
                "pharmacy_channel": {
                    "type": "string",
                    "description": "preferred_retail | standard_retail | preferred_mail | standard_mail",
                    "default": "preferred_retail",
                },
            },
            "required": ["plan_key", "drug_name"],
        },
    },
    {
        "name": "estimate_drug_cost_all_channels",
        "description": (
            "Estimate out-of-pocket cost for all four CMS pharmacy channels "
            "(preferred_retail, standard_retail, preferred_mail, standard_mail) in one "
            "deterministic call. Returns per-channel cost ranges, deductible, tier, "
            "DED_APPLIES_YN, benefit phase, and effective phase after tier exemption. "
            "This call estimates one named product; multi-product insulin requests use one "
            "call per product."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "plan_key": {"type": "string", "description": "Exact plan key, e.g. S5678-012"},
                "drug_name": {"type": "string", "description": "Drug name, e.g. lisinopril"},
                "dosage": {
                    "type": "string",
                    "description": "Optional strength, e.g. 10mg",
                },
                "days_supply": {
                    "type": "integer",
                    "description": "Requested days supply per fill (default 30)",
                    "default": 30,
                },
                "ytd_oop_spend": {
                    "type": "number",
                    "description": "Beneficiary's year-to-date out-of-pocket spend (default 0)",
                    "default": 0,
                },
            },
            "required": ["plan_key", "drug_name"],
        },
    },
    {
        "name": "lookup_plan",
        "description": "Look up a Medicare plan by exact plan_key or fuzzy search text.",
        "parameters": {
            "type": "object",
            "properties": {
                "plan_key": {
                    "type": "string",
                    "description": "Exact plan key, e.g. S5678-012",
                },
                "search_text": {
                    "type": "string",
                    "description": "Fuzzy plan name or ID fragment when plan_key unknown",
                },
            },
        },
    },
    {
        "name": "list_plans",
        "description": "List Medicare Part D / MA-PD plans with optional filters.",
        "parameters": {
            "type": "object",
            "properties": {
                "plan_type": {"type": "string"},
                "state": {"type": "string"},
                "contract_year": {"type": "integer"},
            },
        },
    },
    {
        "name": "get_part_d_benefit_params",
        "description": (
            "Return the CMS statutory Part D annual out-of-pocket maximum for a contract year. "
            "Use when the user asks about the Part D drug-benefit OOP cap — not Medicare "
            "Advantage medical-network MOOP limits."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "contract_year": {
                    "type": "integer",
                    "description": "Medicare contract year (default: current data year, e.g. 2026)",
                },
            },
        },
    },
]


def openai_tools() -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": schema["name"],
                "description": schema["description"],
                "parameters": schema["parameters"],
            },
        }
        for schema in TOOL_SCHEMAS
    ]


def anthropic_tools() -> list[dict]:
    return [
        {
            "name": schema["name"],
            "description": schema["description"],
            "input_schema": schema["parameters"],
        }
        for schema in TOOL_SCHEMAS
    ]
