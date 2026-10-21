"""在安静一段时间后，看一眼要不要先写一封信。"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from importlib.resources import files
import json
import logging
import random
from typing import Any
from zoneinfo import ZoneInfo

from yuwen.chat import ChatModel
from yuwen.config import WakeConfig
from yuwen.context import ContextBuilder
from yuwen.gatekeeper import GatekeeperService
from yuwen.store import Store
from yuwen.surfacing import SurfacingService
from yuwen.worldbook import WorldbookReader


logger = logging.getLogger(__name__)
Clock = Callable[[], datetime]
Notify = Callable[[dict[str, Any]], Awaitable[bool]]


class WakeService:
    def __init__(
        self, store: Store, context: ContextBuilder, worldbook: WorldbookReader,
        llm: ChatModel, *, config: WakeConfig = WakeConfig(),
        player_name: str = "对方", character_name: str = "他",
        surfacing: SurfacingService | None = None,
        gatekeeper: GatekeeperService | None = None,
        turn_lock: asyncio.Lock | None = None,
        clock: Clock = lambda: datetime.now(timezone.utc),
        random_minutes: Callable[[int, int], int] = random.randint,
        notify: Notify | None = None,
    ) -> None:
        self.store = store
        self.context = context
        self.worldbook = worldbook
        self.llm = llm
        self.config = config
        self.player_name = player_name
        self.character_name = character_name
        self.surfacing = surfacing
        self.gatekeeper = gatekeeper
        self.turn_lock = turn_lock or asyncio.Lock()
        self.clock = clock
        self.random_minutes = random_minutes
        self.notify = notify
        self.next_check_at: datetime | None = None
        self._changed = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._template = files("yuwen.prompts").joinpath("wake.md").read_text(encoding="utf-8")

    def _local(self, value: datetime) -> datetime:
        return value.astimezone(ZoneInfo(self.config.timezone)) if self.config.timezone else value.astimezone()

    def reschedule(self) -> None:
        if not self.config.enabled:
            return
        minutes = self.random_minutes(
            self.config.min_interval_minutes, self.config.max_interval_minutes
        )
        self.next_check_at = self.clock() + timedelta(minutes=minutes)
        self._changed.set()

    def start(self) -> None:
        if not self.config.enabled or self._task is not None:
            return
        self._changed = asyncio.Event()
        self.reschedule()
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(self) -> None:
        while True:
            assert self.next_check_at is not None
            self._changed.clear()
            delay = max(0.0, (self.next_check_at - self.clock()).total_seconds())
            try:
                await asyncio.wait_for(self._changed.wait(), timeout=delay)
                continue
            except asyncio.TimeoutError:
                pass
            try:
                await self.check_once()
            except Exception:
                logger.exception("来信检查失败，本次跳过")

    def _unanswered(self, messages: list[dict[str, Any]]) -> int:
        count = 0
        for message in reversed(messages):
            if message["role"] == "user":
                break
            if message["role"] == "assistant":
                try:
                    meta = json.loads(message["meta"]) if message["meta"] else {}
                except (TypeError, ValueError):
                    meta = {}
                if isinstance(meta, dict) and meta.get("letter") is True:
                    count += 1
        return count

    def _blocked_reason(
        self, now: datetime, session: dict[str, Any] | None,
        messages: list[dict[str, Any]], today_sent: int, unanswered: int,
    ) -> str | None:
        local = self._local(now)
        begin, end = self.config.active_hours.split("-")
        current = local.strftime("%H:%M")
        active = begin <= current < end if begin < end else current >= begin or current < end
        if not active:
            return "不在白天"
        if messages and now - datetime.fromisoformat(messages[-1]["created_at"]) < timedelta(
            minutes=self.config.min_silence_minutes
        ):
            return "刚聊过"
        if today_sent >= self.config.max_per_day:
            return "今天写够了"
        if unanswered >= self.config.max_unanswered:
            return "上一封还没回"
        if session is None or not any(message["role"] == "user" for message in messages):
            return "还没开始聊"
        return None

    @staticmethod
    def _ago(now: datetime, then: datetime) -> str:
        minutes = max(0, int((now - then).total_seconds() // 60))
        days = minutes // (24 * 60)
        if days == 1:
            return "昨天"
        if days > 1:
            return f"{days} 天前"
        hours, minute = divmod(minutes, 60)
        return f"{hours} 小时 {minute} 分钟前" if hours else f"{minute} 分钟前"

    def _prompt(
        self, now: datetime, last_user: dict[str, Any], today_sent: int,
        unanswered: int, daily_now: str,
    ) -> str:
        local = self._local(now)
        last_user_at = datetime.fromisoformat(last_user["created_at"])
        memories = [row for row in self.store.list_table("memories") if not row["sealed"]]
        latest = max(
            memories,
            key=lambda row: (datetime.fromisoformat(row["happened_at"]), row["id"]),
        ) if memories else None
        prompt = self._template
        if not daily_now:
            prompt = prompt.replace("按你的日常，你这会儿大概在：{日常此刻}\n", "")
        if latest is None:
            prompt = prompt.replace("你们最后一次聊天时的心情：{最近的心情}\n", "")
        values = {
            "{当地时间}": f"{local.month}月{local.day}日 {local.hour:02d}:{local.minute:02d}",
            "{星期}": ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")[local.weekday()],
            "{多久以前}": self._ago(now, last_user_at),
            "{今天写过}": str(today_sent),
            "{没回的}": str(unanswered),
            "{日常此刻}": daily_now,
            "{最近的心情}": str(latest["emotion_text"]) if latest else "",
            "{玩家}": self.player_name,
        }
        for placeholder, value in values.items():
            prompt = prompt.replace(placeholder, value)
        return prompt

    @staticmethod
    def _parse(text: str) -> tuple[bool, str, str]:
        data = json.loads(text)
        if not isinstance(data, dict) or type(data.get("send")) is not bool:
            raise ValueError("来信决定无效")
        send = data["send"]
        reason = data.get("reason")
        message = data.get("message")
        if not isinstance(reason, str):
            reason = "没想好"
        if send and (not isinstance(message, str) or not message.strip()):
            raise ValueError("来信内容为空")
        return send, reason.strip() or "没想好", message.strip() if send else ""

    async def check_once(self) -> int | None:
        """执行一次检查；返回写出的消息 id，没写则为空。"""

        if not self.config.enabled:
            return None
        try:
            return await self._check_once_locked()
        finally:
            self.reschedule()

    async def _check_once_locked(self) -> int | None:
        async with self.turn_lock:
            now = self.clock()
            local_day = self._local(now).date()
            session = self.store.get_active_session()
            messages = self.store.list_messages(int(session["id"])) if session else []
            today_sent = sum(
                row["decided"] == "send"
                and self._local(datetime.fromisoformat(row["checked_at"])).date() == local_day
                for row in self.store.list_wake_log()
            )
            unanswered = self._unanswered(messages)
            blocked = self._blocked_reason(now, session, messages, today_sent, unanswered)
            if blocked is not None:
                self.store.add_wake_log(now.isoformat(), "blocked", blocked)
                return None
            assert session is not None
            last_user = next(row for row in reversed(messages) if row["role"] == "user")
            last_text = self.store.visible_message_content(messages[-1])
            todos = self.context.find_open_todos(int(session["id"]))
            recall = (
                await self.surfacing.find(
                    int(session["id"]), last_text,
                    exclude_ids=todos.memory_ids if todos else (),
                ) if self.surfacing is not None else None
            )
            context = self.context.build(
                int(session["id"]), surfaced=recall.text if recall else None,
                todo_text=todos.text if todos else None,
            )
            daily_now = self.worldbook.daily_now(self._local(now))
            context.append({
                "role": "system",
                "content": self._prompt(now, last_user, today_sent, unanswered, daily_now),
            })
            try:
                raw = await self.llm.chat(context)
                send, reason, content = self._parse(raw)
            except Exception:
                self.store.add_wake_log(now.isoformat(), "skip", "没想好")
                return None
            if not send:
                self.store.add_wake_log(now.isoformat(), "skip", reason)
                return None
            original = content
            note_reason = ""
            if self.gatekeeper is not None:
                recent = [row for row in context if row["role"] in {"user", "assistant"}][-6:]
                review = await self.gatekeeper.review(content, recent)
                if review.verdict == "ooc" and review.rewritten is not None:
                    content = review.rewritten
                    note_reason = review.reason
            memory_ids = [
                *(recall.memory_ids if recall else ()),
                *(todos.memory_ids if todos else ()),
            ]
            wake_id, message_id = self.store.add_wake_letter(
                int(session["id"]), content, reason, checked_at=now.isoformat(),
                surfaced_memory_ids=memory_ids,
            )
            if note_reason and self.gatekeeper is not None:
                note_id = self.gatekeeper.save_note(
                    source="gatekeeper", message_id=message_id, original=original,
                    rewritten=content, reason=note_reason,
                )
                self.store.update_message_meta(message_id, {
                    "letter": True, "wake_log_id": wake_id,
                    "surfaced_memory_ids": memory_ids,
                    "ooc_rewritten": True, "voice_note_id": note_id,
                })
            if self.notify is not None:
                try:
                    sent = await self.notify({
                        "type": "letter", "message_id": message_id,
                        "character": self.character_name, "content": content,
                        "reason": reason, "sent_at": now.isoformat(),
                        "daily_now": daily_now,
                    })
                    if sent:
                        self.store.mark_wake_notified(wake_id)
                    else:
                        logger.warning("来信通知没有成功，信已保存")
                except Exception:
                    logger.warning("来信通知失败，信已保存")
            return message_id
