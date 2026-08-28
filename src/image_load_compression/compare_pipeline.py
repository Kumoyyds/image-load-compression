import asyncio
import base64
from collections.abc import Iterable, Sequence
from datetime import datetime, timezone

from .compare_client import ChatClient, QwenChatClient
from .compare_prompt import build_messages
from .config import CompareConfig, NormalizeConfig, load_config
from .fetch import Fetcher
from .models import CompareResult, CompareStatus
from .pipeline import normalize_batch


_MIME_TYPES = {
    "GIF": "image/gif",
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "WEBP": "image/webp",
}


def _dedupe_limit(urls: Sequence[str], limit: int) -> list[str]:
    return list(dict.fromkeys(urls))[:limit]


async def compare_batch(
    pairs: Iterable[tuple[Sequence[str], Sequence[str]]],
    config: CompareConfig,
    *,
    normalize_config: NormalizeConfig | None = None,
    chat_client: ChatClient | None = None,
    fetcher: Fetcher | None = None,
) -> list[CompareResult]:
    prepared_pairs = [
        (
            _dedupe_limit(set_a, config.max_images_per_set),
            _dedupe_limit(set_b, config.max_images_per_set),
        )
        for set_a, set_b in pairs
    ]
    all_urls = list(
        dict.fromkeys(url for pair in prepared_pairs for image_set in pair for url in image_set)
    )
    run_id = datetime.now(timezone.utc).strftime("compare-%Y%m%dT%H%M%SZ")
    normalized = await normalize_batch(
        all_urls, normalize_config or load_config(), run_id, fetcher=fetcher
    )

    data_urls: dict[str, str] = {}
    for result in normalized:
        mime = _MIME_TYPES.get((result.output_format or "").upper())
        if result.image_bytes is not None and mime is not None:
            payload = base64.b64encode(result.image_bytes).decode("ascii")
            data_urls[result.url] = f"data:{mime};base64,{payload}"

    owned_client = QwenChatClient(config) if chat_client is None else None
    active_client = chat_client or owned_client
    assert active_client is not None
    semaphore = asyncio.Semaphore(config.concurrency)

    async def one(set_a: list[str], set_b: list[str]) -> CompareResult:
        usable_a = [data_urls[url] for url in set_a if url in data_urls]
        usable_b = [data_urls[url] for url in set_b if url in data_urls]
        dropped = list(
            dict.fromkeys(url for url in (*set_a, *set_b) if url not in data_urls)
        )
        common = dict(
            set_a_images_used=len(usable_a),
            set_b_images_used=len(usable_b),
            dropped_urls=dropped,
            model=config.model,
        )
        if not usable_a or not usable_b:
            missing = "Set A and Set B" if not usable_a and not usable_b else (
                "Set A" if not usable_a else "Set B"
            )
            return CompareResult(
                comment=f"No image analysis is available because {missing} has no usable images.",
                status=CompareStatus.INSUFFICIENT_IMAGES,
                **common,
            )
        try:
            async with semaphore:
                response = await active_client.complete(build_messages(usable_a, usable_b))
            return CompareResult(
                comment=response.text,
                status=CompareStatus.SUCCESS,
                prompt_tokens=response.prompt_tokens,
                completion_tokens=response.completion_tokens,
                **common,
            )
        except Exception as exc:
            detail = f"{type(exc).__name__}: {exc}"
            return CompareResult(
                comment=f"No image analysis is available because the model request failed: {detail}",
                status=CompareStatus.FAILED,
                error_detail=detail,
                **common,
            )

    try:
        return await asyncio.gather(*(one(*pair) for pair in prepared_pairs))
    finally:
        if owned_client:
            await owned_client.aclose()
