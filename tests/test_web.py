from pathlib import Path

from fastapi.testclient import TestClient

from test_chat_api import FakeLLM, make_parts
from yuwen.api import create_app


def test_web_page_exposes_chat_flag_and_handoff_ui(tmp_path: Path) -> None:
    config, store, books = make_parts(tmp_path)
    app = create_app(config, store=store, llm=FakeLLM(), worldbook=books)

    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "他不会这么说" in response.text
    assert "handoff_happened" in response.text
    assert "fetch('/chat'" in response.text
    assert "fetch('/ooc/flag'" in response.text
