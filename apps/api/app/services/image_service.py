"""图像处理（Pillow）：基础操作 + 条漫分格。

安全：白名单格式、限制像素总量、重新编码、不信任扩展名/MIME。
`apply_operation` 返回「结果列表」而不是单个结果：分格类操作会一次产出多张图，
调用方需要逐张入库；其余操作恒返回长度 1 的列表。
"""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageOps

MAX_PIXELS = 40_000_000
MAX_BYTES = 20 * 1024 * 1024
ALLOWED_INPUT = {"image/jpeg", "image/png", "image/webp", "image/gif", "image/bmp"}
FORMAT_BY_MIME = {
    "image/jpeg": "JPEG",
    "image/png": "PNG",
    "image/webp": "WEBP",
}
OUTPUT_FORMATS = {"jpeg", "png", "webp"}

OPERATIONS = {
    "thumbnail",
    "resize",
    "crop",
    "compress",
    "convert",
    "watermark",
    "split_panels",
}

# (bytes, mime, (width, height))
ImageOutput = tuple[bytes, str, tuple[int, int]]


class ImageValidationError(ValueError):
    pass


def validate_upload(mime_type: str, size_bytes: int, width: int, height: int) -> None:
    if mime_type not in ALLOWED_INPUT:
        raise ImageValidationError(f"不支持的图片类型：{mime_type}")
    if size_bytes > MAX_BYTES:
        raise ImageValidationError("图片体积超过 20MB 限制")
    if width * height > MAX_PIXELS:
        raise ImageValidationError("图片像素总量过大")


def _open(data: bytes) -> Image.Image:
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except Exception as exc:  # noqa: BLE001 - 解码失败统一视为非法文件
        raise ImageValidationError("无法解码图片文件") from exc
    if image.width * image.height > MAX_PIXELS:
        raise ImageValidationError("图片像素总量过大")
    return ImageOps.exif_transpose(image)


def _int_option(
    options: dict, key: str, default: int, *, minimum: int = 1, maximum: int | None = None
) -> int:
    """非法/越界的 options 一律转成 ImageValidationError，避免裸 ValueError 变成 500。"""
    raw = options.get(key, default)
    if raw is None or raw == "":
        raw = default
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ImageValidationError(f"{key} 必须是整数") from exc
    if value < minimum or (maximum is not None and value > maximum):
        raise ImageValidationError(f"{key} 需在 {minimum}~{maximum or '∞'} 之间")
    return value


def _float_option(options: dict, key: str, default: float, *, minimum: float, maximum: float) -> float:
    raw = options.get(key, default)
    if raw is None or raw == "":
        raw = default
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise ImageValidationError(f"{key} 必须是数字") from exc
    if not minimum <= value <= maximum:
        raise ImageValidationError(f"{key} 需在 {minimum}~{maximum} 之间")
    return value


def _output_format(options: dict, operation: str) -> str:
    fmt = str(options.get("format", "webp" if operation == "convert" else "jpeg")).lower()
    # 旧实现遇到未知格式会静默编码成 WEBP 却返回 image/gif 之类的 mime，前后不一致
    if fmt not in OUTPUT_FORMATS:
        raise ImageValidationError(f"不支持的输出格式：{fmt}（仅支持 jpeg/png/webp）")
    return fmt


def _encode(image: Image.Image, fmt: str, options: dict) -> ImageOutput:
    pil_format = FORMAT_BY_MIME[f"image/{fmt}"]
    target = image
    if pil_format == "JPEG" and target.mode not in ("RGB", "L"):
        target = target.convert("RGB")
    quality = _int_option(options, "quality", 82, minimum=1, maximum=100)
    save_kwargs = {"quality": quality} if pil_format in ("JPEG", "WEBP") else {}
    out = io.BytesIO()
    target.save(out, format=pil_format, **save_kwargs)
    return out.getvalue(), f"image/{fmt}", target.size


