"""原创度检测：历史记录与报告导出。

GET /api/v1/originality/{content_version_id}/history   - 该版本的历史检测记录
GET /api/v1/originality/{analysis_id}/report/markdown  - Markdown 报告
GET /api/v1/originality/{analysis_id}/report/txt       - 纯文本报告
GET /api/v1/originality/{analysis_id}/report/png       - 长图报告（可直接发朋友圈/群里）
GET /api/v1/originality/{analysis_id}/report/xlsx      - Excel 报告

xlsx 用标准库 zipfile 直接拼 OOXML（Excel / WPS / Numbers 都能打开），
png 用 Pillow 画，字体走系统字体查找，找不到就明确报错而不是输出乱码。
"""

from __future__ import annotations

import io
import zipfile
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable
from xml.sax.saxutils import escape

from PIL import Image, ImageDraw, ImageFont

from fastapi import APIRouter, Query
from fastapi.responses import Response
from sqlalchemy import select

from .. import models
from ..core.deps import CurrentUser, DbSession, default_workspace, not_found
from ..services import originality_service

router = APIRouter(prefix="/originality", tags=["originality"])

LEVEL_LABEL = {
    "excellent": "优秀",
    "good": "良好",
    "fair": "中等",
    "poor": "偏低",
}

DIMENSIONS: list[tuple[str, str, str, str]] = [
    # (字段名, 展示名, 权重, 方向说明)
    ("similarity", "相似度", "40%", "越低越好"),
    ("originality", "观点独特性", "25%", "越高越好"),
    ("structure", "结构层次", "15%", "越高越好"),
    ("data_accuracy", "数据支撑度", "12%", "越高越好"),
    ("style_uniqueness", "文风辨识度", "8%", "越高越好"),
]


# ─────────────────────────── 权限与数据 ───────────────────────────


def _load_analysis(db: DbSession, user: models.User, analysis_id: str) -> tuple[Any, Any, Any]:
    """取分析记录并校验归属，同时带出稿件与版本。"""
    workspace = default_workspace(db, user)
    analysis = db.scalar(
        select(models.OriginalityAnalysis).where(
            models.OriginalityAnalysis.id == analysis_id,
            models.OriginalityAnalysis.workspace_id == workspace.id,
        )
    )
    if not analysis:
        raise not_found("检测记录")

    row = db.execute(
        select(models.ContentVersion, models.ContentItem)
        .join(
            models.ContentItem,
            models.ContentVersion.content_item_id == models.ContentItem.id,
        )
        .where(
            models.ContentVersion.id == analysis.content_version_id,
            models.ContentItem.owner_user_id == user.id,
        )
    ).first()
    if not row:
        raise not_found("检测记录")
    version, item = row
    return analysis, version, item


def _scores(analysis: models.OriginalityAnalysis) -> dict[str, int]:
    return {
        "similarity": analysis.similarity_score or 0,
        "originality": analysis.originality_score or 0,
        "structure": analysis.structure_score or 0,
        "data_accuracy": analysis.data_accuracy_score or 0,
        "style_uniqueness": analysis.style_uniqueness_score or 0,
    }


def _items(payload: dict | None) -> list[dict]:
    if not isinstance(payload, dict):
        return []
    items = payload.get("items")
    return items if isinstance(items, list) else []


def _recommendations(analysis: models.OriginalityAnalysis) -> list[str]:
    return [line.strip() for line in (analysis.recommendations or "").split("\n") if line.strip()]


def _report_lines(analysis: models.OriginalityAnalysis, item: models.ContentItem, version: models.ContentVersion) -> dict:
    """报告所需的纯数据（三种格式共用，避免各自拼一遍）。"""
    scores = _scores(analysis)
    segments = [
        seg
        for article in _items(analysis.similar_articles)
        for seg in (article.get("matching_snippets") or [])
    ]
    return {
        "title": item.title or version.title or "未命名稿件",
        "version_no": version.version_no,
        "analyzed_at": analysis.analyzed_at,
        "corpus_size": analysis.corpus_size or 0,
        "self_version_count": analysis.self_version_count or 0,
        "summary": analysis.summary or "",
        "total": analysis.total_score,
        "level": analysis.level,
        "level_label": LEVEL_LABEL.get(analysis.level, "未知"),
        "scores": scores,
        "similar_articles": _items(analysis.similar_articles),
        "segments": segments,
        "key_points": _items(analysis.key_points),
        "risks": _items(analysis.risk_flags),
        "recommendations": _recommendations(analysis),
        "model_version": analysis.model_version or "",
    }


