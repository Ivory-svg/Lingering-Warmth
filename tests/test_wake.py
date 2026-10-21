import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from email.header import decode_header
from importlib.resources import files
import json
from pathlib import Path

import httpx
import pytest
from authed_client import TestClient

from test_chat_api import FakeLLM, make_parts
from yuwen.chat import ChatService
from yuwen.api import create_app
from yuwen.config import WakeConfig
from yuwen.context import ContextBuilder
from yuwen.gatekeeper import GatekeeperService
from yuwen.llm import WakeNotifier
from yuwen.wake import WakeService


TZ = timezone(timedelta(hours=8))
NOW = datetime(2026, 10, 21, 15, 40, tzinfo=TZ)


def setup_wake(tmp_path: Path, *, reply: str = '{"send":false,"reason":"对方在忙","message":""}',
               config: WakeConfig = WakeConfig(), at: datetime = NOW):
    _, store, books = make_parts(tmp_path)
    llm = FakeLLM(reply)
    context = ContextBuilder(store, books)
    wake = WakeService(
        store, context, books, llm, config=replace(config, timezone="Asia/Shanghai"),
        player_name="阿栀", character_name="沈砚", clock=lambda: at,
        random_minutes=lambda low, high: low,
    )
    return store, books, llm, wake


def test_wake_skips_model_when_disabled(tmp_path: Path) -> None:
    store, _, llm, wake = setup_wake(tmp_path, config=WakeConfig(enabled=False))
    async def run():
        wake.start()
        assert wake._task is None
        assert await wake.check_once() is None
    asyncio.run(run())
    assert llm.calls == [] and store.list_wake_log() == []


@pytest.mark.parametrize("scenario,reason", [
    ("night", "不在白天"),
    ("recent", "刚聊过"),
    ("limit", "今天写够了"),
    ("unanswered", "上一封还没回"),
    ("empty", "还没开始聊"),
])
def test_wake_rules_block_without_model(tmp_path: Path, scenario: str, reason: str) -> None:
    at = NOW.replace(hour=23) if scenario == "night" else NOW
    store, _, llm, wake = setup_wake(tmp_path, at=at)
    session_id = store.create_session()
    if scenario != "empty":
        user_at = at - timedelta(minutes=20 if scenario == "recent" else 180)
        store.add_message(session_id, "user", "你好", created_at=user_at.isoformat())
    if scenario == "limit":
        for _ in range(3):
            store.add_wake_letter(session_id, "信", "想念", checked_at=(at - timedelta(hours=1)).isoformat(), surfaced_memory_ids=[])
    if scenario == "unanswered":
        for _ in range(2):
            store.add_wake_letter(session_id, "信", "想念", checked_at=(at - timedelta(hours=1)).isoformat(), surfaced_memory_ids=[])

    assert asyncio.run(wake.check_once()) is None

    assert llm.calls == []
    assert store.list_wake_log()[-1]["decided"] == "blocked"
    assert store.list_wake_log()[-1]["reason"] == reason


def test_wake_send_false_and_bad_json_record_skip(tmp_path: Path) -> None:
    store, _, llm, wake = setup_wake(tmp_path)
    session_id = store.create_session()
    store.add_message(session_id, "user", "你好", created_at=(NOW - timedelta(hours=3)).isoformat())

    assert asyncio.run(wake.check_once()) is None
    assert store.list_wake_log()[-1]["reason"] == "对方在忙"
    assert len(store.list_messages(session_id)) == 1
    assert llm.calls[-1][-1]["role"] == "system"
    assert "现在是10月21日 15:40" in llm.calls[-1][-1]["content"]
    assert "阿栀上一次说话是3 小时 0 分钟前" in llm.calls[-1][-1]["content"]
    llm.reply = "坏的 JSON"
    assert asyncio.run(wake.check_once()) is None
    assert store.list_wake_log()[-1]["reason"] == "没想好"
    assert store.list_wake_log()[-1]["decided"] == "skip"


def test_wake_sends_letter_with_log_and_recall_ids(tmp_path: Path) -> None:
    store, _, llm, wake = setup_wake(
        tmp_path, reply='{"send":true,"reason":"想她了","message":"记得带伞。"}'
    )
    session_id = store.create_session()
    store.add_message(session_id, "user", "下雨了", created_at=(NOW - timedelta(hours=3)).isoformat())

    message_id = asyncio.run(wake.check_once())

    letter = store.get_message(message_id)
    log = store.list_wake_log()[0]
    meta = json.loads(letter["meta"])
    assert letter["role"] == "assistant" and letter["content"] == "记得带伞。"
    assert meta == {"letter": True, "wake_log_id": log["id"], "surfaced_memory_ids": []}
    assert log["decided"] == "send" and log["message_id"] == message_id
    assert log["reason"] == "想她了" and log["notified"] == 0
    assert llm.calls[0][-1]["role"] == "system"


def test_player_message_reschedules_wake(tmp_path: Path) -> None:
    store, books, _, wake = setup_wake(tmp_path)
    chat = ChatService(store, ContextBuilder(store, books), FakeLLM(),
                       turn_lock=wake.turn_lock, on_player_message=wake.reschedule)
    wake.reschedule()
    first = wake.next_check_at
    wake.clock = lambda: NOW + timedelta(minutes=5)

    asyncio.run(chat.handle_message("你好"))

    assert wake.next_check_at == first + timedelta(minutes=5)


