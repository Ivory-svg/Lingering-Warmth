import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from test_chat_api import FakeLLM, make_parts
from test_inflow import ReplyLLM
from test_handoff import SequenceLLM
from test_surfacing import service as surfacing_service
from yuwen.chat import ChatService
from yuwen.config import TodoConfig
from yuwen.context import ContextBuilder
from yuwen.inflow import InflowService
from yuwen.handoff import HandoffService


NOW = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)


def add_todo(store, session_id, summary, *, at="2026-10-20T10:00:00+00:00", source_ids=None,
             sealed=False):
    return store.add_memory(
        kind="turn", happened_at=at, summary=summary, quote=None, emotion_text="平静",
        certainty=0.9, source_message_ids=source_ids or [], session_id=session_id,
        sealed=sealed, status="open", status_as_of=at,
    )


def test_context_filters_visible_cooled_stale_sealed_and_caps_todos(tmp_path: Path) -> None:
    _, store, books = make_parts(tmp_path)
    previous = store.create_session()
    current = store.create_session()
    tail_id = store.add_message(previous, "user", "上卷末尾的事")
    handoff_id = store.add_handoff(
        session_id=previous, doing="聊天", where_we_are="近况", emotion="平静",
        raw_tail_message_ids=[tail_id],
    )
    store.close_session(current)
    current = store.create_session(continues_handoff_id=handoff_id)
    visible_id = store.add_message(current, "user", "眼前的事")
    add_todo(store, current, "眼前的事", source_ids=[visible_id])
    add_todo(store, previous, "上卷末尾的事", source_ids=[tail_id])
    add_todo(store, previous, "很久以前的事", at="2026-09-01T10:00:00+00:00")
    add_todo(store, previous, "封存的事", sealed=True)
    cooled = add_todo(store, previous, "刚提过的事")
    store.mark_memories_recalled([cooled], recalled_at="2026-10-20T06:00:00+00:00")
    ids = [add_todo(store, previous, f"待续{i}") for i in range(4)]
    builder = ContextBuilder(store, books, clock=lambda: NOW)

    result = builder.find_open_todos(current)

    assert result is not None
    assert result.memory_ids == tuple(reversed(ids[-3:]))
    assert "待续3" in result.text and "眼前的事" not in result.text
    assert "刚提过的事" not in result.text and "很久以前的事" not in result.text
    assert ContextBuilder(store, books, todo=TodoConfig(enabled=False)).find_open_todos(current) is None


def test_chat_todo_is_once_in_context_and_reply_meta(tmp_path: Path) -> None:
    _, store, books = make_parts(tmp_path)
    old = store.create_session()
    todo_id = add_todo(store, old, "周五要去看牙")
    store.close_session(old)
    llm = FakeLLM("记得去看牙。")
    chat = ChatService(
        store, ContextBuilder(store, books, clock=lambda: NOW), llm,
        surfacing=surfacing_service(store),
    )

    result = asyncio.run(chat.handle_message("周五看牙这事怎么办？"))

    context = llm.calls[0]
    assert sum("周五要去看牙" in row["content"] for row in context) == 1
    assert "这些是你们之间还没完结的事" in "\n".join(row["content"] for row in context)
    assert json.loads(store.get_message(result.message_id)["meta"])["surfaced_memory_ids"] == [todo_id]


def test_used_todo_enters_cooldown_after_inflow(tmp_path: Path) -> None:
    _, store, books = make_parts(tmp_path)
    old = store.create_session()
    at = datetime.now(timezone.utc).isoformat()
    todo_id = add_todo(store, old, "周五要去看牙", at=at)
    store.close_session(old)
    context = ContextBuilder(store, books)
    chat = ChatService(store, context, FakeLLM("牙看得怎么样了？"))

    result = asyncio.run(chat.handle_message("周五的安排想起来了吗？"))
    assert todo_id in context.find_open_todos(result.session_id).memory_ids
    inflow = InflowService(store, ReplyLLM(json.dumps({
        "summary": "聊到看牙", "emotion_text": "平静", "certainty": 0.9,
        "used_memory_ids": [todo_id],
    }, ensure_ascii=False)))
    asyncio.run(inflow.process_turn(result.user_message_id, result.message_id))

    assert store.get_memory(todo_id)["recall_count"] == 1
    assert store.get_memory(todo_id)["last_recalled_at"] == store.get_message(result.message_id)["created_at"]
    assert context.find_open_todos(result.session_id) is None


def test_handoff_records_only_todos_in_final_context(tmp_path: Path) -> None:
    _, store, books = make_parts(tmp_path)
    session_id = store.create_session()
    source_id = store.add_message(session_id, "user", "周五要看牙")
    todo_id = add_todo(store, session_id, "周五要看牙", source_ids=[source_id])
    llm = SequenceLLM([
        '{"doing":"聊天","where_we_are":"说起看牙","emotion":"平静"}',
        "我记得你说过周五看牙。",
    ])
    context = ContextBuilder(store, books, clock=lambda: NOW)
    chat = ChatService(
        store, context, llm,
        handoff=HandoffService(store, llm, context_limit=1, trigger_ratio=1, raw_tail=1),
    )

    result = asyncio.run(chat.handle_message("然后呢？"))

    assert result.handoff_happened
    assert "这些是你们之间还没完结的事" not in str(llm.calls[0])
    assert "这些是你们之间还没完结的事" in str(llm.calls[1])
    assert json.loads(store.get_message(result.message_id)["meta"])["surfaced_memory_ids"] == [todo_id]
