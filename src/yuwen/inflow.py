"""把每轮对话在后台写成一条随笔记忆。"""

from __future__ import annotations

import asyncio
from datetime import datetime
from importlib.resources import files
import json
from typing import Any

from yuwen.chat import ChatModel
from yuwen.config import TodoConfig
from yuwen.embedder import MemoryEmbedder
from yuwen.store import Store


def _score(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if 0 <= number <= 1 else None


class InflowService:
    def __init__(
        self,
        store: Store,
        llm: ChatModel,
        *,
        character_name: str = "他",
        player_name: str = "对方",
        every_n_turns: int = 1,
        embedder: MemoryEmbedder | None = None,
        todo: TodoConfig = TodoConfig(),
    ):
        self.store = store
        self.llm = llm
        self.character_name = character_name
        self.player_name = player_name
        self.every_n_turns = every_n_turns
        self.embedder = embedder
        self.todo = todo
        self._lock = asyncio.Lock()
        self._template = files("yuwen.prompts").joinpath("inflow.md").read_text(encoding="utf-8")

    def _surfaced_for_turns(
        self, turns: list[tuple[dict[str, Any], dict[str, Any]]]
    ) -> dict[int, str]:
        """收集实际递给回复的记忆，以及最后一次递给它的回复时间。"""

        surfaced: dict[int, str] = {}
        for _, assistant in turns:
            try:
                meta = json.loads(assistant["meta"]) if assistant["meta"] else {}
            except (TypeError, ValueError):
                continue
            ids = meta.get("surfaced_memory_ids") if isinstance(meta, dict) else None
            if not isinstance(ids, list) or any(type(memory_id) is not int for memory_id in ids):
                continue
            for memory_id in ids:
                if self.store.get_memory(memory_id) is not None:
                    surfaced[memory_id] = str(assistant["created_at"])
        return surfaced

    def _prompt(
        self,
        turns: list[tuple[dict[str, Any], dict[str, Any]]],
        surfaced: dict[int, str],
        open_todos: list[dict[str, Any]] | None = None,
    ) -> str:
        dialogue = "\n".join(
            f"{self.player_name}：{user['content']}\n"
            f"{self.character_name}：{assistant['content']}"
            for user, assistant in turns
        )
        template = self._template
        open_todos = open_todos or []
        if self.todo.enabled and open_todos:
            todo_lines = []
            for memory in open_todos:
                as_of = datetime.fromisoformat(str(memory["status_as_of"]))
                todo_lines.append(
                    f"[#{memory['id']}] {as_of.month}月{as_of.day}日：{memory['summary']}"
                )
            template = template.replace("{还没完结的事}", "\n".join(todo_lines))
        else:
            template = template.replace("\n【还没完结的事】\n{还没完结的事}\n", "")
        if surfaced:
            lines = []
            for memory_id in surfaced:
                memory = self.store.get_memory(memory_id)
                assert memory is not None
                happened = datetime.fromisoformat(str(memory["happened_at"]))
                line = f"[#{memory_id}] {happened.month}月{happened.day}日：{memory['summary']}"
                if memory["quote"]:
                    line += f"。原话「{memory['quote']}」"
                lines.append(line)
            template = template.replace("{递给他的往事}", "\n".join(lines))
        else:
            template = template.replace(
                "\n【这几轮里递给{角色名}的往事】\n{递给他的往事}\n", ""
            )
        lines = template.splitlines()
        lines = [
            line for line in lines
            if not (
                (line.startswith(' "used_memory_ids":') and not surfaced)
                or (line.startswith(' "todos":') and not self.todo.enabled)
                or (line.startswith(' "resolved":') and not (self.todo.enabled and open_todos))
            )
        ]
        field_indices = [
            index for index, line in enumerate(lines)
            if line.startswith('{"summary":') or line.startswith(' "')
        ]
        for index in field_indices:
            lines[index] = lines[index].rstrip(",}") + ("}" if index == field_indices[-1] else ",")
        template = "\n".join(lines)
        return (
            template.replace("{轮数}", str(len(turns)))
            .replace("{对话原文}", dialogue)
            .replace("{这一轮最后一条消息的时间}", str(turns[-1][1]["created_at"]))
            .replace("{玩家}", self.player_name)
            .replace("{角色名}", self.character_name)
        )

    @staticmethod
    def _parse(text: str, *, todo_enabled: bool = True, allow_resolved: bool = True) -> dict[str, object]:
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("随笔结果必须是 JSON 对象")
        summary = data.get("summary")
        emotion_text = data.get("emotion_text")
        certainty = _score(data.get("certainty"))
        if not isinstance(summary, str) or not summary.strip():
            raise ValueError("随笔结果缺少 summary")
        if not isinstance(emotion_text, str) or not emotion_text.strip():
            raise ValueError("随笔结果缺少 emotion_text")
        if certainty is None:
            raise ValueError("随笔结果的 certainty 无效")
        quote = data.get("quote")
        used = data.get("used_memory_ids")
        todos = data.get("todos") if todo_enabled else None
        resolved = data.get("resolved") if todo_enabled and allow_resolved else None
        todos = [
            item for item in todos
            if isinstance(item, dict)
            and isinstance(item.get("summary"), str)
            and isinstance(item.get("quote"), str)
        ] if isinstance(todos, list) else []
        resolved = [
            item for item in resolved
            if isinstance(item, dict)
            and type(item.get("id")) is int
            and item.get("status") in {"done", "dropped"}
            and isinstance(item.get("note"), str)
            and bool(item["note"].strip())
        ] if isinstance(resolved, list) else []
        return {
            "summary": summary.strip(),
            "quote": quote if isinstance(quote, str) and quote else None,
            "emotion_text": emotion_text.strip(),
            "valence": _score(data.get("valence")),
            "arousal": _score(data.get("arousal")),
            "certainty": certainty,
            "used_memory_ids": (
                used
                if isinstance(used, list) and all(type(item) is int for item in used)
                else []
            ),
            "todos": todos[:3],
            "resolved": resolved,
        }

    def _open_for_prompt(self, surfaced: dict[int, str]) -> list[dict[str, Any]]:
        if not self.todo.enabled:
            return []
        rows = self.store.list_open_todos()
        surfaced_rows = [row for row in rows if int(row["id"]) in surfaced]
        other_rows = [row for row in rows if int(row["id"]) not in surfaced]
        selected = surfaced_rows + other_rows[:max(0, self.todo.max_open_in_prompt - len(surfaced_rows))]
        return sorted(selected, key=lambda row: (str(row["status_as_of"]), int(row["id"])), reverse=True)

    async def _summarize(
        self, turns: list[tuple[dict[str, Any], dict[str, Any]]]
    ) -> int:
        """将一批完整轮次写成一条记忆；模型出错时保留原话摘要。"""

        surfaced = self._surfaced_for_turns(turns)
        open_todos = self._open_for_prompt(surfaced)
        try:
            raw = await self.llm.chat(
                [{"role": "user", "content": self._prompt(turns, surfaced, open_todos)}],
                small=True,
                json_response=True,
            )
            memory = self._parse(
                raw, todo_enabled=self.todo.enabled, allow_resolved=bool(open_todos)
            )
        except Exception:
            raw_users = "；".join(str(user["content"]) for user, _ in turns)
            memory = {
                "summary": raw_users[:100],
                "quote": str(turns[0][0]["content"])[:100] or None,
                "emotion_text": "未标注",
                "valence": None,
                "arousal": None,
                "certainty": 0.5,
            }
        last_assistant = turns[-1][1]
        source_ids = [int(row["id"]) for turn in turns for row in turn]
        memory_id = self.store.add_memory(
            kind="turn",
            happened_at=str(last_assistant["created_at"]),
            summary=str(memory["summary"]),
            quote=memory["quote"] if isinstance(memory["quote"], str) else None,
            emotion_text=str(memory["emotion_text"]),
            valence=memory["valence"] if isinstance(memory["valence"], float) else None,
            arousal=memory["arousal"] if isinstance(memory["arousal"], float) else None,
            certainty=float(memory["certainty"]),
            source_message_ids=source_ids,
            session_id=int(last_assistant["session_id"]),
        )
        used_ids = set(memory.get("used_memory_ids", [])) & surfaced.keys()
        for used_id in sorted(used_ids):
            self.store.mark_memories_recalled([used_id], recalled_at=surfaced[used_id])
        if self.todo.enabled:
            open_ids = {int(row["id"]) for row in open_todos}
            for item in memory.get("resolved", []):
                if item["id"] in open_ids:
                    self.store.resolve_todo_from_inflow(
                        item["id"], status=item["status"], note=item["note"].strip(),
                        resolved_at=str(last_assistant["created_at"]),
                    )
            for item in memory.get("todos", []):
                summary = item["summary"].strip()
                if not summary:
                    continue
                todo_id = self.store.add_memory(
                    kind="turn",
                    happened_at=str(last_assistant["created_at"]),
                    summary=summary,
                    quote=item["quote"] or None,
                    emotion_text=str(memory["emotion_text"]),
                    valence=memory["valence"] if isinstance(memory["valence"], float) else None,
                    arousal=memory["arousal"] if isinstance(memory["arousal"], float) else None,
                    certainty=float(memory["certainty"]),
                    source_message_ids=source_ids,
                    session_id=int(last_assistant["session_id"]),
                    status="open",
                    status_as_of=str(last_assistant["created_at"]),
                )
                if self.embedder is not None:
                    await self.embedder.embed_memory(todo_id)
        if self.embedder is not None:
            await self.embedder.embed_memory(memory_id)
        return memory_id

    async def process_turn(
        self, user_message_id: int, assistant_message_id: int
    ) -> int | None:
        """攒满配置的轮数再写记忆；单轮调用仍支持旧数据。"""

        async with self._lock:
            user = self.store.get_message(user_message_id)
            assistant = self.store.get_message(assistant_message_id)
            if user is None or assistant is None:
                raise ValueError("随笔找不到对应的原话")
            meta = json.loads(assistant["meta"]) if assistant["meta"] else {}
            linked_user_id = meta.get("inflow_user_message_id")
            if linked_user_id is None:
                meta["inflow_user_message_id"] = user_message_id
                self.store.update_message_meta(assistant_message_id, meta)
            elif linked_user_id != user_message_id:
                raise ValueError("随笔的玩家原话与回复不匹配")
            session_id = int(assistant["session_id"])
            pending = self.store.list_pending_inflow_turns(session_id)
            last_memory_id = None
            while len(pending) >= self.every_n_turns:
                last_memory_id = await self._summarize(pending[: self.every_n_turns])
                pending = self.store.list_pending_inflow_turns(session_id)
            return last_memory_id

    async def flush_pending(self, session_id: int) -> list[int]:
        """续写前把本窗口没攒满的完整轮次也写下来。"""

        async with self._lock:
            memory_ids: list[int] = []
            pending = self.store.list_pending_inflow_turns(session_id)
            while pending:
                memory_ids.append(await self._summarize(pending[: self.every_n_turns]))
                pending = self.store.list_pending_inflow_turns(session_id)
            return memory_ids
