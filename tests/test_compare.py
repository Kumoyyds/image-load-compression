import asyncio
from io import BytesIO
from types import SimpleNamespace
from typing import Any

import pytest
from PIL import Image

from image_load_compression import CompareConfig, CompareStatus, NormalizeConfig
from image_load_compression.compare_api import compare
from image_load_compression.compare_client import ChatResponse, CompareError, QwenChatClient
from image_load_compression.compare_pipeline import compare_batch
from image_load_compression.compare_prompt import TASK_PROMPT
from image_load_compression.fetch import FetchError, FetchResponse
from image_load_compression.models import ErrorLayer


def image_bytes(image_format: str) -> bytes:
    output = BytesIO()
    mode = "P" if image_format == "GIF" else "RGB"
    Image.new(mode, (10, 10), 1).save(output, image_format)
    return output.getvalue()


class FakeFetcher:
    def __init__(self, formats: dict[str, str] | None = None) -> None:
        self.formats = formats or {}
        self.calls: list[str] = []

    async def get(self, url: str) -> FetchResponse:
        self.calls.append(url)
        if url.startswith("bad"):
            raise FetchError(ErrorLayer.L2C, "HTTP 404", 0)
        return FetchResponse(image_bytes(self.formats.get(url, "PNG")), 0)


class FakeChatClient:
    def __init__(self, failures: set[int] | None = None) -> None:
        self.failures = failures or set()
        self.calls: list[list[dict[str, Any]]] = []

    async def complete(self, messages: list[dict[str, Any]]) -> ChatResponse:
        call_number = len(self.calls)
        self.calls.append(messages)
        if call_number in self.failures:
            raise RuntimeError("model unavailable")
        return ChatResponse(
            text=f"analysis {call_number}",
            prompt_tokens=10 + call_number,
            completion_tokens=2,
        )


def image_parts(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        part
        for part in messages[1]["content"]
        if part["type"] == "image_url"
    ]


@pytest.mark.asyncio
async def test_dedupes_truncates_and_fetches_shared_urls_once() -> None:
    fetcher = FakeFetcher()
    chat = FakeChatClient()
    config = CompareConfig(api_key="unused", max_images_per_set=2)

    results = await compare_batch(
        [(["a1", "a1", "shared", "truncated"], ["shared", "b2"])],
        config,
        normalize_config=NormalizeConfig(),
        chat_client=chat,
        fetcher=fetcher,
    )

    assert fetcher.calls == ["a1", "shared", "b2"]
    assert results[0].set_a_images_used == 2
    assert results[0].set_b_images_used == 2
    assert len(image_parts(chat.calls[0])) == 4


@pytest.mark.asyncio
async def test_failed_fetch_is_dropped_and_absent_from_message() -> None:
    fetcher = FakeFetcher()
    chat = FakeChatClient()

    result = (
        await compare_batch(
            [(["a", "bad-a"], ["b"])],
            CompareConfig(api_key="unused"),
            chat_client=chat,
            fetcher=fetcher,
        )
    )[0]

    assert result.status == CompareStatus.SUCCESS
    assert result.dropped_urls == ["bad-a"]
    assert len(image_parts(chat.calls[0])) == 2
    assert all("bad-a" not in part["image_url"]["url"] for part in image_parts(chat.calls[0]))


@pytest.mark.asyncio
async def test_empty_or_unusable_set_skips_chat_call() -> None:
    chat = FakeChatClient()

    results = await compare_batch(
        [([], ["b"]), (["bad-a"], ["b"])],
        CompareConfig(api_key="unused"),
        chat_client=chat,
        fetcher=FakeFetcher(),
    )

    assert [result.status for result in results] == [
        CompareStatus.INSUFFICIENT_IMAGES,
        CompareStatus.INSUFFICIENT_IMAGES,
    ]
    assert all(result.comment for result in results)
    assert chat.calls == []


@pytest.mark.asyncio
async def test_one_model_failure_does_not_abort_batch_and_order_is_preserved() -> None:
    chat = FakeChatClient(failures={0})

    results = await compare_batch(
        [(["a1"], ["b1"]), (["a2"], ["b2"])],
        CompareConfig(api_key="unused", concurrency=1),
        chat_client=chat,
        fetcher=FakeFetcher(),
    )

    assert [result.status for result in results] == [
        CompareStatus.FAILED,
        CompareStatus.SUCCESS,
    ]
    assert "model unavailable" in results[0].comment
    assert results[1].comment == "analysis 1"
    assert results[1].prompt_tokens == 11