# ─────────────────────────── 历史 ───────────────────────────


@router.get("/{content_version_id}/history", summary="获取稿件版本的历史检测记录")
async def list_history(
    content_version_id: str,
    user: CurrentUser,
    db: DbSession,
    limit: int = Query(default=10, ge=1, le=50),
) -> dict:
    """最新的在前。只返回列表页要用的字段，正文级详情走单独接口。"""
    workspace = default_workspace(db, user)
    owned = db.scalar(
        select(models.ContentVersion.id)
        .join(
            models.ContentItem,
            models.ContentVersion.content_item_id == models.ContentItem.id,
        )
        .where(
            models.ContentVersion.id == content_version_id,
            models.ContentItem.workspace_id == workspace.id,
            models.ContentItem.owner_user_id == user.id,
        )
    )
    if not owned:
        raise not_found("稿件版本")

    rows = originality_service.list_analyses(db, content_version_id, limit)
    items = [
        {
            "analysisId": row.id,
            "analyzedAt": row.analyzed_at.isoformat() if row.analyzed_at else None,
            "totalScore": row.total_score,
            "level": row.level,
            "similarity": row.similarity_score,
            "corpusSize": row.corpus_size or 0,
            "summary": row.summary or "",
            "modelVersion": row.model_version or "",
            "compareScope": row.compare_scope or "all",
        }
        for row in rows
    ]
    return {"items": items, "count": len(items)}


# ─────────────────────────── Markdown ───────────────────────────


def _markdown(data: dict) -> str:
    lines: list[str] = []
    lines.append("# 文章原创度检测报告")
    lines.append("")
    lines.append(f"- **文章标题**：{data['title']}")
    lines.append(f"- **版本**：v{data['version_no']}")
    lines.append(f"- **检测时间**：{data['analyzed_at']:%Y-%m-%d %H:%M}")
    lines.append(f"- **比对语料**：{data['corpus_size']} 篇稿件")
    if data["self_version_count"]:
        lines.append(f"- **同稿其它版本**：{data['self_version_count']} 个（不计入比对）")
    lines.append("")
    lines.append("## 结论")
    lines.append("")
    fallback = f"综合 {data['total']} 分（{data['level_label']}）。"
    lines.append(f"**{data['summary'] or fallback}**")
    lines.append("")
    lines.append("## 五维评分")
    lines.append("")
    lines.append("| 维度 | 评分 | 权重 | 说明 |")
    lines.append("| --- | --- | --- | --- |")
    for key, label, weight, direction in DIMENSIONS:
        value = data["scores"][key]
        if key == "similarity" and data["corpus_size"] == 0:
            value_text = "样本不足"
        else:
            value_text = str(value)
        lines.append(f"| {label} | {value_text} | {weight} | {direction} |")
    lines.append("")

    if data["similar_articles"]:
        lines.append("## 最相似的稿件")
        lines.append("")
        for article in data["similar_articles"]:
            lines.append(
                f"- 《{article.get('title', '')}》—— 相似度 {int(round(article.get('similarity', 0) * 100))}%"
            )
        lines.append("")

    if data["segments"]:
        lines.append("## 重合片段")
        lines.append("")
        for seg in data["segments"]:
            lines.append(f"- **{seg.get('position', '')}**（重合 {int(seg.get('similarity', 0) * 100)}%）")
            lines.append(f"  - 本文：{seg.get('target_text', '')}")
            lines.append(f"  - 对方：{seg.get('source_text', '')}")
        lines.append("")

    if data["key_points"]:
        lines.append("## 关键观点")
        lines.append("")
        lines.append("| 关键词 | 新颖度 | 库内出现过 |")
        lines.append("| --- | --- | --- |")
        for point in data["key_points"]:
            lines.append(
                f"| {point.get('point', '')} | {point.get('novelty', 0)} | {point.get('sources', 0)} 篇 |"
            )
        lines.append("")

    if data["risks"]:
        lines.append("## 风险提示")
        lines.append("")
        for risk in data["risks"]:
            mark = "❗" if risk.get("severity") == "error" else "⚠️"
            lines.append(f"- {mark} {risk.get('position', '')}：{risk.get('reason', '')}")
        lines.append("")

    lines.append("## 改进建议")
    lines.append("")
    for index, rec in enumerate(data["recommendations"], start=1):
        lines.append(f"{index}. {rec}")
    lines.append("")
    lines.append(f"---\n_报告生成于 {datetime.now():%Y-%m-%d %H:%M:%S} · {data['model_version']}_")
    return "\n".join(lines)


