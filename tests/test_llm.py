import asyncio
import json

import httpx
import pytest

from yuwen.config import LLMConfig
from yuwen.llm import LLMClient, LLMResponseError, estimate_tokens


def config() -> LLMConfig:
    return LLMConfig(
        base_url="https://llm.test/v1",
        api_key="secret",
        model="large-model",
        small_model="small-model",
    )


async def no_sleep(_: float) -> None:
    return None


def test_estimate_tokens_uses_documented_approximation() -> None:
    assert estimate_tokens("") == 0
    assert estimate_tokens("今天好累") == 3


def test_chat_uses_small_model_and_openai_shape() -> None:
    captured: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        assert request.headers["Authorization"] == "Bearer secret"
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "先休息一下。"}}]},
        )

    async def run() -> str:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = LLMClient(config(), client=http, sleep=no_sleep)
            return await client.chat([{"role": "user", "content": "累"}], small=True)

    assert asyncio.run(run()) == "先休息一下。"
    assert captured[0]["model"] == "small-model"


def test_chat_retries_twice_after_http_failures() -> None:
    attempts = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            return httpx.Response(503, json={"error": "busy"})
        return httpx.Response(200, json={"choices": [{"message": {"content": "好了"}}]})

    async def run() -> str:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            return await LLMClient(config(), client=http, sleep=no_sleep).chat([])

    assert asyncio.run(run()) == "好了"
    assert attempts == 3


def test_chat_retries_malformed_openai_response() -> None:
    attempts = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(200, json={"choices": []})
        return httpx.Response(200, json={"choices": [{"message": {"content": "恢复了"}}]})

    async def run() -> str:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            return await LLMClient(config(), client=http, sleep=no_sleep).chat([])

    assert asyncio.run(run()) == "恢复了"
    assert attempts == 2


def test_json_response_retries_once_after_invalid_json() -> None:
    replies = iter(["不是 JSON", '{"verdict":"in_character"}'])
    requests: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": next(replies)}}]},
        )

    async def run() -> str:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            return await LLMClient(config(), client=http, sleep=no_sleep).chat(
                [], json_response=True
            )

    assert json.loads(asyncio.run(run()))["verdict"] == "in_character"
    assert len(requests) == 2
    assert requests[0]["response_format"] == {"type": "json_object"}


def test_json_response_raises_after_two_invalid_results() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "坏掉了"}}]})

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            await LLMClient(config(), client=http, sleep=no_sleep).chat([], json_response=True)

    with pytest.raises(LLMResponseError):
        asyncio.run(run())
