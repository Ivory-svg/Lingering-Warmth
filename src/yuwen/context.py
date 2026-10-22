"""按文档规定的顺序拼装模型上下文。"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from yuwen.anniversaries import anniversary_label, local_today, todays_anniversaries
from yuwen.config import AnniversaryConfig, TodoConfig
from yuwen.store import Store
from yuwen.worldbook import WorldbookReader


@dataclass(frozen=True, slots=True)
class TodoResult:
    text: str
    memory_ids: tuple[int, ...]


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
        todo: TodoConfig = TodoConfig(),
        anniversary: AnniversaryConfig = AnniversaryConfig(),
        wake_timezone: str = "",
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ):
        self.store = store
        self.worldbook = worldbook
        self.voice_notes_limit = voice_notes_limit
        self.scan_depth = scan_depth
        self.entry_budget = entry_budget
        self.corpus_sample = corpus_sample
        self.todo = todo
        self.anniversary = anniversary
        self.wake_timezone = wake_timezone
        self.clock = clock

    def find_open_todos(self, session_id: int) -> TodoResult | None:
        """挑出不在眼前、没过期、也没在冷却中的待续。"""

        if not self.todo.enabled:
            return None
        session = self.store.get_session(session_id)
        if session is None:
            raise ValueError(f"找不到窗口：{session_id}")
        visible_ids = {int(row["id"]) for row in self.store.list_messages(session_id)}
        handoff_id = session["continues_handoff_id"]
        if handoff_id is not None:
            handoff = self.store.get_handoff(int(handoff_id))
            if handoff is not None:
                try:
                    raw_ids = json.loads(handoff["raw_tail_message_ids"])
                    if isinstance(raw_ids, list):
                        visible_ids.update(int(item) for item in raw_ids if type(item) is int)
                except (TypeError, ValueError):
                    pass
        now = self.clock()
        selected: list[dict[str, Any]] = []
        for memory in self.store.list_open_todos():
            try:
                as_of = datetime.fromisoformat(str(memory["status_as_of"]))
                last = memory["last_recalled_at"]
                source_ids = json.loads(memory["source_message_ids"])
                if now - as_of > timedelta(days=self.todo.stale_days):
                    continue
                if last and now - datetime.fromisoformat(str(last)) < timedelta(hours=self.todo.cooldown_hours):
                    continue
                if visible_ids.intersection(source_ids):
                    continue
            except (TypeError, ValueError):
                continue
            selected.append(memory)
            if len(selected) >= self.todo.max_in_context:
                break
        if not selected:
            return None
        lines = [
            "这些是你们之间还没完结的事。不用每次都提；隔了一阵、话题合适的时候，可以自然问一句现在怎么样了。不要说成已经做完了，也不要假装知道结果。",
            "",
        ]
        for memory in selected:
            as_of = datetime.fromisoformat(str(memory["status_as_of"]))
            lines.append(f"· {as_of.month}月{as_of.day}日说的：{memory['summary']}")
        return TodoResult("\n".join(lines), tuple(int(row["id"]) for row in selected))

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

    def anniversary_text(self) -> str | None:
        if not self.anniversary.enabled:
            return None
        today = local_today(self.wake_timezone, now=self.clock())
        rows = todays_anniversaries(self.store, today)
        if not rows:
            return None
        lines = ["今天是你们记着的日子。自然地记得就好，不用每句都提。", ""]
        lines.extend(f"· {anniversary_label(row, today)}" for row in rows)
        return "\n".join(lines)

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

    def build(
        self, session_id: int, *, surfaced: str | None = None,
        todo_text: str | None = None,
    ) -> list[dict[str, str]]:
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
        inserted = [
            {"role": "system", "content": text}
            for text in (surfaced, todo_text, self.anniversary_text()) if text
        ]
        if handoff_messages and handoff_messages[0]["role"] == "system":
            handoff_messages[1:1] = inserted
        else:
            handoff_messages[0:0] = inserted
        messages.extend(handoff_messages)
        messages.extend(current_messages)
        return messages
