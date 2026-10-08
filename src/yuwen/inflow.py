"""把每轮对话在后台写成一条记忆。"""

from __future__ import annotations

import asyncio
from importlib.resources import files
import json
from typing import Any

from yuwen.chat import ChatModel
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
    ):
        self.store = store
        self.llm = llm
        self.character_name = character_name
        self.player_name = player_name
        self.every_n_turns = every_n_turns
        self.embedder = embedder
        self._lock = asyncio.Lock()
        self._template = files("yuwen.prompts").joinpath("inflow.md").read_text(encoding="utf-8")

    def _prompt(self, turns: list[tuple[dict[str, Any], dict[str, Any]]]) -> str:
        dialogue = "\n".join(
            f"{self.player_name}：{user['content']}\n"
            f"{self.character_name}：{assistant['content']}"
            for user, assistant in turns
        )
        return (
            self._template.replace("{轮数}", str(len(turns)))
            .replace("{对话原文}", dialogue)
            .replace("{这一轮最后一条消息的时间}", str(turns[-1][1]["created_at"]))
            .replace("{玩家}", self.player_name)
            .replace("{角色名}", self.character_name)
        )

    @staticmethod
    def _parse(text: str) -> dict[str, object]:
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("流入结果必须是 JSON 对象")
        summary = data.get("summary")
        emotion_text = data.get("emotion_text")
        certainty = _score(data.get("certainty"))
        if not isinstance(summary, str) or not summary.strip():
            raise ValueError("流入结果缺少 summary")
        if not isinstance(emotion_text, str) or not emotion_text.strip():
            raise ValueError("流入结果缺少 emotion_text")
        if certainty is None:
            raise ValueError("流入结果的 certainty 无效")
        quote = data.get("quote")
        return {
            "summary": summary.strip(),
            "quote": quote if isinstance(quote, str) and quote else None,
            "emotion_text": emotion_text.strip(),
            "valence": _score(data.get("valence")),
            "arousal": _score(data.get("arousal")),
            "certainty": certainty,
        }

    async def _summarize(
        self, turns: list[tuple[dict[str, Any], dict[str, Any]]]
    ) -> int:
        """将一批完整轮次写成一条记忆；模型出错时保留原话摘要。"""

        try:
            raw = await self.llm.chat(
                [{"role": "user", "content": self._prompt(turns)}],
                small=True,
                json_response=True,
            )
            memory = self._parse(raw)
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
                raise ValueError("流入找不到对应的原话")
            meta = json.loads(assistant["meta"]) if assistant["meta"] else {}
            linked_user_id = meta.get("inflow_user_message_id")
            if linked_user_id is None:
                meta["inflow_user_message_id"] = user_message_id
                self.store.update_message_meta(assistant_message_id, meta)
            elif linked_user_id != user_message_id:
                raise ValueError("流入的玩家原话与回复不匹配")
            session_id = int(assistant["session_id"])
            pending = self.store.list_pending_inflow_turns(session_id)
            last_memory_id = None
            while len(pending) >= self.every_n_turns:
                last_memory_id = await self._summarize(pending[: self.every_n_turns])
                pending = self.store.list_pending_inflow_turns(session_id)
            return last_memory_id

    async def flush_pending(self, session_id: int) -> list[int]:
        """交接前把本窗口没攒满的完整轮次也写下来。"""

        async with self._lock:
            memory_ids: list[int] = []
            pending = self.store.list_pending_inflow_turns(session_id)
            while pending:
                memory_ids.append(await self._summarize(pending[: self.every_n_turns]))
                pending = self.store.list_pending_inflow_turns(session_id)
            return memory_ids
