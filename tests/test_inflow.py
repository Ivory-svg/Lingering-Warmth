import asyncio
from dataclasses import replace
import json
from pathlib import Path

from authed_client import TestClient
import pytest

from test_chat_api import make_parts
from yuwen.api import create_app
from yuwen.config import TodoConfig
from yuwen.config import EmbeddingConfig
from yuwen.embedder import MemoryEmbedder
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


def test_inflow_includes_prior_letters_once_with_next_player_turn(tmp_path: Path) -> None:
    store = Store(tmp_path / "test.db")
    store.initialize()
    session_id = store.create_session()
    store.add_wake_letter(
        session_id, "第一封信", "想念", checked_at="2026-10-21T10:00:00+08:00",
        surfaced_memory_ids=[],
    )
    store.add_wake_letter(
        session_id, "第二封信", "想念", checked_at="2026-10-21T11:00:00+08:00",
        surfaced_memory_ids=[],
    )
    user_id = store.add_message(session_id, "user", "看到你的信了")
    assistant_id = store.add_message(session_id, "assistant", "嗯。")
    llm = ReplyLLM('{"summary":"回了信","emotion_text":"开心","certainty":0.9}')
    inflow = InflowService(store, llm, character_name="沈砚")

    asyncio.run(inflow.process_turn(user_id, assistant_id))

    prompt = llm.messages[0][0]["content"]
    assert prompt.index("沈砚（先写来的信）：第一封信") < prompt.index("沈砚（先写来的信）：第二封信")
    assert prompt.index("第二封信") < prompt.index("对方：看到你的信了")
    next_user = store.add_message(session_id, "user", "明天见")
    next_assistant = store.add_message(session_id, "assistant", "好。")
    asyncio.run(inflow.process_turn(next_user, next_assistant))
    assert "先写来的信" not in llm.messages[1][0]["content"]


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


def test_todo_prompt_fields_follow_enabled_switch(tmp_path: Path) -> None:
    store = Store(tmp_path / "test.db")
    store.initialize()
    user_id, assistant_id = turn(store)
    turns = [(store.get_message(user_id), store.get_message(assistant_id))]

    enabled = InflowService(store, ReplyLLM("{}"))
    prompt = enabled._prompt(turns, {})
    assert '"todos"' in prompt
    assert '"resolved"' not in prompt
    assert "\n【还没完结的事】\n" not in prompt

    disabled = InflowService(store, ReplyLLM("{}"), todo=TodoConfig(enabled=False))
    prompt = disabled._prompt(turns, {})
    assert '"todos"' not in prompt
    assert '"resolved"' not in prompt
    assert "\n【还没完结的事】\n" not in prompt


def test_inflow_writes_at_most_three_todos_with_batch_time_and_vectors(tmp_path: Path) -> None:
    class FakeEmbedding:
        def __init__(self):
            self.calls = []

        async def embed(self, texts):
            self.calls.append(list(texts))
            return [[1.0, 0.0] for _ in texts]

    store = Store(tmp_path / "test.db")
    store.initialize()
    session_id = store.create_session()
    user_id = store.add_message(
        session_id, "user", "下周去看牙，明天去取快递，周末陪你看电影，下个月去体检",
        created_at="2026-10-20T10:00:00+08:00",
    )
    assistant_id = store.add_message(session_id, "assistant", "我记着。", created_at="2026-10-20T10:01:00+08:00")
    llm = ReplyLLM(json.dumps({
        "summary": "阿栀说下周去看牙", "emotion_text": "平静", "certainty": 0.8,
        "todos": [
            {"summary": summary, "quote": quote}
            for summary, quote in [
                ("阿栀下周去看牙", "下周去看牙"),
                ("阿栀明天去取快递", "明天去取快递"),
                ("阿栀周末陪你看电影", "周末陪你看电影"),
                ("阿栀下个月去体检", "下个月去体检"),
            ]
        ],
    }, ensure_ascii=False))
    fake = FakeEmbedding()
    embedder = MemoryEmbedder(store, fake, EmbeddingConfig("https://fake.test"))

    asyncio.run(InflowService(store, llm, embedder=embedder, player_name="阿栀").process_turn(user_id, assistant_id))

    rows = store.list_table("memories")
    assert len(rows) == 4
    assert [row["summary"] for row in rows[1:]] == ["阿栀下周去看牙", "阿栀明天去取快递", "阿栀周末陪你看电影"]
    for row in rows[1:]:
        assert row["kind"] == "turn" and row["status"] == "open"
        assert row["status_as_of"] == row["happened_at"] == "2026-10-20T10:01:00+08:00"
        assert row["certainty"] == 0.8 and row["emotion_text"] == "平静"
        assert json.loads(row["source_message_ids"]) == [user_id, assistant_id]
        assert row["embedding_model"] == "bge-m3"
    assert len(fake.calls) == 4


