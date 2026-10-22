"""纪年日期校验与当天匹配规则。"""

from __future__ import annotations

from datetime import date, datetime
import re
from zoneinfo import ZoneInfo

from yuwen.store import Store


def parse_anniversary_date(value: object, yearly: bool) -> tuple[str, int | None]:
    if not isinstance(value, str):
        raise ValueError("纪年日期必须是 MM-DD 或 YYYY-MM-DD")
    match = re.fullmatch(r"(?:(\d{4})-)?(\d{2})-(\d{2})", value)
    if match is None:
        raise ValueError("纪年日期必须是 MM-DD 或 YYYY-MM-DD")
    year = int(match[1]) if match[1] is not None else None
    if year == 0 or (not yearly and year is None):
        raise ValueError("一次性纪年必须填写年份")
    month, day = int(match[2]), int(match[3])
    try:
        date(year or 2000, month, day)
    except ValueError as exc:
        raise ValueError("纪年日期不存在") from exc
    return f"{month:02d}-{day:02d}", year


def validate_anniversary(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or type(value.get("yearly")) is not bool:
        raise ValueError("纪年格式不对")
    title = value.get("title")
    if not isinstance(title, str) or not 0 < len(title.strip()) <= 30:
        raise ValueError("纪年名字须为 1 到 30 字")
    month_day, year = parse_anniversary_date(value.get("date"), value["yearly"])
    return {"month_day": month_day, "year": year, "yearly": value["yearly"], "title": title.strip()}


def local_today(timezone_name: str, *, now: datetime | None = None) -> date:
    instant = now or datetime.now().astimezone()
    return instant.astimezone(ZoneInfo(timezone_name)).date() if timezone_name else instant.astimezone().date()


def todays_anniversaries(store: Store, today: date) -> list[dict[str, object]]:
    month_day = today.strftime("%m-%d")
    result = []
    for row in store.list_anniversaries():
        if not row["active"]:
            continue
        matching = row["month_day"] == month_day or (
            month_day == "02-28" and not _leap(today.year) and row["month_day"] == "02-29"
        )
        if matching and (row["yearly"] or row["year"] == today.year):
            result.append(row)
    return result


def anniversary_label(row: dict[str, object], today: date, *, for_wake: bool = False) -> str:
    title = str(row["title"])
    year = row["year"]
    if row["yearly"] and isinstance(year, int) and today.year != year:
        if for_wake:
            return f"{title}（第 {today.year - year} 年）"
        return f"{title}（{year} 年那天，今天是第 {today.year - year} 年）"
    return title


def _leap(year: int) -> bool:
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
