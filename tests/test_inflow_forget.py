import asyncio
import json

from yuwen.config import ForgetConfig
from yuwen.inflow import InflowService
from yuwen.store import Store


class FakeLLM:
    def __init__(self, result):
        self.result = result
        self.prompt = ""

    async def chat(self, messages, *, small=False, json_response=False):
        self.prompt = messages[0]["content"]
        return self.result


def setup(tmp_path):
    store = Store(tmp_path / "test.db")
    store.initialize()
    session = store.create_session()
    prior = store.add_memory(
        kind="turn", happened_at="2026-10-20T10:00:00+08:00", summary="之前的心事",
        emotion_text="难过", certainty=0.9, source_message_ids=[], session_id=session,
    )
    todo = store.add_memory(
        kind="turn", happened_at="2026-10-20T11:00:00+08:00", summary="牙还没看",
        emotion_text="担心", certainty=0.9, source_message_ids=[], session_id=session,
        status="open", status_as_of="2026-10-20T11:00:00+08:00",
    )
    user = store.add_message(session, "user", "那个忘掉吧", created_at="2026-10-21T10:00:00+08:00")
    reply = store.add_message(
        session, "assistant", "好，不提了", created_at="2026-10-21T10:01:00+08:00",
        meta={"surfaced_memory_ids": [prior]},
    )
    return store, prior, todo, user, reply


def test_forget_this_batch_seals_history_skips_new_todos_and_resolves(tmp_path):
    store, prior, todo, user, reply = setup(tmp_path)
    llm = FakeLLM(json.dumps({
        "summary": "说了心事", "emotion_text": "难过", "certainty": 0.9,
        "used_memory_ids": [prior],
        "forget": {"this_batch": True, "ids": [prior, todo], "reason": "对方不想再提"},
        "todos": [{"summary": "新待续", "quote": ""}],
        "resolved": [{"id": todo, "status": "done", "note": "看完牙"}],
    }))
    memory_id = asyncio.run(InflowService(store, llm).process_turn(user, reply))
    assert "【可以封起来的往事】" in llm.prompt
    assert store.get_memory(memory_id)["sealed"] == 1
    assert store.get_memory(prior)["sealed"] == 1
    assert store.get_memory(prior)["recall_count"] == 1
    assert store.list_memory_history(prior)[0]["snapshot"]
    assert json.loads(store.list_memory_history(prior)[0]["snapshot"])["recall_count"] == 1
    assert store.list_memory_history(memory_id)[0]["reason"] == "对话里说忘掉：对方不想再提"
    assert store.get_memory(todo)["status"] == "done"
    assert store.get_memory(todo)["sealed"] == 1
    assert len(store.list_memory_history(todo)) == 2
    assert len(store.list_table("memories")) == 3


def test_forget_only_listed_ids_and_no_repeat_history(tmp_path):
    store, prior, _, user, reply = setup(tmp_path)
    unlisted = store.add_memory(
        kind="turn", happened_at="2026-10-01T10:00:00+08:00", summary="很早的事",
        emotion_text="平静", certainty=0.9, source_message_ids=[], session_id=store.get_message(user)["session_id"],
    )
    llm = FakeLLM(json.dumps({
        "summary": "说了心事", "emotion_text": "平静", "certainty": 0.9,
        "forget": {"this_batch": False, "ids": [prior, unlisted], "reason": "别再提"},
    }))
    asyncio.run(InflowService(store, llm, forget=ForgetConfig(recent_memories=0)).process_turn(user, reply))
    assert store.get_memory(prior)["sealed"] == 1
    assert store.get_memory(unlisted)["sealed"] == 0
    assert len(store.list_memory_history(prior)) == 1
    assert store.set_memory_sealed(prior, True, reason="对话里说忘掉：别再提") is False
    assert len(store.list_memory_history(prior)) == 1
    session = store.get_message(user)["session_id"]
    next_user = store.add_message(session, "user", "这件事也别再提")
    next_reply = store.add_message(session, "assistant", "好")
    asyncio.run(InflowService(store, llm, forget=ForgetConfig(recent_memories=0)).process_turn(next_user, next_reply))
    assert len(store.list_memory_history(prior)) == 1


def test_invalid_forget_and_fallback_leave_memory_unsealed(tmp_path):
    for result in (
        json.dumps({"summary": "聊了", "emotion_text": "平静", "certainty": 0.9,
                    "forget": {"this_batch": "yes", "ids": [1], "reason": "忘掉"}}),
        json.dumps({"summary": "聊了", "emotion_text": "平静", "certainty": 0.9,
                    "forget": {"this_batch": True, "ids": [True], "reason": "忘掉"}}),
        json.dumps({"summary": "聊了", "emotion_text": "平静", "certainty": 0.9,
                    "forget": {"this_batch": True, "reason": "忘掉"}}),
        "bad json",
    ):
        path = tmp_path / str(abs(hash(result)))
        store, prior, _, user, reply = setup(path)
        memory_id = asyncio.run(InflowService(store, FakeLLM(result)).process_turn(user, reply))
        assert store.get_memory(memory_id)["sealed"] == 0
        assert store.get_memory(prior)["sealed"] == 0


def test_forget_disabled_removes_prompt_and_ignores_result(tmp_path):
    store, prior, _, user, reply = setup(tmp_path)
    llm = FakeLLM(json.dumps({
        "summary": "聊了", "emotion_text": "平静", "certainty": 0.9,
        "forget": {"this_batch": True, "ids": [prior], "reason": "忘掉"},
    }))
    memory_id = asyncio.run(InflowService(store, llm, forget=ForgetConfig(enabled=False)).process_turn(user, reply))
    assert "【可以封起来的往事】" not in llm.prompt
    assert '"forget"' not in llm.prompt
    assert store.get_memory(memory_id)["sealed"] == 0
    assert store.get_memory(prior)["sealed"] == 0