def test_runtime_wake_prompt_matches_documented_prompt() -> None:
    documented = Path("docs/prompts/wake.md").read_text(encoding="utf-8").split("```", 2)[1].strip()
    runtime = files("yuwen.prompts").joinpath("wake.md").read_text(encoding="utf-8").strip()
    assert runtime == documented


@pytest.mark.parametrize("format", ["text", "json"])
def test_wake_notification_formats(tmp_path: Path, format: str) -> None:
    requests = []
    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(204)
    async def run():
        config = replace(WakeConfig(), notify_url="https://notify.test/topic", notify_format=format)
        client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        notifier = WakeNotifier(config, client=client)
        store, _, _, wake = setup_wake(
            tmp_path, reply='{"send":true,"reason":"想她了","message":"记得带伞。"}'
        )
        wake.notify = notifier.send
        session = store.create_session()
        store.add_message(session, "user", "下雨了", created_at=(NOW - timedelta(hours=3)).isoformat())
        message_id = await wake.check_once()
        await client.aclose()
        return store, message_id

    store, message_id = asyncio.run(run())

    assert store.list_wake_log()[0]["notified"] == 1
    assert len(requests) == 1
    if format == "text":
        assert requests[0].content.decode() == "记得带伞。"
        encoded, charset = decode_header(requests[0].headers["Title"])[0]
        assert encoded.decode(charset) == "沈砚"
    else:
        assert json.loads(requests[0].content) == {
            "type": "letter", "message_id": message_id, "character": "沈砚",
            "content": "记得带伞。", "reason": "想她了",
            "sent_at": NOW.isoformat(), "daily_now": "",
        }


def test_wake_notification_failure_keeps_letter(tmp_path: Path) -> None:
    async def fail(_payload):
        raise RuntimeError("通知失败")
    store, _, _, wake = setup_wake(
        tmp_path, reply='{"send":true,"reason":"想她了","message":"记得带伞。"}'
    )
    wake.notify = fail
    session = store.create_session()
    store.add_message(session, "user", "下雨了", created_at=(NOW - timedelta(hours=3)).isoformat())

    message_id = asyncio.run(wake.check_once())

    assert store.get_message(message_id)["content"] == "记得带伞。"
    assert store.list_wake_log()[0]["notified"] == 0


def test_messages_after_id_returns_only_new_letter(tmp_path: Path) -> None:
    config, store, books = make_parts(tmp_path)
    session_id = store.create_session()
    old_id = store.add_message(session_id, "user", "你好")
    _, letter_id = store.add_wake_letter(
        session_id, "先写给你。", "想念", checked_at=NOW.isoformat(), surfaced_memory_ids=[]
    )
    app = create_app(config, store=store, llm=FakeLLM(), worldbook=books)

    with TestClient(app) as client:
        all_messages = client.get("/messages").json()
        fresh = client.get("/messages", params={"after_id": old_id}).json()
        assert client.get("/messages", params={"after_id": letter_id}).json() == []

    assert [item["letter"] for item in all_messages] == [False, True]
    assert [item["message_id"] for item in fresh] == [letter_id]
    assert fresh[0]["letter"] is True


def test_wake_active_start_and_timezone_local_day(tmp_path: Path) -> None:
    at = NOW.replace(hour=9, minute=0)
    store, _, llm, wake = setup_wake(
        tmp_path, at=at, config=WakeConfig(max_unanswered=10)
    )
    session = store.create_session()
    store.add_message(session, "user", "早上好", created_at=(at - timedelta(hours=3)).isoformat())
    for _ in range(3):
        # UTC 仍是前一天，但配置时区里已经是今天。
        store.add_wake_letter(
            session, "旧信", "想念", checked_at=at.replace(hour=0, minute=10).isoformat(),
            surfaced_memory_ids=[],
        )

    assert asyncio.run(wake.check_once()) is None
    assert store.list_wake_log()[-1]["reason"] == "今天写够了"
    assert llm.calls == []

    fresh_path = tmp_path / "fresh"
    fresh_path.mkdir()
    fresh_store, _, fresh_llm, fresh_wake = setup_wake(fresh_path, at=at)
    fresh_session = fresh_store.create_session()
    fresh_store.add_message(
        fresh_session, "user", "早上好", created_at=(at - timedelta(hours=3)).isoformat()
    )
    assert asyncio.run(fresh_wake.check_once()) is None
    assert fresh_store.list_wake_log()[-1]["decided"] == "skip"
    assert len(fresh_llm.calls) == 1


@pytest.mark.parametrize("reply", [
    "不是 JSON", '{"send":"yes","reason":"想念","message":"你好"}',
    '{"send":true,"reason":"想念","message":"   "}',
])
def test_wake_invalid_model_result_skips_letter(tmp_path: Path, reply: str) -> None:
    store, _, _, wake = setup_wake(tmp_path, reply=reply)
    session = store.create_session()
    store.add_message(session, "user", "你好", created_at=(NOW - timedelta(hours=3)).isoformat())

    assert asyncio.run(wake.check_once()) is None
    assert store.list_wake_log()[-1]["decided"] == "skip"
    assert store.list_wake_log()[-1]["reason"] == "没想好"
    assert len(store.list_messages(session)) == 1


