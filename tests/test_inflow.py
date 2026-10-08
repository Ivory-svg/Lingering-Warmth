import asyncio
from dataclasses import replace
import json
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from test_chat_api import make_parts
from yuwen.api import create_app
from yuwen.handoff import HandoffService
from yuwen.inflow import InflowService
from yuwen.store import Store


class ReplyLLM:
    def __init__(self, reply: str):
        self.reply = reply
        self.options: list[tuple[bool, bool]] = []
        self.messages: list[list[dict[str, str]]] = []

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        small: bool = False,
        json_response: bool = False,
    ) -> str:
        self.options.append((small, json_response))
        self.messages.append(messages)
        return self.reply


def turn(store: Store) -> tuple[int, int]:
    session_id = store.create_session()
    user_id = store.add_message(session_id, "user", "今天加班好累")
    assistant_id = store.add_message(session_id, "assistant", "先去吃饭，我在。")
    return user_id, assistant_id


def test_inflow_writes_valid_model_result(tmp_path: Path) -> None:
    store = Store(tmp_path / "test.db")
    store.initialize()
    user_id, assistant_id = turn(store)
    llm = ReplyLLM(
        '{"summary":"她加班后很累，他让她先吃饭。","quote":"今天加班好累",'
        '"emotion_text":"疲惫，得到关心","valence":0.4,"arousal":0.3,"certainty":0.9}'
    )

    memory_id = asyncio.run(InflowService(store, llm).process_turn(user_id, assistant_id))
    memory = store.get_memory(memory_id)

    assert memory["kind"] == "turn"  # type: ignore[index]
    assert memory["certainty"] == 0.9  # type: ignore[index]
    assert llm.options == [(True, True)]


def test_inflow_garbled_result_uses_fallback_without_raising(tmp_path: Path) -> None:
    store = Store(tmp_path / "test.db")
    store.initialize()
    user_id, assistant_id = turn(store)

    memory_id = asyncio.run(
        InflowService(store, ReplyLLM("这不是 JSON")).process_turn(user_id, assistant_id)
    )
    memory = store.get_memory(memory_id)

    assert memory["summary"] == "今天加班好累"  # type: ignore[index]
    assert memory["emotion_text"] == "未标注"  # type: ignore[index]
    assert memory["certainty"] == 0.5  # type: ignore[index]


def test_inflow_prompt_uses_configured_names(tmp_path: Path) -> None:
    store = Store(tmp_path / "test.db")
    store.initialize()
    user_id, assistant_id = turn(store)
    llm = ReplyLLM("乱码")

    asyncio.run(
        InflowService(store, llm, character_name="林舟", player_name="小雨").process_turn(
            user_id, assistant_id
        )
    )

    prompt = llm.messages[0][0]["content"]
    assert "小雨：今天加班好累" in prompt
    assert "林舟：先去吃饭，我在。" in prompt


class BatchLLM:
    def __init__(self):
        self.calls: list[tuple[bool, bool, str]] = []

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        small: bool = False,
        json_response: bool = False,
    ) -> str:
        self.calls.append((small, json_response, messages[-1]["content"]))
        if small:
            return json.dumps(
                {
                    "summary": "三轮谈了晚饭",
                    "emotion_text": "平静",
                    "certainty": 0.9,
                },
                ensure_ascii=False,
            )
        if json_response:
            return json.dumps(
                {"doing": "聊天", "where_we_are": "晚饭", "emotion": "平静"},
                ensure_ascii=False,
            )
        return "先吃饭。"


def test_inflow_every_three_turns_writes_one_memory_with_all_sources(tmp_path: Path) -> None:
    config, store, books = make_parts(tmp_path)
    config = replace(
        config,
        gatekeeper=replace(config.gatekeeper, pre_send=False),
        inflow=replace(config.inflow, every_n_turns=3),
    )
    llm = BatchLLM()
    app = create_app(config, store=store, llm=llm, worldbook=books)

    with TestClient(app) as client:
        for number in range(1, 4):
            response = client.post("/chat", json={"content": f"第{number}轮"})
            assert response.status_code == 200
            assert len(store.list_table("memories")) == (1 if number == 3 else 0)

    memory = store.list_table("memories")[0]
    assert json.loads(memory["source_message_ids"]) == [1, 2, 3, 4, 5, 6]
    small_calls = [call for call in llm.calls if call[:2] == (True, True)]
    assert len(small_calls) == 1
    assert all(f"第{number}轮" in small_calls[0][2] for number in range(1, 4))


