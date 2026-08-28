from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError

from .config import NormalizeConfig
from .models import ErrorLayer, ImageResult, Status

SUPPORTED_FORMATS = {"GIF", "JPEG", "PNG", "WEBP"}


def normalize_image(
    url: str,
    data: bytes,
    config: NormalizeConfig,
    retry_count: int = 0,
) -> ImageResult:
    try:
        with Image.open(BytesIO(data)) as source:
            source.seek(0)
            source.load()
            original_format = (source.format or "UNKNOWN").upper()
            if original_format not in SUPPORTED_FORMATS:
                raise UnidentifiedImageError(f"unsupported format: {original_format}")
            original_dimensions = source.size
            orientation = source.getexif().get(274)
            frame_count = getattr(source, "n_frames", 1)
            image = ImageOps.exif_transpose(source)
    except (OSError, ValueError, UnidentifiedImageError) as exc:
        return ImageResult(
            url=url,
            status=Status.FAILED,
            original_size_bytes=len(data),
            error_layer=ErrorLayer.L3,
            error_detail=f"invalid image: {type(exc).__name__}: {exc}",
            retry_count=retry_count,
        )

    common = dict(
        url=url,
        original_format=original_format,
        original_dimensions=original_dimensions,
        original_size_bytes=len(data),
        retry_count=retry_count,
    )
    can_passthrough = (
        max(image.size) <= config.max_long_edge
        and len(data) <= config.size_budget_bytes
        and orientation in {None, 1}
        and frame_count == 1
    )
    if can_passthrough:
        return ImageResult(
            **common,
            status=Status.SUCCESS,
            output_format=original_format,
            final_dimensions=image.size,
            final_size_bytes=len(data),
            passthrough=True,
            image_bytes=data,
        )

    image = _to_rgb(image)
    image.thumbnail((config.max_long_edge, config.max_long_edge), Image.Resampling.LANCZOS)

    encoded = b""
    final_quality = config.quality_floor
    for quality in range(config.quality_start, config.quality_floor - 1, -config.quality_step):
        final_quality = max(quality, config.quality_floor)
        encoded = _encode(image, config, final_quality)
        if len(encoded) <= config.size_budget_bytes or final_quality == config.quality_floor:
            break
    if final_quality > config.quality_floor and len(encoded) > config.size_budget_bytes:
        final_quality = config.quality_floor
        encoded = _encode(image, config, final_quality)

    status = Status.SUCCESS if len(encoded) <= config.size_budget_bytes else Status.BUDGET_UNMET
    return ImageResult(
        **common,
        status=status,
        output_format=config.output_format,
        final_dimensions=image.size,
        final_size_bytes=len(encoded),
        final_quality=final_quality,
        image_bytes=encoded,
    )


def _to_rgb(image: Image.Image) -> Image.Image:
    if image.mode == "P" or "A" in image.getbands():
        rgba = image.convert("RGBA")
        background = Image.new("RGB", rgba.size, "white")
        background.paste(rgba, mask=rgba.getchannel("A"))
        return background
    return image.convert("RGB")


def _encode(image: Image.Image, config: NormalizeConfig, quality: int) -> bytes:
    output = BytesIO()
    options: dict[str, int | bool] = {"quality": quality}
    if config.output_format == "JPEG":
        options.update(optimize=True, subsampling=config.jpeg_subsampling)
    else:
        options["method"] = 6
    image.save(output, format=config.output_format, **options)
    return output.getvalue()