def _text(data: dict) -> str:
    lines = [
        "文章原创度检测报告",
        f"文章标题：{data['title']}",
        f"版本：v{data['version_no']}",
        f"检测时间：{data['analyzed_at']:%Y-%m-%d %H:%M}",
        f"比对语料：{data['corpus_size']} 篇稿件",
        "",
        "结论",
        data["summary"] or f"综合 {data['total']} 分（{data['level_label']}）。",
        "",
        "五维评分",
    ]
    for key, label, weight, direction in DIMENSIONS:
        value = data["scores"][key]
        lines.append(f"{label}：{'样本不足' if key == 'similarity' and not data['corpus_size'] else value}（权重 {weight}，{direction}）")
    if data["similar_articles"]:
        lines.extend(["", "最相似的稿件"])
        lines.extend(f"- 《{a.get('title', '')}》：相似度 {int(round(a.get('similarity', 0) * 100))}%" for a in data["similar_articles"])
    if data["risks"]:
        lines.extend(["", "风险提示"])
        lines.extend(f"- {r.get('position', '')}：{r.get('reason', '')}" for r in data["risks"])
    lines.extend(["", "改进建议"])
    lines.extend(f"{i}. {rec}" for i, rec in enumerate(data["recommendations"], 1))
    return "\n".join(lines) + "\n"


def _font() -> ImageFont.FreeTypeFont:
    for path in ("C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf", "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"):
        if Path(path).exists():
            return ImageFont.truetype(path, 28)
    return ImageFont.load_default()


def _png(data: dict) -> bytes:
    font = _font()
    lines = _text(data).splitlines() or ["文章原创度检测报告"]
    width, padding, line_height = 1600, 64, 48
    image = Image.new("RGB", (width, padding * 2 + line_height * len(lines)), "white")
    draw = ImageDraw.Draw(image)
    for index, line in enumerate(lines):
        draw.text((padding, padding + index * line_height), line[:100], fill="#182230", font=font)
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


# ─────────────────────────── xlsx（标准库拼 OOXML）───────────────────────────

_CT = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">\
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>\
<Default Extension="xml" ContentType="application/xml"/>\
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>\
<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>\
<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>\
</Types>"""

_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>\
</Relationships>"""

_WB = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" \
xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">\
<sheets><sheet name="检测报告" sheetId="1" r:id="rId1"/></sheets></workbook>"""

_WB_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>\
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>\
</Relationships>"""

# 三个样式：0 正文 / 1 表头（加粗+底色）/ 2 大标题
_STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">\
<fonts count="3">\
<font><sz val="11"/><name val="微软雅黑"/></font>\
<font><b/><sz val="11"/><name val="微软雅黑"/></font>\
<font><b/><sz val="14"/><name val="微软雅黑"/></font>\
</fonts>\
<fills count="3">\
<fill><patternFill patternType="none"/></fill>\
<fill><patternFill patternType="gray125"/></fill>\
<fill><patternFill patternType="solid"><fgColor rgb="FFEDF2FF"/><bgColor indexed="64"/></patternFill></fill>\
</fills>\
<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>\
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>\
<cellXfs count="3">\
<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="center" wrapText="1"/></xf>\
<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="center" wrapText="1"/></xf>\
<xf numFmtId="0" fontId="2" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="center"/></xf>\
</cellXfs>\
<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>\
</styleSheet>"""


def _col_name(index: int) -> str:
    name = ""
    while index > 0:
        index, rem = divmod(index - 1, 26)
        name = chr(65 + rem) + name
    return name


def _cell(ref: str, value: Any, style: int = 0) -> str:
    if value is None:
        return ""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f'<c r="{ref}" s="{style}"><v>{value}</v></c>'
    text = escape(str(value))
    return f'<c r="{ref}" s="{style}" t="inlineStr"><is><t xml:space="preserve">{text}</t></is></c>'


