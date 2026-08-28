import asyncio
from collections.abc import Iterable, Sequence

from .compare_pipeline import compare_batch
from .config import CompareConfig, NormalizeConfig
from .models import CompareResult


def _ensure_sync(function_name: str) -> None:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return
    raise RuntimeError(
        f"{function_name}() cannot be used inside an event loop; await compare_batch()"
    )


def compare(
    set_a: Sequence[str],
    set_b: Sequence[str],
    config: CompareConfig,
    *,
    normalize_config: NormalizeConfig | None = None,
) -> CompareResult:
    _ensure_sync("compare")
    return asyncio.run(
        compare_batch([(set_a, set_b)], config, normalize_config=normalize_config)
    )[0]


def compare_many(
    pairs: Iterable[tuple[Sequence[str], Sequence[str]]],
    config: CompareConfig,
    *,
    normalize_config: NormalizeConfig | None = None,
) -> list[CompareResult]:
    _ensure_sync("compare_many")
    return asyncio.run(
        compare_batch(list(pairs), config, normalize_config=normalize_config)
    )
