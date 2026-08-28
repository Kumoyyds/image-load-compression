from io import BytesIO

import pytest
from PIL import Image

from image_load_compression import NormalizeConfig, Status
from image_load_compression.fetch import FetchError, FetchResponse
from image_load_compression.models import ErrorLayer
from image_load_compression.pipeline import normalize_batch


class FakeFetcher:
    async def get(self, url: str) -> FetchResponse:
        if url == "bad":
            raise FetchError(ErrorLayer.L2C, "HTTP 404", 0)
        output = BytesIO()
        Image.new("RGB", (10, 10), "white").save(output, "PNG")
        return FetchResponse(output.getvalue(), 1)


@pytest.mark.asyncio
async def test_one_failure_does_not_abort_batch() -> None:
    results = await normalize_batch(
        ["good", "bad"], NormalizeConfig(), "test-run", fetcher=FakeFetcher()
    )

    assert [result.status for result in results] == [Status.SUCCESS, Status.FAILED]
    assert results[0].retry_count == 1
    assert results[1].error_layer == ErrorLayer.L2C