def _xlsx(rows: Iterable[tuple[list[Any], int]], widths: list[int]) -> bytes:
    """rows 里每项是 (单元格列表, 样式序号)。"""
    body: list[str] = []
    for r_index, (values, style) in enumerate(rows, start=1):
        cells = "".join(
            _cell(f"{_col_name(c_index)}{r_index}", value, style)
            for c_index, value in enumerate(values, start=1)
        )
        body.append(f'<row r="{r_index}">{cells}</row>')

    cols = "".join(
        f'<col min="{i}" max="{i}" width="{w}" customWidth="1"/>'
        for i, w in enumerate(widths, start=1)
    )
    sheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f"<cols>{cols}</cols><sheetData>{''.join(body)}</sheetData></worksheet>"
    )

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", _CT)
        zf.writestr("_rels/.rels", _RELS)
        zf.writestr("xl/workbook.xml", _WB)
        zf.writestr("xl/_rels/workbook.xml.rels", _WB_RELS)
        zf.writestr("xl/styles.xml", _STYLES)
        zf.writestr("xl/worksheets/sheet1.xml", sheet)
    return buffer.getvalue()


def _excel(data: dict) -> bytes:
    rows: list[tuple[list[Any], int]] = []
    rows.append((["文章原创度检测报告", "", "", ""], 2))
    rows.append(([], 0))
    rows.append((["文章标题", data["title"], "版本", f"v{data['version_no']}"], 0))
    rows.append((["检测时间", f"{data['analyzed_at']:%Y-%m-%d %H:%M}", "比对语料", f"{data['corpus_size']} 篇"], 0))
    rows.append(([], 0))
    rows.append((["综合评分", data["total"], "等级", data["level_label"]], 1))
    rows.append((["结论", data["summary"], "", ""], 0))
    rows.append(([], 0))
    rows.append((["维度", "评分", "权重", "说明"], 1))
    for key, label, weight, direction in DIMENSIONS:
        value: Any = data["scores"][key]
        if key == "similarity" and data["corpus_size"] == 0:
            value = "样本不足"
        rows.append(([label, value, weight, direction], 0))
    rows.append(([], 0))

    if data["key_points"]:
        rows.append((["关键观点", "新颖度", "库内出现过", ""], 1))
        for point in data["key_points"]:
            rows.append(
                (
                    [
                        point.get("point", ""),
                        point.get("novelty", 0),
                        f"{point.get('sources', 0)} 篇",
                        "",
                    ],
                    0,
                )
            )
        rows.append(([], 0))

    if data["similar_articles"]:
        rows.append((["最相似的稿件", "相似度", "", ""], 1))
        for article in data["similar_articles"]:
            rows.append(
                (
                    [
                        article.get("title", ""),
                        f"{int(round(article.get('similarity', 0) * 100))}%",
                        "",
                        "",
                    ],
                    0,
                )
            )
        rows.append(([], 0))

    if data["segments"]:
        rows.append((["重合位置", "重合度", "本文表述", "对方表述"], 1))
        for seg in data["segments"]:
            rows.append(
                (
                    [
                        seg.get("position", ""),
                        f"{int(round(seg.get('similarity', 0) * 100))}%",
                        seg.get("target_text", ""),
                        seg.get("source_text", ""),
                    ],
                    0,
                )
            )
        rows.append(([], 0))

    if data["risks"]:
        rows.append((["位置", "级别", "说明"], 1))
        for risk in data["risks"]:
            rows.append(
                (
                    [
                        risk.get("position", ""),
                        "严重" if risk.get("severity") == "error" else "提示",
                        risk.get("reason", ""),
                    ],
                    0,
                )
            )
        rows.append(([], 0))

    rows.append((["改进建议", "", "", ""], 1))
    for index, rec in enumerate(data["recommendations"], start=1):
        rows.append(([f"{index}. {rec}", "", "", ""], 0))

    return _xlsx(rows, widths=[18, 14, 46, 46])


# ─────────────────────────── pdf（标准 CJK 字体，不嵌入）───────────────────────────

_PAGE_W, _PAGE_H, _MARGIN = 595.0, 842.0, 52.0
_LEADING = 1.55


def _text_width(text: str, size: float) -> float:
    """粗估行宽：中文与全角标点按 1em，ASCII 按 0.5em。"""
    width = 0.0
    for char in text:
        width += size if ord(char) > 0x2E7F else size * 0.5
    return width


