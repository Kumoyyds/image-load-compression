import asyncio
from collections.abc import Iterable

from .config import NormalizeConfig
from .fetch import FetchError, Fetcher, HttpxFetcher
from .models import ErrorLayer, ImageResult, Status
from .process import normalize_image


async def normalize_batch(
    urls: Iterable[str],
    config: NormalizeConfig,
    run_id: str,
    *,
    fetcher: Fetcher | None = None,
) -> list[ImageResult]:
    del run_id  # The caller uses it to locate the batch's persisted output.
    semaphore = asyncio.Semaphore(config.concurrency)
    http_fetcher = HttpxFetcher(config) if fetcher is None else None
    active_fetcher = fetcher or http_fetcher
    assert active_fetcher is not None

    async def one(url: str) -> ImageResult:
        async with semaphore:
            try:
                response = await active_fetcher.get(url)
                return await asyncio.to_thread(
                    normalize_image, url, response.data, config, response.retry_count
                )
            except FetchError as exc:
                return ImageResult(
                    url=url,
                    status=Status.FAILED,
                    error_layer=exc.layer,
                    error_detail=exc.detail,
                    retry_count=exc.retry_count,
                )
            except Exception as exc:  # A bad item must never abort the batch.
                return ImageResult(
                    url=url,
                    status=Status.FAILED,
                    error_layer=ErrorLayer.L3,
                    error_detail=f"unexpected error: {type(exc).__name__}: {exc}",
                )

    try:
        return await asyncio.gather(*(one(url) for url in urls))
    finally:
        if http_fetcher:
            await http_fetcher.aclose()

