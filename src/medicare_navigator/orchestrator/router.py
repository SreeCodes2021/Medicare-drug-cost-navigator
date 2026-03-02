from __future__ import annotations

from medicare_navigator.agent.navigator import navigator
from medicare_navigator.config import settings
from medicare_navigator.models.query import QuerySlots
from medicare_navigator.models.response import QueryResponse
from medicare_navigator.orchestrator.pipeline import Orchestrator

_legacy = Orchestrator()


class OrchestratorRouter:
    async def run(
        self,
        message: str,
        filter_slots: QuerySlots | None = None,
        session_id: str | None = None,
    ) -> QueryResponse:
        if settings.navigator_mode == "legacy_pipeline":
            return await _legacy.run(message, filter_slots=filter_slots, session_id=session_id)
        return await navigator.run(message, filter_slots=filter_slots, session_id=session_id)


orchestrator = OrchestratorRouter()
