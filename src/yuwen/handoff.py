"""上下文接近上限时写交接并开启新窗口。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from importlib.resources import files
import json
from typing import Protocol

from yuwen.llm import LLMError, estimate_tokens
from yuwen.store import Store, now_iso


class HandoffModel(Protocol):
    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        small: bool = False,
        json_response: bool = False,
    ) -> str: ...


@dataclass(frozen=True, slots=True)
class HandoffResult:
    session_id: int
    happened: bool
    memory_id: int | None = None


class HandoffService:
    def __init__(
        self,
        store: Store,
        llm: HandoffModel,
        *,
        context_limit: int,
        trigger_ratio: float,
        raw_tail: int,
        min_turns: int = 20,
        before_handoff: Callable[[int], Awaitable[object]] | None = None,
        player_name: str = "对方",
    ):
        self.store = store
        self.llm = llm
        self.context_limit = context_limit
        self.trigger_ratio = trigger_ratio
        self.raw_tail = raw_tail
        self.min_turns = min_turns
        self.before_handoff = before_handoff
        self._prompt = (
            files("yuwen.prompts")
            .joinpath("handoff.md")
            .read_text(encoding="utf-8")
            .replace("{玩家}", player_name)
        )

    def should_handoff(self, messages: Sequence[dict[str, str]]) -> bool:
        text = "\n".join(message["content"] for message in messages)
        return estimate_tokens(text) >= self.context_limit * self.trigger_ratio

    @staticmethod
    def _parse(text: str) -> dict[str, str]:
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("交接结果必须是 JSON 对象")
        result: dict[str, str] = {}
        for key in ("doing", "where_we_are", "emotion"):
            value = data.get(key)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"交接结果缺少 {key}")
            result[key] = value.strip()
        return result

    async def maybe_handoff(
        self,
        session_id: int,
        messages: list[dict[str, str]],
    ) -> HandoffResult:
        session = self.store.get_session(session_id)
        if session is None:
            raise ValueError(f"找不到窗口：{session_id}")
        if session["continues_handoff_id"] is not None:
            user_turns = sum(
                message["role"] == "user" for message in self.store.list_messages(session_id)
            )
            if user_turns < self.min_turns:
                return HandoffResult(session_id=session_id, happened=False)
        if not self.should_handoff(messages):
            return HandoffResult(session_id=session_id, happened=False)

        if self.before_handoff is not None:
            await self.before_handoff(session_id)

        degraded = False
        try:
            raw = await self.llm.chat(
                [*messages, {"role": "user", "content": self._prompt}],
                small=False,
                json_response=True,
            )
            content = self._parse(raw)
        except (LLMError, ValueError, TypeError):
            # 交接摘要缺失时仍要换窗，让原话继续陪着下一轮对话。
            content = {
                "doing": "未记录",
                "where_we_are": "未记录",
                "emotion": "未标注",
            }
            degraded = True
        tail = self.store.list_tail_messages(session_id, self.raw_tail)
        tail_ids = [int(message["id"]) for message in tail]
        created_at = now_iso()
        handoff_id = self.store.add_handoff(
            session_id=session_id,
            doing=content["doing"],
            where_we_are=content["where_we_are"],
            emotion=content["emotion"],
            raw_tail_message_ids=tail_ids,
            created_at=created_at,
        )
        memory_id = self.store.add_memory(
            kind="handoff",
            happened_at=created_at,
            summary=f"{content['doing']}；{content['where_we_are']}",
            emotion_text=content["emotion"],
            certainty=0.5 if degraded else 0.9,
            source_message_ids=tail_ids,
            session_id=session_id,
            created_at=created_at,
        )
        self.store.close_session(session_id, ended_at=created_at)
        new_session_id = self.store.create_session(
            continues_handoff_id=handoff_id,
            started_at=created_at,
        )
        return HandoffResult(session_id=new_session_id, happened=True, memory_id=memory_id)
