# Image-set comparison (product matching evidence)

## Context

A separate repo is building a product-matching feature that answers "are these two listings literally the exact same product?" using title, description, **and** images. This module currently only fetches + normalizes images (`normalize_batch` → JPEG/WEBP under a size budget). The new feature adds the image half of that decision: given two sets of image URLs (one per listing), produce an English `comment` describing what the images show, where the two products agree, and where they conflict.

It deliberately stops short of a verdict. The downstream repo weighs images against title/description, so this module emits **evidence and opinion, never a same/different conclusion or match score**.

Key constraints driving the design:
- Focus on the product itself — ignore models, backgrounds, props, lighting, crop, watermarks.
- Matching standard downstream is strict: different pack count / size / colorway = different product.
- This is a library (`import image_load_compression`), not a service. It must expose a clean sync API and an async core.
- **The package never supplies an API key.** The caller injects it via config.

## Decisions (confirmed with user)

| Decision | Choice |
|---|---|
| Output | `CompareResult` dataclass, `comment: str` is the main field |
| Comment language | English |
| Model strategy | Single multi-image chat call per pair (both sets in one message) |
| Endpoint | DashScope compatible mode, `qwen3-vl-flash`, base_url/model configurable |

## Approach

Reuse everything that exists. The whole fetch/compress path (`pipeline.normalize_batch`, `fetch.HttpxFetcher`, `process.normalize_image`) already produces exactly the bytes a VLM should be fed, and `openai` is already a core dependency. The new code is: a config, a prompt, a data-URL adapter, and an async fan-out.

### Files to change

**1. [config.py](src/image_load_compression/config.py) — add `CompareConfig` + `load_compare_config`**

Add a frozen slots dataclass alongside `NormalizeConfig`:

```python
@dataclass(frozen=True, slots=True)
class CompareConfig:
    api_key: str                     # required — injected by the caller, never read from YAML/env here
    base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    model: str = "qwen3-vl-flash"
    max_images_per_set: int = 6
    concurrency: int = 4             # concurrent VLM calls (pairs in flight)
    request_timeout: float = 90.0
    max_retries: int = 2
    max_pixels: int = 1024 * 1024    # DashScope visual-token cap per image
    temperature: float = 0.0
    max_output_tokens: int = 700
```
`__post_init__` validates positivity and non-empty `api_key`, matching the existing style.

The YAML loader must gain a `compare:` section without breaking the existing "unknown keys raise" guarantee (`config.py:105-107`). Restructure `_FIELD_MAP` values from `(field, factor)` to `(target, field, factor)` where `target` is `"normalize"` or `"compare"`; factor out the read+flatten into a `_read_flat(path)` helper; `load_config` keeps only `target == "normalize"` entries, and `load_compare_config(path=None, *, api_key)` keeps `"compare"` ones. Unknown-key validation still runs against the whole map, so typos in either section still raise.

Explicitly reject `compare.api_key` in YAML with a clear error — the key is a caller responsibility (constraint 5).

**2. [config.yaml](src/image_load_compression/config.yaml) — new `compare:` block**

```yaml
compare:
  model: qwen3-vl-flash
  base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1"
  max_images_per_set: 6
  concurrency: 4
  request_timeout: 90.0
  max_retries: 2
  max_pixels: 1048576
  temperature: 0.0
  max_output_tokens: 700
```

**3. [models.py](src/image_load_compression/models.py) — add `CompareStatus` + `CompareResult`**

Models live here already; keep it that way.

```python
class CompareStatus(StrEnum):
    SUCCESS = "success"
    INSUFFICIENT_IMAGES = "insufficient_images"   # one/both sets had no usable image
    FAILED = "failed"                             # model/transport error

@dataclass(slots=True)
class CompareResult:
    comment: str
    status: CompareStatus
    set_a_images_used: int = 0
    set_b_images_used: int = 0
    dropped_urls: list[str] = field(default_factory=list)
    model: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    error_detail: str | None = None

    def to_dict(self) -> dict[str, Any]: ...   # plain asdict, mirrors ImageResult.to_dict
```
On any non-success path `comment` still holds readable English text explaining why no analysis is available, so a downstream caller that only reads `.comment` never gets an empty string.

