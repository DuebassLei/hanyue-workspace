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
from ..core.config import get_settings
from ..core.deps import ApiError
from ..core.security import sign_asset_url
from . import article_blocks
from . import wechat_layout_engine as engine
from .wechat_layout_engine import (
    STYLES as STUDIO_STYLES,
    SVG_CARD_INFO as STUDIO_SVG_CARD_INFO,
    THEMES as STUDIO_THEMES,
)

_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"\s")
_OPENING_BANNERS = {"none": "无横幅", "poster": "大字海报", "neo": "潮酷横幅", "card": "标题卡"}


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
    opening_banner = None
    if "openingBanner" in merged:
        opening_banner = merged.pop("openingBanner")
        if not isinstance(opening_banner, str) or opening_banner not in _OPENING_BANNERS:
            raise ApiError(
                "invalid_layout_options",
                "排版选项不合法：openingBanner 必须是 none、poster、neo 或 card",
                status_code=422,
            )
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
    if opening_banner is not None:
        options["openingBanner"] = opening_banner
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


_ASSET_REF_RE = re.compile(r"asset://([A-Za-z0-9_-]+)")


def resolve_asset_refs(text: str, base_url: str | None) -> str:
    """把正文里的 `asset://<id>` 换成素材直链。

    `asset://` 只是后端内部的图片记号，排版引擎只认 http/https 的真实地址，
    不换的话正文里的插图会变成「图片地址无效」。
    直链带签名（`<img>` 带不了 Authorization 头），匿名可取。
    """
    if not text or "asset://" not in text:
        return text

    def replace(match: re.Match) -> str:
        asset_id = match.group(1)
        url = f"{get_settings().api_prefix}/assets/{asset_id}/raw?{sign_asset_url(asset_id)}"
        if base_url:
            url = f"{base_url.rstrip('/')}{url}"
        return url

    return _ASSET_REF_RE.sub(replace, text)


def banner_spec(data: dict, style: str | None = None) -> dict | None:
    """取指定风格的规格；未指定时沿用稿件的旧横幅风格。"""
    if style is None:
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


def render_article(data: dict, incoming: dict | None = None, base_url: str | None = None) -> dict:
    """渲染一篇稿件，返回 `{html, warnings, stats, options}`。

    `base_url` 是这次请求的站点根（如 `http://127.0.0.1:8000/`）：
    正文里的 `asset://<id>` 要换成能直接打开的绝对直链，浏览器才认。
    """
    options = effective_options(data, incoming)
    body = resolve_asset_refs(article_blocks.to_markdown(data), base_url)
    urls = [resolve_asset_refs(url, base_url) for url in collect_images(data)]

    opening_banner = options.get("openingBanner")
    spec = banner_spec(data, opening_banner)
    if opening_banner is not None and spec is not None and spec.get("style") != opening_banner:
        spec = None
    banner_html, banner_warnings = ("", [])
    render_options = options if opening_banner is None else {**options, "banner": opening_banner}
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


# 固定样例由同一渲染引擎输出；仅用于选择卡型，不代表文章生成结果。
_SVG_SAMPLES = {
    "svg-table": ["项目|说明", "准备|收集素材", "发布|检查内容"],
    "svg-bars": ["项目 A|30", "项目 B|60"],
    "svg-steps": ["准备|收集素材", "完成|检查内容"],
    "svg-timeline": ["第一天|准备素材", "第二天|完成发布"],
    "svg-breakdown": ["目标|明确方向", "行动|逐步推进"],
    "svg-compare": ["之前|现在", "手动整理|集中整理", "逐项检查|统一检查"],
    "svg-concept": ["从准备到完成", "以前|收集|整理", "现在|协作|完成"],
    "svg-phone": ["一次简单对话", "提问|我|下一步做什么？", "回答|助手|先整理素材。"],
}


def _opening_banner_sample(mode: str) -> str:
    if mode == "none":
        return engine.render_studio_document(
            "## 周末阅读计划\n\n留出一段安静的时间，从喜欢的一页读起。",
            {"title": "周末阅读计划", "outro": False},
        )["html"]
    common = {"eyebrow": "周末计划", "title1": "慢下来", "title2": "读点好书", "avatar": False}
    spec = {"style": mode}
    if mode == "card":
        spec["card"] = {
            **common, "right": "steps", "sub": "给自己一点阅读时间",
            "steps": [{"label": "挑一本书", "icon": "doc"}, {"label": "读几页", "icon": "spark"}],
            "result": "周末阅读",
        }
    else:
        spec["poster"] = {
            **common, "right": "flow", "big": "读", "ask": "挑一本书",
            "result": "读几页", "result_sub": "周末阅读",
        }
    return render_spec_banner(spec, "orange")[0]


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
            {
                "id": key, "name": value["name"], "lead": value["lead"],
                "previewHtml": engine.render_svg_card(key, value["lead"], _SVG_SAMPLES[key])["html"],
            }
            for key, value in STUDIO_SVG_CARD_INFO.items()
        ],
        "banners": [
            {"id": mode, "name": name, "previewHtml": _opening_banner_sample(mode)}
            for mode, name in _OPENING_BANNERS.items()
        ],
        "defaults": default_options(),
    }
