import asyncio
from collections.abc import Iterable
from datetime import datetime, timezone
from typing import overload

from .config import NormalizeConfig, load_config
from .models import ImageResult
from .pipeline import normalize_batch


@overload
def run(
    urls: str, config: NormalizeConfig | None = None, *, run_id: str | None = None
) -> ImageResult: ...


@overload
def run(
    urls: Iterable[str], config: NormalizeConfig | None = None, *, run_id: str | None = None
) -> list[ImageResult]: ...


def run(
    urls: str | Iterable[str],
    config: NormalizeConfig | None = None,
    *,
    run_id: str | None = None,
) -> ImageResult | list[ImageResult]:
    """Synchronously normalize one URL or a batch of URLs."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        raise RuntimeError("run() cannot be used inside an event loop; await normalize_batch()")

    single = isinstance(urls, str)
    items = [urls] if single else list(urls)
    batch_id = run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    results = asyncio.run(normalize_batch(items, config or load_config(), batch_id))
    return results[0] if single else results

