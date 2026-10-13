from dataclasses import replace
from pathlib import Path

from authed_client import TestClient
import pytest

from test_chat_api import FakeLLM, make_parts
from yuwen.api import create_app
from yuwen.worldbook import WorldbookError


def test_web_page_exposes_chat_flag_and_handoff_ui(tmp_path: Path) -> None:
    config, store, books = make_parts(tmp_path)
    app = create_app(config, store=store, llm=FakeLLM(), worldbook=books)

    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "他不会这么说" in response.text
    assert 'id="message-menu"' in response.text
    assert "item.addEventListener('contextmenu'" in response.text
    assert "item.addEventListener('pointerdown'" in response.text
    assert "item.append(flag)" not in response.text
    assert "apiFetch('/messages')" in response.text
    assert "showOOCMarker(item, ooc)" in response.text
    assert "其实他会这么说" in response.text
    assert "apiFetch(`/voice-notes/${ooc.voice_note_id}/overturn`" in response.text
    assert "mark.remove();" in response.text
    assert "if (!ooc) return;" in response.text
    assert "data.ooc ?" in response.text
    assert "handoff_happened" in response.text
    assert "apiFetch('/chat'" in response.text
    assert "apiFetch('/ooc/flag'" in response.text
    assert "response.status === 401" in response.text
    assert 'id="logout"' in response.text
    assert 'href="/export?format=markdown"' in response.text
    assert 'href="/export?format=json"' in response.text
    assert 'href="/catalog"' in response.text


def test_app_fails_at_startup_when_worldbook_core_is_missing(tmp_path: Path) -> None:
    config, store, _ = make_parts(tmp_path)
    config = replace(
        config,
        character=replace(
            config.character,
            prompt_file=tmp_path / "missing.md",
            worldbook_dir=tmp_path / "missing_worldbook",
        ),
    )
    app = create_app(config, store=store, llm=FakeLLM())

    with pytest.raises(WorldbookError, match="核心"):
        with TestClient(app):
            pass
