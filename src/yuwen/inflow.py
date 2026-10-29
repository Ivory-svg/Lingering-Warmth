"""把每轮对话在后台写成一条随笔记忆。"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
from importlib.resources import files
import json
import re
from typing import Any

from yuwen.chat import ChatModel
from yuwen.anniversaries import validate_anniversary
from yuwen.config import AnniversaryConfig, ForgetConfig, TodoConfig
from yuwen.embedder import MemoryEmbedder
from yuwen.store import Store


def _score(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if 0 <= number <= 1 else None


_DATE = re.compile(
    r"(?<!\d)(?:(\d{4})[-/年])?(\d{1,2})[-/月](\d{1,2})(?:日|号)?(?!\d)"
)
_RELATIVE_DAYS = {"今天": 0, "明天": 1, "后天": 2, "昨天": -1, "前天": -2}
_CITY_NAMES = (
    "北京", "上海", "天津", "重庆", "广州", "深圳", "杭州", "南京", "成都", "武汉",
    "西安", "苏州", "巴黎", "东京", "纽约", "伦敦", "香港", "澳门", "台北",
)
_PLACE = re.compile(r"[\u4e00-\u9fff]{2,5}(?:省|市|县|镇|村|路|街|站|医院|学校|公园)")
_PERSON = re.compile(
    r"(?:阿|小)[\u4e00-\u9fff]|"
    r"[王李张刘陈杨黄赵吴周徐孙马朱胡郭何林高罗郑梁谢宋唐许韩冯邓曹彭曾萧田董袁潘于蒋蔡余杜叶程苏魏吕丁任沈姚卢姜崔钟谭陆汪范金石廖贾夏韦傅方白邹孟熊秦邱江尹薛闫段雷侯龙史陶黎贺顾毛郝龚邵万钱严赖覃洪武莫孔向汤]"
    r"[\u4e00-\u9fff]{1,2}(?=[说问答去来和与把给在陪跟帮带送买做等，。！？：]|$)"
)
_TODO_CUE = re.compile(
    r"答应|约好|约定|承诺|下次|回头|明天|后天|下周|周[一二三四五六日天末]|下个月|月底|"
    r"还没|尚未|正在|等着|等待|在路上|快递|结果|准备|打算|计划|要去|会去|会给|会帮|帮你|陪你"
)


def _dates_in(text: str, day: date) -> set[tuple[int | None, int, int]]:
    found: set[tuple[int | None, int, int]] = set()
    for match in _DATE.finditer(text):
        year = int(match[1]) if match[1] else None
        month, month_day = int(match[2]), int(match[3])
        try:
            date(year or 2000, month, month_day)
        except ValueError:
            continue
        found.add((year, month, month_day))
    for word, offset in _RELATIVE_DAYS.items():
        if word in text:
            actual = day + timedelta(days=offset)
            found.add((actual.year, actual.month, actual.day))
            found.add((None, actual.month, actual.day))
    for match in re.finditer(r"下个月\s*(\d{1,2})[日号]", text):
        year = day.year + (day.month == 12)
        month = day.month % 12 + 1
        try:
            actual = date(year, month, int(match[1]))
        except ValueError:
            continue
        found.add((actual.year, actual.month, actual.day))
        found.add((None, actual.month, actual.day))
    return found


def _date_is_grounded(
    candidate: tuple[int | None, int, int], source: set[tuple[int | None, int, int]]
) -> bool:
    year, month, day = candidate
    return candidate in source or (year is None and any((m, d) == (month, day) for _, m, d in source))


def _has_unsupported_fact(
    text: str, original: str, day: date,
    source_dates: set[tuple[int | None, int, int]], known_names: set[str],
) -> bool:
    if any(not _date_is_grounded(item, source_dates) for item in _dates_in(text, day)):
        return True
    for pattern in (_PERSON, _PLACE):
        for match in pattern.finditer(text):
            entity = match[0]
            if pattern is _PLACE:
                entity = re.split(r"去了|到了|来到|回到|去|在|到", entity)[-1]
                if entity in {"医院", "学校", "公园"}:
                    continue
            elif any(entity.startswith(name) for name in known_names if len(name) > 1):
                continue
            if entity not in original and not (
                pattern is _PLACE and any(
                    entity[offset:] in original for offset in range(1, len(entity) - 2)
                )
            ):
                return True
    return any(city in text and city not in original for city in _CITY_NAMES)


def _todo_is_grounded(item: dict[str, str], original: str) -> bool:
    quote = item["quote"].strip()
    if quote and quote not in original:
        return False
    # 无原话摘录时，也要能从摘要里找到具体说过的事。
    summary = re.sub(r"[\s，。！？：、]", "", item["summary"])
    for clause in re.split(r"[，。！？；\n]", original):
        if not _TODO_CUE.search(clause) or (quote and quote not in clause):
            continue
        pairs = {summary[i:i + 2] for i in range(max(0, len(summary) - 1))}
        shared = {pair for pair in pairs if pair in clause}
        if len(shared) >= (1 if len(summary) <= 4 else 2) and len(shared) * 2 >= len(pairs):
            return True
    return False


def _resolution_is_grounded(item: dict[str, object], previous: dict[str, Any], original: str) -> bool:
    cue = r"做完|看完|完成|弄好|解决|收到了|已经|结束|出结果" if item["status"] == "done" else r"不做|不去|取消|算了|放弃|下架|不看"
    topic = re.sub(r"[说要去在了的会把给和与还没做完看完已完成结束不做取消]", "", str(previous["summary"]))
    return any(
        re.search(cue, clause) and any(char in clause for char in topic if "\u4e00" <= char <= "\u9fff")
        for clause in re.split(r"[，。！？；\n]", original)
    )


class InflowService:
    def __init__(
        self,
        store: Store,
        llm: ChatModel,
        *,
        character_name: str = "他",
        player_name: str = "对方",
        every_n_turns: int = 1,
        embedder: MemoryEmbedder | None = None,
        todo: TodoConfig = TodoConfig(),
        forget: ForgetConfig = ForgetConfig(),
        anniversary: AnniversaryConfig = AnniversaryConfig(),
    ):
        self.store = store
        self.llm = llm
        self.character_name = character_name
        self.player_name = player_name
        self.every_n_turns = every_n_turns
        self.embedder = embedder
        self.todo = todo
        self.forget = forget
        self.anniversary = anniversary
        self._lock = asyncio.Lock()
        self._template = files("yuwen.prompts").joinpath("inflow.md").read_text(encoding="utf-8")

    def _surfaced_for_turns(
        self, turns: list[tuple[dict[str, Any], dict[str, Any]]]
    ) -> dict[int, str]:
        """收集实际递给回复的记忆，以及最后一次递给它的回复时间。"""

        surfaced: dict[int, str] = {}
        for _, assistant in turns:
            try:
                meta = json.loads(assistant["meta"]) if assistant["meta"] else {}
            except (TypeError, ValueError):
                continue
            ids = meta.get("surfaced_memory_ids") if isinstance(meta, dict) else None
            if not isinstance(ids, list) or any(type(memory_id) is not int for memory_id in ids):
                continue
            for memory_id in ids:
                if self.store.get_memory(memory_id) is not None:
                    surfaced[memory_id] = str(assistant["created_at"])
        return surfaced

    def _prompt(
        self,
        turns: list[tuple[dict[str, Any], dict[str, Any]]],
        surfaced: dict[int, str],
        open_todos: list[dict[str, Any]] | None = None,
        sealable: list[dict[str, Any]] | None = None,
    ) -> str:
        letter_lines = self._letters_before(turns)
        dialogue = "\n".join(
            f"{self.player_name}：{user['content']}\n"
            f"{self.character_name}：{assistant['content']}"
            for user, assistant in turns
        )
        if letter_lines:
            dialogue = "\n".join([*letter_lines, dialogue])
        template = self._template
        open_todos = open_todos or []
        if self.todo.enabled and open_todos:
            todo_lines = []
            for memory in open_todos:
                as_of = datetime.fromisoformat(str(memory["status_as_of"]))
                todo_lines.append(
                    f"[#{memory['id']}] {as_of.month}月{as_of.day}日：{memory['summary']}"
                )
            template = template.replace("{还没完结的事}", "\n".join(todo_lines))
        else:
            template = template.replace("\n【还没完结的事】\n{还没完结的事}\n", "")
        if surfaced:
            lines = []
            for memory_id in surfaced:
                memory = self.store.get_memory(memory_id)
                assert memory is not None
                happened = datetime.fromisoformat(str(memory["happened_at"]))
                line = f"[#{memory_id}] {happened.month}月{happened.day}日：{memory['summary']}"
                if memory["quote"]:
                    line += f"。原话「{memory['quote']}」"
                lines.append(line)
            template = template.replace("{递给他的往事}", "\n".join(lines))
        else:
            template = template.replace(
                "\n【这几轮里递给{角色名}的往事】\n{递给他的往事}\n", ""
            )
        if self.forget.enabled:
            seal_lines = []
            for memory in sealable or []:
                happened = datetime.fromisoformat(str(memory["happened_at"]))
                line = f"[#{memory['id']}] {happened.month}月{happened.day}日：{memory['summary']}"
                if memory["quote"]:
                    line += f"。原话「{memory['quote']}」"
                seal_lines.append(line)
            template = template.replace("{可以封起来的往事}", "\n".join(seal_lines) or "（没有）")
        else:
            template = template.replace("\n【可以封起来的往事】\n{可以封起来的往事}\n", "")
        lines = template.splitlines()
        lines = [
            line for line in lines
            if not (
                (line.startswith(' "used_memory_ids":') and not surfaced)
                or (line.startswith(' "todos":') and not self.todo.enabled)
                or (line.startswith(' "resolved":') and not (self.todo.enabled and open_todos))
                or (line.startswith(' "forget":') and not self.forget.enabled)
                or (line.startswith(' "anniversaries":') and not self.anniversary.enabled)
            )
        ]
        field_indices = [
            index for index, line in enumerate(lines)
            if line.startswith('{"summary":') or line.startswith(' "')
        ]
        for index in field_indices:
            lines[index] = lines[index].rstrip(",}") + ("}" if index == field_indices[-1] else ",")
        template = "\n".join(lines)
        return (
            template.replace("{轮数}", str(len(turns)))
            .replace("{对话原文}", dialogue)
            .replace("{这一轮最后一条消息的时间}", str(turns[-1][1]["created_at"]))
            .replace("{玩家}", self.player_name)
            .replace("{角色名}", self.character_name)
        )

    def _letters_before(
        self, turns: list[tuple[dict[str, Any], dict[str, Any]]]
    ) -> list[str]:
        """只带上一批随笔之后、这批第一句玩家原话之前的来信。"""

        first_user_id = int(turns[0][0]["id"])
        session_id = int(turns[0][1]["session_id"])
        previous_end = 0
        for memory in self.store.list_table("memories"):
            if memory["kind"] != "turn" or int(memory["session_id"]) != session_id:
                continue
            try:
                ids = json.loads(memory["source_message_ids"])
            except (TypeError, ValueError):
                continue
            if isinstance(ids, list):
                previous_end = max([
                    previous_end,
                    *(item for item in ids if type(item) is int and item < first_user_id),
                ])
        lines = []
        for message in self.store.list_messages(session_id):
            if not previous_end < int(message["id"]) < first_user_id:
                continue
            if message["role"] != "assistant":
                continue
            try:
                meta = json.loads(message["meta"]) if message["meta"] else {}
            except (TypeError, ValueError):
                continue
            if isinstance(meta, dict) and meta.get("letter") is True:
                lines.append(
                    f"{self.character_name}（先写来的信）：{self.store.visible_message_content(message)}"
                )
        return lines

    @staticmethod
    def _parse(text: str, *, todo_enabled: bool = True, allow_resolved: bool = True) -> dict[str, object]:
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("随笔结果必须是 JSON 对象")
        summary = data.get("summary")
        emotion_text = data.get("emotion_text")
        certainty = _score(data.get("certainty"))
        if not isinstance(summary, str) or not summary.strip():
            raise ValueError("随笔结果缺少 summary")
        if not isinstance(emotion_text, str) or not emotion_text.strip():
            raise ValueError("随笔结果缺少 emotion_text")
        if certainty is None:
            raise ValueError("随笔结果的 certainty 无效")
        quote = data.get("quote")
        used = data.get("used_memory_ids")
        todos = data.get("todos") if todo_enabled else None
        resolved = data.get("resolved") if todo_enabled and allow_resolved else None
        forget = data.get("forget")
        if not (
            isinstance(forget, dict)
            and type(forget.get("this_batch")) is bool
            and isinstance(forget.get("ids"), list)
            and all(type(item) is int for item in forget["ids"])
            and isinstance(forget.get("reason"), str)
        ):
            forget = None
        anniversaries = data.get("anniversaries")
        valid_anniversaries = []
        if isinstance(anniversaries, list):
            for item in anniversaries[:2]:
                try:
                    valid_anniversaries.append(validate_anniversary(item))
                except ValueError:
                    continue
        todos = [
            item for item in todos
            if isinstance(item, dict)
            and isinstance(item.get("summary"), str)
            and isinstance(item.get("quote"), str)
        ] if isinstance(todos, list) else []
        resolved = [
            {
                "id": item["id"],
                "status": item["status"],
                "note": item["note"].strip() if isinstance(item.get("note"), str) else "",
            }
            for item in resolved
            if isinstance(item, dict)
            and type(item.get("id")) is int
            and item.get("status") in {"done", "dropped"}
        ] if isinstance(resolved, list) else []
        return {
            "summary": summary.strip(),
            "quote": quote if isinstance(quote, str) and quote else None,
            "emotion_text": emotion_text.strip(),
            "valence": _score(data.get("valence")),
            "arousal": _score(data.get("arousal")),
            "certainty": certainty,
            "used_memory_ids": (
                used
                if isinstance(used, list) and all(type(item) is int for item in used)
                else []
            ),
            "todos": todos[:3],
            "resolved": resolved,
            "forget": forget,
            "anniversaries": valid_anniversaries,
        }

    def _sealable_for_prompt(self, session_id: int, surfaced: dict[int, str]) -> list[dict[str, Any]]:
        if not self.forget.enabled:
            return []
        candidates = self.store.list_recent_turn_memories(session_id, self.forget.recent_memories)
        known = {int(row["id"]) for row in candidates}
        for memory_id in surfaced:
            if memory_id in known:
                continue
            memory = self.store.get_memory(memory_id)
            if memory is not None and not memory["sealed"]:
                candidates.append(memory)
                known.add(memory_id)
        return sorted(candidates, key=lambda row: (str(row["happened_at"]), int(row["id"])), reverse=True)

    def _open_for_prompt(self, surfaced: dict[int, str]) -> list[dict[str, Any]]:
        if not self.todo.enabled:
            return []
        rows = self.store.list_open_todos()
        surfaced_rows = [row for row in rows if int(row["id"]) in surfaced]
        other_rows = [row for row in rows if int(row["id"]) not in surfaced]
        selected = surfaced_rows + other_rows[:max(0, self.todo.max_open_in_prompt - len(surfaced_rows))]
        return sorted(selected, key=lambda row: (str(row["status_as_of"]), int(row["id"])), reverse=True)

    async def _summarize(
        self, turns: list[tuple[dict[str, Any], dict[str, Any]]]
    ) -> int:
        """将一批完整轮次写成一条记忆；模型出错时保留原话摘要。"""

        surfaced = self._surfaced_for_turns(turns)
        open_todos = self._open_for_prompt(surfaced)
        sealable = self._sealable_for_prompt(int(turns[-1][1]["session_id"]), surfaced)
        try:
            raw = await self.llm.chat(
                [{"role": "user", "content": self._prompt(turns, surfaced, open_todos, sealable)}],
                small=True,
                json_response=True,
            )
            memory = self._parse(
                raw, todo_enabled=self.todo.enabled, allow_resolved=bool(open_todos)
            )
        except Exception:
            raw_users = "；".join(str(user["content"]) for user, _ in turns)
            memory = {
                "summary": raw_users[:100],
                "quote": str(turns[0][0]["content"])[:100] or None,
                "emotion_text": "未标注",
                "valence": None,
                "arousal": None,
                "certainty": 0.5,
            }
        last_assistant = turns[-1][1]
        batch_day = datetime.fromisoformat(str(last_assistant["created_at"])).date()
        original = "\n".join(
            str(row["content"]) for turn in turns for row in turn
        )
        source_dates = set().union(*(
            _dates_in(str(row["content"]), datetime.fromisoformat(str(row["created_at"])).date())
            for turn in turns for row in turn
        ))
        known_names = {self.character_name, self.player_name}
        if _has_unsupported_fact(
            "\n".join(str(memory.get(field) or "") for field in ("summary", "quote", "emotion_text")),
            original, batch_day, source_dates, known_names,
        ):
            memory["certainty"] = min(float(memory["certainty"]), 0.3)
        source_ids = [int(row["id"]) for turn in turns for row in turn]
        memory_id = self.store.add_memory(
            kind="turn",
            happened_at=str(last_assistant["created_at"]),
            summary=str(memory["summary"]),
            quote=memory["quote"] if isinstance(memory["quote"], str) else None,
            emotion_text=str(memory["emotion_text"]),
            valence=memory["valence"] if isinstance(memory["valence"], float) else None,
            arousal=memory["arousal"] if isinstance(memory["arousal"], float) else None,
            certainty=float(memory["certainty"]),
            source_message_ids=source_ids,
            session_id=int(last_assistant["session_id"]),
        )
        used_ids = set(memory.get("used_memory_ids", [])) & surfaced.keys()
        for used_id in sorted(used_ids):
            self.store.mark_memories_recalled([used_id], recalled_at=surfaced[used_id])
        if self.todo.enabled:
            open_by_id = {int(row["id"]): row for row in open_todos}
            for item in memory.get("resolved", []):
                previous = open_by_id.get(item["id"])
                if (
                    previous is not None
                    and _resolution_is_grounded(item, previous, original)
                    and not _has_unsupported_fact(
                        str(item["note"]), original, batch_day, source_dates, known_names,
                    )
                ):
                    self.store.resolve_todo_from_inflow(
                        item["id"], status=item["status"], note=item["note"],
                        resolved_at=str(last_assistant["created_at"]),
                    )
        forget = memory.get("forget") if self.forget.enabled else None
        forget_reason = f"对话里说忘掉：{forget['reason']}" if isinstance(forget, dict) else ""
        if isinstance(forget, dict):
            allowed = {int(row["id"]) for row in sealable}
            for target_id in set(forget["ids"]) & allowed:
                self.store.set_memory_sealed(target_id, True, reason=forget_reason)
            if forget["this_batch"]:
                self.store.set_memory_sealed(memory_id, True, reason=forget_reason)
        if self.todo.enabled:
            for item in ([] if isinstance(forget, dict) and forget["this_batch"] else memory.get("todos", [])):
                summary = item["summary"].strip()
                if not summary or not _todo_is_grounded(item, original) or _has_unsupported_fact(
                    f"{summary}\n{item['quote']}", original, batch_day, source_dates, known_names,
                ):
                    continue
                todo_id = self.store.add_memory(
                    kind="turn",
                    happened_at=str(last_assistant["created_at"]),
                    summary=summary,
                    quote=item["quote"] or None,
                    emotion_text=str(memory["emotion_text"]),
                    valence=memory["valence"] if isinstance(memory["valence"], float) else None,
                    arousal=memory["arousal"] if isinstance(memory["arousal"], float) else None,
                    certainty=float(memory["certainty"]),
                    source_message_ids=source_ids,
                    session_id=int(last_assistant["session_id"]),
                    status="open",
                    status_as_of=str(last_assistant["created_at"]),
                )
                if self.embedder is not None:
                    await self.embedder.embed_memory(todo_id)
        if self.embedder is not None:
            await self.embedder.embed_memory(memory_id)
        if self.anniversary.enabled:
            for item in memory.get("anniversaries", []):
                if not _date_is_grounded(
                    (item["year"], *map(int, str(item["month_day"]).split("-"))), source_dates
                ):
                    continue
                self.store.add_anniversary(**item, source="conversation", memory_id=memory_id)
        return memory_id

    async def process_turn(
        self, user_message_id: int, assistant_message_id: int
    ) -> int | None:
        """攒满配置的轮数再写记忆；单轮调用仍支持旧数据。"""

        async with self._lock:
            user = self.store.get_message(user_message_id)
            assistant = self.store.get_message(assistant_message_id)
            if user is None or assistant is None:
                raise ValueError("随笔找不到对应的原话")
            meta = json.loads(assistant["meta"]) if assistant["meta"] else {}
            linked_user_id = meta.get("inflow_user_message_id")
            if linked_user_id is None:
                meta["inflow_user_message_id"] = user_message_id
                self.store.update_message_meta(assistant_message_id, meta)
            elif linked_user_id != user_message_id:
                raise ValueError("随笔的玩家原话与回复不匹配")
            session_id = int(assistant["session_id"])
            pending = self.store.list_pending_inflow_turns(session_id)
            last_memory_id = None
            while len(pending) >= self.every_n_turns:
                last_memory_id = await self._summarize(pending[: self.every_n_turns])
                pending = self.store.list_pending_inflow_turns(session_id)
            return last_memory_id

    async def flush_pending(self, session_id: int) -> list[int]:
        """续写前把本窗口没攒满的完整轮次也写下来。"""

        async with self._lock:
            memory_ids: list[int] = []
            pending = self.store.list_pending_inflow_turns(session_id)
            while pending:
                memory_ids.append(await self._summarize(pending[: self.every_n_turns]))
                pending = self.store.list_pending_inflow_turns(session_id)
            return memory_ids