**4. `compare_prompt.py` (new) — prompt text and message assembly**

Isolated because it is mostly long English prose. Contains:
- `SYSTEM_PROMPT`: e-commerce product image analyst; reports evidence, not verdicts.
- `TASK_PROMPT`, encoding the constraints:
  - Consider only the product itself. Ignore human models, backgrounds, props, staging, lighting, camera angle, crop, resolution, retailer overlay badges and watermarks.
  - Downstream matching is strict and literal: brand, model/variant name, colorway, size/capacity/weight, **pack count/quantity**, and included accessories must all match. A different count or size is a different product.
  - Transcribe legible packaging/label text verbatim when it carries an identifying attribute.
  - Report in this shape: `Set A`, `Set B`, `Agreements`, `Discrepancies`, `Cannot be determined from images`.
  - Hard rule: **do not** state whether the two are the same product, and do not emit a match score, probability, or yes/no. Confidence may be expressed about individual observations only.
  - Flag when a set is lifestyle-only, partial, or too low-quality to read attributes.
  - Plain prose, ≤ ~250 words.
- `build_messages(set_a_urls: list[str], set_b_urls: list[str]) -> list[dict]` — interleaved content parts: a `[SET A] N image(s)` text marker, the Set A images, a `[SET B] M image(s)` marker, the Set B images, then `TASK_PROMPT`. Same content-part shape as [playground.ipynb](playground.ipynb) cell 6 and `scripts/calibrate_qwen.py:ask`.

**5. `compare_client.py` (new) — VLM transport, injectable**

Mirrors the `Fetcher` protocol pattern in [fetch.py](src/image_load_compression/fetch.py:37) so tests can run offline.

```python
@dataclass(frozen=True, slots=True)
class ChatResponse:
    text: str
    prompt_tokens: int | None
    completion_tokens: int | None

class ChatClient(Protocol):
    async def complete(self, messages: list[dict]) -> ChatResponse: ...

class QwenChatClient:
    def __init__(self, config: CompareConfig, client: AsyncOpenAI | None = None): ...
    async def aclose(self) -> None: ...        # only closes a client it owns
    async def complete(self, messages) -> ChatResponse: ...
```
Uses `AsyncOpenAI(api_key=..., base_url=..., timeout=config.request_timeout, max_retries=config.max_retries)` — the SDK's own retry/backoff, no hand-rolled loop needed here. Passes `extra_body={"max_pixels": config.max_pixels}` and `max_tokens`/`temperature`. Raises a `CompareError(detail)` on empty/refused completions.

**6. `compare_pipeline.py` (new) — async core**

```python
async def compare_batch(
    pairs: Iterable[tuple[Sequence[str], Sequence[str]]],
    config: CompareConfig,
    *,
    normalize_config: NormalizeConfig | None = None,
    chat_client: ChatClient | None = None,
    fetcher: Fetcher | None = None,
) -> list[CompareResult]
```

Two phases:

1. **Prepare images once.** Per set: dedupe preserving order, truncate to `max_images_per_set`. Union all URLs across *all* pairs, dedupe globally, and make a single `normalize_batch(all_urls, normalize_config or load_config(), run_id, fetcher=fetcher)` call — one httpx client, one semaphore, and shared URLs are fetched once. Build `url -> data_url` from results where `image_bytes` is set (`Status.SUCCESS` and `Status.BUDGET_UNMET` both carry bytes; `FAILED` does not). Every dropped URL is recorded on the owning pair's result.
   - MIME map must cover all of `process.SUPPORTED_FORMATS` — a passthrough result keeps its original format, so PNG and GIF are reachable, not just JPEG/WEBP.
