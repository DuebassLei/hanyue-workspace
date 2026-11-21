"""成稿台排版渲染：把稿件 dict 渲染成可直接贴进公众号的正文 HTML。

契约见 `docs/paper-studio-contract.md` 3.1。本模块只做纯函数编排：

- 选项合并（请求覆盖已存，浅合并）与默认值补齐；
- 正文来自 `article_blocks.to_markdown`，不在这里拼 markdown；
- 图片取自封面与已就绪插图，统一成 `asset://<id>`；
- 横幅按稿件已存的 spec 渲染并置于正文之前；
- 复用 `wechat_layout_engine.render_studio_document` 出正文。

不 import `modules.wechat_mp` 的私有函数，`_studio_render` 中需要的那几行
（warnings 规整 + stats 补齐）在这里重写一份。
"""

from __future__ import annotations

import copy
import re

from pydantic import ValidationError

from .. import schemas
from ..core.deps import ApiError
from . import article_blocks
from . import wechat_layout_engine as engine
from .wechat_layout_engine import (
    STYLES as STUDIO_STYLES,
    SVG_CARD_INFO as STUDIO_SVG_CARD_INFO,
    THEMES as STUDIO_THEMES,
)

_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"\s")


def _warning(field: str, message: str, level: str = "warning") -> dict:
    return {"field": field, "level": level, "message": message}


def normalize_warnings(items, field: str = "body") -> list[dict]:
    """把引擎给的字符串/字典告警统一成 `{field, level, message}`。"""
    out: list[dict] = []
    for item in items or ():
        if isinstance(item, str):
            if item.strip():
                out.append(_warning(field, item))
        elif isinstance(item, dict):
            message = str(item.get("message") or "").strip()
            if not message:
                continue
            out.append({
                "field": item.get("field") or field,
                "level": item.get("level") or "warning",
                "message": message,
            })
    return out


def effective_options(data: dict, incoming: dict | None = None) -> dict:
    """稿件已存选项与请求选项浅合并后校验成完整选项。

    请求里没给的字段保留已存值（而不是被 schema 默认值覆盖），
    标题缺省用稿件标题。
    """
    stored = data.get("layout_options")
    merged = {**(stored if isinstance(stored, dict) else {}),
              **((incoming or {}) if isinstance(incoming, dict) else {})}
    try:
        options = schemas.WeChatStudioOptions.model_validate(merged).model_dump(by_alias=True)
    except ValidationError as exc:
        first = exc.errors()[0] if exc.errors() else {}
        where = ".".join(str(part) for part in first.get("loc") or ()) or "options"
        raise ApiError(
            "invalid_layout_options",
            f"排版选项不合法：{where} {first.get('msg') or ''}".strip(),
            status_code=422,
        ) from exc
    if not options.get("title"):
        options["title"] = str(data.get("title") or "")
    return options


def collect_images(data: dict) -> list[str]:
    """封面 + 已就绪插图 → `asset://<id>`，按出现顺序去重。"""
    urls: list[str] = []

    def push(entry) -> None:
        if not isinstance(entry, dict):
            return
        asset_id = entry.get("assetId")
        if entry.get("status") != "ready" or not isinstance(asset_id, str) or not asset_id:
            return
        url = f"asset://{asset_id}"
        if url not in urls:
            urls.append(url)

    push(data.get("cover"))
    diagrams = data.get("inline_diagrams")
    for item in diagrams if isinstance(diagrams, list) else ():
        push(item)
    return urls


def banner_spec(data: dict) -> dict | None:
    """稿件当前横幅风格对应的规格；none / 缺失 / 空规格都返回 None。"""
    style = data.get("banner_style")
    if not isinstance(style, str) or not style or style == "none":
        return None
    banners = data.get("banners")
    entry = banners.get(style) if isinstance(banners, dict) else None
    spec = entry.get("spec") if isinstance(entry, dict) else None
    if isinstance(spec, dict) and spec:
        return spec
    return None


def render_spec_banner(spec: dict, theme: str) -> tuple[str, list[dict]]:
    """渲染开头横幅。返回 `(html, warnings)`，画不出来时 html 为空、告警可读。"""
    try:
        rendered = engine.render_banner(copy.deepcopy(spec), theme=theme)
    except Exception as exc:  # noqa: BLE001 - 规格错误不能让整篇排版中断
        return "", [_warning("banner", f"横幅规格有误：{exc}")]
    return str(rendered.get("html") or ""), normalize_warnings(rendered.get("warnings"), "banner")


def render_article(data: dict, incoming: dict | None = None) -> dict:
    """渲染一篇稿件，返回 `{html, warnings, stats, options}`。"""
    options = effective_options(data, incoming)
    body = article_blocks.to_markdown(data)
    urls = collect_images(data)

    spec = banner_spec(data)
    banner_html, banner_warnings = ("", [])
    render_options = options
    if spec is not None:
        banner_html, banner_warnings = render_spec_banner(spec, options.get("theme") or "orange")
        if banner_html:
            # 正文引擎自己也会按 options["banner"] 画一条自动横幅；这里已经用稿件
            # 存的规格画过了，关掉它避免出现两条开头横幅。
            render_options = {**options, "banner": "none"}

    result = engine.render_studio_document(body, render_options, urls)
    html = f"{banner_html}{result.get('html') or ''}"
    warnings = normalize_warnings(result.get("warnings")) + banner_warnings

    stats = {
        **(result.get("stats") or {}),
        "sourceChars": len(body),
        "htmlChars": len(html),
        "renderedChars": len(_TAG_RE.sub("", html)),
        "images": (result.get("stats") or {}).get("images", 0),
        "readingMinutes": max(1, (len(_SPACE_RE.sub("", body)) + 299) // 300),
    }
    # 返回生效值而不是 render_options：后者为了防重复把 banner 改成了 none，
    # 前端「保存为默认」会把它写回稿件，导致以后自动横幅永久关掉。
    return {"html": html, "warnings": warnings, "stats": stats, "options": options}


def default_options() -> dict:
    """控件默认值：与 `wechat_mp._studio_options()` 等价，不手写第二份默认值。"""
    return schemas.WeChatStudioOptions().model_dump(by_alias=True)


def catalog() -> dict:
    """风格 / 配色 / SVG 卡 / 横幅目录，字段与 `wechat_mp.layout_studio_catalog` 对齐。"""
    return {
        "styles": [
            {"id": key, "name": value["name"], "desc": value["desc"]}
            for key, value in STUDIO_STYLES.items()
        ],
        "themes": [
            {
                "id": key,
                "name": value["name"],
                "main": value["main"],
                "soft": value["soft"],
                "tint": value["tint"],
            }
            for key, value in STUDIO_THEMES.items()
        ],
        "svgCards": [
            {"id": key, "name": value["name"], "lead": value["lead"]}
            for key, value in STUDIO_SVG_CARD_INFO.items()
        ],
        "banners": [
            {"id": "none", "name": "无横幅"},
            {"id": "poster", "name": "海报横幅"},
            {"id": "neo", "name": "质感横幅"},
            {"id": "card", "name": "卡片横幅"},
        ],
        "defaults": default_options(),
    }
