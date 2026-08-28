import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Protocol

import httpx

from .config import NormalizeConfig
from .models import ErrorLayer


@dataclass(frozen=True, slots=True)
class FetchResponse:
    data: bytes
    retry_count: int


class FetchError(Exception):
    def __init__(self, layer: ErrorLayer, detail: str, retry_count: int) -> None:
        super().__init__(detail)
        self.layer = layer
        self.detail = detail
        self.retry_count = retry_count


class Fetcher(Protocol):
    async def get(self, url: str) -> FetchResponse: ...


class HttpxFetcher:
    def __init__(self, config: NormalizeConfig, client: httpx.AsyncClient | None = None) -> None:
        self.config = config
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(
            follow_redirects=True,
            headers={"User-Agent": config.user_agent, "Accept": "image/*,*/*;q=0.8"},
            timeout=httpx.Timeout(config.read_timeout, connect=config.connect_timeout),
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    async def get(self, url: str) -> FetchResponse:
        retries = 0
        while True:
            try:
                async with self.client.stream("GET", url) as response:
                    layer, limit = self._classify(response.status_code)
                    if layer:
                        detail = f"HTTP {response.status_code}"
                        if retries >= limit:
                            raise FetchError(layer, detail, retries)
                        await asyncio.sleep(self._delay(layer, retries, response.headers.get("Retry-After")))
                        retries += 1
                        continue

                    content_length = response.headers.get("Content-Length", "")
                    if content_length.isdecimal() and int(content_length) > self.config.max_download_bytes:
                        raise FetchError(ErrorLayer.L3, "download exceeds byte limit", retries)

                    chunks: list[bytes] = []
                    size = 0
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > self.config.max_download_bytes:
                            raise FetchError(ErrorLayer.L3, "download exceeds byte limit", retries)
                        chunks.append(chunk)
                    return FetchResponse(b"".join(chunks), retries)
            except FetchError:
                raise
            except httpx.HTTPError as exc:
                if retries >= self.config.l1_retries:
                    raise FetchError(ErrorLayer.L1, f"{type(exc).__name__}: {exc}", retries) from exc
                await asyncio.sleep(1)
                retries += 1

    def _classify(self, status: int) -> tuple[ErrorLayer | None, int]:
        if 200 <= status < 300:
            return None, 0
        if status in {403, 429}:
            return ErrorLayer.L2A, self.config.l2a_retries
        if 500 <= status < 600:
            return ErrorLayer.L2B, self.config.l2b_retries
        return ErrorLayer.L2C, 0

    @staticmethod
    def _delay(layer: ErrorLayer, retry_count: int, retry_after: str | None) -> float:
        if layer != ErrorLayer.L2A:
            return 1
        if retry_after:
            try:
                return max(0, float(retry_after))
            except ValueError:
                try:
                    when = parsedate_to_datetime(retry_after)
                    return max(0, (when - datetime.now(timezone.utc)).total_seconds())
                except (TypeError, ValueError):
                    pass
        return 2**retry_count