@pytest.mark.asyncio
async def test_message_markers_images_and_prompt_order() -> None:
    chat = FakeChatClient()
    await compare_batch(
        [(["a1", "a2"], ["b1"])],
        CompareConfig(api_key="unused"),
        chat_client=chat,
        fetcher=FakeFetcher(),
    )

    messages = chat.calls[0]
    content = messages[1]["content"]
    assert messages[0]["role"] == "system"
    assert content[0]["text"] == "[SET A] 2 image(s)"
    assert content[3]["text"] == "[SET B] 1 image(s)"
    assert content[-1] == {"type": "text", "text": TASK_PROMPT}
    assert len(image_parts(messages)) == 3


@pytest.mark.asyncio
async def test_data_urls_cover_every_supported_mime_type() -> None:
    formats = {"jpeg": "JPEG", "webp": "WEBP", "png": "PNG", "gif": "GIF"}
    chat = FakeChatClient()
    await compare_batch(
        [(["jpeg", "webp"], ["png", "gif"])],
        CompareConfig(api_key="unused"),
        normalize_config=NormalizeConfig(size_budget_bytes=100_000),
        chat_client=chat,
        fetcher=FakeFetcher(formats),
    )

    prefixes = [part["image_url"]["url"].split(";", 1)[0] for part in image_parts(chat.calls[0])]
    assert prefixes == ["data:image/jpeg", "data:image/webp", "data:image/png", "data:image/gif"]


@pytest.mark.asyncio
async def test_compare_sync_api_rejects_running_loop() -> None:
    with pytest.raises(
        RuntimeError,
        match=r"compare\(\) cannot be used inside an event loop; await compare_batch\(\)",
    ):
        compare(["a"], ["b"], CompareConfig(api_key="unused"))


@pytest.mark.asyncio
async def test_concurrency_limit_is_enforced() -> None:
    class BlockingChat(FakeChatClient):
        def __init__(self) -> None:
            super().__init__()
            self.active = 0
            self.maximum = 0

        async def complete(self, messages):
            self.calls.append(messages)
            self.active += 1
            self.maximum = max(self.maximum, self.active)
            await asyncio.sleep(0)
            self.active -= 1
            return ChatResponse("analysis")

    chat = BlockingChat()
    await compare_batch(
        [([f"a{i}"], [f"b{i}"]) for i in range(4)],
        CompareConfig(api_key="unused", concurrency=2),
        chat_client=chat,
        fetcher=FakeFetcher(),
    )

    assert chat.maximum == 2


@pytest.mark.asyncio
async def test_qwen_client_sends_config_and_returns_usage() -> None:
    class Completions:
        def __init__(self) -> None:
            self.kwargs = None

        async def create(self, **kwargs):
            self.kwargs = kwargs
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="  evidence  ", refusal=None))],
                usage=SimpleNamespace(prompt_tokens=12, completion_tokens=3),
            )

    completions = Completions()
    injected = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    config = CompareConfig(
        api_key="unused", model="test-vl", temperature=0.2, max_output_tokens=321
    )
    client = QwenChatClient(config, client=injected)

    response = await client.complete([{"role": "user", "content": "test"}])
    await client.aclose()

    assert response == ChatResponse("evidence", 12, 3)
    assert completions.kwargs["model"] == "test-vl"
    assert completions.kwargs["temperature"] == 0.2
    assert completions.kwargs["max_tokens"] == 321
    assert completions.kwargs["extra_body"] == {"max_pixels": 1024 * 1024}


@pytest.mark.asyncio
async def test_qwen_client_rejects_refusal_even_when_text_is_present() -> None:
    class Completions:
        async def create(self, **kwargs):
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content="partial", refusal="not allowed")
                    )
                ],
                usage=None,
            )

    injected = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
    client = QwenChatClient(CompareConfig(api_key="unused"), client=injected)

    with pytest.raises(CompareError, match="model refused the request: not allowed"):
        await client.complete([{"role": "user", "content": "test"}])
