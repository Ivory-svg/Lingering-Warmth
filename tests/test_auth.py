"""登录边界的接口测试。"""

import hashlib
from pathlib import Path
import re
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
import pytest

from test_chat_api import FakeLLM, make_parts
from yuwen.api import create_app
from yuwen.config import ConfigError


def test_non_json_post_and_patch_are_rejected(tmp_path: Path) -> None:
    config, store, books = make_parts(tmp_path)
    app = create_app(config, store=store, llm=FakeLLM(), worldbook=books)

    with TestClient(app) as client:
        token = app.state.auth_service.create_session()
        client.cookies.set("yuwen_session", token)
        assert client.post("/chat", data={"content": "你好"}).status_code == 415
        assert client.patch("/voice-notes/1", data={"active": "true"}).status_code == 415
        assert client.post("/login", data={"password": "bad"}).status_code == 415


def test_unset_password_prevents_startup(tmp_path: Path) -> None:
    config, store, books = make_parts(tmp_path)
    app = create_app(replace(config, auth=replace(config.auth, password="")),
                     store=store, llm=FakeLLM(), worldbook=books)
    with pytest.raises(ConfigError, match="config.toml 的 \\[auth\\]"):
        with TestClient(app):
            pass


def test_login_logout_and_all_routes_are_locked_by_default(tmp_path: Path) -> None:
    config, store, books = make_parts(tmp_path)
    app = create_app(config, store=store, llm=FakeLLM(), worldbook=books)
    allowed = {("GET", "/login"), ("POST", "/login"), ("GET", "/health")}

    with TestClient(app) as client:
        for route in app.routes:
            for method in (route.methods or set()) - {"HEAD", "OPTIONS"}:
                path = re.sub(r"\{[^}]+\}", "1", route.path)
                response = client.request(method, path, json={} if method in {"POST", "PATCH"} else None,
                                          follow_redirects=False)
                if (method, route.path) in allowed:
                    assert response.status_code not in {303, 401}, (method, path)
                elif method == "GET" and route.path in {"/", "/catalog"}:
                    assert response.status_code == 303 and response.headers["location"] == "/login"
                else:
                    assert response.status_code == 401, (method, path, response.text)

        assert client.post("/login", json={"password": "wrong"}).status_code == 401
        login = client.post("/login", json={"password": config.auth.password})
        assert login.status_code == 200 and login.json() == {"ok": True}
        assert "httponly" in login.headers["set-cookie"].lower()
        assert "samesite=lax" in login.headers["set-cookie"].lower()
        assert client.get("/").status_code == 200
        assert client.get("/login", follow_redirects=False).status_code == 303
        logout = client.post("/logout", json={})
        assert logout.json() == {"ok": True}
        assert client.get("/messages").status_code == 401


def test_fifth_bad_password_locks_for_fifteen_minutes(tmp_path: Path) -> None:
    config, store, books = make_parts(tmp_path)
    app = create_app(config, store=store, llm=FakeLLM(), worldbook=books)
    moment = [datetime(2026, 10, 13, tzinfo=timezone.utc)]
    app.state.auth_service.clock = lambda: moment[0]
    with TestClient(app) as client:
        for _ in range(4):
            assert client.post("/login", json={"password": "wrong"}).status_code == 401
        fifth = client.post("/login", json={"password": "wrong"})
        sixth = client.post("/login", json={"password": "wrong"})
        assert fifth.status_code == sixth.status_code == 429
        assert fifth.json()["retry_after_seconds"] == 900
        assert sixth.headers["retry-after"] == "900"
        assert client.post("/login", json={"password": config.auth.password}).status_code == 429
        moment[0] += timedelta(minutes=15, seconds=1)
        assert client.post("/login", json={"password": config.auth.password}).status_code == 200


def test_secure_cookie_option_and_ip_limits_are_independent(tmp_path: Path) -> None:
    config, store, books = make_parts(tmp_path)
    config = replace(config, auth=replace(config.auth, secure_cookie=True))
    app = create_app(config, store=store, llm=FakeLLM(), worldbook=books)
    auth = app.state.auth_service
    for _ in range(5):
        auth.record_failure("one-ip")
    assert auth.retry_after("one-ip") > 0
    assert auth.retry_after("another-ip") == 0
    with TestClient(app) as client:
        response = client.post("/login", json={"password": config.auth.password})
        assert response.status_code == 200
        assert "secure" in response.headers["set-cookie"].lower()


def test_cookie_expiry_forgery_password_change_and_last_seen(tmp_path: Path) -> None:
    config, store, books = make_parts(tmp_path)
    app = create_app(config, store=store, llm=FakeLLM(), worldbook=books)
    moment = [datetime(2026, 10, 13, tzinfo=timezone.utc)]
    app.state.auth_service.clock = lambda: moment[0]
    with TestClient(app) as client:
        client.cookies.set("yuwen_session", "forged")
        assert client.get("/messages").status_code == 401
        client.cookies.delete("yuwen_session")
        login = client.post("/login", json={"password": config.auth.password})
        assert login.status_code == 200
        token = client.cookies["yuwen_session"]
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        initial = store.get_auth_session(token_hash)["last_seen_at"]
        moment[0] += timedelta(minutes=30)
        assert client.get("/messages").status_code == 200
        assert store.get_auth_session(token_hash)["last_seen_at"] == initial
        moment[0] += timedelta(minutes=31)
        assert client.get("/messages").status_code == 200
        assert store.get_auth_session(token_hash)["last_seen_at"] == moment[0].isoformat()

        changed = create_app(replace(config, auth=replace(config.auth, password="new-password-123")),
                             store=store, llm=FakeLLM(), worldbook=books)
        with TestClient(changed) as another:
            another.cookies.set("yuwen_session", token)
            assert another.get("/messages").status_code == 401
        assert store.get_auth_session(token_hash) is None

        client.post("/login", json={"password": config.auth.password})
        moment[0] += timedelta(days=config.auth.session_days, seconds=1)
        assert client.get("/messages").status_code == 401
