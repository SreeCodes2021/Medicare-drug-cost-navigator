from __future__ import annotations

from medicare_navigator.agent.navigator import navigator
from medicare_navigator.models.query import QuerySlots
from medicare_navigator.models.response import QueryResponse


class OrchestratorRouter:
    async def run(
        self,
        message: str,
        filter_slots: QuerySlots | None = None,
        session_id: str | None = None,
    ) -> QueryResponse:
        return await navigator.run(message, filter_slots=filter_slots, session_id=session_id)


orchestrator = OrchestratorRouter()
