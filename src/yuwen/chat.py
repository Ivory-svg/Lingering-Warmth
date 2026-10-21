"""一轮对话的总流程。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Protocol

from yuwen.context import ContextBuilder
from yuwen.gatekeeper import GatekeeperService
from yuwen.handoff import HandoffService
from yuwen.store import Store
from yuwen.surfacing import SurfacingService


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
    ooc: dict[str, str | int] | None = None
    handoff_happened: bool = False
    handoff_memory_id: int | None = None

    def as_dict(self) -> dict[str, object]:
        data = asdict(self)
        data.pop("user_message_id")
        data.pop("handoff_memory_id")
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
        surfacing: SurfacingService | None = None,
    ):
        self.store = store
        self.context = context
        self.llm = llm
        self.handoff = handoff
        self.gatekeeper = gatekeeper
        self.surfacing = surfacing

    async def handle_message(self, content: str) -> ChatResult:
        """保存原话、生成并保存回复。"""

        if not content.strip():
            raise ValueError("消息内容不能为空")
        session = self.store.get_or_create_active_session()
        session_id = int(session["id"])
        user_message_id = self.store.add_message(session_id, "user", content)
        first_todos = self.context.find_open_todos(session_id)
        first_recall = (
            await self.surfacing.find(
                session_id, content,
                exclude_ids=first_todos.memory_ids if first_todos else (),
            )
            if self.surfacing is not None else None
        )
        messages = self.context.build(
            session_id, surfaced=first_recall.text if first_recall else None,
            todo_text=first_todos.text if first_todos else None,
        )
        handoff_happened = False
        handoff_memory_id = None
        final_recall = first_recall
        final_todos = first_todos
        if self.handoff is not None:
            handoff = await self.handoff.maybe_handoff(session_id, messages)
            session_id = handoff.session_id
            handoff_happened = handoff.happened
            handoff_memory_id = handoff.memory_id
        if handoff_happened:
            final_todos = self.context.find_open_todos(session_id)
            final_recall = (
                await self.surfacing.find(
                    session_id, content,
                    exclude_ids=final_todos.memory_ids if final_todos else (),
                )
                if self.surfacing is not None else None
            )
            messages = self.context.build(
                session_id, surfaced=final_recall.text if final_recall else None,
                todo_text=final_todos.text if final_todos else None,
            )
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
        reply_meta = {
            "ooc_rewritten": ooc_rewritten,
            "inflow_user_message_id": user_message_id,
            "surfaced_memory_ids": [
                *(final_recall.memory_ids if final_recall else ()),
                *(final_todos.memory_ids if final_todos else ()),
            ],
        }
        message_id = self.store.add_message(
            session_id,
            "assistant",
            reply,
            meta=reply_meta,
        )
        ooc: dict[str, str | int] | None = None
        if ooc_rewritten and gatekeeper_result is not None and self.gatekeeper is not None:
            note_id = self.gatekeeper.save_note(
                source="gatekeeper",
                message_id=message_id,
                original=original_reply,
                rewritten=reply,
                reason=gatekeeper_result.reason,
            )
            self.store.update_message_meta(
                message_id,
                {**reply_meta, "voice_note_id": note_id},
            )
            ooc = {
                "original": original_reply,
                "reason": gatekeeper_result.reason,
                "voice_note_id": note_id,
            }
        return ChatResult(
            user_message_id=user_message_id,
            message_id=message_id,
            reply=reply,
            session_id=session_id,
            ooc_rewritten=ooc_rewritten,
            ooc=ooc,
            handoff_happened=handoff_happened,
            handoff_memory_id=handoff_memory_id,
        )