def _split_panels(image: Image.Image, options: dict) -> list[Image.Image]:
    """按水平空白带把条漫长图切成多个分格（真实投影算法，不调用外部视觉服务）。

    做法：缩放到宽度 ≤ 800 的灰度投影，逐行统计与背景色的差异像素比例；
    连续空白行达到 minGap 视为分格间隔，取间隔中点映射回原图切分。
    检测不到间隔或分格数超过上限时明确报错，不把「一张原图」伪装成分格成功。
    """
    min_gap = _int_option(options, "minGap", 12, minimum=2, maximum=200)
    max_panels = _int_option(options, "maxPanels", 12, minimum=1, maximum=30)
    padding = _int_option(options, "padding", 0, minimum=0, maximum=200)
    threshold = _float_option(options, "threshold", 0.02, minimum=0.001, maximum=0.5)

    rgb = image.convert("RGB")
    scale = min(1.0, 800 / max(1, rgb.width))
    small = rgb.resize(
        (max(1, int(rgb.width * scale)), max(1, int(rgb.height * scale))),
        Image.LANCZOS,
    )
    width, height = small.size
    pixels = small.load()
    corners = [pixels[0, 0], pixels[width - 1, 0], pixels[0, height - 1], pixels[width - 1, height - 1]]
    background = tuple(sum(c[i] for c in corners) // len(corners) for i in range(3))

    blank_rows: list[bool] = []
    for y in range(height):
        ink = 0
        for x in range(width):
            pixel = pixels[x, y]
            if (
                abs(pixel[0] - background[0])
                + abs(pixel[1] - background[1])
                + abs(pixel[2] - background[2])
                > 60
            ):
                ink += 1
        blank_rows.append(ink / width < threshold)

    gaps: list[tuple[int, int]] = []
    start: int | None = None
    for y, is_blank in enumerate([*blank_rows, False]):
        if is_blank and start is None:
            start = y
        elif not is_blank and start is not None:
            # 忽略紧贴图片首尾的留白，否则会把页边空白也误切成一格
            if y - start >= min_gap and start > 0 and y < height:
                gaps.append((start, y))
            start = None

    if not gaps:
        raise ImageValidationError("未检测到可用于分格的空白间隔；请确认素材是条漫长图")
    if len(gaps) + 1 > max_panels:
        raise ImageValidationError(
            f"检测到 {len(gaps) + 1} 个分格，超过 maxPanels={max_panels}；请调大上限或缩小 minGap"
        )

    scale_y = image.height / height
    cuts = [0, *(int(((top + bottom) / 2) * scale_y) for top, bottom in gaps), image.height]
    min_height = max(8, int(image.width * 0.05))
    panels: list[Image.Image] = []
    for index in range(len(cuts) - 1):
        top = max(0, cuts[index] - padding)
        bottom = min(image.height, cuts[index + 1] + padding)
        if bottom - top < min_height:
            raise ImageValidationError("分格后出现高度过小的碎片，请调整 minGap / padding")
        panels.append(image.crop((0, top, image.width, bottom)))
    return panels


def apply_operation(data: bytes, operation: str, options: dict) -> list[ImageOutput]:
    """返回 [(bytes, mime, (width, height)), ...]；除分格外恒为 1 项。"""
    if operation not in OPERATIONS:
        raise ImageValidationError(f"不支持的操作：{operation}")
    options = options or {}
    image = _open(data)

    if operation == "thumbnail":
        max_size = _int_option(options, "maxSize", 512, minimum=1, maximum=8000)
        image.thumbnail((max_size, max_size))
    elif operation == "resize":
        width = _int_option(options, "width", image.width, minimum=1, maximum=8000)
        height = _int_option(options, "height", image.height, minimum=1, maximum=8000)
        image = image.resize((width, height), Image.LANCZOS)
    elif operation == "crop":
        ratio = options.get("aspectRatio") or "1:1"
        try:
            rw, rh = (float(x) for x in str(ratio).split(":"))
        except (ValueError, TypeError) as exc:
            raise ImageValidationError("裁切比例格式应为 W:H") from exc
        if rw <= 0 or rh <= 0:
            raise ImageValidationError("裁切比例必须为正数 W:H")
        target = rw / rh
        w, h = image.size
        if w / h > target:
            new_w = max(1, int(h * target))
            left = (w - new_w) // 2
            image = image.crop((left, 0, left + new_w, h))
        else:
            new_h = max(1, int(w / target))
            top = (h - new_h) // 2
            image = image.crop((0, top, w, top + new_h))
    elif operation == "watermark":
        text = str(options.get("text") or "")[:120]
        if text:
            from PIL import ImageDraw

            # 旧实现在临时副本上作图后丢弃，水印其实是 no-op；这里真正合成回原图。
            # 深色描边保证浅色/深色底都能看清，不用纯白字糊在白底上。
            layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
            ImageDraw.Draw(layer).text(
                (12, max(0, image.height - 32)),
                text,
                fill=(255, 255, 255, 235),
                stroke_width=2,
                stroke_fill=(0, 0, 0, 210),
            )
            image = Image.alpha_composite(image.convert("RGBA"), layer)

    fmt = _output_format(options, operation)
    if operation == "split_panels":
        images = _split_panels(image, options)
    else:
        images = [image]
    return [_encode(item, fmt, options) for item in images]


def read_bounds(data: bytes) -> tuple[int, int]:
    image = _open(data)
    return image.size


def human_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes / 1024 / 1024:.1f} MB"


def storage_path(root: Path, key: str) -> Path:
    path = (root / key).resolve()
    if not str(path).startswith(str(root.resolve())):
        raise ImageValidationError("非法存储路径")
    return path
