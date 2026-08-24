# image_normalize 施工方案 v1

## Context

`plans/spec/image_normalize_spec.md` (v1.1) 已定稿 D-IMG-01..16，但仓库是空的（只有 `.env`、`data/input.txt`）。本方案把 spec 落成可运行的第一版模块，并用 `data/input.txt` 的 3 条真实 URL（Tesco marketplace ×2、Argos 无扩展名 ×1）跑通端到端。

vision 模型初步选定 **Qwen3-VL-Flash**，这关掉了 spec 里两个悬挂的"待选型确认"：

| 事项 | 结论 | 影响 |
|---|---|---|
| Qwen3-VL 单 visual token ≈ **32×32 px**；百炼商用 VL 接口单图**默认上限约 1280 token ≈ 131 万像素**（≈1145×1145），可显式放开至 16384 token | 默认参数下 2048 的图会被服务端二次下采样 —— 即 D-IMG-03 自己警告的"双重损失" | 长边**仍锁 2048**，但新增跨模块契约：调用端必须显式放开 `max_pixels` |
| Qwen3-VL 接受 `image/webp` | D-IMG-01 的推翻条件已满足 | 但字节是次级约束，WebP 的 ~25% 体积收益不作用于主约束，**默认仍 JPEG**，保留配置项 |

以上两点已与用户确认。端点走百炼 DashScope 兼容模式，`base_url` 做成配置项（`.env` 里的 `QWEN_KEY ` 键名带空格，读取时需 strip）。

---

## 技术选型

- Python 3.12 + **uv**（系统 Python 是 3.9 且无 Pillow，必须独立 venv）
- `pillow`（decode/EXIF/resize/encode）、`httpx`（async client）、`python-dotenv`、`openai`（仅校准脚本用，走 DashScope 兼容模式）
- dev: `pytest`、`pytest-asyncio`

## 文件结构

```
pyproject.toml
src/image_normalize/
  config.py      # NormalizeConfig：spec 全部可调参数集中一处
  models.py      # ImageResult(§4 十三字段) / Status / ErrorLayer
  fetch.py       # Fetcher 协议 + HttpxFetcher（D-IMG-09）+ 分层重试（D-IMG-07）
  process.py     # 纯 CPU 同步管线，严格按 D-IMG-05 八步
  pipeline.py    # asyncio 编排 + semaphore（D-IMG-14）+ to_thread
  cli.py         # python -m image_normalize data/input.txt -o out/
scripts/calibrate_qwen.py   # Qwen3-VL-Flash 校准：定 quality 地板 + 验证 token 上限
tests/test_process.py       # 合成图覆盖各分支，不打网络
out/                        # {run_id}/{sha256(url)[:16]}.{ext} + results.jsonl
```

## 各模块要点

### config.py —— 所有 spec 参数只在这里出现一次
`max_long_edge=2048`(D-IMG-03/13)、`size_budget_bytes=300*1024`、`output_format="JPEG"`(D-IMG-01)、`jpeg_subsampling=0`(D-IMG-02, 4:4:4)、`quality_start=90 / quality_step=5 / quality_floor=65`(D-IMG-15，待校准)、`concurrency=8`(D-IMG-14)、timeout(connect 5s/read 20s)、分层重试次数 `{L1:2, L2a:3, L2b:2}`、`max_download_bytes=20MB`。
硬编码常量一律不写进 process.py —— D-IMG-01/03 都明确要求这些是配置项而非常量。

### fetch.py
- `Fetcher` Protocol（`async def get(url) -> bytes`）+ `HttpxFetcher` 实现，Bright Data 后续替换只需换实现（D-IMG-09）。
- `follow_redirects=True`；带浏览器 UA（Argos/Tesco CDN 会看）；**streaming 下载**并在超过 `max_download_bytes` 时中止。
- 错误分层（D-IMG-07）：`TimeoutException/ConnectError/DNS`→L1 常规重试；`403/429`→L2a 指数退避（1/2/4s，优先遵守 `Retry-After`）；`5xx`→L2b 常规重试；`404` 及其余 4xx→L2c 不重试。返回 `(bytes, retry_count)` 或分层错误。

### process.py —— 顺序即约束（D-IMG-05）
1. **decode 验证**：`Image.open` + `im.load()`，任何异常 → L3。SVG/HEIC/AVIF 天然落进 L3，无需特判（§3）。格式取 `im.format`，绝不看 URL 或 Content-Type（D-IMG-06）。GIF 只取第 0 帧。
2. **EXIF 方向校正**：`ImageOps.exif_transpose`（D-IMG-10）。
3. **透传门（D-IMG-04）**：`长边 ≤ max_long_edge` **且** `原字节 ≤ budget`。
   > ⚠️ **spec 未覆盖的交互**：EXIF orientation ≠ 1 时若原样透传，下游拿到的仍是横躺的字节，D-IMG-10 形同虚设。故门增加第三个条件 `orientation ∈ {1, 缺失}`；需要转向的图强制走重编码路径。这是 D-IMG-04 与 D-IMG-10 的必然推论，须回写 spec。
