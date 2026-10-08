"""按相关性和遗忘程度，把少量旧记忆递进当前对话。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
import math
import unicodedata
from typing import Any

import numpy as np

from yuwen.config import DecayConfig, EmbeddingConfig, SurfacingConfig
from yuwen.embedder import EmbeddingModel
from yuwen.store import Store


logger = logging.getLogger(__name__)

_INTRO = (
    "这些是你此刻自然想起来的往事。不要复述，不要列出来，用得上就自然带进对话；\n"
    "标着“记不太清”的，要说“好像是”“我记得大概是”，不能说得很肯定。"
)


@dataclass(frozen=True, slots=True)
class SurfacedResult:
    text: str
    memory_ids: tuple[int, ...]


def _bigrams(text: str) -> set[str]:
    letters = "".join(
        char
        for char in text.casefold()
        if not char.isspace() and unicodedata.category(char)[0] not in {"P", "Z"}
    )
    return {letters[index : index + 2] for index in range(len(letters) - 1)}


class SurfacingService:
    def __init__(
        self,
        store: Store,
        *,
        config: SurfacingConfig = SurfacingConfig(),
        decay: DecayConfig = DecayConfig(),
        embedding: EmbeddingConfig = EmbeddingConfig(),
        client: EmbeddingModel | None = None,
        player_name: str = "你",
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ):
        self.store = store
        self.config = config
        self.decay = decay
        self.embedding = embedding
        self.client = client
        self.player_name = player_name
        self.clock = clock

    def _visible_messages(self, session_id: int) -> list[dict[str, Any]]:
        session = self.store.get_session(session_id)
        if session is None:
            raise ValueError(f"找不到窗口：{session_id}")
        previous: list[dict[str, Any]] = []
        if session["continues_handoff_id"] is not None:
            handoff = self.store.get_handoff(int(session["continues_handoff_id"]))
            if handoff is not None:
                ids = json.loads(handoff["raw_tail_message_ids"])
                previous = self.store.get_messages_by_ids(ids)
        return [*previous, *self.store.list_messages(session_id)]

    @staticmethod
    def _literal_similarity(query: str, memory: dict[str, Any]) -> float:
        query_pairs = _bigrams(query)
        if not query_pairs:
            return 0.0
        text = str(memory["summary"]) + " " + str(memory["quote"] or "")
        return len(query_pairs & _bigrams(text)) / len(query_pairs)

    async def _semantic_similarities(
        self, query: str, candidates: list[dict[str, Any]]
    ) -> list[tuple[dict[str, Any], float]]:
        assert self.client is not None
        query_vectors = await self.client.embed([query])
        if len(query_vectors) != 1:
            raise ValueError("查询向量数量不正确")
        query_vector = np.asarray(query_vectors[0], dtype="<f4")
        if query_vector.ndim != 1 or not query_vector.size or not np.isfinite(query_vector).all():
            raise ValueError("查询向量无效")
        rows: list[dict[str, Any]] = []
        vectors: list[np.ndarray] = []
        for memory in candidates:
            vector = np.frombuffer(memory["embedding"], dtype="<f4")
            if vector.size != query_vector.size or not np.isfinite(vector).all():
                raise ValueError("记忆向量无效或维度不一致")
            rows.append(memory)
            vectors.append(vector)
        matrix = np.stack(vectors)
        lengths = np.linalg.norm(matrix, axis=1) * np.linalg.norm(query_vector)
        similarities = np.divide(
            matrix @ query_vector,
            lengths,
            out=np.zeros(len(rows), dtype=float),
            where=lengths > 0,
        )
        return list(zip(rows, (float(value) for value in similarities)))

    def _retention(self, memory: dict[str, Any], now: datetime) -> float:
        happened = datetime.fromisoformat(str(memory["happened_at"]))
        last = memory["last_recalled_at"]
        remembered = max(happened, datetime.fromisoformat(str(last))) if last else happened
        days = max(0.0, (now - remembered).total_seconds() / 86_400)
        arousal = float(memory["arousal"]) if memory["arousal"] is not None else 0.3
        stability = self.decay.base_days * (1 + int(memory["recall_count"])) * (1 + arousal)
        return math.exp(-days / stability)

    def _line(self, memory: dict[str, Any], now: datetime) -> str:
        happened = datetime.fromisoformat(str(memory["happened_at"]))
        date = f"{happened.month}月{happened.day}日"
        if happened.year != now.year:
            date = f"{happened.year}年{date}"
        clarity = (
            "记得清楚"
            if float(memory["certainty"]) >= self.config.clear_threshold
            else "记不太清"
        )
        summary = str(memory["summary"]).replace("她", self.player_name)
        line = f"· {date}（{clarity}）：{summary}。当时的心情：{memory['emotion_text']}。"
        if memory["quote"]:
            line += f"原话「{memory['quote']}」"
        return line

    async def find(self, session_id: int, player_text: str) -> SurfacedResult | None:
        """挑选记忆但暂不计次，供交接前后决定最终带入内容。"""

        if not self.config.enabled or self.config.max_chars <= len(_INTRO) + 1:
            return None
        try:
            visible = self._visible_messages(session_id)
            visible_ids = {int(message["id"]) for message in visible}
            recent = [
                self.store.visible_message_content(message)
                for message in visible
                if message["role"] in {"user", "assistant"}
            ]
            if recent and recent[-1] == player_text:
                recent.pop()
            query = "\n".join([*recent[-(self.config.query_depth - 1) :], player_text]) if self.config.query_depth > 1 else player_text

            def eligible(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
                return [
                    row for row in rows
                    if not visible_ids.intersection(json.loads(row["source_message_ids"]))
                ]

            use_embedding = bool(self.embedding.base_url and self.client)
            if use_embedding:
                candidates = eligible(self.store.list_recall_candidates(self.embedding.model))
                try:
                    scored = await self._semantic_similarities(query, candidates) if candidates else []
                    threshold = self.config.min_similarity
                except Exception as exc:
                    logger.warning("向量浮现失败，改用字面匹配：%s", exc)
                    use_embedding = False
            if not use_embedding:
                candidates = eligible(self.store.list_recall_candidates())
                scored = [(row, self._literal_similarity(query, row)) for row in candidates]
                threshold = 0.2

            now = self.clock()
            ranked = sorted(
                (
                    (row, similarity, similarity * (0.6 + 0.4 * self._retention(row, now)))
                    for row, similarity in scored
                    if similarity >= threshold
                ),
                key=lambda item: (-item[2], -item[1], -int(item[0]["id"])),
            )
            lines: list[str] = []
            selected_ids: list[int] = []
            length = len(_INTRO)
            for row, _, _ in ranked:
                line = self._line(row, now)
                if length + 1 + len(line) > self.config.max_chars:
                    continue
                lines.append(line)
                selected_ids.append(int(row["id"]))
                length += 1 + len(line)
                if len(lines) >= self.config.top_k:
                    break
            if not lines:
                return None
            return SurfacedResult(_INTRO + "\n" + "\n".join(lines), tuple(selected_ids))
        except Exception as exc:
            logger.warning("字面浮现失败，本轮跳过：%s", exc)
            return None

    def mark_recalled(self, *results: SurfacedResult | None) -> None:
        ids = sorted({memory_id for result in results if result for memory_id in result.memory_ids})
        if not ids:
            return
        try:
            self.store.mark_memories_recalled(ids, recalled_at=self.clock().isoformat())
        except Exception as exc:
            logger.warning("记录记忆浮现失败，继续本轮对话：%s", exc)

    async def recall(self, session_id: int, player_text: str) -> str | None:
        """直接浮现一轮；失败时不打断对话。"""

        result = await self.find(session_id, player_text)
        self.mark_recalled(result)
        return result.text if result else None
