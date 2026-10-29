import asyncio
import json

import pytest

from yuwen.inflow import InflowService
from yuwen.store import Store


class FakeLLM:
    def __init__(self, result):
        self.result = result

    async def chat(self, messages, *, small=False, json_response=False):
        return json.dumps(self.result, ensure_ascii=False)


def write_turn(tmp_path, spoken, result, *, at="2026-10-20T10:00:00+08:00"):
    store = Store(tmp_path / "test.db")
    store.initialize()
    session = store.create_session()
    user = store.add_message(session, "user", spoken, created_at=at)
    reply = store.add_message(session, "assistant", "好，我记着。", created_at=at)
    memory_id = asyncio.run(InflowService(
        store, FakeLLM({"emotion_text": "平静", "certainty": 0.9, **result}),
        player_name="阿栀", character_name="沈砚",
    ).process_turn(user, reply))
    return store, memory_id


@pytest.mark.parametrize("summary", [
    "阿栀和李明说了这件事",
    "阿栀去了北京",
    "阿栀说2026年11月12日见",
])
def test_invented_person_place_or_date_lowers_certainty(tmp_path, summary):
    store, memory_id = write_turn(tmp_path, "我们下次再说", {"summary": summary})
    assert store.get_memory(memory_id)["certainty"] == 0.3


def test_grounded_facts_keep_certainty_across_date_formats(tmp_path):
    store, memory_id = write_turn(
        tmp_path, "李明说11月12日去北京找阿栀",
        {"summary": "李明说11-12去北京找阿栀"},
    )
    assert store.get_memory(memory_id)["certainty"] == 0.9


def test_existing_low_certainty_is_not_raised(tmp_path):
    store, memory_id = write_turn(
        tmp_path, "我们下次再说", {"summary": "去了上海", "certainty": 0.2},
    )
    assert store.get_memory(memory_id)["certainty"] == 0.2


def test_only_todos_supported_by_their_own_clause_are_written(tmp_path):
    store, memory_id = write_turn(
        tmp_path, "明天去看牙，昨天喝了茶",
        {"summary": "阿栀聊了看牙和喝茶", "todos": [
            {"summary": "阿栀明天去看牙", "quote": "明天去看牙"},
            {"summary": "阿栀昨天喝了茶", "quote": "昨天喝了茶"},
            {"summary": "阿栀明天去上海", "quote": "明天去上海"},
            {"summary": "阿栀明天去买房", "quote": "明天去看牙"},
        ]},
    )
    todos = [row for row in store.list_table("memories") if row["status"] == "open"]
    assert [row["summary"] for row in todos] == ["阿栀明天去看牙"]
    assert memory_id is not None


@pytest.mark.parametrize("spoken,note", [
    ("今天聊聊电影", "看完牙了"),
    ("牙看完了", "李明陪着看完牙了"),
])
def test_unspoken_or_invented_resolution_does_not_change_todo(tmp_path, spoken, note):
    store = Store(tmp_path / "test.db")
    store.initialize()
    session = store.create_session()
    todo_id = store.add_memory(
        kind="turn", happened_at="2026-10-19T10:00:00+08:00", summary="阿栀要看牙",
        emotion_text="平静", certainty=0.9, source_message_ids=[], session_id=session,
        status="open", status_as_of="2026-10-19T10:00:00+08:00",
    )
    user = store.add_message(session, "user", spoken)
    reply = store.add_message(session, "assistant", "好")
    result = {"summary": "聊了电影", "emotion_text": "平静", "certainty": 0.9,
              "resolved": [{"id": todo_id, "status": "done", "note": note}]}
    asyncio.run(InflowService(store, FakeLLM(result)).process_turn(user, reply))
    assert store.get_memory(todo_id)["status"] == "open"
    assert store.list_memory_history(todo_id) == []


def test_anniversary_date_must_match_spoken_or_relative_day(tmp_path):
    store, _ = write_turn(
        tmp_path, "明天是第一次见面纪念日，11月12日也要记住",
        {"summary": "说了纪年", "anniversaries": [
            {"date": "2026-10-21", "title": "第一次见面", "yearly": True},
            {"date": "2026-11-12", "title": "编出的年份", "yearly": True},
        ]},
    )
    assert [row["title"] for row in store.list_anniversaries()] == ["第一次见面"]


def test_anniversary_rejects_mismatched_date(tmp_path):
    store, _ = write_turn(
        tmp_path, "11月12日是纪念日",
        {"summary": "说了纪年", "anniversaries": [
            {"date": "11-13", "title": "错日", "yearly": True},
        ]},
    )
    assert store.list_anniversaries() == []