def test_inflow_resolves_only_listed_open_todos_and_saves_history(tmp_path: Path) -> None:
    store = Store(tmp_path / "test.db")
    store.initialize()
    session_id = store.create_session()
    open_id = store.add_memory(
        kind="turn", happened_at="2026-10-19T10:00:00+08:00", summary="阿栀要看牙",
        emotion_text="平静", certainty=0.9, source_message_ids=[], session_id=session_id,
        status="open", status_as_of="2026-10-19T10:00:00+08:00",
    )
    user_id = store.add_message(session_id, "user", "牙看完了", created_at="2026-10-20T10:00:00+08:00")
    assistant_id = store.add_message(session_id, "assistant", "那就好。", created_at="2026-10-20T10:01:00+08:00")
    llm = ReplyLLM(json.dumps({
        "summary": "阿栀看完牙", "emotion_text": "放松", "certainty": 0.9,
        "resolved": [
            {"id": open_id, "status": "done", "note": "阿栀说牙看完了"},
            {"id": 999, "status": "done", "note": "乱写的编号"},
            {"id": open_id, "status": "unknown", "note": "乱写的状态"},
        ],
    }, ensure_ascii=False))

    asyncio.run(InflowService(store, llm, player_name="阿栀").process_turn(user_id, assistant_id))

    assert f"[#{open_id}]" in llm.messages[0][0]["content"]
    row = store.get_memory(open_id)
    assert row["status"] == "done"
    assert row["resolved_at"] == row["status_as_of"] == "2026-10-20T10:01:00+08:00"
    assert row["resolved_note"] == "阿栀说牙看完了"
    history = store.list_memory_history(open_id)
    assert len(history) == 1 and history[0]["reason"] == "完结：阿栀说牙看完了"
    assert json.loads(history[0]["snapshot"])["status"] == "open"


def test_inflow_resolves_todo_without_note(tmp_path: Path) -> None:
    store = Store(tmp_path / "test.db")
    store.initialize()
    session_id = store.create_session()
    open_id = store.add_memory(
        kind="turn", happened_at="2026-10-19T10:00:00+08:00", summary="阿栀要看牙",
        emotion_text="平静", certainty=0.9, source_message_ids=[], session_id=session_id,
        status="open", status_as_of="2026-10-19T10:00:00+08:00",
    )
    user_id = store.add_message(session_id, "user", "牙看完了")
    assistant_id = store.add_message(session_id, "assistant", "那就好。")
    llm = ReplyLLM(json.dumps({
        "summary": "阿栀看完牙", "emotion_text": "放松", "certainty": 0.9,
        "resolved": [{"id": open_id, "status": "done", "note": ""}],
    }, ensure_ascii=False))

    asyncio.run(InflowService(store, llm).process_turn(user_id, assistant_id))

    row = store.get_memory(open_id)
    assert row["status"] == "done" and row["resolved_note"] is None
    assert store.list_memory_history(open_id)[0]["reason"] == "完结"


@pytest.mark.parametrize("broken_field", ["todos", "resolved"])
def test_inflow_bad_todo_field_does_not_discard_other_field_or_summary(
    tmp_path: Path, broken_field: str
) -> None:
    store = Store(tmp_path / "test.db")
    store.initialize()
    session_id = store.create_session()
    open_id = store.add_memory(
        kind="turn", happened_at="2026-10-19T10:00:00+00:00", summary="看牙",
        emotion_text="平静", certainty=0.9, source_message_ids=[], session_id=session_id,
        status="open", status_as_of="2026-10-19T10:00:00+00:00",
    )
    user_id = store.add_message(session_id, "user", "牙看完了，周末去看电影")
    assistant_id = store.add_message(session_id, "assistant", "好。")
    payload = {
        "summary": "阿栀看完牙，约好周末看电影", "emotion_text": "开心", "certainty": 0.9,
        "todos": [{"summary": "周末看电影", "quote": "周末去看电影"}],
        "resolved": [{"id": open_id, "status": "done", "note": "牙看完了"}],
    }
    payload[broken_field] = "错误格式"
    memory_id = asyncio.run(InflowService(store, ReplyLLM(json.dumps(payload, ensure_ascii=False))).process_turn(
        user_id, assistant_id
    ))

    assert store.get_memory(memory_id)["summary"] == "阿栀看完牙，约好周末看电影"
    assert (store.get_memory(open_id)["status"] == "done") == (broken_field == "todos")
    assert (len(store.list_table("memories")) == 3) == (broken_field == "resolved")


