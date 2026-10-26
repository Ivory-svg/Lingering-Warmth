from authed_client import TestClient

from yuwen.demo import create_demo_app


def test_demo_uses_real_memory_flow_without_model_api(tmp_path):
    app, password = create_demo_app(tmp_path / "demo.db")
    assert len(password) >= 8
    with TestClient(app) as client:
        first = client.post("/chat", json={"content": "我喜欢茉莉花茶"})
        assert first.status_code == 200
        assert "演示回复" in first.json()["reply"]
        memories = client.get("/memories").json()
        assert memories["total"] == 1
        assert "茉莉花茶" in memories["items"][0]["summary"]

        store = app.state.store
        store.close_session(first.json()["session_id"])
        store.create_session()
        second = client.post("/chat", json={"content": "还记得我喜欢茉莉花茶吗"})
        assert second.status_code == 200
        assert "我记得" in second.json()["reply"]


def test_demo_can_show_sealed_memory_and_anniversary(tmp_path):
    app, _ = create_demo_app(tmp_path / "demo.db")
    with TestClient(app) as client:
        birthday = client.post("/chat", json={"content": "我生日是3月14日"})
        assert birthday.status_code == 200
        anniversaries = client.get("/anniversaries").json()
        assert [(row["month_day"], row["title"]) for row in anniversaries] == [
            ("03-14", "对方的生日")
        ]
        forgotten = client.post("/chat", json={"content": "这个忘掉吧"})
        assert forgotten.status_code == 200
        assert client.get("/memories?filter=sealed").json()["total"] == 1
