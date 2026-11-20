"""公众号图文内容的准备：图片规整、正文渲染、草稿字段校验。

微信对素材有硬限制（官方文档）：
- 封面（thumb_media_id）必须是**图片永久素材**，支持 jpg/png，≤ 10 MB；
- 正文内图片必须先用 `media/uploadimg` 上传，仅支持 jpg/png 且 ≤ 1 MB，
  正文里引用非微信域名的图片会被拒绝（45166）。

因此这里不做「原样上传碰运气」，而是按限制真实转码；转不动就明确报错。
"""

from __future__ import annotations

import io
import re

from PIL import Image

from .wechat_layout_engine import render_studio_document

MAX_THUMB_BYTES = 10 * 1024 * 1024
MAX_INLINE_BYTES = 1 * 1024 * 1024
MAX_TITLE_CHARS = 64
MAX_DIGEST_CHARS = 120
MAX_AUTHOR_CHARS = 16
MAX_BODY_CHARS = 20000


class ArticleError(ValueError):
    """内容不满足微信草稿要求。"""


def _open(raw: bytes) -> Image.Image:
    try:
        image = Image.open(io.BytesIO(raw))
        image.load()
    except Exception as exc:  # Pillow 对损坏文件的异常类型不统一
        raise ArticleError("图片无法解析，请更换文件") from exc
    # 动图/多帧只取第一帧，微信正文与封面都不接受动图
    if getattr(image, "is_animated", False):
        image.seek(0)
    return image


def _flatten(image: Image.Image) -> Image.Image:
    """透明通道铺白底，避免 JPEG 转码后出现黑块。"""
    if image.mode in ("RGBA", "LA", "P"):
        rgba = image.convert("RGBA")
        canvas = Image.new("RGB", rgba.size, (255, 255, 255))
        canvas.paste(rgba, mask=rgba.split()[-1])
        return canvas
    return image.convert("RGB")


def _encode(image: Image.Image, fmt: str, quality: int = 88) -> bytes:
    buffer = io.BytesIO()
    if fmt == "PNG":
        image.convert("RGBA").save(buffer, format="PNG", optimize=True)
    else:
        image.save(buffer, format="JPEG", quality=quality, optimize=True, progressive=True)
    return buffer.getvalue()


def prepare_thumb(raw: bytes) -> tuple[bytes, str, str]:
    """封面：jpg/png，≤10MB。超限先降质再降分辨率，仍超限则报错。"""
    image = _open(raw)
    flat = _flatten(image)
    for max_width in (1600, 1080, 720):
        candidate = flat
        if flat.width > max_width:
            ratio = max_width / flat.width
            candidate = flat.resize(
                (max_width, max(1, int(flat.height * ratio))), Image.LANCZOS
            )
        data = _encode(candidate, "JPEG", 88)
        if len(data) <= MAX_THUMB_BYTES:
            return data, "image/jpeg", "thumb.jpg"
    raise ArticleError("封面图片超过微信 10 MB 限制，请先压缩或裁剪")


def prepare_inline(raw: bytes) -> tuple[bytes, str, str]:
    """正文图片：jpg/png，≤1MB。优先保留原格式，超限转 JPEG 并逐级降质。"""
    image = _open(raw)
    if raw and len(raw) <= MAX_INLINE_BYTES:
        head = raw[:12]
        if head.startswith(b"\x89PNG\r\n\x1a\n"):
            return raw, "image/png", "inline.png"
        if head.startswith(b"\xff\xd8"):
            return raw, "image/jpeg", "inline.jpg"
    flat = _flatten(image)
    for max_width in (1080, 900, 720, 540):
        candidate = flat
        if flat.width > max_width:
            ratio = max_width / flat.width
            candidate = flat.resize(
                (max_width, max(1, int(flat.height * ratio))), Image.LANCZOS
            )
        for quality in (85, 70, 55, 40):
            data = _encode(candidate, "JPEG", quality)
            if len(data) <= MAX_INLINE_BYTES:
                return data, "image/jpeg", "inline.jpg"
    raise ArticleError("正文图片压到 1 MB 以内失败，请换更小的图片")


def render_html(
    body: str,
    image_urls: list[str] | None = None,
    studio_options: dict | None = None,
) -> str:
    """把正文通过工作室单引擎渲染成微信可接受的 HTML。"""
    result = render_studio_document(body, studio_options or {}, image_urls)
    return result["html"]


def build_article(
    *,
    title: str,
    html: str,
    thumb_media_id: str,
    digest: str = "",
    author: str = "",
    content_source_url: str = "",
    need_open_comment: int = 0,
    only_fans_can_comment: int = 0,
) -> dict:
    article = {
        "article_type": "news",
        "title": title.strip(),
        "content": html,
        "thumb_media_id": thumb_media_id,
        "need_open_comment": 1 if need_open_comment else 0,
        "only_fans_can_comment": 1 if only_fans_can_comment else 0,
    }
    if author.strip():
        article["author"] = author.strip()
    if digest.strip():
        article["digest"] = digest.strip()
    if content_source_url.strip():
        article["content_source_url"] = content_source_url.strip()
    return article


def validate_article(
    *,
    title: str,
    body: str,
    digest: str,
    author: str,
    asset_count: int,
    content_source_url: str = "",
) -> list[dict]:
    """推送前预检，规则全部来自微信官方限制。"""
    issues: list[dict] = []
    title = (title or "").strip()
    if not title:
        issues.append({"field": "title", "level": "error", "message": "标题不能为空"})
    elif len(title) > MAX_TITLE_CHARS:
        issues.append(
            {
                "field": "title",
                "level": "error",
                "message": f"标题 {len(title)} 字，超过微信 {MAX_TITLE_CHARS} 字限制",
            }
        )
    if not (body or "").strip():
        issues.append({"field": "body", "level": "error", "message": "正文不能为空"})
    elif len(body) > MAX_BODY_CHARS:
        issues.append(
            {
                "field": "body",
                "level": "error",
                "message": f"正文 {len(body)} 字，超过微信 {MAX_BODY_CHARS} 字限制",
            }
        )
    if len(digest or "") > MAX_DIGEST_CHARS:
        issues.append(
            {
                "field": "digest",
                "level": "error",
                "message": f"摘要 {len(digest)} 字，超过微信 {MAX_DIGEST_CHARS} 字限制",
            }
        )
    if len(author or "") > MAX_AUTHOR_CHARS:
        issues.append(
            {
                "field": "author",
                "level": "warning",
                "message": f"作者 {len(author)} 字，建议控制在 {MAX_AUTHOR_CHARS} 字以内",
            }
        )
    if content_source_url and not content_source_url.startswith(("https://", "http://")):
        issues.append(
            {
                "field": "contentSourceUrl",
                "level": "error",
                "message": "原文链接必须是完整的 http(s) 地址",
            }
        )
    if asset_count == 0:
        issues.append(
            {
                "field": "assets",
                "level": "error",
                "message": "微信草稿必须有封面图（第一张素材会作为 thumb_media_id）",
            }
        )
    return issues
