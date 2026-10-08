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
        scan_depth: int = 4,
        entry_budget: int = 3000,
        corpus_sample: int = 8,
    ):
        self.store = store
        self.worldbook = worldbook
        self.voice_notes_limit = voice_notes_limit
        self.scan_depth = scan_depth
        self.entry_budget = entry_budget
        self.corpus_sample = corpus_sample

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

    def _as_model_messages(self, rows: list[dict[str, Any]]) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = []
        for row in rows:
            role = str(row["role"])
            content = self.store.visible_message_content(row)
            if role == "event":
                role = "user"
                content = f"[事件] {content}"
            messages.append({"role": role, "content": content})
        return messages

    def build(self, session_id: int, *, surfaced: str | None = None) -> list[dict[str, str]]:
        session = self.store.get_session(session_id)
        if session is None:
            raise ValueError(f"找不到窗口：{session_id}")
        handoff_messages = self._handoff_messages(session)
        current_messages = self._as_model_messages(self.store.list_messages(session_id))
        recent = [
            message["content"]
            for message in [*handoff_messages, *current_messages]
            if message["role"] in {"user", "assistant"}
        ]
        messages: list[dict[str, str]] = [
            {"role": "system", "content": self.worldbook.core()}
        ]
        for entry in self.worldbook.match_entries(
            recent,
            scan_depth=self.scan_depth,
            entry_budget=self.entry_budget,
        ):
            messages.append(
                {"role": "system", "content": f"词条：{entry.name}\n{entry.content}"}
            )
        corpus = self.worldbook.sample_corpus(self.corpus_sample)
        if corpus:
            messages.append(
                {
                    "role": "system",
                    "content": (
                        "参考这些句子的感觉，用他的方式说新的话，不要原句复述：\n"
                        + "\n".join(corpus)
                    ),
                }
            )
        voice_notes = self._voice_notes_message()
        if voice_notes is not None:
            messages.append(voice_notes)
        if surfaced:
            recall_message = {"role": "system", "content": surfaced}
            if handoff_messages and handoff_messages[0]["role"] == "system":
                handoff_messages.insert(1, recall_message)
            else:
                handoff_messages.insert(0, recall_message)
        messages.extend(handoff_messages)
        messages.extend(current_messages)
        return messages
