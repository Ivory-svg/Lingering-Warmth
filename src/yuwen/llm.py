"""OpenAI 兼容模型客户端；项目内只有这个模块发外部网络请求。"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
import json
import math
from typing import Any

import httpx

from yuwen.config import LLMConfig


class LLMError(RuntimeError):
    """模型调用失败。"""


class LLMResponseError(LLMError):
    """模型响应缺少所需内容或 JSON 格式错误。"""


Sleep = Callable[[float], Awaitable[None]]


def estimate_tokens(text: str) -> int:
    """按文档约定，用字符数除以 1.5 粗估 token。"""

    return math.ceil(len(text) / 1.5) if text else 0


class LLMClient:
    def __init__(
        self,
        config: LLMConfig,
        *,
        client: httpx.AsyncClient | None = None,
        sleep: Sleep = asyncio.sleep,
    ):
        self.config = config
        self._client = client or httpx.AsyncClient(timeout=60)
        self._owns_client = client is None
        self._sleep = sleep

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _request_text(
        self,
        messages: Sequence[dict[str, str]],
        *,
        small: bool,
        json_response: bool,
    ) -> str:
        payload: dict[str, Any] = {
            "model": self.config.small_model if small else self.config.model,
            "messages": list(messages),
        }
        if json_response:
            payload["response_format"] = {"type": "json_object"}

        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = await self._client.post(
                    f"{self.config.base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.config.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()
                content = data["choices"][0]["message"]["content"]
                if not isinstance(content, str) or not content:
                    raise LLMResponseError("模型响应里没有文本内容")
                return content
            except (
                httpx.HTTPError,
                LLMResponseError,
                KeyError,
                IndexError,
                TypeError,
                ValueError,
            ) as exc:
                last_error = exc
                if attempt == 2:
                    break
                await self._sleep(2**attempt)
        raise LLMError("模型调用在重试后仍然失败") from last_error

    async def chat(
        self,
        messages: Sequence[dict[str, str]],
        *,
        small: bool = False,
        json_response: bool = False,
    ) -> str:
        """生成回复；要求 JSON 时会验证格式，并在解析失败后重试一次。"""

        parse_error: Exception | None = None
        parse_attempts = 2 if json_response else 1
        for _ in range(parse_attempts):
            text = await self._request_text(
                messages,
                small=small,
                json_response=json_response,
            )
            if not json_response:
                return text
            try:
                parsed = json.loads(text)
                if not isinstance(parsed, dict):
                    raise ValueError("JSON 顶层必须是对象")
                return text
            except (json.JSONDecodeError, ValueError) as exc:
                parse_error = exc
        raise LLMResponseError("模型连续返回了无效 JSON") from parse_error
