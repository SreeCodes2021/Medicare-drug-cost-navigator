from __future__ import annotations

import re

from medicare_navigator.models.query import ParsedQuery, QuerySlots

_INTENT_KEYWORDS: dict[str, list[str]] = {
    "alternatives": ["alternative", "generic", "equivalent"],
    "explain_cost_change": ["why", "change", "went up", "increase", "explain"],
    "tier_lookup": ["tier", "copay", "cost share", "cost-sharing"],
}


class InputMerger:
    @staticmethod
    def _spend_mentioned(raw_message: str) -> bool:
        return bool(
            re.search(r"(?:spent|spend)\s+\$?\s*\d", raw_message, re.I)
            or re.search(r"already\s+(?:spent|spend)\s+\$?\s*\d", raw_message, re.I)
            or re.search(r"\$\d+(?:\.\d+)?\s+ytd", raw_message, re.I)
            or re.search(r"what if i'?ve spent", raw_message, re.I)
        )

    @staticmethod
    def _message_signals_intent(message: str) -> set[str]:
        text = message.lower()
        detected: set[str] = set()
        for intent, keywords in _INTENT_KEYWORDS.items():
            if any(kw in text for kw in keywords):
                detected.add(intent)
        return detected

    @staticmethod
    def merge(
        chat_slots: QuerySlots,
        filter_slots: QuerySlots | None = None,
        session_slots: QuerySlots | None = None,
        raw_message: str = "",
    ) -> QuerySlots:
        base = QuerySlots()
        if session_slots:
            base = session_slots.model_copy(deep=True)
        if filter_slots:
            for field, value in filter_slots.model_dump(exclude_none=True).items():
                if field == "ytd_oop_spend" and value in (0, 0.0):
                    continue
                if value is not None and value != "":
                    setattr(base, field, value)

        spend_mentioned = InputMerger._spend_mentioned(raw_message)

        for field, value in chat_slots.model_dump(exclude_none=True).items():
            if field in ("raw_message", "intents"):
                continue
            if field == "ytd_oop_spend" and value == 0.0 and not spend_mentioned:
                continue
            if value is not None and value != "":
                setattr(base, field, value)

        if chat_slots.raw_message:
            base.raw_message = chat_slots.raw_message

        session_intents = set(session_slots.intents if session_slots and session_slots.intents else [])
        new_intents = set(chat_slots.intents or [])
        message_intents = InputMerger._message_signals_intent(raw_message or chat_slots.raw_message)

        if message_intents:
            base.intents = sorted(session_intents | new_intents | message_intents)
        elif new_intents and new_intents != {"tier_lookup"}:
            base.intents = sorted(session_intents | new_intents)
        elif session_intents:
            base.intents = sorted(session_intents)
        elif new_intents:
            base.intents = sorted(new_intents)
        else:
            base.intents = ["tier_lookup"]

        return base

    @staticmethod
    def slots_unchanged(previous: QuerySlots | None, current: QuerySlots) -> bool:
        if not previous:
            return False
        keys = ("drug", "dosage", "plan_id", "ytd_oop_spend", "contract_year")
        for key in keys:
            if getattr(previous, key) != getattr(current, key):
                return False
        return True

    @staticmethod
    def to_parsed_query(slots: QuerySlots, drug_data: dict | None = None) -> ParsedQuery | None:
        if not slots.drug and not drug_data:
            return None

        plan_key = slots.plan_id
        contract_id = None
        plan_segment_id = None
        if plan_key and "-" in plan_key:
            parts = plan_key.split("-", 1)
            contract_id = parts[0]
            plan_segment_id = parts[1]

        ytd = slots.ytd_oop_spend if slots.ytd_oop_spend is not None else 0.0
        ytd_provided = InputMerger._spend_mentioned(slots.raw_message) or (
            slots.ytd_oop_spend is not None and slots.ytd_oop_spend != 0.0
        )

        return ParsedQuery(
            drug_name=drug_data.get("drug_name", slots.drug) if drug_data else (slots.drug or ""),
            rxcui=drug_data.get("rxcui") if drug_data else None,
            ndc=drug_data.get("ndc") if drug_data else None,
            dosage=drug_data.get("dosage", slots.dosage) if drug_data else slots.dosage,
            plan_key=plan_key,
            contract_id=contract_id,
            plan_segment_id=plan_segment_id,
            contract_year=slots.contract_year or 2026,
            ytd_oop_spend=ytd,
            ytd_oop_spend_provided=ytd_provided,
            pharmacy_channel=slots.pharmacy_channel or "preferred_retail",
            days_supply=slots.days_supply or 30,
            include_alternatives=slots.include_alternatives if slots.include_alternatives is not None else True,
            include_cost_trend=slots.include_cost_trend if slots.include_cost_trend is not None else True,
            intents=slots.intents or ["tier_lookup"],
            raw_message=slots.raw_message,
        )
