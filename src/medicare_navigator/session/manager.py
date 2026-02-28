from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from medicare_navigator.config import settings
from medicare_navigator.models.query import QuerySlots

_sessions: dict[str, dict] = {}

MAX_HISTORY_TURNS = 5


class SessionManager:
    def get_or_create(self, session_id: str | None = None) -> dict:
        if session_id and session_id in _sessions:
            session = _sessions[session_id]
            if datetime.utcnow() < session["expires_at"]:
                return session
        sid = session_id or str(uuid.uuid4())
        session = {
            "session_id": sid,
            "turn_count": 0,
            "slots": QuerySlots(),
            "parsed_query": None,
            "tool_artifacts": {},
            "last_tool_artifacts": {},
            "chat_history": [],
            "expires_at": datetime.utcnow() + timedelta(minutes=settings.session_ttl_minutes),
        }
        _sessions[sid] = session
        return session

    def increment_turn(self, session: dict) -> None:
        session["turn_count"] += 1

    def can_continue(self, session: dict) -> bool:
        return session["turn_count"] < settings.max_chat_turns

    def append_turn(
        self,
        session: dict,
        user_msg: str,
        assistant_msg: str,
        query_id: str | None = None,
    ) -> None:
        session["chat_history"].append(
            {"role": "user", "content": user_msg, "query_id": query_id}
        )
        session["chat_history"].append({"role": "assistant", "content": assistant_msg})
        max_messages = MAX_HISTORY_TURNS * 2
        if len(session["chat_history"]) > max_messages:
            session["chat_history"] = session["chat_history"][-max_messages:]


session_manager = SessionManager()