def test_wake_gatekeeper_rewrites_letter_and_saves_note(tmp_path: Path) -> None:
    store, books, _, wake = setup_wake(
        tmp_path, reply='{"send":true,"reason":"想念","message":"我好想你！"}'
    )
    wake.gatekeeper = GatekeeperService(
        store, books,
        FakeLLM('{"verdict":"ooc","reason":"太直白","rewritten":"别忘了带伞。"}'),
    )
    session = store.create_session()
    store.add_message(session, "user", "外面下雨", created_at=(NOW - timedelta(hours=3)).isoformat())

    message_id = asyncio.run(wake.check_once())

    letter = store.get_message(message_id)
    note = store.list_voice_notes()[0]
    assert letter["content"] == "别忘了带伞。"
    assert note["message_id"] == message_id and note["original"] == "我好想你！"
    assert json.loads(letter["meta"])["voice_note_id"] == note["id"]


def test_chat_waits_for_wake_to_finish_writing(tmp_path: Path) -> None:
    store, books, _, wake = setup_wake(tmp_path)
    session = store.create_session()
    store.add_message(session, "user", "先前的话", created_at=(NOW - timedelta(hours=3)).isoformat())
    entered = asyncio.Event()
    release = asyncio.Event()

    class SlowWakeModel:
        async def chat(self, messages, *, small=False, json_response=False):
            entered.set()
            await release.wait()
            return '{"send":true,"reason":"想念","message":"先写封信。"}'

    wake.llm = SlowWakeModel()
    chat = ChatService(
        store, ContextBuilder(store, books), FakeLLM("我在。"),
        turn_lock=wake.turn_lock, on_player_message=wake.reschedule,
    )

    async def run():
        checking = asyncio.create_task(wake.check_once())
        await entered.wait()
        replying = asyncio.create_task(chat.handle_message("我回来了"))
        await asyncio.sleep(0)
        assert [row["content"] for row in store.list_messages(session)] == ["先前的话"]
        release.set()
        await checking
        await replying

    asyncio.run(run())
    assert [row["content"] for row in store.list_messages(session)] == [
        "先前的话", "先写封信。", "我回来了", "我在。",
    ]


def test_wake_prompt_omits_missing_daily_and_emotion(tmp_path: Path) -> None:
    store, _, llm, wake = setup_wake(tmp_path)
    session = store.create_session()
    store.add_message(session, "user", "你好", created_at=(NOW - timedelta(hours=3)).isoformat())

    asyncio.run(wake.check_once())

    prompt = llm.calls[0][-1]["content"]
    assert "按你的日常" not in prompt
    assert "最后一次聊天时的心情" not in prompt
    assert wake.next_check_at == NOW + timedelta(minutes=40)


def test_wake_carries_open_todo_and_daily_emotion_into_letter(tmp_path: Path) -> None:
    store, books, _, wake = setup_wake(
        tmp_path, reply='{"send":true,"reason":"关心","message":"今天还好吗？"}'
    )
    directory = tmp_path / "worldbook"
    directory.mkdir()
    (directory / "daily.md").write_text("- 09:00 开店\n- 14:00 修表\n", encoding="utf-8")
    session = store.create_session()
    store.add_message(session, "user", "你好", created_at=(NOW - timedelta(hours=3)).isoformat())
    todo_id = store.add_memory(
        kind="turn", happened_at=(NOW - timedelta(days=1)).isoformat(),
        summary="阿栀说周五要看牙", emotion_text="有点担心", certainty=0.9,
        source_message_ids=[], session_id=session, status="open",
        status_as_of=(NOW - timedelta(days=1)).isoformat(),
    )
    wake.context.clock = lambda: NOW

    message_id = asyncio.run(wake.check_once())

    assert json.loads(store.get_message(message_id)["meta"])["surfaced_memory_ids"] == [todo_id]
    prompt = wake.llm.calls[0][-1]["content"]
    assert "按你的日常，你这会儿大概在：修表" in prompt
    assert "你们最后一次聊天时的心情：有点担心" in prompt


def test_wake_background_task_checks_and_stops(tmp_path: Path) -> None:
    store, _, _, wake = setup_wake(tmp_path)
    current = [NOW]
    wake.clock = lambda: current[0]
    session = store.create_session()
    store.add_message(session, "user", "你好", created_at=(NOW - timedelta(hours=3)).isoformat())

    async def run():
        wake.start()
        assert wake._task is not None
        current[0] = wake.next_check_at
        wake._changed.set()
        for _ in range(100):
            if store.list_wake_log():
                break
            await asyncio.sleep(0.001)
        assert store.list_wake_log()[0]["decided"] == "skip"
        assert wake.next_check_at == current[0] + timedelta(minutes=40)
        await wake.stop()
        assert wake._task is None

    asyncio.run(run())
