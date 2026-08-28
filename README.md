# image-load-compression

Downloads product images and normalizes them for vision-model matching: one long-edge cap, one size budget, one predictable output format. Images that already satisfy both constraints are passed through byte-for-byte, so nothing is re-encoded for no reason. Built for [competitor_product_search](../competitor_product_search) — scraping yields `image_urls`, this turns them into model-ready bytes.

> **Status**: v1 of [plans/spec/image_normalize_spec.md](plans/spec/image_normalize_spec.md) (D-IMG-01..16). The `suspicious` status and error layer `L4` are reserved in the model but never triggered — spec Q4 deferred the "abnormally small image" threshold.

## What it does

```
URL in  -->  fetch: streamed, size-capped, retry ladder (L1 / L2a / L2b / L2c)
              |
              v
             decode, reject non-image, apply EXIF orientation
              |
              +-->  already within both limits, no rotation, single frame
              |         -->  passthrough: original bytes returned untouched
              v
             flatten transparency onto white  -->  RGB
              |
              v
             resize long edge (LANCZOS, never upscales)
              |
              v
             encode from quality_start down by quality_step until under budget
              |
              v
out     -->  ImageResult: bytes + original/final format, dimensions, size, quality
```

If the quality floor still cannot meet the size budget, the floor artifact is kept and the
status becomes `budget_unmet` rather than a failure — the image is usable, it just needs review.

Decoding runs in a worker thread; downloads run concurrently under a semaphore, so one slow
host does not stall the batch. A single bad URL never aborts a run.

## Configuration

Every tunable lives in [src/image_load_compression/config.yaml](src/image_load_compression/config.yaml).
That file is the source of truth for the current values — this table is the meaning of each key.

| Key | Controls |
|---|---|
| `output.max_long_edge` | **尺寸** — long-edge cap in px; proportional, never upscales |
| `output.size_budget_kb` | **大小** — per-image file size budget |
| `output.format` | `JPEG` or `WEBP` |
| `output.jpeg_subsampling` | `0` = 4:4:4 |
| `quality.start` / `step` / `floor` | the encode ladder: start here, step down, stop at the floor |
| `fetch.concurrency` | in-flight downloads |
| `fetch.connect_timeout` / `read_timeout` | seconds |
| `fetch.max_download_mb` | hard cap; anything larger fails at `L3` |
| `fetch.retries.l1` / `l2a` / `l2b` | per-layer retry budgets |
| `fetch.user_agent` | sent on every request |

Keys you omit fall back to the defaults in `NormalizeConfig`, so an override file can be three
lines. A misspelled key raises instead of being silently ignored.

```python
from image_load_compression import load_config

config = load_config()              # the packaged config.yaml
config = load_config("my.yaml")     # or your own
```

Resolution order: an explicit path, then `$IMAGE_LOAD_COMPRESSION_CONFIG`, then the packaged file.

## Use from another project

```bash
cd ~/programming/my-app
uv add --editable ../image-load-compression
```

This records the dependency in `pyproject.toml` and `uv.lock`, so it survives `uv sync`;
a bare `uv pip install -e` does not. Edits here take effect in the app with no reinstall.

```python
from image_load_compression import normalize_batch, load_config

results = await normalize_batch(image_urls, load_config(), run_id="nb")
```

Use `run(urls)` for the synchronous version — it accepts one URL or many and returns one
result or a list to match. It raises inside a running event loop, so notebooks and async
code must `await normalize_batch(...)` instead.

## CLI

```bash
uv sync --extra dev
uv run pytest
uv run python -m image_load_compression data/input.txt -o out/
uv run python -m image_load_compression data/input.txt -o out/ --config my.yaml
uv run python -m image_load_compression data/input.txt -o out/ --max-long-edge 800 --size-budget-kb 80
```

`--max-long-edge` and `--size-budget-kb` override the resolved config, which makes A/B runs
cheap without editing a file.

## Input

| What | Type / format | Source |
|---|---|---|
| Image URLs | one URL per line, UTF-8 | `data/input.txt`, or any path given to the CLI |
| Image URLs (library) | `str` or `Iterable[str]` | the caller |
| Settings | YAML, or a `NormalizeConfig` | packaged `config.yaml`, `--config`, `$IMAGE_LOAD_COMPRESSION_CONFIG` |

Decodable input formats: GIF, JPEG, PNG, WEBP. Anything else fails at `L3`.

## Output

| What | Type / format | Destination |
|---|---|---|
| Normalized image | JPEG or WEBP bytes | `ImageResult.image_bytes`; CLI writes `out/<run_id>/<sha256(url)[:16]>.jpg` |
| Per-image metadata | `ImageResult`, `.to_dict()` drops the bytes | CLI writes `out/<run_id>/results.jsonl` |
| Run summary | counts by status, error layer, passthrough | stdout |

### Statuses and error layers

| Value | Meaning |
|---|---|
| `success` | within both limits, whether passthrough or re-encoded |
| `budget_unmet` | quality floor reached, still over budget; artifact kept for review |
| `failed` | see `error_layer` |
| `suspicious` | reserved for `L4`, not emitted in v1 |
| `L1` | transport error — retried `l1_retries` times |
| `L2a` | HTTP 403 / 429 — honours `Retry-After`, else exponential backoff |
| `L2b` | HTTP 5xx — retried `l2b_retries` times |
| `L2c` | any other non-2xx — not retried |
| `L3` | undecodable, unsupported format, or over the download cap |
| `L4` | reserved: decodes fine but semantically useless (tracking pixel, placeholder) |

## Script map

```
config.yaml --> [config.py] --> NormalizeConfig
                                      |
                                      v
[cli.py] --> [api.py] --> [pipeline.py] --> [fetch.py] --> [process.py] --> [models.py]
```

`cli.py` handles argument parsing and writes the run directory. `api.py` is the sync wrapper.
`pipeline.py` owns concurrency and error containment. `fetch.py` is network only, `process.py`
is pixels only, `models.py` is the result shape.

## Files to maintain

| File | Contents | Update when |
|---|---|---|
| [src/image_load_compression/config.yaml](src/image_load_compression/config.yaml) | output size and dimensions, quality ladder, fetch tuning | the target vision model, its pixel budget, or the size budget changes |
| [data/input.txt](data/input.txt) | sample URLs for the CLI smoke run | the sample sites change |

## Calibration

Checking a candidate long edge and quality against a real vision model needs `QWEN_KEY` in `.env`:

```bash
uv run --extra calibrate python scripts/calibrate_qwen.py path-or-image-url
```
