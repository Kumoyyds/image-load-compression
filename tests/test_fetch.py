import httpx
import pytest

from image_load_compression import ErrorLayer, NormalizeConfig
from image_load_compression.fetch import FetchError, HttpxFetcher


@pytest.mark.asyncio
async def test_server_error_retries_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(503 if attempts == 1 else 200, content=b"image")

    async def no_wait(_: float) -> None:
        pass

    monkeypatch.setattr("image_load_compression.fetch.asyncio.sleep", no_wait)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        response = await HttpxFetcher(NormalizeConfig(), client).get("https://example.test/image")

    assert response.data == b"image"
    assert response.retry_count == 1


@pytest.mark.asyncio
async def test_dead_link_does_not_retry() -> None:
    attempts = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(FetchError) as caught:
            await HttpxFetcher(NormalizeConfig(), client).get("https://example.test/missing")

    assert caught.value.layer == ErrorLayer.L2C
    assert attempts == 1


@pytest.mark.asyncio
async def test_declared_oversize_download_is_rejected() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"Content-Length": "11"}, content=b"too-large!!")

    config = NormalizeConfig(max_download_bytes=10)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(FetchError) as caught:
            await HttpxFetcher(config, client).get("https://example.test/large")

    assert caught.value.layer == ErrorLayer.L3

