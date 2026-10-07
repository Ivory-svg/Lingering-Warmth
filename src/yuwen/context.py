"""按文档规定的顺序拼装模型上下文。"""

from __future__ import annotations

import json
from typing import Any

from yuwen.store import Store
from yuwen.worldbook import WorldbookReader


class ContextBuilder:
    def __init__(
        self,
        store: Store,
        worldbook: WorldbookReader,
        *,
        voice_notes_limit: int = 10,
    ):
        self.store = store
        self.worldbook = worldbook
        self.voice_notes_limit = voice_notes_limit

    def _voice_notes_message(self) -> dict[str, str] | None:
        notes = self.store.list_voice_notes(
            active_only=True,
            limit=self.voice_notes_limit,
        )
        if not notes:
            return None
        lines = ["这些是你要记住的语感笔记："]
        for note in reversed(notes):
            lines.append(
                f"原来想说：{note['original']} / "
                f"他会说：{note['rewritten']} / 因为：{note['reason']}"
            )
        return {"role": "system", "content": "\n".join(lines)}

    def _handoff_messages(self, session: dict[str, Any]) -> list[dict[str, str]]:
        handoff_id = session.get("continues_handoff_id")
        if handoff_id is None:
            return []
        handoff = self.store.get_handoff(int(handoff_id))
        if handoff is None:
            return []
        result = [
            {
                "role": "system",
                "content": (
                    "上一窗口留给你的交接：\n"
                    f"正在做：{handoff['doing']}\n"
                    f"聊到：{handoff['where_we_are']}\n"
                    f"此刻情绪：{handoff['emotion']}"
                ),
            }
        ]
        try:
            raw_ids = json.loads(handoff["raw_tail_message_ids"])
        except (TypeError, json.JSONDecodeError):
            raw_ids = []
        if isinstance(raw_ids, list) and all(isinstance(item, int) for item in raw_ids):
            result.extend(self._as_model_messages(self.store.get_messages_by_ids(raw_ids)))
        return result

    @staticmethod
    def _as_model_messages(rows: list[dict[str, Any]]) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = []
        for row in rows:
            role = str(row["role"])
            content = str(row["content"])
            if role == "event":
                role = "user"
                content = f"[事件] {content}"
            messages.append({"role": role, "content": content})
        return messages

    def build(self, session_id: int) -> list[dict[str, str]]:
        session = self.store.get_session(session_id)
        if session is None:
            raise ValueError(f"找不到窗口：{session_id}")
        messages: list[dict[str, str]] = [
            {"role": "system", "content": self.worldbook.read()}
        ]
        voice_notes = self._voice_notes_message()
        if voice_notes is not None:
            messages.append(voice_notes)
        messages.extend(self._handoff_messages(session))
        messages.extend(self._as_model_messages(self.store.list_messages(session_id)))
        return messages
