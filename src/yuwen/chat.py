"""一轮对话的总流程。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Protocol

from yuwen.context import ContextBuilder
from yuwen.gatekeeper import GatekeeperService
from yuwen.handoff import HandoffService
from yuwen.store import Store


class ChatModel(Protocol):
    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        small: bool = False,
        json_response: bool = False,
    ) -> str: ...


@dataclass(frozen=True, slots=True)
class ChatResult:
    user_message_id: int
    message_id: int
    reply: str
    session_id: int
    ooc_rewritten: bool = False
    handoff_happened: bool = False

    def as_dict(self) -> dict[str, object]:
        data = asdict(self)
        data.pop("user_message_id")
        return data


class ChatService:
    def __init__(
        self,
        store: Store,
        context: ContextBuilder,
        llm: ChatModel,
        *,
        handoff: HandoffService | None = None,
        gatekeeper: GatekeeperService | None = None,
    ):
        self.store = store
        self.context = context
        self.llm = llm
        self.handoff = handoff
        self.gatekeeper = gatekeeper

    async def handle_message(self, content: str) -> ChatResult:
        """保存原话、生成并保存回复。"""

        if not content.strip():
            raise ValueError("消息内容不能为空")
        session = self.store.get_or_create_active_session()
        session_id = int(session["id"])
        user_message_id = self.store.add_message(session_id, "user", content)
        messages = self.context.build(session_id)
        handoff_happened = False
        if self.handoff is not None:
            handoff = await self.handoff.maybe_handoff(session_id, messages)
            session_id = handoff.session_id
            handoff_happened = handoff.happened
            if handoff_happened:
                messages = self.context.build(session_id)
        original_reply = await self.llm.chat(messages)
        gatekeeper_result = None
        if self.gatekeeper is not None:
            recent = [
                message for message in messages if message["role"] in {"user", "assistant"}
            ][-6:]
            gatekeeper_result = await self.gatekeeper.review(original_reply, recent)
        ooc_rewritten = bool(
            gatekeeper_result is not None and gatekeeper_result.verdict == "ooc"
        )
        reply = (
            gatekeeper_result.rewritten
            if ooc_rewritten and gatekeeper_result is not None
            else original_reply
        )
        assert reply is not None
        message_id = self.store.add_message(
            session_id,
            "assistant",
            reply,
            meta={"ooc_rewritten": ooc_rewritten},
        )
        if ooc_rewritten and gatekeeper_result is not None:
            note_id = self.gatekeeper.save_note(  # type: ignore[union-attr]
                source="gatekeeper",
                message_id=message_id,
                original=original_reply,
                rewritten=reply,
                reason=gatekeeper_result.reason,
            )
            self.store.update_message_meta(
                message_id,
                {"ooc_rewritten": True, "voice_note_id": note_id},
            )
        return ChatResult(
            user_message_id=user_message_id,
            message_id=message_id,
            reply=reply,
            session_id=session_id,
            ooc_rewritten=ooc_rewritten,
            handoff_happened=handoff_happened,
        )