def test_inflow_invalid_json_does_not_create_or_resolve_todos(tmp_path: Path) -> None:
    store = Store(tmp_path / "test.db")
    store.initialize()
    session_id = store.create_session()
    open_id = store.add_memory(
        kind="turn", happened_at="2026-10-19T10:00:00+00:00", summary="看牙",
        emotion_text="平静", certainty=0.9, source_message_ids=[], session_id=session_id,
        status="open", status_as_of="2026-10-19T10:00:00+00:00",
    )
    user_id = store.add_message(session_id, "user", "牙看完了")
    assistant_id = store.add_message(session_id, "assistant", "好。")

    memory_id = asyncio.run(InflowService(store, ReplyLLM("无效 JSON")).process_turn(
        user_id, assistant_id
    ))

    assert store.get_memory(memory_id)["summary"] == "牙看完了"
    assert store.get_memory(open_id)["status"] == "open"
    assert len(store.list_table("memories")) == 2

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
    assert "玩家写“小雨”，角色写“林舟”" in prompt
    assert "不要用“他”“她”指代这两个人" in prompt
    assert "{玩家}" not in prompt
    assert "{角色名}" not in prompt
    assert "递给林舟的往事" not in prompt
    assert "used_memory_ids" not in prompt


def test_inflow_prompt_lists_only_surfaced_memories(tmp_path: Path) -> None:
    store = Store(tmp_path / "test.db")
    store.initialize()
    user_id, assistant_id = turn(store)
    session_id = store.get_message(assistant_id)["session_id"]  # type: ignore[index]
    memory_id = store.add_memory(
        kind="turn",
        happened_at="2026-10-03T10:00:00+08:00",
        summary="小雨加班很累，林舟让小雨先吃饭",
        quote="今天好累",
        emotion_text="疲惫",
        certainty=0.9,
        source_message_ids=[],
        session_id=session_id,
    )
    store.update_message_meta(assistant_id, {"surfaced_memory_ids": [memory_id]})
    llm = ReplyLLM(
        '{"summary":"聊了晚饭","emotion_text":"平静","certainty":0.9,'
        f'"used_memory_ids":[{memory_id}]' + "}"
    )

    asyncio.run(InflowService(store, llm, character_name="林舟").process_turn(user_id, assistant_id))

    prompt = llm.messages[0][0]["content"]
    assert "【这几轮里递给林舟的往事】" in prompt
    assert f"[#{memory_id}] 10月3日：小雨加班很累，林舟让小雨先吃饭。原话「今天好累」" in prompt
    assert "used_memory_ids" in prompt
    assert "{递给他的往事}" not in prompt


def test_inflow_counts_only_used_surfaced_memory_at_reply_time(tmp_path: Path) -> None:
    store = Store(tmp_path / "test.db")
    store.initialize()
    session_id = store.create_session()
    used = store.add_memory(
        kind="turn", happened_at="2026-10-03T10:00:00+08:00",
        summary="约好带伞", emotion_text="平静", certainty=0.9,
        source_message_ids=[], session_id=session_id,
    )
    unused = store.add_memory(
        kind="turn", happened_at="2026-10-03T10:00:00+08:00",
        summary="买了书", emotion_text="开心", certainty=0.9,
        source_message_ids=[], session_id=session_id,
    )
    not_surfaced = store.add_memory(
        kind="turn", happened_at="2026-10-03T10:00:00+08:00",
        summary="聊了电影", emotion_text="开心", certainty=0.9,
        source_message_ids=[], session_id=session_id,
    )
    user_id = store.add_message(session_id, "user", "今天下雨")
    reply_time = "2026-10-07T20:15:00+08:00"
    assistant_id = store.add_message(
        session_id, "assistant", "带上伞。", created_at=reply_time,
        meta={"surfaced_memory_ids": [used, unused]},
    )
    llm = ReplyLLM(
        '{"summary":"聊了下雨","emotion_text":"平静","certainty":0.9,'
        f'"used_memory_ids":[{used},{not_surfaced}]' + "}"
    )

    asyncio.run(InflowService(store, llm).process_turn(user_id, assistant_id))

    assert store.get_memory(used)["recall_count"] == 1  # type: ignore[index]
    assert store.get_memory(used)["last_recalled_at"] == reply_time  # type: ignore[index]
    assert store.get_memory(unused)["recall_count"] == 0  # type: ignore[index]
    assert store.get_memory(unused)["last_recalled_at"] is None  # type: ignore[index]
    assert store.get_memory(not_surfaced)["recall_count"] == 0  # type: ignore[index]