2. **Fan out the calls.** `asyncio.Semaphore(config.concurrency)`, one `complete()` per pair, `asyncio.gather`. Per-pair `try/except` so one bad pair never aborts the batch — same shape as [pipeline.py:38-44](src/image_load_compression/pipeline.py#L38-L44). A pair where either set has zero usable images short-circuits to `INSUFFICIENT_IMAGES` **without** spending a model call.

Owns and closes the `QwenChatClient` it creates (`try/finally`), same as `normalize_batch` does for `HttpxFetcher`.

**7. `compare_api.py` (new) — sync facade**

```python
def compare(set_a, set_b, config, *, normalize_config=None) -> CompareResult
def compare_many(pairs, config, *, normalize_config=None) -> list[CompareResult]
```
Both guard against a running event loop with the same `RuntimeError` message style as [api.py:30-35](src/image_load_compression/api.py#L30-L35), then `asyncio.run(compare_batch(...))`.

**8. [\_\_init\_\_.py](src/image_load_compression/__init__.py) — export the new surface**

Add `CompareConfig`, `CompareResult`, `CompareStatus`, `load_compare_config`, `compare`, `compare_many`, `compare_batch` to the imports and `__all__`.

No `pyproject.toml` change — `openai`, `httpx`, `pillow`, `pyyaml` are all already core deps.

### Downstream usage this enables

```python
from image_load_compression import compare_many, load_compare_config

cfg = load_compare_config(api_key=my_secret)          # key comes from the caller
results = compare_many([(site1_urls, site2_urls), ...], cfg)
comments = [r.comment for r in results]
```

## Verification

1. **Offline unit tests** — `tests/test_compare.py` and `tests/test_compare_config.py`, using the `FakeFetcher` injection pattern from `tests/test_pipeline.py` plus a new `FakeChatClient`. Cover:
   - per-set dedupe and `max_images_per_set` truncation
   - failed-fetch URLs land in `dropped_urls` and are absent from the messages
   - empty set → `INSUFFICIENT_IMAGES` with **zero** chat calls
   - one pair raising does not abort the batch; order of results matches order of `pairs`
   - message shape: set markers present, image-part count == usable images, prompt is last
   - MIME mapping for JPEG/WEBP/PNG/GIF
   - YAML with a `compare:` section loads both configs; `compare.api_key` in YAML raises; a typo in either section still raises
   - `compare()` inside a running loop raises `RuntimeError`
   Run: `uv run pytest`

2. **Live smoke** — new `scripts/compare_smoke.py`:
   - Parses [data/input_compare.txt](data/input_compare.txt) (`# example N` / `## website N` headers, blank-line separated) into `[(set1, set2), ...]` — 2 examples today. This parser stays in `scripts/`, not the package: it is a fixture format, not part of the API. Note the plain-URL `cli.py` parser cannot read this file.
   - Reads `QWEN_KEY` / `QWEN_BASE_URL` with the `env_value()` helper copied from `scripts/calibrate_qwen.py:env_value` — it uses `dotenv_values()` with key stripping specifically because [.env](.env) has `QWEN_KEY =...` with a space before `=`.
   - Runs `compare_many`, prints each comment, writes `output/compare/results.jsonl`.
   Run: `uv run python scripts/compare_smoke.py`

   Manual check on the output: example 1 (a 12-pack bundle on joy-sourcing vs an Amazon bundle listing) should surface pack-count and variant observations; example 2 (Tesco vs joy-sourcing) is a cross-retailer pair. Confirm no comment declares a same/different verdict or emits a score — that is the one behavior worth eyeballing every run.

3. **README** — add the compare section to the existing script/status tables.

## Out of scope

- Final match verdict or score (owned by the downstream repo, by design).
- Caching of model responses across runs.
- Persisting the normalized images used for a comparison (the smoke script writes JSONL only).