4. **alpha 合成白底（D-IMG-11）**：mode ∈ {RGBA, LA, PA, P} → `convert("RGBA")` 后 `Image.new("RGB", size, (255,255,255))` + `paste(im, mask=alpha)`。P 模式必须走这条路（调色板可带透明色）。
5. **resize（D-IMG-12/13）**：`im.thumbnail((cap, cap), Image.LANCZOS)` —— thumbnail 天然只缩不放、保长宽比、大幅降图时先整数倍 reduce，同时满足两条决策。
6. **quality 收敛（D-IMG-15/16）**：从 `quality_start` 定步长递减，`optimize=True, subsampling=0`。命中预算即停；递减到 `quality_floor` 仍超 → status=`budget_unmet`，**保留地板产物**（D-IMG-16），不降分辨率、不击穿地板、不返回空。
7. 产出 bytes + `ImageResult`。

L4 层在 `Status`/`ErrorLayer` 枚举中保留但不触发（§6 Q4）。

### pipeline.py
`asyncio.Semaphore(concurrency)` 控并发；下载 async、CPU 段用 `asyncio.to_thread` 避免阻塞事件循环；每条独立 try，异常一律转成 failed 的 `ImageResult`，绝不中断批次（D-IMG-08）。签名 `async def normalize_batch(urls, config, run_id) -> list[ImageResult]`。

### cli.py
`python -m image_normalize data/input.txt -o out/ --run-id <id>`：读 URL 列表（跳过空行）→ 跑批 → 写 `out/{run_id}/` 下的图片与 `results.jsonl` → stdout 打印汇总表（透传率、budget_unmet 数、各层错误数）。`run_id` 默认时间戳（§5 追溯要求）。存储/缓存（S3）不在本版范围，落地点就是这个输出目录。

### scripts/calibrate_qwen.py —— 把"实测确定"的参数真正测出来
用 `.env` 的 `QWEN_KEY` + `QWEN_BASE_URL`（默认 `https://dashscope.aliyuncs.com/compatible-mode/v1`），OpenAI SDK 调 `qwen3-vl-flash`：
1. **验证 token 上限假设**：同一张图分别以 2048 和 1145 长边送入，打印 `usage.prompt_tokens`。若 2048 的图不是约 4096 token，说明服务端已默认下采样 → 必须在调用参数里显式放开 `max_pixels=4194304`（注意 `vl_high_resolution_images=True` 会启用固定策略并忽略 `max_pixels`，两者别混用）。
2. **定 quality 地板**：对样本图在 quality 90/80/70/65/60 各编一版，让模型逐版转写包装上最小号的规格文字，人工比对第一个开始出错的档位，`quality_floor` 取其上一档回写 config。

## 需回写 spec 的四处

1. D-IMG-01：补记"Qwen3-VL 接受 WebP，推翻条件已满足；因字节为次级约束，v1 仍保持 JPEG"。
2. D-IMG-03：Q1 彻底结案 —— 长边锁 2048，**并新增跨模块契约**：matching 层调用 Qwen3-VL-Flash 时必须显式设 `max_pixels`，否则服务端按 ~1280 token 下采样，本模块的 2048 产物白费。
3. D-IMG-04 / D-IMG-10：透传门增加 `orientation ∈ {1, 缺失}` 第三条件。
4. §6 待实测表：填入校准得到的 timeout / 重试次数 / quality 起始值与地板。

---

## 验证

1. `uv sync && uv run pytest` —— 合成图单测覆盖：透传双条件门（含"3000×3000 但 120KB 不得透传"这一 D-IMG-04 关键反例）、EXIF orientation=6 强制不透传且方向正确、RGBA/P 模式合成后**背景为白非黑**、小图不放大、HTML 字节 → L3、触底超预算 → `budget_unmet` 且产物非空。
2. `uv run python -m image_normalize data/input.txt -o out/` —— 3 条真实 URL 全部 success；肉眼开图确认 Argos 那条（无扩展名）格式判定正确、Tesco marketplace 两条方向正确；核对 `results.jsonl` 的 `original_format` / `passthrough` / `final_quality` 是否自洽。
3. `uv run python scripts/calibrate_qwen.py` —— 打印各档 token 数与转写结果，据此回填 `quality_floor` 与 spec §6。
