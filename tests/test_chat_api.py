import asyncio
import json
from pathlib import Path

from fastapi.testclient import TestClient

from yuwen.api import create_app
from yuwen.chat import ChatService
from yuwen.config import (
    AppConfig,
    CharacterConfig,
    GatekeeperConfig,
    HandoffConfig,
    LLMConfig,
    ServerConfig,
    StorageConfig,
)
from yuwen.context import ContextBuilder
from yuwen.store import Store
from yuwen.worldbook import WorldbookReader


class FakeLLM:
    def __init__(self, reply: str = "先去吃点东西。"):
        self.reply = reply
        self.calls: list[list[dict[str, str]]] = []

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        small: bool = False,
        json_response: bool = False,
    ) -> str:
        self.calls.append(messages)
        return self.reply


def make_parts(tmp_path: Path) -> tuple[AppConfig, Store, WorldbookReader]:
    prompt = tmp_path / "character.md"
    prompt.write_text("你是一个嘴硬心软的人。", encoding="utf-8")
    config = AppConfig(
        llm=LLMConfig("https://example.test/v1", "key", "model", "small"),
        character=CharacterConfig(prompt, tmp_path / "worldbook"),
        handoff=HandoffConfig(),
        gatekeeper=GatekeeperConfig(),
        storage=StorageConfig(tmp_path / "data.db"),
        server=ServerConfig(),
        config_path=tmp_path / "config.toml",
    )
    store = Store(config.storage.db_path)
    store.initialize()
    return config, store, WorldbookReader(config.character)


def test_chat_service_runs_one_basic_turn(tmp_path: Path) -> None:
    _, store, books = make_parts(tmp_path)
    llm = FakeLLM()
    service = ChatService(store, ContextBuilder(store, books), llm)

    result = asyncio.run(service.handle_message("今天好累"))

    assert result.reply == "先去吃点东西。"
    assert not result.ooc_rewritten
    assert not result.handoff_happened
    rows = store.list_messages(result.session_id)
    assert [(row["role"], row["content"]) for row in rows] == [
        ("user", "今天好累"),
        ("assistant", "先去吃点东西。"),
    ]
    assert json.loads(rows[1]["meta"])["surfaced_memory_ids"] == []
    assert llm.calls[0][-1] == {"role": "user", "content": "今天好累"}


def test_post_chat_returns_documented_response(tmp_path: Path) -> None:
    config, store, books = make_parts(tmp_path)
    app = create_app(config, store=store, llm=FakeLLM(), worldbook=books)

    with TestClient(app) as client:
        response = client.post("/chat", json={"content": "今天好累"})

    assert response.status_code == 200
    assert response.json() == {
        "message_id": 2,
        "reply": "先去吃点东西。",
        "session_id": 1,
        "ooc_rewritten": False,
        "ooc": None,
        "handoff_happened": False,
    }


def test_post_chat_rejects_blank_message(tmp_path: Path) -> None:
    config, store, books = make_parts(tmp_path)
    app = create_app(config, store=store, llm=FakeLLM(), worldbook=books)

    with TestClient(app) as client:
        response = client.post("/chat", json={"content": "   "})

    assert response.status_code == 422
    assert response.json()["detail"] == "消息内容不能为空"


def test_web_ui_uses_book_names_for_visible_controls(tmp_path: Path) -> None:
    config, store, books = make_parts(tmp_path)
    app = create_app(config, store=store, llm=FakeLLM(), worldbook=books)

    with TestClient(app) as client:
        html = client.get("/").text

    assert "导出全书（Markdown）" in html
    assert "备份全书（JSON）" in html
    assert "查看勘误" in html
    assert "为什么这条勘误不对（可不填）" in html
    assert "续写 · 新的一卷" in html
    assert "他不会这么说" in html
    assert "其实他会这么说" in html


def test_get_messages_restores_current_session_with_latest_voice_note(tmp_path: Path) -> None:
    config, store, books = make_parts(tmp_path)
    old_session_id = store.create_session()
    store.add_message(old_session_id, "user", "旧窗口的话")
    store.close_session(old_session_id)
    session_id = store.create_session()
    store.add_message(session_id, "event", "预留事件")
    user_id = store.add_message(
        session_id, "user", "你好", created_at="2026-10-07T10:00:00+08:00"
    )
    assistant_id = store.add_message(
        session_id, "assistant", "今天怎么没来。", created_at="2026-10-07T10:01:00+08:00"
    )
    store.add_voice_note(
        source="gatekeeper",
        message_id=assistant_id,
        original="我好想你！",
        rewritten="今天怎么没来。",
        reason="太直白",
    )
    latest_note_id = store.add_voice_note(
        source="player",
        message_id=assistant_id,
        original="今天怎么没来。",
        rewritten="……回来了？",
        reason="玩家觉得这句仍不合适",
    )
    plain_id = store.add_message(
        session_id, "assistant", "先坐下。", created_at="2026-10-07T10:02:00+08:00"
    )
    app = create_app(config, store=store, llm=FakeLLM(), worldbook=books)

    with TestClient(app) as client:
        response = client.get("/messages")

    assert response.status_code == 200
    assert response.json() == [
        {
            "message_id": user_id,
            "role": "user",
            "content": "你好",
            "created_at": "2026-10-07T10:00:00+08:00",
            "ooc": None,
        },
        {
            "message_id": assistant_id,
            "role": "assistant",
            "content": "今天怎么没来。",
            "created_at": "2026-10-07T10:01:00+08:00",
            "ooc": {
                "original": "今天怎么没来。",
                "reason": "玩家觉得这句仍不合适",
                "rewritten": "……回来了？",
                "voice_note_id": latest_note_id,
                "source": "player",
            },
        },
        {
            "message_id": plain_id,
            "role": "assistant",
            "content": "先坐下。",
            "created_at": "2026-10-07T10:02:00+08:00",
            "ooc": None,
        },
    ]
