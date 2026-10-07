"""OOC 守门人与语感笔记。"""

from __future__ import annotations

from dataclasses import dataclass
from importlib.resources import files
import json
from typing import Any, Protocol

from yuwen.store import Store
from yuwen.worldbook import WorldbookReader


class GatekeeperModel(Protocol):
    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        small: bool = False,
        json_response: bool = False,
    ) -> str: ...


@dataclass(frozen=True, slots=True)
class GatekeeperResult:
    verdict: str
    reason: str
    rewritten: str | None
    voice_note_id: int | None = None


class GatekeeperService:
    def __init__(self, store: Store, worldbook: WorldbookReader, llm: GatekeeperModel):
        self.store = store
        self.worldbook = worldbook
        self.llm = llm
        self._template = (
            files("yuwen.prompts")
            .joinpath("ooc-gatekeeper.md")
            .read_text(encoding="utf-8")
        )

    def _prompt(self, reply: str, recent: list[dict[str, str]]) -> str:
        recent_text = "\n".join(
            f"{message['role']}：{message['content']}" for message in recent[-6:]
        ) or "暂无"
        return (
            self._template.replace("{角色名}", "这个角色")
            .replace("{世界书}", self.worldbook.read())
            .replace("{重铸层变化，没有则写“暂无”}", "暂无")
            .replace("{最近 6 条原话}", recent_text)
            .replace("{回复}", reply)
        )

    @staticmethod
    def _parse(text: str) -> GatekeeperResult:
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("守门人结果必须是 JSON 对象")
        verdict = data.get("verdict")
        if verdict == "in_character":
            return GatekeeperResult("in_character", "", None)
        reason = data.get("reason")
        rewritten = data.get("rewritten")
        if (
            verdict != "ooc"
            or not isinstance(reason, str)
            or not reason.strip()
            or not isinstance(rewritten, str)
            or not rewritten.strip()
        ):
            raise ValueError("守门人结果字段无效")
        return GatekeeperResult("ooc", reason.strip(), rewritten.strip())

    async def review(
        self,
        reply: str,
        recent: list[dict[str, str]],
    ) -> GatekeeperResult:
        """任何模型异常都降级为原样发出。"""

        try:
            raw = await self.llm.chat(
                [{"role": "user", "content": self._prompt(reply, recent)}],
                small=True,
                json_response=True,
            )
            return self._parse(raw)
        except Exception:
            return GatekeeperResult("in_character", "", None)

    def save_note(
        self,
        *,
        source: str,
        message_id: int,
        original: str,
        rewritten: str,
        reason: str,
    ) -> int:
        return self.store.add_voice_note(
            source=source,
            message_id=message_id,
            original=original,
            rewritten=rewritten,
            reason=reason,
        )

    @staticmethod
    def _recent_rows(rows: list[dict[str, Any]]) -> list[dict[str, str]]:
        return [
            {"role": str(row["role"]), "content": str(row["content"])}
            for row in rows
        ]

    async def flag_message(
        self,
        message_id: int,
        player_rewrite: str | None,
    ) -> GatekeeperResult:
        message = self.store.get_message(message_id)
        if message is None or message["role"] != "assistant":
            raise ValueError("找不到这条角色回复")
        recent = self._recent_rows(
            self.store.list_tail_messages(int(message["session_id"]), 6)
        )
        reviewed = await self.review(str(message["content"]), recent)
        if player_rewrite is None:
            if reviewed.verdict == "in_character":
                return GatekeeperResult(
                    "in_character",
                    "守门人认为这句没有偏离角色。",
                    None,
                )
            rewritten = reviewed.rewritten
            assert rewritten is not None
            reason = reviewed.reason
        else:
            if not player_rewrite.strip():
                raise ValueError("玩家改写不能为空")
            rewritten = player_rewrite
            reason = reviewed.reason or "玩家认为这句不符合角色语感"

        note_id = self.save_note(
            source="player",
            message_id=message_id,
            original=str(message["content"]),
            rewritten=rewritten,
            reason=reason,
        )
        return GatekeeperResult("ooc", reason, rewritten, note_id)
