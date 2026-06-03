"""JSON Schema definitions for MCP / LLM tool calling."""

TOOL_SCHEMAS: list[dict] = [
    {
        "name": "estimate_drug_cost",
        "description": (
            "Estimate the out-of-pocket cost of a single drug fill on a Medicare plan's regular "
            "formulary. Runs the full resolve-plan -> resolve-drug -> formulary -> pricing -> "
            "cost-share pipeline server-side and returns a cost range plus any required caveats "
            "(quantity limits, prior authorization/step therapy, multi-NDC pricing spread, "
            "unconfirmed coinsurance base). Also used to route insulin and suppressed-plan "
            "requests to their required out-of-scope / hard-stop messages — call this whenever "
            "the user asks what a drug will cost on a plan, even before you know if it's covered."
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
