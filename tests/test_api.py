from image_load_compression import ImageResult, Status
from image_load_compression import api


async def fake_batch(urls, config, run_id):
    return [ImageResult(url=url, status=Status.SUCCESS) for url in urls]


def test_run_returns_one_result_for_one_url(monkeypatch) -> None:
    monkeypatch.setattr(api, "normalize_batch", fake_batch)

    result = api.run("https://example.test/image")

    assert isinstance(result, ImageResult)
    assert result.url == "https://example.test/image"


def test_run_returns_a_list_for_multiple_urls(monkeypatch) -> None:
    monkeypatch.setattr(api, "normalize_batch", fake_batch)

    results = api.run(["one", "two"])

    assert [result.url for result in results] == ["one", "two"]
