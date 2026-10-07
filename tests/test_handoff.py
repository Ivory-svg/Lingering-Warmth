import asyncio
import json
from pathlib import Path

from yuwen.chat import ChatService
from yuwen.config import CharacterConfig
from yuwen.context import ContextBuilder
from yuwen.handoff import HandoffService
from yuwen.store import Store
from yuwen.worldbook import WorldbookReader


class SequenceLLM:
    def __init__(self, replies: list[str]):
        self.replies = iter(replies)
        self.calls: list[tuple[list[dict[str, str]], bool, bool]] = []

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        small: bool = False,
        json_response: bool = False,
    ) -> str:
        self.calls.append((messages, small, json_response))
        return next(self.replies)


def parts(tmp_path: Path) -> tuple[Store, ContextBuilder]:
    prompt = tmp_path / "character.md"
    prompt.write_text("角色设定很长，足以触发测试交接。", encoding="utf-8")
    store = Store(tmp_path / "test.db")
    store.initialize()
    context = ContextBuilder(
        store,
        WorldbookReader(CharacterConfig(prompt, tmp_path / "worldbook")),
    )
    return store, context


def test_handoff_closes_old_session_and_continues_with_tail(tmp_path: Path) -> None:
    store, context = parts(tmp_path)
    llm = SequenceLLM(
        [
            json.dumps(
                {
                    "doing": "陪她准备面试",
                    "where_we_are": "刚答应模拟一遍",
                    "emotion": "担心，也想让她安心",
                },
                ensure_ascii=False,
            ),
            "先从自我介绍开始。",
        ]
    )
    handoff = HandoffService(
        store,
        llm,
        context_limit=1,
        trigger_ratio=1,
        raw_tail=20,
    )
    service = ChatService(store, context, llm, handoff=handoff)

    result = asyncio.run(service.handle_message("明天要面试"))

    assert result.handoff_happened
    assert result.session_id == 2
    assert store.get_session(1)["ended_at"] is not None  # type: ignore[index]
    new_session = store.get_session(2)
    assert new_session["continues_handoff_id"] == 1  # type: ignore[index]
    assert store.list_messages(2)[0]["content"] == "先从自我介绍开始。"
    assert store.list_table("memories")[0]["kind"] == "handoff"
    handoff_call, chat_call = llm.calls
    assert handoff_call[1:] == (False, True)
    assert "这条消息来自系统" in handoff_call[0][-1]["content"]
    assert any(message["content"] == "明天要面试" for message in chat_call[0])
    assert any("刚答应模拟一遍" in message["content"] for message in chat_call[0])


def test_handoff_does_not_run_below_threshold(tmp_path: Path) -> None:
    store, context = parts(tmp_path)
    llm = SequenceLLM(["普通回复"])
    handoff = HandoffService(
        store,
        llm,
        context_limit=100_000,
        trigger_ratio=0.75,
        raw_tail=20,
    )

    result = asyncio.run(ChatService(store, context, llm, handoff=handoff).handle_message("你好"))

    assert not result.handoff_happened
    assert result.session_id == 1
    assert len(llm.calls) == 1