def test_inflow_batch_counts_repeated_memory_once_at_last_reply(tmp_path: Path) -> None:
    store = Store(tmp_path / "test.db")
    store.initialize()
    session_id = store.create_session()
    old = store.add_memory(
        kind="turn", happened_at="2026-10-03T10:00:00+08:00",
        summary="约好带伞", emotion_text="平静", certainty=0.9,
        source_message_ids=[], session_id=session_id,
    )
    llm = ReplyLLM(
        '{"summary":"三轮聊了下雨","emotion_text":"平静","certainty":0.9,'
        f'"used_memory_ids":[{old},{old}]' + "}"
    )
    inflow = InflowService(store, llm, every_n_turns=3)
    for number in range(1, 4):
        user_id = store.add_message(session_id, "user", f"第{number}轮")
        assistant_id = store.add_message(
            session_id, "assistant", f"第{number}句",
            created_at=f"2026-10-07T20:0{number}:00+08:00",
            meta={"surfaced_memory_ids": [old]},
        )
        asyncio.run(inflow.process_turn(user_id, assistant_id))

    prompt = llm.messages[0][0]["content"]
    assert "下面是3轮对话" in prompt
    assert prompt.split("【可以封起来的往事】")[0].count(f"[#{old}]") == 1
    assert store.get_memory(old)["recall_count"] == 1  # type: ignore[index]
    assert store.get_memory(old)["last_recalled_at"] == "2026-10-07T20:03:00+08:00"  # type: ignore[index]


@pytest.mark.parametrize(
    "model_reply",
    [
        '{"summary":"聊了下雨","emotion_text":"平静","certainty":0.9,"used_memory_ids":[]}',
        '{"summary":"聊了下雨","emotion_text":"平静","certainty":0.9}',
        '{"summary":"聊了下雨","emotion_text":"平静","certainty":0.9,"used_memory_ids":"1"}',
        '{"summary":"聊了下雨","emotion_text":"平静","certainty":0.9,"used_memory_ids":[true]}',
        '{"emotion_text":"平静","certainty":0.9,"used_memory_ids":[1]}',
        "这不是 JSON",
    ],
)
def test_inflow_unconfirmed_or_invalid_used_result_never_counts_recall(
    tmp_path: Path, model_reply: str
) -> None:
    store = Store(tmp_path / "test.db")
    store.initialize()
    session_id = store.create_session()
    old = store.add_memory(
        kind="turn", happened_at="2026-10-03T10:00:00+08:00",
        summary="约好带伞", emotion_text="平静", certainty=0.9,
        source_message_ids=[], session_id=session_id,
    )
    user_id = store.add_message(session_id, "user", "今天下雨")
    assistant_id = store.add_message(
        session_id, "assistant", "带上伞。",
        meta={"surfaced_memory_ids": [old]},
    )

    new_id = asyncio.run(
        InflowService(store, ReplyLLM(model_reply)).process_turn(user_id, assistant_id)
    )

    assert new_id is not None
    assert store.get_memory(old)["recall_count"] == 0  # type: ignore[index]
    assert store.get_memory(old)["last_recalled_at"] is None  # type: ignore[index]
    if model_reply == "这不是 JSON" or '"summary"' not in model_reply:
        assert store.get_memory(new_id)["emotion_text"] == "未标注"  # type: ignore[index]


def test_inflow_accepts_old_reply_without_surfaced_memory_ids(tmp_path: Path) -> None:
    store = Store(tmp_path / "test.db")
    store.initialize()
    session_id = store.create_session()
    old = store.add_memory(
        kind="turn", happened_at="2026-10-03T10:00:00+08:00",
        summary="约好带伞", emotion_text="平静", certainty=0.9,
        source_message_ids=[], session_id=session_id,
    )
    user_id = store.add_message(session_id, "user", "今天下雨")
    assistant_id = store.add_message(
        session_id, "assistant", "带上伞。",
        meta={"inflow_user_message_id": user_id},
    )
    llm = ReplyLLM(
        '{"summary":"聊了下雨","emotion_text":"平静","certainty":0.9,'
        f'"used_memory_ids":[{old}]' + "}"
    )

    new_id = asyncio.run(InflowService(store, llm).process_turn(user_id, assistant_id))

    assert new_id is not None
    assert store.get_memory(old)["recall_count"] == 0  # type: ignore[index]
    assert "递给他的往事" not in llm.messages[0][0]["content"]
    assert "used_memory_ids" not in llm.messages[0][0]["content"]


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
