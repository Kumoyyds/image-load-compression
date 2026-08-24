# image-normalize

Downloads product images and normalizes them for vision-model matching while preserving already-compliant source bytes.

```bash
uv sync --extra dev
uv run pytest
uv run python -m image_normalize data/input.txt -o out/
```

Each run writes images and `results.jsonl` under `out/<run_id>/`. The processor detects formats from decoded content, applies EXIF orientation, composites transparency onto white, never upscales, caps the long edge at 2048px, and retains a quality-65 artifact when the 300KB secondary budget cannot be met.

It can also be imported and called like a regular function:

```python
from image_normalize import run

result = run("https://example.com/product.jpg")
results = run(["https://example.com/one.jpg", "https://example.com/two.jpg"])
```

Inside async code, use `await normalize_batch(urls, config, run_id)` instead.

The optional Qwen3-VL calibration requires `QWEN_KEY` in `.env`:

```bash
uv run --extra calibrate python scripts/calibrate_qwen.py path-or-image-url
```
