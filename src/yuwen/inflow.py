"""把每轮对话在后台写成一条记忆。"""

from __future__ import annotations

from importlib.resources import files
import json
from typing import Any

from yuwen.chat import ChatModel
from yuwen.store import Store


def _score(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if 0 <= number <= 1 else None


class InflowService:
    def __init__(self, store: Store, llm: ChatModel):
        self.store = store
        self.llm = llm
        self._template = files("yuwen.prompts").joinpath("inflow.md").read_text(encoding="utf-8")

    def _prompt(self, user: dict[str, Any], assistant: dict[str, Any]) -> str:
        return (
            self._template.replace("{玩家原话}", str(user["content"]))
            .replace("{回复}", str(assistant["content"]))
            .replace("{这一轮最后一条消息的时间}", str(assistant["created_at"]))
            .replace("{玩家}", "玩家")
            .replace("{角色名}", "角色")
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

    async def process_turn(self, user_message_id: int, assistant_message_id: int) -> int:
        """模型异常时仍按降级路径写入记忆。"""

        user = self.store.get_message(user_message_id)
        assistant = self.store.get_message(assistant_message_id)
        if user is None or assistant is None:
            raise ValueError("流入找不到对应的原话")
        try:
            raw = await self.llm.chat(
                [{"role": "user", "content": self._prompt(user, assistant)}],
                small=True,
                json_response=True,
            )
            memory = self._parse(raw)
        except Exception:
            memory = {
                "summary": str(user["content"])[:100],
                "quote": str(user["content"])[:100] or None,
                "emotion_text": "未标注",
                "valence": None,
                "arousal": None,
                "certainty": 0.5,
            }
        return self.store.add_memory(
            kind="turn",
            happened_at=str(assistant["created_at"]),
            summary=str(memory["summary"]),
            quote=memory["quote"] if isinstance(memory["quote"], str) else None,
            emotion_text=str(memory["emotion_text"]),
            valence=memory["valence"] if isinstance(memory["valence"], float) else None,
            arousal=memory["arousal"] if isinstance(memory["arousal"], float) else None,
            certainty=float(memory["certainty"]),
            source_message_ids=[user_message_id, assistant_message_id],
            session_id=int(assistant["session_id"]),
        )