def _wrap(text: str, size: float, max_width: float) -> list[str]:
    if not text:
        return [""]
    lines: list[str] = []
    current = ""
    for char in text:
        if _text_width(current + char, size) > max_width:
            lines.append(current)
            current = char
        else:
            current += char
    if current:
        lines.append(current)
    return lines


def _hex_text(text: str) -> str:
    return text.encode("utf-16-be").hex().upper()


class _Pdf:
    """极简 PDF 生成器：只支持「一行一段文字」，够写报告用。"""

    def __init__(self) -> None:
        self.pages: list[list[str]] = [[]]
        self.y = _PAGE_H - _MARGIN
        self.x = _MARGIN

    def _ensure(self, height: float) -> None:
        if self.y - height < _MARGIN:
            self.pages.append([])
            self.y = _PAGE_H - _MARGIN

    def line(self, text: str, size: float = 10.5, gap: float = 0.35, indent: float = 0.0) -> None:
        leading = size * _LEADING
        for piece in _wrap(text, size, _PAGE_W - 2 * _MARGIN - indent):
            self._ensure(leading)
            self.y -= leading
            self.pages[-1].append(
                f"BT /F1 {size:g} Tf {_MARGIN + indent:.1f} {self.y:.1f} Td <{_hex_text(piece)}> Tj ET"
            )
        self.y -= size * gap

    def heading(self, text: str, size: float = 14.0) -> None:
        self.y -= 6
        self.line(text, size=size, gap=0.2)
        self._ensure(1)
        self.y -= 4
        self.pages[-1].append(
            f"0.6 w 0.75 0.78 0.85 RG {_MARGIN:.1f} {self.y:.1f} m {_PAGE_W - _MARGIN:.1f} {self.y:.1f} l S"
        )
        self.y -= 6

    def space(self, amount: float = 8) -> None:
        self.y -= amount

    def build(self) -> bytes:
        objects: list[bytes] = []

        # 1 catalog, 2 pages, 3 font(type0), 4 cidfont, 5 descriptor,
        # 之后每页占两个对象：先 content(6+2i)，再 page(7+2i)
        total_pages = len(self.pages)
        objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
        kids = " ".join(f"{7 + 2 * i} 0 R" for i in range(total_pages))
        objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {total_pages} >>".encode())
        objects.append(
            b"<< /Type /Font /Subtype /Type0 /BaseFont /STSong-Light "
            b"/Encoding /UniGB-UCS2-H /DescendantFonts [4 0 R] >>"
        )
        objects.append(
            b"<< /Type /Font /Subtype /CIDFontType0 /BaseFont /STSong-Light "
            b"/CIDSystemInfo << /Registry (Adobe) /Ordering (GB1) /Supplement 4 >> "
            b"/FontDescriptor 5 0 R /DW 1000 >>"
        )
        objects.append(
            b"<< /Type /FontDescriptor /FontName /STSong-Light /Flags 6 "
            b"/FontBBox [-25 -254 1000 880] /ItalicAngle 0 /Ascent 880 "
            b"/Descent -120 /CapHeight 880 /StemV 80 >>"
        )

        for index, ops in enumerate(self.pages):
            content = "\n".join(ops).encode("latin-1")
            objects.append(
                b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream"
            )
            objects.append(
                (
                    f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {_PAGE_W:g} {_PAGE_H:g}] "
                    f"/Resources << /Font << /F1 3 0 R >> >> /Contents {6 + 2 * index} 0 R >>"
                ).encode()
            )

        out = io.BytesIO()
        out.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets: list[int] = []
        for number, body in enumerate(objects, start=1):
            offsets.append(out.tell())
            out.write(f"{number} 0 obj\n".encode())
            out.write(body)
            out.write(b"\nendobj\n")

        xref_at = out.tell()
        count = len(objects) + 1
        out.write(f"xref\n0 {count}\n".encode())
        out.write(b"0000000000 65535 f \n")
        for offset in offsets:
            out.write(f"{offset:010d} 00000 n \n".encode())
        out.write(f"trailer\n<< /Size {count} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n".encode())
        return out.getvalue()


