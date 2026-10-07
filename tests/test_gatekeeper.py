import asyncio
import json
from pathlib import Path

from fastapi.testclient import TestClient

from test_chat_api import FakeLLM, make_parts
from yuwen.api import create_app
from yuwen.chat import ChatService
from yuwen.context import ContextBuilder
from yuwen.gatekeeper import GatekeeperService


class SequenceLLM:
    def __init__(self, replies: list[str]):
        self.replies = iter(replies)
        self.calls: list[tuple[bool, bool]] = []

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        small: bool = False,
        json_response: bool = False,
    ) -> str:
        self.calls.append((small, json_response))
        return next(self.replies)


def test_pre_send_ooc_is_rewritten_and_saved_as_voice_note(tmp_path: Path) -> None:
    _, store, books = make_parts(tmp_path)
    llm = SequenceLLM(
        [
            "我好想你！",
            json.dumps(
                {
                    "verdict": "ooc",
                    "reason": "他不会直白地说想念",
                    "rewritten": "……今天怎么没来。",
                },
                ensure_ascii=False,
            ),
        ]
    )
    gatekeeper = GatekeeperService(store, books, llm)
    service = ChatService(
        store,
        ContextBuilder(store, books),
        llm,
        gatekeeper=gatekeeper,
    )

    result = asyncio.run(service.handle_message("你想我吗"))

    assert result.ooc_rewritten
    assert result.reply == "……今天怎么没来。"
    assert store.get_message(result.message_id)["content"] == result.reply  # type: ignore[index]
    note = store.list_voice_notes()[0]
    assert note["source"] == "gatekeeper"
    assert note["message_id"] == result.message_id
    assert llm.calls == [(False, False), (True, True)]


def test_garbled_gatekeeper_result_does_not_interrupt_reply(tmp_path: Path) -> None:
    _, store, books = make_parts(tmp_path)
    llm = SequenceLLM(["普通回复", "乱码"])
    service = ChatService(
        store,
        ContextBuilder(store, books),
        llm,
        gatekeeper=GatekeeperService(store, books, llm),
    )

    result = asyncio.run(service.handle_message("你好"))

    assert result.reply == "普通回复"
    assert not result.ooc_rewritten
    assert store.list_voice_notes() == []


def test_post_chat_includes_ooc_details_after_rewrite(tmp_path: Path) -> None:
    config, store, books = make_parts(tmp_path)
    llm = SequenceLLM(
        [
            "我好想你！",
            json.dumps(
                {
                    "verdict": "ooc",
                    "reason": "他不会说得这么直白",
                    "rewritten": "……今天怎么没来。",
                },
                ensure_ascii=False,
            ),
            "流入模型乱码",
        ]
    )
    app = create_app(config, store=store, llm=llm, worldbook=books)

    with TestClient(app) as client:
        response = client.post("/chat", json={"content": "你想我吗"})
        history = client.get("/messages")

    assert response.status_code == 200
    assert response.json()["reply"] == "……今天怎么没来。"
    assert response.json()["ooc_rewritten"] is True
    assert response.json()["ooc"] == {
        "original": "我好想你！",
        "reason": "他不会说得这么直白",
        "voice_note_id": 1,
    }
    assert history.status_code == 200
    assert history.json()[-1]["ooc"] == {
        "original": "我好想你！",
        "reason": "他不会说得这么直白",
        "rewritten": "……今天怎么没来。",
        "voice_note_id": 1,
        "source": "gatekeeper",
    }


def test_in_character_reply_has_no_ooc_marker(tmp_path: Path) -> None:
    config, store, books = make_parts(tmp_path)
    llm = SequenceLLM(
        [
            "先坐下。",
            '{"verdict":"in_character","reason":"","rewritten":""}',
            "流入模型乱码",
        ]
    )
    app = create_app(config, store=store, llm=llm, worldbook=books)

    with TestClient(app) as client:
        response = client.post("/chat", json={"content": "你好"})
        history = client.get("/messages")

    assert response.status_code == 200
    assert response.json()["ooc_rewritten"] is False
    assert response.json()["ooc"] is None
    assert history.status_code == 200
    assert history.json()[-1]["ooc"] is None
    assert store.list_voice_notes() == []


def test_player_flag_and_voice_note_endpoints(tmp_path: Path) -> None:
    config, store, books = make_parts(tmp_path)
    session_id = store.create_session()
    message_id = store.add_message(session_id, "assistant", "我好想你！")
    llm = FakeLLM(
        json.dumps(
            {
                "verdict": "ooc",
                "reason": "太直白",
                "rewritten": "……今天怎么没来。",
            },
            ensure_ascii=False,
        )
    )
    app = create_app(config, store=store, llm=llm, worldbook=books)

    with TestClient(app) as client:
        flagged = client.post("/ooc/flag", json={"message_id": message_id})
        listed = client.get("/voice-notes")
        note_id = flagged.json()["voice_note_id"]
        patched = client.patch(f"/voice-notes/{note_id}", json={"active": False})

    assert flagged.status_code == 200
    assert flagged.json()["rewritten"] == "……今天怎么没来。"
    assert listed.json()[0]["source"] == "player"
    assert patched.json() == {"id": note_id, "active": False}


def test_player_rewrite_is_kept_even_when_guard_disagrees(tmp_path: Path) -> None:
    _, store, books = make_parts(tmp_path)
    session_id = store.create_session()
    message_id = store.add_message(session_id, "assistant", "原回复")
    llm = SequenceLLM(['{"verdict":"in_character","reason":"","rewritten":""}'])
    service = GatekeeperService(store, books, llm)

    result = asyncio.run(service.flag_message(message_id, "玩家自己的改法"))

    assert result.verdict == "ooc"
    assert result.rewritten == "玩家自己的改法"
    assert result.voice_note_id is not None
