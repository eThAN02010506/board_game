import asyncio
import json

import httpx
import pytest

from ai_kp.infrastructure.llm.openai_compatible import OpenAICompatibleClient
from ai_kp.platform.ports.llm import ChatMessage


def test_gpt_oss_uses_low_harmony_reasoning_effort_by_default() -> None:
    payloads: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payloads.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": '{"ok":true}',
                            "reasoning_content": "brief",
                        },
                    }
                ]
            },
        )

    async def exercise() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
        ) as http_client:
            client = OpenAICompatibleClient(
                "http://model.test/v1",
                "",
                r"C:\models\GPT-OSS-20B-MXFP4.gguf",
                client=http_client,
            )
            result = await client.complete(
                [ChatMessage(role="user", content="返回 JSON")],
                temperature=0.1,
            )
            assert result == '{"ok":true}'

    asyncio.run(exercise())

    assert payloads[0]["chat_template_kwargs"] == {
        "reasoning_effort": "low"
    }
    assert payloads[0]["temperature"] == 1.0
    assert payloads[0]["top_p"] == 1.0
    assert payloads[0]["response_format"] == {"type": "json_object"}


def test_non_gpt_oss_provider_does_not_receive_llamacpp_template_hints() -> None:
    payloads: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payloads.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": "ok"},
                    }
                ]
            },
        )

    async def exercise() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
        ) as http_client:
            client = OpenAICompatibleClient(
                "http://model.test/v1",
                "",
                "qwen-local",
                client=http_client,
            )
            assert await client.complete(
                [ChatMessage(role="user", content="ping")]
            ) == "ok"

    asyncio.run(exercise())

    assert "chat_template_kwargs" not in payloads[0]
    assert payloads[0]["temperature"] == 0.7
    assert "top_p" not in payloads[0]
    assert "thinking" not in payloads[0]


def test_deepseek_v4_disables_default_thinking_for_bounded_agent_output() -> None:
    payloads: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payloads.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": '{"ok":true}'},
                    }
                ]
            },
        )

    async def exercise() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
        ) as http_client:
            client = OpenAICompatibleClient(
                "https://api.deepseek.com",
                "secret",
                "deepseek-v4-flash",
                client=http_client,
            )
            assert await client.complete(
                [ChatMessage(role="user", content="返回 JSON")]
            ) == '{"ok":true}'

    asyncio.run(exercise())

    assert payloads == [
        {
            "model": "deepseek-v4-flash",
            "temperature": 0.7,
            "max_tokens": 4096,
            "messages": [{"role": "user", "content": "返回 JSON"}],
            "thinking": {"type": "disabled"},
        }
    ]


def test_deepseek_v4_retries_without_optional_thinking_for_strict_proxy() -> None:
    payloads: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        payloads.append(payload)
        if "thinking" in payload:
            return httpx.Response(422, json={"error": "extra fields forbidden"})
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": "ok"},
                    }
                ]
            },
        )

    async def exercise() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
        ) as http_client:
            client = OpenAICompatibleClient(
                "http://strict-proxy.test/v1",
                "",
                "deepseek-v4-pro",
                client=http_client,
            )
            assert await client.complete(
                [ChatMessage(role="user", content="ping")]
            ) == "ok"

    asyncio.run(exercise())

    assert len(payloads) == 2
    assert payloads[0]["thinking"] == {"type": "disabled"}
    assert "thinking" not in payloads[1]


def test_gpt_oss_retries_without_llamacpp_extension_for_strict_provider() -> None:
    payloads: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        payloads.append(payload)
        if "chat_template_kwargs" in payload:
            return httpx.Response(400, json={"error": "extra fields forbidden"})
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": '{"ok":true}'},
                    }
                ]
            },
        )

    async def exercise() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
        ) as http_client:
            client = OpenAICompatibleClient(
                "http://strict-provider.test/v1",
                "",
                "gpt-oss-20b",
                client=http_client,
            )
            assert await client.complete(
                [ChatMessage(role="user", content="返回 JSON")],
                temperature=0.0,
            ) == '{"ok":true}'

    asyncio.run(exercise())

    assert len(payloads) == 2
    assert "chat_template_kwargs" in payloads[0]
    assert "chat_template_kwargs" not in payloads[1]
    assert payloads[1]["temperature"] == 1.0
    assert payloads[1]["top_p"] == 1.0


def test_chat_response_is_streamed_with_a_decoded_body_limit() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"x" * (OpenAICompatibleClient.max_response_bytes + 1),
            headers={"content-length": "invalid-on-purpose"},
        )

    async def exercise() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
        ) as http_client:
            client = OpenAICompatibleClient(
                "http://model.test/v1",
                "",
                "bounded-local-model",
                client=http_client,
            )
            with pytest.raises(RuntimeError, match="8 MiB"):
                await client.complete([ChatMessage(role="user", content="ping")])

    asyncio.run(exercise())