def _pdf(data: dict) -> bytes:
    pdf = _Pdf()
    pdf.line("文章原创度检测报告", size=20, gap=0.6)
    pdf.line(f"文件：{data['title']}（v{data['version_no']}）", size=10.5)
    pdf.line(f"检测时间：{data['analyzed_at']:%Y-%m-%d %H:%M}", size=10.5)
    pdf.line(f"比对语料：{data['corpus_size']} 篇稿件", size=10.5)
    if data["self_version_count"]:
        pdf.line(f"同稿其它版本：{data['self_version_count']} 个（不计入比对）", size=10.5)

    pdf.heading("结论")
    pdf.line(
        data["summary"] or f"综合 {data['total']} 分，等级：{data['level_label']}",
        size=11,
    )

    pdf.heading("五维评分")
    for key, label, weight, direction in DIMENSIONS:
        value: Any = data["scores"][key]
        if key == "similarity" and data["corpus_size"] == 0:
            value = "样本不足"
        pdf.line(f"{label}：{value} 分（权重 {weight}，{direction}）", size=10.5)

    if data["key_points"]:
        pdf.heading("关键观点")
        for point in data["key_points"]:
            sources = point.get("sources", 0)
            origin = f"库内 {sources} 篇提过" if sources else "库内未出现"
            pdf.line(
                f"{point.get('point', '')}（新颖度 {point.get('novelty', 0)}，{origin}）",
                size=10.5,
            )

    if data["similar_articles"]:
        pdf.heading("最相似的稿件")
        for article in data["similar_articles"]:
            pdf.line(
                f"《{article.get('title', '')}》 相似度 {int(round(article.get('similarity', 0) * 100))}%",
                size=10.5,
            )

    if data["segments"]:
        pdf.heading("重合片段")
        for seg in data["segments"]:
            pdf.line(f"{seg.get('position', '')}（重合 {int(round(seg.get('similarity', 0) * 100))}%）", size=10.5)
            pdf.line(f"  本文：{seg.get('target_text', '')}", size=10)
            pdf.line(f"  对方：{seg.get('source_text', '')}", size=10, gap=0.6)

    if data["risks"]:
        pdf.heading("风险提示")
        for risk in data["risks"]:
            mark = "严重" if risk.get("severity") == "error" else "提示"
            pdf.line(f"[{mark}] {risk.get('position', '')}：{risk.get('reason', '')}", size=10.5)

    pdf.heading("改进建议")
    for index, rec in enumerate(data["recommendations"], start=1):
        pdf.line(f"{index}. {rec}", size=10.5)

    pdf.space(6)
    pdf.line(f"报告生成于 {datetime.now():%Y-%m-%d %H:%M} · {data['model_version']}", size=9)
    return pdf.build()


# ─────────────────────────── 导出端点 ───────────────────────────


@router.get("/{analysis_id}/report/markdown", summary="导出 Markdown 报告")
async def export_markdown(
    analysis_id: str, user: CurrentUser, db: DbSession
) -> Response:
    analysis, version, item = _load_analysis(db, user, analysis_id)
    data = _report_lines(analysis, item, version)
    return Response(
        content=_markdown(data),
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": f"attachment; filename=originality-{analysis_id[:8]}.md"
        },
    )


@router.get("/{analysis_id}/report/txt", summary="导出纯文本报告")
async def export_text(analysis_id: str, user: CurrentUser, db: DbSession) -> Response:
    analysis, version, item = _load_analysis(db, user, analysis_id)
    data = _report_lines(analysis, item, version)
    return Response(
        content=_text(data),
        media_type="text/plain; charset=utf-8",
        headers={
            "Content-Disposition": f"attachment; filename=originality-{analysis_id[:8]}.txt"
        },
    )


@router.get("/{analysis_id}/report/png", summary="导出图片报告")
async def export_png(analysis_id: str, user: CurrentUser, db: DbSession) -> Response:
    analysis, version, item = _load_analysis(db, user, analysis_id)
    data = _report_lines(analysis, item, version)
    return Response(
        content=_png(data),
        media_type="image/png",
        headers={
            "Content-Disposition": f"attachment; filename=originality-{analysis_id[:8]}.png"
        },
    )


@router.get("/{analysis_id}/report/xlsx", summary="导出 Excel 报告")
async def export_excel(analysis_id: str, user: CurrentUser, db: DbSession) -> Response:
    analysis, version, item = _load_analysis(db, user, analysis_id)
    data = _report_lines(analysis, item, version)
    return Response(
        content=_excel(data),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f"attachment; filename=originality-{analysis_id[:8]}.xlsx"
        },
    )


# 旧版 PDF 端点已移除：原创度报告统一导出为 TXT、Markdown 或图片。
