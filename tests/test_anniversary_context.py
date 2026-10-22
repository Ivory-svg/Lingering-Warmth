from datetime import date, datetime, timezone

from test_worldbook_context import make_reader
from yuwen.anniversaries import local_today, todays_anniversaries
from yuwen.config import AnniversaryConfig, WakeConfig
from yuwen.context import ContextBuilder
from yuwen.store import Store
from yuwen.wake import WakeService


def test_today_matches_yearly_once_off_and_leap_day(tmp_path):
    store = Store(tmp_path / "db")
    store.initialize()
    store.add_anniversary(month_day="02-29", year=2024, yearly=True, title="闰日", source="manual")
    store.add_anniversary(month_day="02-28", year=2025, yearly=False, title="这一次", source="manual")
    off = store.add_anniversary(month_day="02-28", year=None, yearly=True, title="停用", source="manual")
    store.update_anniversary(off, active=False)
    assert [row["title"] for row in todays_anniversaries(store, date(2025, 2, 28))] == ["这一次", "闰日"]
    assert [row["title"] for row in todays_anniversaries(store, date(2026, 2, 28))] == ["闰日"]
    assert [row["title"] for row in todays_anniversaries(store, date(2024, 2, 29))] == ["闰日"]
    assert local_today("Asia/Shanghai", now=datetime(2026, 2, 27, 16, 30, tzinfo=timezone.utc)) == date(2026, 2, 28)


def test_context_and_wake_anniversary_text(tmp_path):
    store = Store(tmp_path / "db")
    store.initialize()
    session = store.create_session()
    store.add_anniversary(month_day="02-28", year=2024, yearly=True, title="第一次见面", source="manual")
    instant = datetime(2026, 2, 27, 16, 30, tzinfo=timezone.utc)
    reader = make_reader(tmp_path)
    context = ContextBuilder(store, reader, wake_timezone="Asia/Shanghai", clock=lambda: instant)
    lines = [row["content"] for row in context.build(session)]
    assert any("第一次见面（2024 年那天，今天是第 2 年）" in line for line in lines)
    store.add_memory(
        kind="turn", happened_at="2026-02-01T10:00:00+00:00", summary="待续",
        emotion_text="平静", certainty=0.9, source_message_ids=[], session_id=session,
        status="open", status_as_of="2026-02-01T10:00:00+00:00",
    )
    todo_result = context.find_open_todos(session)
    assert todo_result is not None
    lines = [row["content"] for row in context.build(session, todo_text=todo_result.text)]
    assert next(i for i, line in enumerate(lines) if "还没完结的事" in line) < next(
        i for i, line in enumerate(lines) if "今天是你们记着的日子" in line
    )
    last_user = {"created_at": "2026-02-27T10:00:00+00:00"}
    wake = WakeService(store, context, reader, None, config=WakeConfig(timezone="Asia/Shanghai"))
    assert "今天是你们记着的日子：第一次见面（第 2 年）" in wake._prompt(instant, last_user, 0, 0, "")
    next_day = datetime(2026, 2, 28, 16, 30, tzinfo=timezone.utc)
    assert "今天是你们记着的日子" not in wake._prompt(next_day, last_user, 0, 0, "")
    disabled = ContextBuilder(store, reader, anniversary=AnniversaryConfig(False), clock=lambda: instant, wake_timezone="Asia/Shanghai")
    assert not any("今天是你们记着的日子" in row["content"] for row in disabled.build(session))
