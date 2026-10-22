import asyncio
import json

from yuwen.config import AnniversaryConfig
from yuwen.inflow import InflowService
from yuwen.store import Store


class FakeLLM:
    def __init__(self, data):
        self.data = data
        self.prompt = ""

    async def chat(self, messages, *, small=False, json_response=False):
        self.prompt = messages[0]["content"]
        return json.dumps(self.data, ensure_ascii=False)


def test_inflow_anniversaries_validate_limit_and_dedupe(tmp_path):
    store = Store(tmp_path / "db")
    store.initialize()
    session = store.create_session()
    user = store.add_message(session, "user", "记下这些日子")
    reply = store.add_message(session, "assistant", "记下了")
    llm = FakeLLM({
        "summary": "说了纪年", "emotion_text": "开心", "certainty": 0.9,
        "anniversaries": [
            {"date": "02-30", "title": "错日", "yearly": True},
            {"date": "02-29", "title": "闰日", "yearly": True},
            {"date": "2026-11-12", "title": "约定", "yearly": False},
        ],
    })
    memory_id = asyncio.run(InflowService(store, llm).process_turn(user, reply))
    rows = store.list_anniversaries()
    assert len(rows) == 1  # 只看前两件，坏的丢掉
    assert rows[0]["month_day"] == "02-29"
    assert rows[0]["memory_id"] == memory_id
    assert rows[0]["source"] == "conversation"

    user2 = store.add_message(session, "user", "还有两天")
    reply2 = store.add_message(session, "assistant", "嗯")
    llm.data["anniversaries"] = [
        {"date": "02-29", "title": "闰日", "yearly": True},
        {"date": "11-12", "title": "没年份", "yearly": False},
    ]
    asyncio.run(InflowService(store, llm).process_turn(user2, reply2))
    assert len(store.list_anniversaries()) == 1


def test_inflow_anniversaries_once_and_disabled(tmp_path):
    store = Store(tmp_path / "db")
    store.initialize()
    session = store.create_session()
    user = store.add_message(session, "user", "演唱会")
    reply = store.add_message(session, "assistant", "好")
    llm = FakeLLM({
        "summary": "约好演唱会", "emotion_text": "期待", "certainty": 0.9,
        "anniversaries": [{"date": "2026-11-12", "title": "演唱会", "yearly": False}],
    })
    asyncio.run(InflowService(store, llm, anniversary=AnniversaryConfig(False)).process_turn(user, reply))
    assert '"anniversaries"' not in llm.prompt
    assert store.list_anniversaries() == []
    user2 = store.add_message(session, "user", "再说一次")
    reply2 = store.add_message(session, "assistant", "好")
    asyncio.run(InflowService(store, llm).process_turn(user2, reply2))
    assert store.list_anniversaries()[0]["year"] == 2026
    assert store.list_anniversaries()[0]["yearly"] == 0


def test_inflow_anniversaries_only_first_two_valid_entries(tmp_path):
    store = Store(tmp_path / "db")
    store.initialize()
    session = store.create_session()
    user = store.add_message(session, "user", "三个日子")
    reply = store.add_message(session, "assistant", "记住了")
    llm = FakeLLM({
        "summary": "说了三个日子", "emotion_text": "开心", "certainty": 0.9,
        "anniversaries": [
            {"date": "01-01", "title": "元旦", "yearly": True},
            {"date": "2026-11-12", "title": "约定", "yearly": False},
            {"date": "12-25", "title": "第三个", "yearly": True},
        ],
    })
    asyncio.run(InflowService(store, llm).process_turn(user, reply))
    assert {row["title"] for row in store.list_anniversaries()} == {"元旦", "约定"}
