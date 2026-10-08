import asyncio
from dataclasses import replace
import json
from pathlib import Path

from fastapi.testclient import TestClient
import httpx

from test_chat_api import make_parts
from yuwen.api import create_app
from yuwen.chat import ChatService
from yuwen.config import CharacterConfig
from yuwen.context import ContextBuilder
from yuwen.handoff import HandoffService
from yuwen.llm import LLMClient
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


def test_chat_continues_after_two_invalid_handoff_json_responses(tmp_path: Path) -> None:
    config, store, books = make_parts(tmp_path)
    config = replace(
        config,
        llm=replace(config.llm, context_limit=1),
        gatekeeper=replace(config.gatekeeper, pre_send=False),
    )
    replies = iter(
        [
            "无效的交接 JSON",
            "仍然不是 JSON",
            "先从自我介绍开始。",
            json.dumps(
                {
                    "summary": "她准备面试，他陪她练习。",
                    "quote": "明天要面试",
                    "emotion_text": "紧张，得到支持",
                    "certainty": 0.9,
                },
                ensure_ascii=False,
            ),
        ]
    )
    requests: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": next(replies)}}]},
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    model = LLMClient(config.llm, client=http_client)
    app = create_app(config, store=store, llm=model, worldbook=books)

    with TestClient(app) as client:
        response = client.post("/chat", json={"content": "明天要面试"})
    asyncio.run(http_client.aclose())

    assert response.status_code == 200
    assert response.json()["reply"] == "先从自我介绍开始。"
    assert response.json()["session_id"] == 2
    assert response.json()["handoff_happened"] is True
    assert len(requests) == 4
    assert all(request["response_format"] == {"type": "json_object"} for request in requests[:2])
    assert store.get_session(1)["ended_at"] is not None  # type: ignore[index]
    handoff = store.list_table("handoffs")[0]
    assert (handoff["doing"], handoff["where_we_are"], handoff["emotion"]) == (
        "未记录",
        "未记录",
        "未标注",
    )
    assert json.loads(handoff["raw_tail_message_ids"]) == [1]
    assert store.list_table("memories")[0]["certainty"] == 0.5
    assert [(row["role"], row["content"]) for row in store.list_table("messages")] == [
        ("user", "明天要面试"),
        ("assistant", "先从自我介绍开始。"),
    ]
    assert any(
        message["content"] == "明天要面试"
        for message in app.state.chat_service.context.build(2)
    )


def test_handoff_with_missing_json_fields_uses_fallback(tmp_path: Path) -> None:
    store, context = parts(tmp_path)
    llm = SequenceLLM(['{"doing":"准备面试"}', "我们继续练习。"])
    handoff = HandoffService(
        store,
        llm,
        context_limit=1,
        trigger_ratio=1,
        raw_tail=20,
    )

    result = asyncio.run(
        ChatService(store, context, llm, handoff=handoff).handle_message("先练一遍吧")
    )

    assert result.handoff_happened
    assert result.reply == "我们继续练习。"
    handoff_row = store.list_table("handoffs")[0]
    assert handoff_row["where_we_are"] == "未记录"
    assert handoff_row["emotion"] == "未标注"


def test_new_window_waits_for_minimum_user_turns_before_next_handoff(
    tmp_path: Path,
) -> None:
    store, context = parts(tmp_path)
    handoff_json = json.dumps(
        {"doing": "聊天", "where_we_are": "继续", "emotion": "平静"}, ensure_ascii=False
    )
    llm = SequenceLLM(
        [handoff_json, "第一句", "第二句", "第三句", handoff_json, "第四句"]
    )
    handoff = HandoffService(
        store, llm, context_limit=1, trigger_ratio=1, raw_tail=20, min_turns=3
    )
    chat = ChatService(store, context, llm, handoff=handoff)

    results = [
        asyncio.run(chat.handle_message(content))
        for content in ["第一轮", "第二轮", "第三轮", "第四轮"]
    ]

    assert [result.handoff_happened for result in results] == [True, False, False, True]
    assert [result.session_id for result in results] == [2, 2, 2, 3]
    assert sum(json_response for _, _, json_response in llm.calls) == 2