def test_handoff_flushes_partial_inflow_batch_first(tmp_path: Path) -> None:
    store = Store(tmp_path / "test.db")
    store.initialize()
    session_id = store.create_session()
    source_ids = []
    for number in range(2):
        user_id = store.add_message(session_id, "user", f"第{number}轮")
        assistant_id = store.add_message(
            session_id,
            "assistant",
            "先吃饭。",
            meta={"inflow_user_message_id": user_id},
        )
        source_ids.extend([user_id, assistant_id])
    store.add_message(session_id, "user", "触发交接的这轮还没有回复")
    llm = BatchLLM()
    inflow = InflowService(store, llm, every_n_turns=3)
    handoff = HandoffService(
        store,
        llm,
        context_limit=1,
        trigger_ratio=1,
        raw_tail=20,
        before_handoff=inflow.flush_pending,
    )

    result = asyncio.run(
        handoff.maybe_handoff(session_id, [{"role": "user", "content": "触发交接"}])
    )

    assert result.happened
    assert llm.calls[0][:2] == (True, True)
    assert llm.calls[1][:2] == (False, True)
    turn_memory = next(row for row in store.list_table("memories") if row["kind"] == "turn")
    assert json.loads(turn_memory["source_message_ids"]) == source_ids
    assert len(store.list_pending_inflow_turns(session_id)) == 0


def test_inflow_direct_calls_keep_pending_turns_and_do_not_duplicate(tmp_path: Path) -> None:
    store = Store(tmp_path / "test.db")
    store.initialize()
    session_id = store.create_session()
    llm = BatchLLM()
    service = InflowService(store, llm, every_n_turns=2)
    user_one = store.add_message(session_id, "user", "第一轮")
    assistant_one = store.add_message(session_id, "assistant", "第一句")
    assert asyncio.run(service.process_turn(user_one, assistant_one)) is None

    user_two = store.add_message(session_id, "user", "第二轮")
    assistant_two = store.add_message(session_id, "assistant", "第二句")
    memory_id = asyncio.run(service.process_turn(user_two, assistant_two))
    repeated = asyncio.run(service.process_turn(user_two, assistant_two))

    assert memory_id is not None
    assert repeated is None
    assert len(store.list_table("memories")) == 1
    assert json.loads(store.get_memory(memory_id)["source_message_ids"]) == [  # type: ignore[index]
        user_one, assistant_one, user_two, assistant_two
    ]


class SlowFirstInflowLLM:
    def __init__(self):
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = 0

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        small: bool = False,
        json_response: bool = False,
    ) -> str:
        self.calls += 1
        if self.calls == 1:
            self.entered.set()
            await self.release.wait()
        return '{"summary":"两轮对话","emotion_text":"平静","certainty":0.9}'


@pytest.mark.parametrize("second_call", ["process_turn", "flush_pending"])
def test_concurrent_inflow_calls_write_each_turn_once(
    tmp_path: Path, second_call: str
) -> None:
    store = Store(tmp_path / "test.db")
    store.initialize()
    session_id = store.create_session()
    turns = []
    for number in range(2):
        user_id = store.add_message(session_id, "user", f"第{number + 1}轮")
        assistant_id = store.add_message(
            session_id,
            "assistant",
            f"第{number + 1}句",
            meta={"inflow_user_message_id": user_id},
        )
        turns.append((user_id, assistant_id))
    llm = SlowFirstInflowLLM()
    service = InflowService(store, llm)

    async def overlap() -> int:
        first = asyncio.create_task(service.process_turn(*turns[0]))
        await asyncio.wait_for(llm.entered.wait(), timeout=1)
        if second_call == "process_turn":
            second = asyncio.create_task(service.process_turn(*turns[1]))
        else:
            second = asyncio.create_task(service.flush_pending(session_id))
        await asyncio.sleep(0)
        calls_while_first_waits = llm.calls
        llm.release.set()
        await asyncio.gather(first, second)
        return calls_while_first_waits

    calls_while_first_waits = asyncio.run(overlap())
    memories = store.list_table("memories")
    assert calls_while_first_waits == 1
    assert llm.calls == 2
    assert len(memories) == 2
    assert [json.loads(row["source_message_ids"]) for row in memories] == [
        list(turns[0]),
        list(turns[1]),
    ]
