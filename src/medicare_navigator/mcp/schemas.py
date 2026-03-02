"""JSON Schema definitions for MCP / LLM tool calling."""

TOOL_SCHEMAS: list[dict] = [
    {
        "name": "normalize_drug",
        "description": (
            "Resolve a drug name (and optional dosage) to RxCUI, NDC, and candidate matches."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "drug_name": {"type": "string", "description": "Drug name, e.g. lisinopril"},
                "dosage": {
                    "type": "string",
                    "description": "Optional strength, e.g. 10mg (not quantity like '10 pieces')",
                },
            },
            "required": ["drug_name"],
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
        "description": "List demo Medicare Part D / MA-PD plans with optional filters.",
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
        "name": "formulary_benefit_lookup",
        "description": (
            "Look up formulary tier, cost-sharing, benefit phase, and optional supply cost "
            "estimate for a drug NDC on a plan."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "plan_key": {"type": "string"},
                "ndc": {"type": "string"},
                "ytd_oop_spend": {"type": "number", "default": 0},
                "ytd_oop_spend_provided": {"type": "boolean", "default": False},
                "contract_year": {"type": "integer", "default": 2026},
                "quantity": {
                    "type": "integer",
                    "description": "Number of tablets/units for supply estimate",
                },
                "fills": {
                    "type": "integer",
                    "description": "Number of pharmacy fills for supply estimate",
                },
                "days_supply": {
                    "type": "integer",
                    "description": "Days supply per fill (default 30)",
                },
            },
            "required": ["plan_key", "ndc"],
        },
    },
    {
        "name": "cost_trend_lookup",
        "description": "Multi-year program spending / unit cost trend for a drug by RxCUI.",
        "parameters": {
            "type": "object",
            "properties": {
                "rxcui": {"type": "string"},
            },
            "required": ["rxcui"],
        },
    },
    {
        "name": "alternatives_finder",
        "description": "Find therapeutically equivalent alternative drugs by RxCUI.",
        "parameters": {
            "type": "object",
            "properties": {
                "rxcui": {"type": "string"},
            },
            "required": ["rxcui"],
        },
    },
    {
        "name": "policy_retrieval",
        "description": "Retrieve CMS/regulatory policy passages relevant to a query.",
        "parameters": {
            "type": "object",
            "properties": {
                "query_text": {"type": "string"},
            },
            "required": ["query_text"],
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
