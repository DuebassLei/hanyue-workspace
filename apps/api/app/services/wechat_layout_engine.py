"""公众号排版增强引擎：SVG 动画卡 + 开头横幅 + 风格/配色目录。

移植自 dsh-wechat-studio 的 vendor/layout（原始出处 lqshow/wechat-article-skills，MIT）。
MIT License
Copyright (c) 2026 林月半子的AI笔记

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

原引擎是纯 JS，本模块用 Python 忠实重写出「与正文无关的自包含片段」部分：

- 15 套配色 THEMES（main/soft/tint/line/mark）与 10 套风格 STYLES 的元数据；
- 8 种 SMIL 动画卡（表格/柱状/步骤/时间线/拆解/对比/概念/手机演示）；
- 开头横幅 banner（poster / neo / card，15 套配色）；
- 独立工作台正文 render_studio_document（十种完整风格，实现在 wechat_layout_studio）；
- 完整单文件 HTML 预览页 render_preview_page（配色切换 + 复制 + 自查报告）。

设计约束：
1. 只输出微信可接受的内联样式：SVG 全用 presentation 属性，不含 class/id/<style>/<script>。
2. 不依赖 Node、不依赖第三方运行时；只用 Python 标准库。
3. 纯函数：输入确定则输出确定，便于 golden 测试与发布链路复用。
4. 独立工作台通过 render_studio_document 使用参考十风格正文引擎，不依赖旧 18-token 排版实现。
"""

from __future__ import annotations

import html as _html
import math
import re
from collections import OrderedDict

# ---------------------------------------------------------------------------
# 配色与风格目录（移植自 vendor/layout/layout.js 的 THEMES / STYLES）
# ---------------------------------------------------------------------------


def _theme(name, main, soft, tint, line, mark):
    return {"name": name, "main": main, "soft": soft, "tint": tint, "line": line, "mark": mark}


THEMES: dict[str, dict] = {
    "orange": _theme("品牌橙", "#C9643E", "#FCF6F2", "#F7E3D5", "#DAB196", "#FCE7A6"),
    "blue": _theme("科技蓝", "#2952B6", "#F4F8FD", "#DDE8FB", "#A6BBE2", "#F9E8A7"),
    "teal": _theme("薄荷绿", "#0C7D6C", "#F3F9F7", "#D8EDE6", "#9BC5B9", "#F8E8AB"),
    "ink": _theme("墨黑", "#1A1D23", "#F9F7F4", "#EAE7E1", "#BFBAB2", "#FAE39F"),
    "navy": _theme("藏青金", "#183053", "#F5F8FB", "#E0E8F3", "#CAAD71", "#F3E0B0"),
    "violet": _theme("葡萄紫", "#5F3F98", "#F8F6FD", "#E9E4F9", "#BFB3DD", "#F8E8AB"),
    "rose": _theme("胭脂红", "#A6344C", "#FDF5F6", "#FAE0E3", "#DEABB2", "#FBE6B3"),
    "amber": _theme("琥珀", "#9D6311", "#FAF7F2", "#F2E5D3", "#CFB791", "#CEE9FD"),
    "morandi": _theme("莫兰迪", "#7D6560", "#FAF7F5", "#ECE6E3", "#C4B8B2", "#F1E4C3"),
    "forest": _theme("松柏金", "#184731", "#F5F8F6", "#E0EAE2", "#CAAD71", "#F3E0B0"),
    "petrol": _theme("孔雀青", "#0A5765", "#F4F8F9", "#DAEBEE", "#A0C2C8", "#FBE7AB"),
    "mist": _theme("雾霾蓝", "#4B6D8A", "#F5F8FA", "#DFE9F0", "#AABECC", "#F9E7B7"),
    "matcha": _theme("橄榄绿", "#596B3A", "#F7F8F4", "#E6E9DB", "#B9BEA3", "#FDE4BB"),
    "cocoa": _theme("可可棕", "#603F2C", "#F9F7F5", "#EEE5DF", "#C8B7A9", "#F8E3B0"),
    "dusty": _theme("豆沙粉", "#9D5C61", "#FBF6F6", "#F5E2E2", "#D5B0AF", "#F9E6BF"),
}

STYLES: dict[str, dict] = {
    "std": {"name": "标准", "r": 14, "desc": "PART 大标题 + 荧光笔 + 圆角卡片"},
    "mag": {"name": "杂志", "r": 2, "desc": "宋体标题居中、细线、留白多"},
    "min": {"name": "极简", "r": 0, "desc": "黑白为主、无底色、左对齐"},
    "geek": {"name": "极客", "r": 6, "desc": "等宽字标签、虚线框、终端感"},
    "bold": {"name": "醒目", "r": 16, "desc": "大色块标题、粗圆角、强对比"},
    "neo": {"name": "质感", "r": 16, "desc": "巨型数字和大引号做底纹、细线描边、柔和阴影"},
    "note": {"name": "手账", "r": 6, "desc": "便签贴纸编号、荧光笔、虚线和纸胶带"},
    "zen": {"name": "雅致", "r": 2, "desc": "印章编号、宋体标题居中、双线金句"},
    "bento": {"name": "卡片", "r": 20, "desc": "大圆角色块、胶囊编号、轻盈扁平"},
    "dark": {"name": "暗夜", "r": 14, "desc": "深色底、亮色强调，科技感"},
}

SVG_CARD_INFO: dict[str, dict] = {
    "svg-table": {"name": "表格", "lead": "一张表看懂"},
    "svg-bars": {"name": "柱状图", "lead": "数据对比"},
    "svg-steps": {"name": "步骤", "lead": "照着做"},
    "svg-timeline": {"name": "时间线", "lead": "时间线"},
    "svg-breakdown": {"name": "拆解", "lead": "拆开看"},
    "svg-compare": {"name": "对比", "lead": "对比"},
    "svg-concept": {"name": "概念", "lead": "一张图看懂"},
    "svg-phone": {"name": "手机演示", "lead": "演示"},
}

# ---------------------------------------------------------------------------
# SVG 基础工具（移植自 vendor/layout/layout.js 的 s* 系列辅助函数）
# ---------------------------------------------------------------------------

SW = 750.0
SD = 10.0
S_OUT = 9.1
SFONT = "'PingFang SC','Helvetica Neue','Microsoft YaHei',sans-serif"
MONO = "Menlo,Consolas,'SF Mono',monospace"
INK = "#111827"
BODY = "#3F3F46"
GRAY = "#6B7280"
BORDER = "#E5E7EB"


def _esc(s) -> str:
    return _html.escape(str(s), quote=False)


def _fixed(x, n) -> str:
    return f"{x:.{n}f}"


def _float_str(x) -> str:
    """Python 的 f\"{x}\" 语义：float 的整数值也带 .0。"""
    if isinstance(x, int):
        return str(x)
    v = float(x)
    return str(v) if not v.is_integer() else f"{v:.1f}"


def _sR(v) -> str:
    s = _fixed(float(v), 1).rstrip("0").rstrip(".")
    return s or "0"


def _clamp_key(x) -> str:
    if x < 0:
        return "0"
    if x > 1:
        return "1"
    return _float_str(round(x, 4))


def _sk(t) -> str:
    return _clamp_key(t / SD)


def _scw(c: str, size: float) -> float:
    return float(size) if ord(c) > 255 else size * 0.56


def _stw(s: str, size: float) -> float:
    return sum(_scw(c, size) for c in str(s))


def _swrap(s: str, size: float, maxw: float) -> list[str]:
    lines: list[str] = []
    cur = ""
    w = 0.0
    for c in str(s):
        cw = _scw(c, size)
        if w + cw > maxw and cur:
            lines.append(cur)
            cur = ""
            w = 0.0
            if c == " ":
                continue
        cur += c
        w += cw
    if cur:
        lines.append(cur)
    return lines or [""]


def _sA(attr, vals, ts, spline=None, typ=None) -> str:
    kt = ";".join(_sk(t) for t in ts)
    vs = ";".join(str(v) for v in vals)
    ex = ""
    if spline:
        ex = f' calcMode="spline" keySplines="{";".join([spline] * (len(ts) - 1))}"'
    tag = (
        f'animateTransform attributeName="transform" type="{typ}"'
        if typ
        else f'animate attributeName="{attr}"'
    )
    return f'<{tag} values="{vs}" keyTimes="{kt}"{ex} dur="{_float_str(SD)}s" repeatCount="indefinite"/>'


def _sIn(t, d=0.35) -> str:
    return _sA("opacity", [0, 0, 1, 1, 0, 0], [0, t, t + d, S_OUT, S_OUT + 0.4, SD])


def _sGrow(attr, a, b, t, d=0.4) -> str:
    return _sA(attr, [a, a, b, b], [0, t, t + d, SD], "0.42 0 0.58 1")


def _sFade(inner, t, dx=0, dy=14) -> str:
    mv = ""
    if dx or dy:
        mv = _sA(
            None,
            [f"{dx} {dy}", f"{dx} {dy}", "0 0", "0 0"],
            [0, t, t + 0.45, SD],
            "0.23 1 0.32 1",
            "translate",
        )
    return f'<g opacity="0">{_sIn(t)}<g>{mv}{inner}</g></g>'


def _sText(x, y, s, size=22, fill="#333333", w=400, anchor="start") -> str:
    return (
        f'<text x="{_sR(x)}" y="{_sR(y)}" font-size="{size}" font-weight="{w}" '
        f'fill="{fill}" text-anchor="{anchor}">{_esc(s)}</text>'
    )


def _sCheck(cx, cy, t, col, r=16) -> str:
    L = round(r * 2.1)
    return (
        f'<g opacity="0">{_sIn(t, 0.1)}'
        f'<circle cx="{_sR(cx)}" cy="{_sR(cy)}" r="{r}" fill="#FFFFFF" stroke="{col}" stroke-width="2.5"/>'
        f'<path d="M{_sR(cx - r * 0.45)} {_sR(cy)}l{_sR(r * 0.32)} {_sR(r * 0.32)}l{_sR(r * 0.6)} {_sR(-r * 0.62)}" '
        f'fill="none" stroke="{col}" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" '
        f'stroke-dasharray="{L}" stroke-dashoffset="{L}">{_sGrow("stroke-dashoffset", L, 0, t + 0.05, 0.35)}</path></g>'
    )


def _sFrame(title, body, H, T) -> str:
    st = T.get("st", "std")
    M = T["main"]
    capW = _stw(title, 24) + 48
    W = SW
    Hh = H
    if st == "mag":
        bg = (
            f'<rect x="1" y="1" width="{_sR(SW - 2)}" height="{_sR(H - 2)}" rx="4" fill="#FFFFFF" stroke="{T["line"]}" stroke-width="1.5"/>'
            + _sText(40, 64, title, 26, INK, 800)
            + f'<rect x="40" y="80" width="44" height="3" fill="{M}"/>'
        )
    elif st == "min":
        bg = (
            f'<rect x="1" y="1" width="{_sR(SW - 2)}" height="{_sR(H - 2)}" fill="#FFFFFF" stroke="{INK}" stroke-width="2"/>'
            + f'<rect x="1" y="1" width="{_sR(SW - 2)}" height="88" fill="{INK}"/>'
            + _sText(40, 56, title, 25, "#FFFFFF", 800)
        )
    elif st == "geek":
        bg = (
            f'<rect x="1" y="1" width="{_sR(SW - 2)}" height="{_sR(H - 2)}" rx="10" fill="{T["soft"]}" stroke="{T["line"]}" stroke-width="1.5" stroke-dasharray="7 6"/>'
            + f'<rect x="36" y="30" width="{_sR(capW + 26)}" height="48" rx="8" fill="#0B0B0C"/>'
            + f'<text x="52" y="62" font-size="22" font-weight="700" fill="{T["line"]}" font-family="Menlo,Consolas,monospace">&gt;</text>'
            + _sText(36 + 26 + capW / 2, 62, title, 24, "#FFFFFF", 800, "middle")
        )
    elif st == "bold":
        bg = (
            f'<rect x="0" y="0" width="{_sR(SW)}" height="{_sR(H)}" rx="28" fill="{T["tint"]}"/>'
            + f'<rect x="36" y="28" width="{_sR(capW)}" height="52" rx="26" fill="{M}"/>'
            + _sText(36 + capW / 2, 63, title, 25, "#FFFFFF", 800, "middle")
        )
    elif st == "neo":
        Hh = H + 18
        bg = "".join(
            f'<rect x="{8 - k * 2}" y="{10 + k * 3}" width="{_sR(SW - 16 + k * 4)}" height="{_sR(H - 6)}" rx="26" fill="#0F172A" opacity="0.028"/>'
            for k in range(3)
        )
        bg += (
            f'<rect x="6" y="1" width="{_sR(SW - 12)}" height="{_sR(H)}" rx="24" fill="#FFFFFF" stroke="{T["tint"]}" stroke-width="1.5"/>'
            + f'<rect x="40" y="38" width="5" height="34" rx="2.5" fill="{M}"/>'
            + _sText(58, 65, title, 27, INK, 800)
        )
    elif st == "dark":
        bg = (
            f'<rect x="0" y="0" width="{_sR(SW)}" height="{_sR(H)}" rx="24" fill="#151A21" stroke="#2A313C" stroke-width="2"/>'
            + f'<rect x="40" y="38" width="5" height="34" rx="2.5" fill="{T["line"]}"/>'
            + _sText(58, 65, title, 27, "#FFFFFF", 800)
        )
    elif st == "note":
        # 手账：白纸卡 + 虚线边 + 纸胶带斜贴 + 荧光笔底（借 dsh 文档级 note 语言的卡面化原创设计）。
        # 胶带用 path 近似旋转矩形而不用 transform，保持微信对 SVG 变换的最大兼容。
        tape_x2 = 40 + 88
        hl_w = _stw(title, 26) + 10
        bg = (
            f'<rect x="1" y="1" width="{_sR(SW - 2)}" height="{_sR(H - 2)}" rx="10" fill="#FFFFFF" stroke="{T["line"]}" stroke-width="1.5" stroke-dasharray="7 6"/>'
            + f'<path d="M40 22L{_sR(tape_x2)} 16L{_sR(tape_x2 + 6)} 44L46 50Z" fill="{T["mark"]}"/>'
            + f'<rect x="40" y="70" width="{_sR(hl_w)}" height="13" fill="{T["tint"]}"/>'
            + _sText(40, 84, title, 26, INK, 800)
        )
    elif st == "zen":
        # 雅致：双线内框 + 宋体居中标题 + 主色短粗线/细线双下划线。
        serif = "Georgia,'Songti SC','STSong','Source Han Serif SC','Noto Serif SC',serif"
        bg = (
            f'<rect x="1" y="1" width="{_sR(SW - 2)}" height="{_sR(H - 2)}" rx="4" fill="#FFFFFF" stroke="{T["line"]}" stroke-width="1.5"/>'
            + f'<rect x="10" y="10" width="{_sR(SW - 20)}" height="{_sR(H - 20)}" rx="2" fill="none" stroke="{T["line"]}" stroke-width="1"/>'
            + f'<text x="{_sR(SW / 2)}" y="62" font-size="25" font-weight="700" fill="{INK}" text-anchor="middle" font-family="{serif}">{_esc(title)}</text>'
            + f'<rect x="{_sR(SW / 2 - 22)}" y="76" width="44" height="2.5" fill="{M}"/>'
            + f'<rect x="{_sR(SW / 2 - 34)}" y="82" width="68" height="1" fill="{T["line"]}"/>'
        )
    elif st == "bento":
        # 卡片：大圆角 soft 底 + 白色胶囊标题（与 std 的主色药丸形成区分）。
        pill_w = capW + 24
        bg = (
            f'<rect x="0" y="0" width="{_sR(SW)}" height="{_sR(H)}" rx="28" fill="{T["soft"]}"/>'
            + f'<rect x="36" y="28" width="{_sR(pill_w)}" height="52" rx="26" fill="#FFFFFF" stroke="{T["tint"]}" stroke-width="1.5"/>'
            + _sText(36 + pill_w / 2, 63, title, 25, INK, 800, "middle")
        )
    else:
        bg = (
            f'<rect x="0" y="0" width="{_sR(SW)}" height="{_sR(H)}" rx="24" fill="{T["soft"]}"/>'
            + f'<rect x="36" y="30" width="{_sR(capW)}" height="48" rx="24" fill="{M}"/>'
            + _sText(36 + capW / 2, 62, title, 24, "#FFFFFF", 800, "middle")
        )
    return (
        f'<section style="margin:28px 0;"><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {_sR(W)} {_sR(Hh)}" width="100%" '
        f'style="display:block;max-width:100%;height:auto;margin:0 auto;font-family:{SFONT}">{bg}{body}</svg></section>'
    )


def _sFooter(quote, y, t, T):
    if not quote:
        return "", y
    w = min(_stw(quote, 24), 640)
    x0 = (SW - w) / 2
    return (
        _sFade(_sText(SW / 2, y + 30, quote, 24, INK, 800, "middle"), t, 0, 10)
        + f'<g opacity="0">{_sIn(t + 0.3, 0.05)}'
        + f'<line x1="{_sR(x0)}" y1="{_sR(y + 44)}" x2="{_sR(x0)}" y2="{_sR(y + 44)}" stroke="{T["main"]}" stroke-width="5" stroke-linecap="round">{_sGrow("x2", _sR(x0), _sR(x0 + w), t + 0.3, 0.5)}</line></g>',
        y + 80,
    )


def _terminal(lines) -> str:
    """黑底 terminal：动画卡格式不对时的兜底代码框（与参考 terminal 一致）。"""
    rows = "".join(
        f'<p style="margin:0;white-space:pre;font-size:14px;line-height:1.9;color:#E5E7EB;font-family:{MONO};">{_esc(l) or " "}</p>'
        for l in lines
    )
    dots = "".join(
        f'<span style="display:inline-block;width:11px;height:11px;border-radius:50%;background:{c};margin-right:7px;"></span>'
        for c in ("#FF5F57", "#FEBC2E", "#28C840")
    )
    return (
        '<section style="margin:24px 0;background:#0B0B0C;border-radius:10px;padding:14px 18px 16px;">'
        f'<section style="margin-bottom:10px;line-height:0;">{dots}</section>'
        f'<section style="overflow-x:auto;">{rows}</section></section>'
    )


_SEP_RE = re.compile(r"^\|?\s*:?-{2,}")


def _sSplit(lines) -> tuple[list, str]:
    rows = []
    quote = ""
    for line in lines:
        s = str(line).strip()
        if not s:
            continue
        if s.startswith(">"):
            quote = s[1:].strip()
            continue
        if _SEP_RE.match(s) and all(c in "|-: " for c in s):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        hl = any("★" in c for c in cells)
        rows.append([[c.replace("★", "").strip() for c in cells], hl])
    return rows, quote


def _range(n) -> range:
    return range(max(0, n))


# ---------------------------------------------------------------------------
# 8 种动画卡
# ---------------------------------------------------------------------------


def _svg_table(title, lines, T):
    rows, quote = _sSplit(lines)
    head = rows[0][0]
    body = rows[1:]
    n = len(head)
    X0, WID = 40.0, SW - 80
    need = []
    for c in range(n):
        widths = [_stw(head[c], 20)] + [
            _stw(r[0][c], 22) if c < len(r[0]) else 0.0 for r in body
        ]
        need.append(max(widths) + 32)
    tot = sum(need) or 1
    cw = [WID * w / tot for w in need]
    xs = [X0 + sum(cw[:i]) for i in _range(n)]
    o = [f'<rect x="{_sR(X0)}" y="104" width="{_sR(WID)}" height="50" rx="12" fill="{T["tint"]}"/>']
    for c in _range(n):
        o.append(_sText(xs[c] + 18, 136, head[c], 20, T["main"], 800))
    y, t = 166.0, 0.5
    for cells, hl in body:
        wl = [_swrap(cells[c] if c < len(cells) else "", 22, max(cw[c] - 30, 1)) for c in _range(n)]
        h = max((len(w) for w in wl), default=1) * 32 + 30
        inner = (
            f'<rect x="{_sR(X0)}" y="{_sR(y)}" width="{_sR(WID)}" height="{_sR(h)}" rx="12" fill="#FFFFFF" '
            f'stroke="{T["main"] if hl else T["tint"]}" stroke-width="{2.5 if hl else 1.5}"/>'
        )
        if hl:
            inner += f'<rect x="{_sR(X0)}" y="{_sR(y)}" width="6" height="{_sR(h)}" rx="3" fill="{T["main"]}"/>'
        for c in _range(n):
            for j, ln in enumerate(wl[c]):
                fill = T["main"] if (hl and c == 0) else (INK if c == 0 else BODY)
                inner += _sText(xs[c] + 18, y + 36 + j * 32, ln, 22, fill, 700 if c == 0 else 400)
        o.append(_sFade(inner, t, -24, 0))
        y += h + 10
        t += 0.35
    if any(hl for _, hl in body):
        o.append(f'<g opacity="0">{_sIn(t + 0.1, 0.1)}' + _sText(SW - 52, 136, "★ 重点", 18, T["main"], 800, "end") + "</g>")
    f, y2 = _sFooter(quote, y + 6, t + 0.4, T)
    o.append(f)
    return _sFrame(title, "".join(o), y2 + 24, T)


def _svg_bars(title, lines, T):
    rows, quote = _sSplit(lines)
    vals = []
    for r in rows:
        digits = re.sub(r"[^\d.]", "", r[0][1]) or "0"
        vals.append(float(digits))
    vmax = max(vals) or 1
    hls = [r[1] for r in rows]
    if not any(hls):
        hls = [v == vmax for v in vals]
    lw = min(220, max(_stw(r[0][0], 24) for r in rows) + 24)
    bx = 40 + lw
    bwmax = SW - 40 - bx - 110
    o = []
    y, t = 116.0, 0.5
    for (cells, _), v, hl in zip(rows, vals, hls):
        col = T["main"] if hl else T["line"]
        w = max(8.0, bwmax * v / vmax)
        cy = y + 22
        txt = cells[2] if len(cells) > 2 and cells[2] else (cells[1] if len(cells) > 1 else "")
        o.append(_sFade(_sText(40, cy + 9, cells[0], 24, INK, 700 if hl else 500), t, -16, 0))
        o.append(
            f'<g opacity="0">{_sIn(t, 0.1)}'
            f'<rect x="{_sR(bx)}" y="{_sR(cy - 16)}" width="{_sR(bwmax)}" height="32" rx="16" fill="#FFFFFF"/>'
            f'<rect x="{_sR(bx)}" y="{_sR(cy - 16)}" width="0" height="32" rx="16" fill="{col}">{_sGrow("width", 0, _sR(w), t + 0.1, 0.6)}</rect></g>'
        )
        o.append(
            f'<g opacity="0">{_sIn(t + 0.6, 0.2)}'
            + _sText(bx + w + 14, cy + 9, txt, 24, T["main"] if hl else GRAY, 800)
            + "</g>"
        )
        y += 64
        t += 0.45
    f, y2 = _sFooter(quote, y + 4, t + 0.4, T)
    o.append(f)
    return _sFrame(title, "".join(o), y2 + 24, T)


def _svg_steps(title, lines, T):
    rows, quote = _sSplit(lines)
    o = []
    X0, WID = 40.0, SW - 80
    y, t = 108.0, 0.5
    for step_no, (cells, _) in enumerate(rows, 1):
        tl = _swrap(cells[0], 24, WID - 170)
        sl = _swrap(cells[1], 19, WID - 170) if len(cells) > 1 and cells[1] else []
        h = 36 + len(tl) * 32 + len(sl) * 28 + (6 if sl else 0)
        inner = (
            f'<rect x="{_sR(X0)}" y="{_sR(y)}" width="{_sR(WID)}" height="{_sR(h)}" rx="18" fill="#FFFFFF" stroke="{T["tint"]}" stroke-width="1.5"/>'
            f'<circle cx="{_sR(X0 + 46)}" cy="{_sR(y + h / 2)}" r="24" fill="{T["main"]}"/>'
            + _sText(X0 + 46, y + h / 2 + 9, str(step_no), 24, "#FFFFFF", 800, "middle")
        )
        ty = y + 18 + 26
        for ln in tl:
            inner += _sText(X0 + 92, ty, ln, 24, INK, 800)
            ty += 32
        ty += 4
        for ln in sl:
            inner += _sText(X0 + 92, ty, ln, 19, GRAY)
            ty += 28
        o.append(_sFade(inner, t, -28, 0))
        o.append(_sCheck(X0 + WID - 44, y + h / 2, t + 0.45, T["main"]))
        y += h + 14
        t += 0.6
    f, y2 = _sFooter(quote, y + 4, t + 0.3, T)
    o.append(f)
    return _sFrame(title, "".join(o), y2 + 24, T)


def _svg_timeline(title, lines, T):
    rows, quote = _sSplit(lines)
    rows = rows[:6]
    n = len(rows)
    xa, xb = 90.0, SW - 90
    xs = [xa + (xb - xa) * i / max(n - 1, 1) for i in _range(n)]
    span = (xb - xa) / max(n - 1, 1)
    ly, t0, dur = 196.0, 0.5, 0.45 * n
    o = [
        f'<g opacity="0">{_sIn(t0, 0.05)}'
        f'<line x1="{_sR(xa)}" y1="{_sR(ly)}" x2="{_sR(xa)}" y2="{_sR(ly)}" stroke="{T["line"]}" stroke-width="4" stroke-linecap="round">{_sGrow("x2", _sR(xa), _sR(xb), t0, dur)}</line></g>'
    ]
    maxl = 1
    for (cells, hl), xi in zip(rows, xs):
        ti = t0 + dur * (xi - xa) / max(xb - xa, 1)
        tl = _swrap(cells[1] if len(cells) > 1 else "", 19, min(span - 14, 220))
        maxl = max(maxl, len(tl))
        inner = (
            _sText(xi, ly - 30, cells[0], 22, T["main"], 800, "middle")
            + f'<circle cx="{_sR(xi)}" cy="{_sR(ly)}" r="{12 if hl else 10}" fill="{T["main"]}" stroke="#FFFFFF" stroke-width="4"/>'
            + "".join(
                _sText(xi, ly + 46 + j * 28, ln, 19, INK if hl else BODY, 700 if hl else 400, "middle")
                for j, ln in enumerate(tl)
            )
        )
        o.append(_sFade(inner, ti, 0, 12))
    y = ly + 46 + maxl * 28
    f, y2 = _sFooter(quote, y, t0 + dur + 0.4, T)
    o.append(f)
    return _sFrame(title, "".join(o), y2 + 24, T)


def _svg_breakdown(title, lines, T):
    rows, quote = _sSplit(lines)
    lw = max(_stw(r[0][0], 20) for r in rows) + 30
    tx = 40 + lw + 18
    tmax = SW - 40 - tx
    o = []
    y, t = 112.0, 0.5
    for cells, hl in rows:
        tl = _swrap(cells[1] if len(cells) > 1 else "", 22, tmax)
        h = len(tl) * 36
        o.append(
            _sFade(
                f'<rect x="40" y="{_sR(y - 2)}" width="{_sR(lw)}" height="36" rx="18" fill="{T["main"] if hl else T["tint"]}"/>'
                + _sText(40 + lw / 2, y + 23, cells[0], 20, "#FFFFFF" if hl else T["main"], 800, "middle"),
                t,
                0,
                0,
            )
        )
        for line_idx, ln in enumerate(tl):
            ly = y + line_idx * 36
            w = _stw(ln, 22) + 12
            ts = t + 0.35 + line_idx * 0.25
            o.append(
                f'<g opacity="0">{_sIn(t + 0.15, 0.2)}'
                f'<rect x="{_sR(tx - 6)}" y="{_sR(ly + 1)}" width="0" height="32" rx="6" fill="{T["main"] if hl else T["tint"]}">{_sGrow("width", 0, _sR(w), ts, 0.35)}</rect>'
                f'<text x="{_sR(tx)}" y="{_sR(ly + 25)}" font-size="22" font-weight="{700 if hl else 400}" fill="#9CA3AF">'
                f'{_sA("fill", ["#9CA3AF", "#9CA3AF", "#FFFFFF" if hl else INK, "#FFFFFF" if hl else INK], [0, ts + 0.2, ts + 0.35, SD])}'
                f"{_esc(ln)}</text></g>"
            )
        y += max(h, 36) + 20
        t += 0.35 + len(tl) * 0.25 + 0.15
    f, y2 = _sFooter(quote, y, t + 0.2, T)
    o.append(f)
    return _sFrame(title, "".join(o), y2 + 24, T)


def _svg_compare(title, lines, T):
    rows, quote = _sSplit(lines)
    head_cells = rows[0][0][:2]
    if len(head_cells) != 2:
        raise ValueError("not enough values to unpack (expected 2)")
    lt, rt = head_cells
    items = rows[1:]
    cw, lx = 300.0, 40.0
    rx = SW - 40 - cw
    tw = cw - 74
    wl = [
        (_swrap(c[0], 21, tw), _swrap(c[1] if len(c) > 1 else "", 21, tw))
        for c, _ in items
    ]
    hs = [max(len(a), len(b)) * 30 + 26 for a, b in wl]
    top = 112.0
    H = 64 + sum(hs) + 10 * (len(hs) - 1) + 20
    o = [
        _sFade(
            f'<rect x="{_sR(lx)}" y="{_sR(top)}" width="{_sR(cw)}" height="{_sR(H)}" rx="18" fill="#FFFFFF" stroke="{BORDER}" stroke-width="1.5"/>'
            + _sText(lx + cw / 2, top + 42, lt, 22, GRAY, 800, "middle"),
            0.3,
            0,
            10,
        ),
        _sFade(
            f'<rect x="{_sR(rx)}" y="{_sR(top)}" width="{_sR(cw)}" height="{_sR(H)}" rx="18" fill="#FFFFFF" stroke="{T["main"]}" stroke-width="2.5"/>'
            + f'<rect x="{_sR(rx)}" y="{_sR(top)}" width="{_sR(cw)}" height="60" rx="18" fill="{T["main"]}"/>'
            + f'<rect x="{_sR(rx)}" y="{_sR(top + 40)}" width="{_sR(cw)}" height="20" fill="{T["main"]}"/>'
            + _sText(rx + cw / 2, top + 42, rt, 22, "#FFFFFF", 800, "middle"),
            0.5,
            0,
            10,
        ),
        f'<g opacity="0">{_sIn(0.7)}'
        f'<path d="M{_float_str(SW / 2 - 14)} {_float_str(top + H / 2 - 14)}L{_float_str(SW / 2 + 10)} {_float_str(top + H / 2)}L{_float_str(SW / 2 - 14)} {_float_str(top + H / 2 + 14)}" '
        f'fill="none" stroke="{T["main"]}" stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/></g>',
    ]
    y, t = top + 74, 0.9
    for (a, b), h in zip(wl, hs):
        cy = y + 18
        o.append(
            _sFade(
                f'<circle cx="{_sR(lx + 28)}" cy="{_sR(cy)}" r="11" fill="#E5E7EB"/>'
                f'<path d="M{_sR(lx + 24)} {_sR(cy - 4)}l8 8M{_sR(lx + 32)} {_sR(cy - 4)}l-8 8" stroke="#9CA3AF" stroke-width="2.4" stroke-linecap="round"/>'
                + "".join(_sText(lx + 50, y + 25 + j * 30, ln, 21, "#9CA3AF") for j, ln in enumerate(a)),
                t,
                -14,
                0,
            )
        )
        o.append(
            _sFade(
                f'<circle cx="{_sR(rx + 28)}" cy="{_sR(cy)}" r="11" fill="{T["main"]}"/>'
                f'<path d="M{_sR(rx + 23)} {_sR(cy)}l4 4l7 -8" fill="none" stroke="#FFFFFF" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"/>'
                + "".join(_sText(rx + 50, y + 25 + j * 30, ln, 21, INK, 700) for j, ln in enumerate(b)),
                t + 0.3,
                14,
                0,
            )
        )
        y += h + 10
        t += 0.6
    f, y2 = _sFooter(quote, top + H + 16, t + 0.2, T)
    o.append(f)
    return _sFrame(title, "".join(o), (y2 if quote else top + H) + 24, T)


def _svg_concept(title, lines, T):
    rows, quote = _sSplit([l for l in lines if not str(l).strip().startswith("!")])
    notes = [str(l).strip()[1:].strip() for l in lines if str(l).strip().startswith("!")]
    lead = [r[0][0] for r in rows if len(r[0]) == 1]
    tracks = [r for r in rows if len(r[0]) > 1][:2]
    if len(tracks) == 2:
        old, nw = tracks[0], tracks[1]
    else:
        old, nw = None, tracks[0]
    M = T["main"]
    o = []
    y, t = 104.0, 0.4
    for ld in lead[:1]:
        for ln in _swrap(ld, 26, SW - 80):
            y += 36
            o.append(_sFade(_sText(40, y, ln, 26, INK, 800), t, 0, 10))
        y += 14
        t += 0.4
    refs = [tr for tr in (old, nw) if tr]
    lw = min(230, max(_stw(tr[0][0], 21) for tr in refs) + 34)
    xa = 40 + lw + 34
    xb = SW - 70

    def track(tr, yy, tt, live):
        cells = tr[0]
        nodes = cells[1:5]
        n = len(nodes)
        col = M if live else "#B6BCC6"
        xs = [xa + (xb - xa) * (i + 0.5) / n for i in _range(n)]
        span = (xb - xa) / n
        dur = 0.5 * n if live else 0.3
        out = [
            _sFade(
                f'<rect x="40" y="{_sR(yy - 19)}" width="{_sR(lw)}" height="38" rx="19" fill="{M if live else "#E5E7EB"}"/>'
                + _sText(40 + lw / 2, yy + 7, cells[0], 21, "#FFFFFF" if live else GRAY, 800, "middle"),
                tt,
                -12,
                0,
            )
        ]
        dash = "" if live else ' stroke-dasharray="3 9"'
        out.append(
            f'<g opacity="0">{_sIn(tt + 0.15, 0.05)}'
            f'<line x1="{_sR(xa)}" y1="{_sR(yy)}" x2="{_sR(xa)}" y2="{_sR(yy)}" stroke="{col}" stroke-width="{5 if live else 4}" stroke-linecap="round"{dash}>{_sGrow("x2", _sR(xa), _sR(xb), tt + 0.15, dur)}</line></g>'
        )
        maxl = 1
        for i, nd in enumerate(nodes):
            ti = tt + 0.15 + dur * (xs[i] - xa) / max(xb - xa, 1)
            tl = _swrap(nd, 19, span - 12)
            maxl = max(maxl, len(tl))
            out.append(
                _sFade(
                    f'<circle cx="{_sR(xs[i])}" cy="{_sR(yy)}" r="{11 if live else 8}" fill="{col if live else "#FFFFFF"}" stroke="{"#FFFFFF" if live else col}" stroke-width="{4 if live else 3}"/>'
                    + "".join(
                        _sText(xs[i], yy + 40 + j * 26, ln, 19, INK if live else "#9CA3AF", 700 if live else 400, "middle")
                        for j, ln in enumerate(tl)
                    ),
                    ti,
                    0,
                    8,
                )
            )
        return out, yy + 40 + maxl * 26, tt + 0.15 + dur + 0.3

    if old:
        a, y2, t2 = track(old, y + 34, t, False)
        o.extend(a)
        y, t = y2 + 6, t2
    a, y2, t2 = track(nw, y + 34, t, True)
    o.extend(a)
    y, t = y2 + 4, t2
    for nt in notes[:1]:
        tl = _swrap(nt, 21, SW - 140)
        h = len(tl) * 32 + 28
        o.append(
            _sFade(
                f'<rect x="40" y="{_sR(y)}" width="{_sR(SW - 80)}" height="{_sR(h)}" rx="14" fill="#FFFFFF" stroke="{T["line"]}" stroke-width="2" stroke-dasharray="8 6"/>'
                + "".join(_sText(SW / 2, y + 38 + j * 32, ln, 21, M, 700, "middle") for j, ln in enumerate(tl)),
                t,
                0,
                10,
            )
        )
        y += h + 14
        t += 0.5
    f, y3 = _sFooter(quote, y + 4, t + 0.2, T)
    o.append(f)
    return _sFrame(title, "".join(o), y3 + 24, T)


def _svg_phone(title, lines, T):
    rows, _ = _sSplit(lines)
    subs = [r[0][0] for r in rows if len(r[0]) == 1]
    sc = [r[0] for r in rows if len(r[0]) > 1][:5]
    sub = subs[0] if subs else "示意演示，非真实截图"
    M = T["main"]
    n = len(sc)
    S, D = 2.8, n * 2.8

    def kk(t):
        return _clamp_key(t / D)

    def A(attr, vals, ts, disc=False):
        return (
            f'<animate attributeName="{attr}" values="{";".join(str(v) for v in vals)}" '
            f'keyTimes="{";".join(kk(t) for t in ts)}"'
            + (' calcMode="discrete"' if disc else "")
            + f' dur="{_sR(D)}s" repeatCount="indefinite"/>'
        )

    def win(i, a=0.0, f=0.25):
        t0, t1 = i * S + a, (i + 1) * S
        if i == 0 and a == 0:
            return A("opacity", [1, 1, 0, 0], [0, t1 - 0.25, t1, D])
        return A("opacity", [0, 0, 1, 1, 0, 0], [0, t0, t0 + f, t1 - 0.25, t1, D])

    px, py, pw, ph = 44.0, 132.0, 262.0, 520.0
    sx, sy = px + 12, py + 12
    sw, sh = pw - 24, ph - 24
    o = [
        _sText(40, 112, sub, 19, GRAY),
        f'<rect x="{_sR(px)}" y="{_sR(py)}" width="{_sR(pw)}" height="{_sR(ph)}" rx="42" fill="{INK}"/>',
        f'<rect x="{_sR(sx)}" y="{_sR(sy)}" width="{_sR(sw)}" height="{_sR(sh)}" rx="31" fill="#F3F4F6"/>',
        f'<rect x="{_float_str(px + pw / 2 - 36)}" y="{_sR(sy + 10)}" width="72" height="18" rx="9" fill="{INK}"/>',
    ]
    for i, c in enumerate(sc):
        name = c[0]
        who = c[1] if len(c) > 2 else ""
        text = c[-1]
        me = who in ("我", "me", "Me", "用户")
        tl = _swrap(text, 19, sw - 74)
        bw = max(_stw(l, 19) for l in tl) + 30
        bh = len(tl) * 28 + 22
        bx = sx + sw - 16 - bw if me else sx + 16
        by = sy + 132
        o.append(
            f'<g opacity="{1 if i == 0 else 0}">{win(i)}'
            + _sText(px + pw / 2, sy + 66, name, 20, INK, 800, "middle")
            + f'<rect x="{_sR(sx + 20)}" y="{_sR(sy + 82)}" width="{_sR(sw - 40)}" height="1.5" fill="#E5E7EB"/>'
            + (_sText(px + pw / 2, sy + 112, who, 15, "#9CA3AF", 400, "middle") if who and not me else "")
            + "</g>"
        )
        o.append(
            f'<g opacity="0">{win(i, 0.5)}'
            f'<rect x="{_sR(bx)}" y="{_sR(by)}" width="{_sR(bw)}" height="{bh}" rx="16" fill="{M if me else "#FFFFFF"}"/>'
            + "".join(_sText(bx + 15, by + 31 + j * 28, l, 19, "#FFFFFF" if me else INK) for j, l in enumerate(tl))
            + "</g>"
        )
        o.append(
            f'<g opacity="0">{win(i, 1.3)}'
            f'<circle cx="{_float_str(px + pw / 2)}" cy="{_sR(by + bh + 62)}" r="24" fill="#FFFFFF" stroke="{M}" stroke-width="3"/>'
            f'<path d="M{_float_str(px + pw / 2 - 11)} {_sR(by + bh + 62)}l8 8l14 -15" fill="none" stroke="{M}" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/></g>'
        )
        dx = px + pw / 2 - (n - 1) * 9 + i * 18
        o.append(
            f'<circle cx="{_sR(dx)}" cy="{_sR(sy + sh - 22)}" r="4" fill="#D1D5DB">'
            + A("fill", ["#D1D5DB", M, "#D1D5DB"] if i else [M, "#D1D5DB", "#D1D5DB"], [0, i * S, (i + 1) * S] if i else [0, S, D], True)
            + "</circle>"
        )
    lx = px + pw + 40
    lwid = SW - 40 - lx
    rh = min(92, (ph - 12 * (n - 1)) / n)
    ly = py + (ph - (n * rh + (n - 1) * 12)) / 2
    for i, c in enumerate(sc):
        yy = ly + i * (rh + 12)
        on = [0, i * S, (i + 1) * S] if i else [0, S, D]

        def hi(pair, b, _i=i, _on=on):
            a0, a1 = pair
            return A(a0, [b, a1, b] if _i else [a1, b, b], _on, True)

        o.append(
            f'<rect x="{_sR(lx)}" y="{_sR(yy)}" width="{_sR(lwid)}" height="{_sR(rh)}" rx="16" fill="#FFFFFF" stroke="{T["tint"]}" stroke-width="2">'
            + hi(("fill", M), "#FFFFFF")
            + "</rect>"
            + f'<circle cx="{_sR(lx + 40)}" cy="{_sR(yy + rh / 2)}" r="18" fill="{T["tint"]}">'
            + hi(("fill", "#FFFFFF"), T["tint"])
            + "</circle>"
            + f'<text x="{_sR(lx + 40)}" y="{_sR(yy + rh / 2 + 7)}" font-size="19" font-weight="800" fill="{M}" text-anchor="middle">{i + 1}</text>'
            + f'<text x="{_sR(lx + 74)}" y="{_sR(yy + rh / 2 + 8)}" font-size="23" font-weight="800" fill="{INK}">'
            + hi(("fill", "#FFFFFF"), INK)
            + _esc(_swrap(c[0], 23, lwid - 96)[0])
            + "</text>"
        )
    return _sFrame(title, "".join(o), py + ph + 30, T)


_CARD_RENDERERS = {
    "svg-table": _svg_table,
    "svg-bars": _svg_bars,
    "svg-steps": _svg_steps,
    "svg-timeline": _svg_timeline,
    "svg-breakdown": _svg_breakdown,
    "svg-compare": _svg_compare,
    "svg-concept": _svg_concept,
    "svg-phone": _svg_phone,
}

SVG_CARD_KINDS = tuple(_CARD_RENDERERS)


def resolve_style_theme(style=None, theme=None) -> dict:
    """把风格与配色合成渲染上下文 T；非法值回退默认。"""
    st = style if style in STYLES else "std"
    th = theme if theme in THEMES else "orange"
    T = dict(THEMES[th])
    T["st"] = st
    T["r"] = STYLES[st]["r"]
    return T


def render_svg_card(kind, title, lines, *, style="std", theme="orange") -> dict:
    """渲染一张 SMIL 动画卡。

    @returns {{html, warnings}}：格式不对时 html 回退为安全代码框，warnings 给出原因。
    """
    warnings: list[str] = []
    T = resolve_style_theme(style, theme)
    lead = SVG_CARD_INFO[kind]["lead"] if kind in SVG_CARD_INFO else "undefined"
    title = title or lead
    try:
        renderer = _CARD_RENDERERS.get(kind)
        if renderer is None:
            raise TypeError("'NoneType' object is not callable")
        html = renderer(title, lines or [], T)
    except (ValueError, IndexError, ZeroDivisionError, TypeError) as exc:
        warnings.append(
            f"{kind}「{title}」的内容格式不对（{type(exc).__name__}），没画成动画卡，先按普通代码框显示。"
            "对照写法表检查每行的 | 分隔"
        )
        html = _terminal(lines or [])
    return {"html": html, "warnings": warnings}


# ---------------------------------------------------------------------------
# 开头横幅 banner（移植自 vendor/layout/banner.js 的 build / checkSpec）
#
# 范围：poster / neo / card 三种 style 的完整路径（poster 的 flow/tags/stat/steps
# 右栏、card 的 12 种 RIGHTS 右栏、avatar/brand 分支、GIF_URL_HERE 占位符）。
# banner.js 里的 scene 模板（LAYOUTS：pipeline/fly/path/before_after/hub/stack/
# dialog/calendar/funnel/chart）与点阵品牌头（SLOGAN/PyRandom）不在本次移植范围：
# build 对其他 style 返回空 html，由 checkSpec 给出可读提示；checkSpec 仍保留
# 场景模板分支的自检逻辑。
# ---------------------------------------------------------------------------

_BANNER_THEMES = {
    "blue": {"main": "#2563EB", "light": "#DBEAFE", "line": "#93C5FD", "border": "#BFDBFE", "bg": "#F8FAFC"},
    "orange": {"main": "#C9643E", "light": "#F6E1D6", "line": "#E3A688", "border": "#EBCBB9", "bg": "#FBF8F5"},
    "teal": {"main": "#12A37F", "light": "#D5F2E8", "line": "#7FD1B6", "border": "#BCE7D8", "bg": "#F6FBF9"},
}

_BCW, _BCH, _BTT = 1246, 420, 4.4
_BEASE = "0.23 1 0.32 1"
_BTEAL = "#12A37F"
_BINK2, _BGRAY, _BGRAY2, _BMONO = "#111827", "#6B7280", "#94A3B8", "#94A3B8"
_BX0, _BX1 = 592, 1196
_BRIGHTS = ("steps", "remix", "pick", "timeline", "converge", "tree",
            "compare", "storyboard", "chat", "stats", "checklist", "terminal")
_BLAYOUTS = ("pipeline", "fly", "path", "before_after", "hub", "stack",
             "dialog", "calendar", "funnel", "chart")
_BPY_WS = ("\t\n\u000b\f\r\u001c\u001d\u001e\u001f \u0085\u00a0\u1680"
           "\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a"
           "\u2028\u2029\u202f\u205f\u3000")
_BWS_ATTR = re.compile("[" + re.escape(_BPY_WS) + r'](id|class)="')


def _pyStr(v) -> str:
    if isinstance(v, str):
        return v
    if v is None:
        return "None"
    if isinstance(v, bool):
        return "True" if v else "False"
    if isinstance(v, (int, float)):
        return _float_str(v)
    return repr(v)


def _jsnum(v) -> str:
    """JavaScript 的 String(number)：整数型 float 不带 .0。"""
    if isinstance(v, str):
        return v
    if isinstance(v, bool):
        return "True" if v else "False"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return str(int(v)) if v.is_integer() else repr(v)
    return str(v)


def _bEsc(v) -> str:
    """py.js escQ：html.escape(s, quote=True)。"""
    return _html.escape(_pyStr(v), quote=True)


def _truthy(v) -> bool:
    if v is None or v is False:
        return False
    if isinstance(v, (str, list, tuple, dict)):
        return len(v) > 0
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return v != 0
    return True


def _g(obj, k, default=None):
    if not isinstance(obj, dict):
        raise TypeError(f"'{type(obj).__name__}' object has no attribute 'get'")
    return obj[k] if k in obj else default


def _sub(obj, k):
    if isinstance(obj, dict):
        if k in obj:
            return obj[k]
        raise KeyError(k)
    raise TypeError(f"'{type(obj).__name__}' object is not subscriptable")


def _mapping(v):
    if not isinstance(v, dict):
        raise TypeError(f"'{type(v).__name__}' object is not a mapping")
    return v


def _tget(table, k, default):
    return table[k] if isinstance(k, str) and k in table else default


def _head(v, n):
    if isinstance(v, list):
        return v[:n]
    if isinstance(v, str):
        return list(v)[:n]
    raise TypeError("not subscriptable")


def _iter(v):
    if isinstance(v, list):
        return v
    if isinstance(v, str):
        return list(v)
    if isinstance(v, dict):
        return list(v)
    raise TypeError(f"'{type(v).__name__}' object is not iterable")


def _pyLen(v) -> int:
    if isinstance(v, (list, str, dict, tuple)):
        return len(v)
    raise TypeError(f"object of type '{type(v).__name__}' has no len()")


def _bR(v) -> str:
    """banner.js 的 R：f\"{v:.1f}\".rstrip('0').rstrip('.')，-0 归零。"""
    x = float(v)
    if x == 0:
        x = 0.0
    return f"{x:.1f}".rstrip("0").rstrip(".") or "0"


def _bS(v, is_float) -> str:
    return _float_str(v) if is_float else str(int(v))


def _key_time(t, dur) -> str:
    q = t / dur
    if q < 0:
        return "0"
    if q > 1:
        return "1"
    return _float_str(round(q, 4))


def _Fz(attr, vals, ts, spline=None, typ=None) -> str:
    kt = ";".join(_key_time(t, _BTT) for t in ts)
    vs = ";".join(_jsnum(v) for v in vals)
    ex = ""
    if spline:
        sp = spline if isinstance(spline, list) else [spline] * (len(ts) - 1)
        ex = f' calcMode="spline" keySplines="{";".join(sp)}"'
    if typ:
        return (f'<animateTransform attributeName="transform" type="{typ}" values="{vs}" '
                f'keyTimes="{kt}"{ex} dur="{_float_str(_BTT)}s" fill="freeze"/>')
    return (f'<animate attributeName="{attr}" values="{vs}" keyTimes="{kt}"{ex} '
            f'dur="{_float_str(_BTT)}s" fill="freeze"/>')


def _fIn(t, d=0.3) -> str:
    return _Fz("opacity", [0, 0, 1, 1], [0, t, t + d, _BTT])


def _fRise(t, inner, dy=18, d=0.4) -> str:
    return (f'<g opacity="0">{_fIn(t, d)}<g>'
            + _Fz(None, [f"0 {_jsnum(dy)}", f"0 {_jsnum(dy)}", "0 0", "0 0"],
                 [0, t, t + d, _BTT], ["0 0 1 1", _BEASE, "0 0 1 1"], "translate")
            + f"{inner}</g></g>")


def _fPop(cx, cy, t, inner, big=1.08) -> str:
    return (f'<g opacity="0">{_fIn(t, 0.08)}<g transform="translate({_bR(cx)} {_bR(cy)})"><g>'
            + _Fz(None, [0.6, 0.6, _float_str(float(big)), 0.98, 1, 1],
                 [0, t, t + 0.18, t + 0.28, t + 0.36, _BTT],
                 ["0 0 1 1", "0.3 0 0.6 1", "0.4 0 0.6 1", "0.4 0 0.6 1", "0 0 1 1"], "scale")
            + f'<g transform="translate({_bR(-cx)} {_bR(-cy)})">{inner}</g></g></g></g>')


def _line_icon(name, cx, cy, c, lt, sc=1.0) -> str:
    sn = f'stroke="{c}" stroke-width="{_bR(4 / sc)}" stroke-linecap="round" stroke-linejoin="round"'
    s = 'fill="none" ' + sn
    I = {
        "grid": f'<path d="M-12-30V30M12-30V30M-34-11H34M-34 11H34" {s}/>',
        "code": f'<path d="M-20-18L-38 0L-20 18M20-18L38 0L20 18M6-24L-6 24" {s}/>',
        "play": f'<rect x="-36" y="-26" width="72" height="52" rx="6" fill="{lt}"/><path d="M-10-15L16 0L-10 15Z" fill="{c}"/>',
        "doc": f'<path d="M-20-30H8L22-16V30H-20Z" {s}/><path d="M8-30V-16H22M-10-2H12M-10 12H8" {s}/>',
        "lines": f'<path d="M-36-16H36M-36 0H24M-36 16H30" {s}/>',
        "chat": f'<path d="M-32-24H32V14H-6L-20 28V14H-32Z" {s}/><path d="M-16-6H16" {s}/>',
        "spark": f'<path d="M0-30Q4-4 30 0Q4 4 0 30Q-4 4-30 0Q-4-4 0-30Z" {s}/>',
        "image": f'<rect x="-34" y="-24" width="68" height="48" rx="6" {s}/><path d="M-30 18L-8-4L6 10L14 2L30 18" {s}/><circle cx="14" cy="-10" r="5" fill="{c}"/>',
        "mic": f'<rect x="-9" y="-30" width="18" height="34" rx="9" {s}/><path d="M-20-2A20 20 0 0 0 20-2M0 18V28M-12 28H12" {s}/>',
        "music": f'<path d="M-10 20V-24L22-30V14" {s}/><circle cx="-18" cy="20" r="8" {s}/><circle cx="14" cy="14" r="8" {s}/>',
        "bell": f'<path d="M-20 14V-4A20 20 0 0 1 20-4V14L26 20H-26Z" {s}/><path d="M-6 26H6" {s}/>',
        "calendar": f'<rect x="-30" y="-24" width="60" height="52" rx="6" {s}/><path d="M-30-8H30M-14-32V-18M14-32V-18" {s}/>',
        "chart": f'<rect x="-36" y="-26" width="72" height="52" rx="6" fill="{lt}" opacity=".6"/><path d="M-24 14V2M-12 14V-8M0 14V-2M10-12H28M10 0H28M10 12H22" {s}/>',
        "mail": f'<rect x="-34" y="-22" width="68" height="44" rx="5" {s}/><path d="M-32-18L0 6L32-18" {s}/>',
        "person": f'<circle cy="-14" r="13" {s}/><path d="M-26 28Q-26 4 0 4Q26 4 26 28" {s}/>',
        "cube": f'<path d="M0-30L28-15V15L0 30L-28 15V-15Z" fill="{lt}" {sn}/><path d="M-28-15L0 0L28-15M0 0V30" {s}/>',
        "game": f'<path d="M-24-14H24A14 14 0 0 1 36 4L32 18A8 8 0 0 1 18 22L10 12H-10L-18 22A8 8 0 0 1-32 18L-36 4A14 14 0 0 1-24-14Z" {s}/><path d="M-20-2V8M-25 3H-15" {s}/><circle cx="18" cy="0" r="3" fill="{c}"/><circle cx="24" cy="7" r="3" fill="{c}"/>',
        "scissors": f'<circle cx="-14" cy="18" r="9" {s}/><circle cx="14" cy="18" r="9" {s}/><path d="M-8 11L18-28M8 11L-18-28" {s}/>',
        "text": f'<path d="M-26-24H26M0-24V28M-12 28H12" {s}/>',
        "wand": f'<path d="M-26 26L14-14" {s}/><path d="M18-34L21-23L32-20L21-17L18-6L15-17L4-20L15-23Z" fill="{c}"/>',
        "robot": f'<rect x="-28" y="-18" width="56" height="42" rx="10" {s}/><path d="M0-18V-30" {s}/><circle cy="-32" r="3" fill="{c}"/><circle cx="-11" cy="2" r="4" fill="{c}"/><circle cx="11" cy="2" r="4" fill="{c}"/>',
        "web": f'<rect x="-40" y="-26" width="80" height="52" rx="3" {s}/><rect x="-32" y="-18" width="64" height="36" fill="{lt}"/><path d="M-20 10L-10-6H10L20 10" {s}/><circle cy="4" r="4" fill="{c}"/>',
        "home": f'<path d="M-26-4L0-28L26-4V26H-26Z" fill="{lt}" {sn}/>',
        "dice": f'<rect x="-26" y="-26" width="52" height="52" rx="8" {s}/><circle cx="-11" cy="-11" r="4" fill="{c}"/><circle r="4" fill="{c}"/><circle cx="11" cy="11" r="4" fill="{c}"/>',
        "box": f'<rect x="-30" y="-24" width="60" height="48" rx="3" {s}/><path d="M-30-10H30M-8 2H8" {s}/>',
        "layers": f'<path d="M0-26L30-12L0 2L-30-12Z" {s}/><path d="M-30 0L0 14L30 0M-30 12L0 26L30 12" {s}/>',
        "monitor": f'<rect x="-34" y="-26" width="68" height="44" rx="5" fill="{lt}" {sn}/><path d="M-18-10H18M-18 2H10M-12 30H12M0 18V30" {s}/>',
        "heart": f'<path d="M0 26C-36 4-28-26 0-12C28-26 36 4 0 26Z" {s}/>',
        "film": f'<rect x="-36" y="-24" width="72" height="48" rx="5" {s}/><path d="M-24-24V24M24-24V24M-36-8H-24M-36 8H-24M24-8H36M24 8H36" {s}/>',
        "scene_wide": f'<path d="M-24-28H24L16-16H-16Z" {s}/><path d="M-40 26H40M32 26V-30H44V26" {s}/><circle cy="0" r="7" fill="{c}"/><path d="M-12 26L0 6L12 26Z" fill="{c}"/>',
        "scene_mid": f'<circle cy="-24" r="11" fill="{c}"/><path d="M-22 30L0-10L22 30Z" fill="{c}"/><path d="M-40 10L-18-2M18-2L40-14" {s}/>',
        "scene_close": f'<path d="M-40 24L-10 4L10 14L40-12" fill="none" stroke="{c}" stroke-width="{_bR(8 / sc)}" stroke-linecap="round" opacity=".35"/><rect x="-18" y="-24" width="36" height="26" rx="3" fill="#FFFFFF" {sn}/><path d="M-18-22L0-8L18-22" {s}/>',
    }
    return f'<g transform="translate({_bR(cx)} {_bR(cy)}) scale({_float_str(float(sc))})">{I.get(name, I["spark"])}</g>'


def _txt(x, y, s, size=16, w=400, fill=_BINK2, anchor="start", ls=0) -> str:
    lsa = f' letter-spacing="{ls}"' if ls else ""
    return (f'<text x="{_bR(x)}" y="{_bR(y)}" font-size="{_jsnum(size)}" font-weight="{_jsnum(w)}" '
            f'fill="{fill}" text-anchor="{anchor}"{lsa}>{_bEsc(s)}</text>')


def _box(x, y, w, h, stroke, fill="#FFFFFF", rx=16, sw=2) -> str:
    return (f'<rect x="{_bR(x)}" y="{_bR(y)}" width="{_bR(w)}" height="{_bR(h)}" '
            f'rx="{_jsnum(rx)}" fill="{fill}" stroke="{stroke}" stroke-width="{_jsnum(sw)}"/>')


def _gline(x1, y1, x2, y2, col, t, d=0.25, sw=2.5, dash="") -> str:
    da = f' stroke-dasharray="{dash}"' if dash else ""
    return (f'<line x1="{_bR(x1)}" y1="{_bR(y1)}" x2="{_bR(x1)}" y2="{_bR(y1)}" stroke="{col}" '
            f'stroke-width="{_jsnum(sw)}" stroke-linecap="round"{da}>'
            + _Fz("x2", [_bR(x1), _bR(x1), _bR(x2), _bR(x2)], [0, t, t + d, _BTT])
            + _Fz("y2", [_bR(y1), _bR(y1), _bR(y2), _bR(y2)], [0, t, t + d, _BTT])
            + "</line>")


def _gpath(d, L, col, t, dur=0.4, sw=2.5) -> str:
    return (f'<path d="{d}" fill="none" stroke="{col}" stroke-width="{_jsnum(sw)}" '
            f'stroke-linecap="round" stroke-linejoin="round" stroke-dasharray="{_jsnum(L)}" '
            f'stroke-dashoffset="{_jsnum(L)}">'
            + _Fz("stroke-dashoffset", [L, L, 0, 0], [0, t, t + dur, _BTT]) + "</path>")


def _bArrow(x1, x2, y, col, t, yF=False) -> str:
    return (_gline(x1, y, x2 - 2, y, col, t)
            + f'<g opacity="0">{_fIn(t + 0.22, 0.1)}'
            + f'<path d="M{_bR(x2 - 10)} {_bS(y - 7, yF)}L{_bR(x2)} {_bS(y, yF)}L{_bR(x2 - 10)} {_bS(y + 7, yF)}" '
              'fill="none" stroke="' + col + '" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/></g>')


def _cycle(i, k, each=1.5, low=0.4) -> str:
    if k < 2:
        return ""
    e = 0.12 / k
    s0 = i / k
    s1 = (i + 1) / k
    lo = (low, True)
    one = (1, False)
    if i == 0:
        pts = [((0, False), one), ((s1 - e, True), one), ((s1, True), lo),
               ((1 - e, True), lo), ((1, False), one)]
    elif i == k - 1:
        pts = [((0, False), lo), ((s0, True), lo), ((s0 + e, True), one),
               ((1 - e, True), one), ((1, False), lo)]
    else:
        pts = [((0, False), lo), ((s0, True), lo), ((s0 + e, True), one),
               ((s1 - e, True), one), ((s1, True), lo), ((1, False), lo)]
    values = ";".join(_bS(v, f) for _, (v, f) in pts)
    keytimes = ";".join(_float_str(round(v, 4)) if f else str(v) for (v, f), _ in pts)
    return (f'<animate attributeName="opacity" values="{values}" keyTimes="{keytimes}" '
            f'dur="{_float_str(k * each)}s" begin="{_float_str(_BTT)}s" repeatCount="indefinite"/>')


def _button(y, h, text, th, t, x=_BX0, w=None, size=22) -> str:
    if w is None:
        w = _BX1 - x
    mid = x + w / 2
    shine = (f'<path d="M{_jsnum(x + 14)} {_jsnum(y)}h34l-{_bR(h * 0.35)} {_jsnum(h)}h-34Z" '
             'fill="#FFFFFF" opacity="0">'
             f'<animateTransform attributeName="transform" type="translate" values="0 0;{_bR(w - 60)} 0;{_bR(w - 60)} 0" '
             f'keyTimes="0;0.35;1" dur="3.2s" begin="{_float_str(_BTT)}s" repeatCount="indefinite"/>'
             '<animate attributeName="opacity" values="0;0.28;0.28;0;0" keyTimes="0;0.05;0.3;0.35;1" '
             f'dur="3.2s" begin="{_float_str(_BTT)}s" repeatCount="indefinite"/></path>')
    rx = _float_str(h / 2) if h / 2 < 16 else "16"
    return _fPop(mid, y + h / 2, t,
                 f'<rect x="{_jsnum(x)}" y="{_jsnum(y)}" width="{_bR(w)}" height="{_jsnum(h)}" rx="{rx}" fill="{th["main"]}"/>'
                 + _txt(mid, y + h / 2 + size * 0.36, text, size, 800, "#FFFFFF", "middle") + shine, 1.05)


def _mono(x, y, s, t, anchor="start") -> str:
    return f'<g opacity="0">{_fIn(t)}{_txt(x, y, s, 14, 500, _BMONO, anchor, 2)}</g>'


def _caption(y, s, th, t) -> str:
    return _fRise(t, _txt((_BX0 + _BX1) / 2, y, s, 22, 800, th["main"], "middle"), 8)


def _items(c, k, n):
    return _head(_g(c, k, []), n)


def _tw(s, size) -> float:
    acc = 0.0
    for ch in _pyStr(s):
        acc += float(size) if ord(ch) > 255 else size * 0.58
    return acc


def _r_steps(c, th, t):
    M, LT, LN, BD = th["main"], th["light"], th["line"], th["border"]
    o = []
    steps = _items(c, "steps", 4)
    n = len(steps)
    gap = 26
    cw = (_BX1 - _BX0 - gap * (n - 1)) / max(n, 1)
    y0, h = 72, 150
    for i, st in enumerate(steps):
        x = _BX0 + i * (cw + gap)
        ti = t + i * 0.28
        if i:
            o.append(_gline(x - gap, y0 + h / 2, x, y0 + h / 2, LN, ti - 0.1, 0.18))
        card = (_box(x, y0, cw, h, BD, "#FFFFFF", 18)
                + _line_icon(_g(st, "icon", "spark"), x + cw / 2, y0 + 56, M, LT)
                + _txt(x + cw / 2, y0 + 126, _g(st, "label", ""), 19, 700, M, "middle"))
        o.append(_fPop(x + cw / 2, y0 + h / 2, ti, card))
    mid = (_BX0 + _BX1) / 2
    t2 = t + n * 0.28 + 0.15
    o.append(f'<line x1="{_float_str(mid)}" y1="262" x2="{_float_str(mid)}" y2="262" stroke="{LN}" stroke-width="2.5">'
             + _Fz("x1", [_float_str(mid), _float_str(mid), _float_str(mid - 215), _float_str(mid - 215)],
                   [0, t2, t2 + 0.35, _BTT], ["0 0 1 1", _BEASE, "0 0 1 1"])
             + _Fz("x2", [_float_str(mid), _float_str(mid), _float_str(mid + 215), _float_str(mid + 215)],
                   [0, t2, t2 + 0.35, _BTT], ["0 0 1 1", _BEASE, "0 0 1 1"])
             + "</line>")
    t3 = t2 + 0.25
    o.append(_button(294, 64, _g(c, "result", ""), th, t3))
    flow_raw = _g(c, "flow")
    flow = _iter(flow_raw) if _truthy(flow_raw) else [_g(s, "label", "") for s in steps]
    t4 = t3 + 0.35
    o.append(f'<g opacity="0">{_fIn(t4, 0.2)}<circle cx="{_BX0 + 30}" cy="383" r="5" fill="{M}"/></g>')
    spans = ""
    for i, seg in enumerate(flow):
        if not isinstance(seg, str):
            raise TypeError(f'can only concatenate str (not "{type(seg).__name__}") to str')
        spans += (f'<tspan fill-opacity="0">{_Fz("fill-opacity", [0, 0, 1, 1], [0, t4 + 0.15 * i, t4 + 0.15 * i + 0.2, _BTT])}'
                  f'{_bEsc((" → " if i else "") + seg)}</tspan>')
    o.append(f'<text x="{_BX0 + 46}" y="389" font-size="16" font-weight="700" fill="#4B5563" xml:space="preserve">{spans}</text>')
    return o


def _r_remix(c, th, t):
    M, LT, LN, BD = th["main"], th["light"], th["line"], th["border"]
    o = []
    cards = _items(c, "cards", 3)
    gap = 50
    cw = (_BX1 - _BX0 - 2 * gap) / 3
    y0, h = 64, 158
    centers = []
    for i, cd in enumerate(cards):
        x = _BX0 + i * (cw + gap)
        ti = t + i * 0.35
        cx = x + cw / 2
        centers.append(cx)
        if _truthy(_g(cd, "hl")):
            hy = y0 + 22
            hh = h - 44
            inner = (_box(x, hy, cw, hh, LN, LT, 16)
                     + _txt(cx, hy + 48, _g(cd, "title", ""), 21, 800, M, "middle")
                     + _txt(cx, hy + 84, _g(cd, "sub", ""), 19, 600, "#475569", "middle"))
        else:
            art = _g(cd, "art", "nodes")
            if art == "nodes":
                ax = x + 24
                ay = y0 + 70
                a = (f'<path d="M{_bR(ax + 48)} {ay + 13}H{_bR(ax + 78)}M{_bR(ax + 30)} {ay + 26}V{ay + 56}H{_bR(ax + 78)}" fill="none" stroke="{BD}" stroke-width="2"/>'
                     f'<rect x="{_bR(ax)}" y="{ay}" width="48" height="26" rx="5" fill="#E2E8F0"/>'
                     f'<rect x="{_bR(ax + 78)}" y="{ay}" width="{_bR(cw - 126)}" height="26" rx="5" fill="{LT}"/>'
                     f'<rect x="{_bR(ax + 78)}" y="{ay + 44}" width="{_bR(cw - 126)}" height="24" rx="5" fill="{LT}" opacity=".55"/>')
            elif art == "image":
                ax = x + 18
                ay = y0 + 58
                aw = cw - 36
                a = (f'<rect x="{_bR(ax)}" y="{ay}" width="{_bR(aw)}" height="80" rx="6" fill="{LT}"/>'
                     f'<path d="M{_bR(ax)} {ay + 76}L{_bR(ax + aw * 0.3)} {ay + 40}L{_bR(ax + aw * 0.55)} {ay + 66}'
                     f'L{_bR(ax + aw * 0.75)} {ay + 30}L{_bR(ax + aw)} {ay + 70}V{ay + 80}H{_bR(ax)}Z" fill="{LN}" opacity=".7"/>'
                     f'<circle cx="{_bR(ax + aw / 2)}" cy="{ay + 28}" r="9" fill="{M}"/>'
                     f'<path d="M{_bR(ax + aw / 2 - 18)} {ay + 58}A18 16 0 0 1 {_bR(ax + aw / 2 + 18)} {ay + 58}Z" fill="{M}"/>')
            else:
                a = _line_icon(art, cx, y0 + 100, M, LT, 0.85)
            col = M if i == len(cards) - 1 else "#475569"
            inner = (_box(x, y0, cw, h, BD) + _txt(cx, y0 + 34, _g(cd, "title", ""), 19, 800, col, "middle") + a)
        if i:
            o.append(_bArrow(x - gap + 8, x - 6, y0 + h / 2, LN, ti - 0.15, True))
        o.append(_fPop(cx, y0 + h / 2, ti, inner))
    t2 = t + len(cards) * 0.35 + 0.1
    tx = _BX0 + (_BX1 - _BX0) * 0.5
    if not centers:
        raise IndexError("list index out of range")
    last = centers[-1]
    d = f"M{_bR(last)} {y0 + h}V{y0 + h + 30}H{_bR(tx)}V{y0 + h + 60}"
    L = round(30 + abs(last - tx) + 30 + 4)
    o.append(_gpath(d, L, LN, t2, 0.35))
    ty = y0 + h + 60
    thh = 76
    t3 = t2 + 0.3
    o.append(f'<g opacity="0">{_fIn(t3)}{_box(_BX0, ty, _BX1 - _BX0, thh, BD)}'
             + _txt(_BX0 + 22, ty + 26, _g(c, "track_label", "SHOT"), 14, 700, _BGRAY2, "start", 2) + "</g>")
    segs = _iter(_g(c, "track", [0.36, 0.4, 0.24]))
    wtot = _BX1 - _BX0 - 44
    cols = [LN, LT, LT]
    x = _BX0 + 22
    for i, f in enumerate(segs):
        if isinstance(f, (str, list)):
            raise TypeError(f"unsupported operand type(s) for -: '{type(f).__name__}' and 'int'")
        if isinstance(f, bool):
            fv = 1.0 if f else 0.0
        elif isinstance(f, (int, float)):
            fv = float(f)
        else:
            raise TypeError(f"unsupported operand type(s) for -: '{type(f).__name__}' and 'int'")
        w = wtot * fv - 10
        ti = t3 + 0.15 + i * 0.18
        o.append(f'<rect x="{_bR(x)}" y="{ty + 40}" width="0" height="20" rx="4" fill="{cols[i % 3]}" '
                 f'opacity="{1 if i < 2 else 0.6}">{_Fz("width", [0, 0, _bR(w), _bR(w)], [0, ti, ti + 0.25, _BTT])}</rect>')
        x += wtot * fv
    o.append(f'<g opacity="0">{_fIn(t3 + 0.7)}<rect x="{_BX0 + 22}" y="{ty + 32}" width="3" height="36" '
             f'rx="1.5" fill="{M}"><animateTransform attributeName="transform" type="translate" '
             f'values="0 0;{_bR(wtot - 4)} 0;{_bR(wtot - 4)} 0" keyTimes="0;0.8;1" dur="4s" '
             f'begin="{_float_str(_BTT)}s" repeatCount="indefinite"/></rect></g>')
    o.append(_caption(ty + thh + 32, _g(c, "caption", ""), th, t3 + 0.6))
    return o


def _r_pick(c, th, t):
    M, LT, LN = th["main"], th["light"], th["line"]
    o = []
    gx, gy, tw_, th_, gp = _BX0, 66, 60, 76, 12
    for i in range(6):
        r_ = i // 3
        cidx = i % 3
        x = gx + cidx * (tw_ + gp)
        y = gy + r_ * (th_ + gp)
        tile = (_box(x, y, tw_, th_, "#E2E8F0", "#FFFFFF", 8, 1.5)
                + f'<circle cx="{x + 30}" cy="{y + 24}" r="9" fill="#CBD5E1"/>'
                + f'<rect x="{x + 14}" y="{y + 44}" width="32" height="4" rx="2" fill="#CBD5E1"/>'
                + f'<rect x="{x + 14}" y="{y + 56}" width="32" height="4" rx="2" fill="#CBD5E1"/>')
        o.append(_fPop(x + tw_ / 2, y + th_ / 2, t + i * 0.08, tile))
    o.append(f'<g opacity="0">{_fIn(t + 0.5)}{_txt(gx + 102, gy + 2 * (th_ + gp) + 20, _g(c, "grid_label", ""), 17, 600, _BGRAY2, "middle")}</g>')
    t2 = t + 0.7
    cy = 152
    px = _BX0 + 300
    o.append(_gline(_BX0 + 222, cy, px - 34, cy, LN, t2, 0.2))
    o.append(_fPop(px, cy, t2 + 0.15,
                   f'<circle cx="{px}" cy="{cy}" r="32" fill="{LT}" stroke="{LN}" stroke-width="2"/>'
                   + _txt(px, cy + 9, _g(c, "pick", "选"), 24, 800, M, "middle"), 1.15))
    o.append(f'<g opacity="0">{_fIn(t2 + 0.3)}{_txt(px, cy + 60, _g(c, "pick_label", ""), 17, 600, "#475569", "middle")}</g>')
    o.append(_bArrow(px + 36, _BX0 + 404, cy, LN, t2 + 0.35))
    hx, hy, hw, hh = _BX0 + 418, 44, 176, 236
    hc = [hx + hw / 2, hy + hh / 2]
    t3 = t2 + 0.6
    card = (f'<g transform="rotate(5 {_bR(hc[0])} {_bR(hc[1])})">' + _box(hx, hy, hw, hh, M, "#FFFFFF", 14, 3.5)
            + f'<line x1="{hx + 26}" y1="{hy + 24}" x2="{hx + hw - 26}" y2="{hy + 24}" stroke="{LT}" stroke-width="2"/>'
            + _txt(hx + 24, hy + 66, _g(c, "card_title1", ""), 20, 800, _BINK2)
            + _txt(hx + 24, hy + 102, _g(c, "card_title2", ""), 28, 800, M)
            + f'<circle cx="{hx + 118}" cy="{hy + 168}" r="38" fill="{LT}"/>'
              f'<path d="M{hx + 48} {hy + 204}L{hx + 96} {hy + 136}L{hx + 150} {hy + 200}Z" fill="{M}"/>'
            + f'<line x1="{hx + 26}" y1="{hy + hh - 18}" x2="{hx + hw - 26}" y2="{hy + hh - 18}" stroke="{LN}" stroke-width="2"/></g>')
    float_ = (f'<g>{card}<animateTransform attributeName="transform" type="translate" '
              f'values="0 0;0 -5;0 0" keyTimes="0;0.5;1" dur="3s" begin="{_float_str(_BTT)}s" repeatCount="indefinite"/></g>')
    o.append(_fPop(hc[0], hc[1], t3, float_, 1.12))
    o.append(_gline(_BX0, 326, _BX1, 326, LT, t3 + 0.3, 0.4, 2, "6 7"))
    o.append(_caption(372, _g(c, "caption", ""), th, t3 + 0.5))
    return o


def _r_timeline(c, th, t):
    M, LN, BD = th["main"], th["line"], th["border"]
    o = []
    items = _items(c, "items", 4)
    k = len(items)
    lx = _BX0 + 40
    cx0 = _BX0 + 96
    top = 44
    bottom = 344 if _truthy(_g(c, "result")) else 390
    gap = 14
    h = (bottom - top - gap * (k - 1)) / k
    ys = [top + i * (h + gap) for i in range(k)]
    o.append(_gline(lx, ys[0] + h / 2, lx, ys[-1] + h / 2, LN, t, 0.25 * k, 4))
    for i, it in enumerate(items):
        y = ys[i]
        ti = t + i * 0.25
        cy = y + h / 2
        box = _box(cx0, y, _BX1 - cx0, h, BD, "#FFFFFF", 14)
        inner = (box + _txt(cx0 + 28, cy - 4 if _truthy(_g(it, "desc")) else cy + 8, _g(it, "title", ""), 21, 800, M)
                 + (_txt(cx0 + 28, cy + 24, _g(it, "desc", ""), 16, 400, "#475569") if _truthy(_g(it, "desc")) else ""))
        dot = f'<circle cx="{lx}" cy="{_bR(cy)}" r="10" fill="{M}"/>'
        o.append(f'<g>{_cycle(i, k)}{_fPop(lx, cy, ti + 0.05, dot, 1.4)}'
                 f'{_fPop(cx0 + (_BX1 - cx0) / 2, cy, ti, inner, 1.04)}</g>')
    if _truthy(_g(c, "result")):
        o.append(_button(358, 38, c["result"], th, t + k * 0.25 + 0.2, cx0, _BX1 - cx0, 18))
    return o


def _r_converge(c, th, t):
    M, LT, LN, BD = th["main"], th["light"], th["line"], th["border"]
    o = []
    o.append(_mono((_BX0 + _BX1) / 2, 58, _g(c, "caption", ""), t, "middle"))
    cards = _items(c, "cards", 4)
    n = len(cards)
    gap = 16
    cw = (_BX1 - _BX0 - gap * (n - 1)) / n
    y0, h = 74, 170
    xs = []
    for i, cd in enumerate(cards):
        x = _BX0 + i * (cw + gap)
        ti = t + 0.15 + i * 0.28
        xs.append(x + cw / 2)
        if i:
            o.append(_gline(x - gap, y0 + h / 2, x, y0 + h / 2, LN, ti - 0.05, 0.12))
        inner = (_box(x, y0, cw, h, BD) + _txt(x + 22, y0 + 30, _g(cd, "num", ""), 15, 700, _BGRAY2)
                 + _line_icon(_g(cd, "icon", "doc"), x + cw / 2, y0 + 86, LN if i == 0 else M, LT, 0.95)
                 + _txt(x + cw / 2, y0 + 146, _g(cd, "label", ""), 20, 800, M, "middle"))
        o.append(_fPop(x + cw / 2, y0 + h / 2, ti, inner))
    t2 = t + 0.15 + n * 0.28
    by = y0 + h + 22
    mid = (_BX0 + _BX1) / 2
    for x in xs:
        o.append(_gline(x, y0 + h, x, by, LN, t2, 0.15))
    o.append(_gline(mid, by, xs[0], by, LN, t2 + 0.15, 0.25))
    o.append(_gline(mid, by, xs[-1], by, LN, t2 + 0.15, 0.25))
    o.append(_gline(mid, by, mid, by + 18, LN, t2 + 0.35, 0.12))
    bxY = by + 18
    t3 = t2 + 0.45
    inner = (_box(_BX0, bxY, _BX1 - _BX0, 392 - bxY, BD, LT, 16)
             + _txt(mid, bxY + 40, _g(c, "box_title", ""), 23, 800, M, "middle")
             + _txt(mid, bxY + 72, _g(c, "box_sub", ""), 17, 500, "#475569", "middle"))
    o.append(_fPop(mid, (bxY + 392) / 2, t3, inner, 1.04))
    return o


def _r_tree(c, th, t):
    M, LT, LN, BD = th["main"], th["light"], th["line"], th["border"]
    o = []
    mid = (_BX0 + _BX1) / 2
    hw = 440
    o.append(_fPop(mid, 70, t, _box(mid - hw / 2, 44, hw, 52, BD, "#FFFFFF", 26)
                   + _txt(mid, 77, _g(c, "head", ""), 20, 500, "#475569", "middle")))
    items = _items(c, "items", 4)
    gap = 18
    cw = (_BX1 - _BX0 - gap) / 2
    ch = 88
    ys = [130, 130 + ch + 14]
    cxs = [_BX0 + cw / 2, _BX0 + cw + gap + cw / 2]
    t2 = t + 0.3
    o.append(_gline(mid, 96, mid, 112, LN, t2, 0.1))
    o.append(_gline(mid, 112, cxs[0], 112, LN, t2 + 0.1, 0.2))
    o.append(_gline(mid, 112, cxs[1], 112, LN, t2 + 0.1, 0.2))
    o.append(_gline(cxs[0], 112, cxs[0], 130, LN, t2 + 0.3, 0.1))
    o.append(_gline(cxs[1], 112, cxs[1], 130, LN, t2 + 0.3, 0.1))
    for i, it in enumerate(items):
        r_ = i // 2
        ci = i % 2
        x = _BX0 + ci * (cw + gap)
        y = ys[r_]
        ti = t2 + 0.4 + i * 0.15
        inner = (_box(x, y, cw, ch, BD, "#FFFFFF", 14)
                 + _line_icon(_g(it, "icon", "spark"), x + 46, y + ch / 2, LN, LT, 0.62)
                 + _txt(x + 86, y + 38, _g(it, "title", ""), 20, 800, M)
                 + _txt(x + 86, y + 66, _g(it, "desc", ""), 15, 400, _BGRAY))
        o.append(f'<g>{_cycle(i, len(items))}{_fPop(x + cw / 2, y + ch / 2, ti, inner, 1.05)}</g>')
    o.append(_button(338, 54, _g(c, "result", ""), th, t2 + 0.5 + len(items) * 0.15, _BX0, None, 19))
    return o


def _r_compare(c, th, t):
    M, LT, LN, BD = th["main"], th["light"], th["line"], th["border"]
    o = []
    o.append(_mono(_BX0, 56, _g(c, "caption", ""), t))
    mid = (_BX0 + _BX1) / 2
    o.append(_fPop(mid, 98, t + 0.1, _box(_BX0, 72, _BX1 - _BX0, 52, BD, LT, 14)
                   + _txt(mid, 105, _g(c, "head", ""), 20, 800, M, "middle", 2), 1.04))
    items = _items(c, "items", 4)
    n = len(items)
    gap = 22
    cw = (_BX1 - _BX0 - gap * (n - 1)) / n
    y0, h = 150, 132
    cxs = [_BX0 + i * (cw + gap) + cw / 2 for i in range(n)]
    t2 = t + 0.4
    o.append(_gline(mid, 124, mid, 138, LN, t2, 0.1))
    o.append(_gline(mid, 138, cxs[0], 138, LN, t2 + 0.1, 0.2))
    o.append(_gline(mid, 138, cxs[-1], 138, LN, t2 + 0.1, 0.2))
    for x in cxs:
        o.append(_gline(x, 138, x, y0, LN, t2 + 0.3, 0.08))
    for i, it in enumerate(items):
        x = _BX0 + i * (cw + gap)
        ti = t2 + 0.4 + i * 0.2
        inner = (_box(x, y0, cw, h, BD)
                 + _line_icon(_g(it, "icon", "web"), x + cw / 2, y0 + 52, LN, LT, 0.95)
                 + _txt(x + cw / 2, y0 + 108, _g(it, "label", ""), 19, 800, "#334155", "middle"))
        o.append(f'<g>{_cycle(i, n)}{_fPop(x + cw / 2, y0 + h / 2, ti, inner)}</g>')
    o.append(_button(306, 66, _g(c, "result", ""), th, t2 + 0.5 + n * 0.2))
    return o


def _r_storyboard(c, th, t):
    M, LT, LN, BD = th["main"], th["light"], th["line"], th["border"]
    o = []
    o.append(_mono(_BX0, 56, _g(c, "caption", ""), t))
    who = _g(c, "who", {})
    wx, wy, ww, wh = _BX0, 72, 152, 186
    if _truthy(_g(who, "avatar")):
        art = (f'<image x="{wx + 18}" y="{wy + 18}" width="{ww - 36}" height="120" '
               'preserveAspectRatio="xMidYMax meet" href="GIF_URL_HERE"/>')
    else:
        art = _line_icon(_g(who, "icon", "person"), wx + ww / 2, wy + 78, M, LT, 1.2)
    o.append(_fPop(wx + ww / 2, wy + wh / 2, t + 0.1,
                   _box(wx, wy, ww, wh, BD)
                   + f'<rect x="{wx + 16}" y="{wy + 16}" width="{ww - 32}" height="124" rx="10" fill="{LT}" opacity=".6"/>'
                   + art
                   + _txt(wx + ww / 2, wy + wh - 18, _g(who, "label", ""), 19, 800, "#1F2937", "middle")))
    o.append(_bArrow(wx + ww + 12, wx + ww + 48, wy + wh / 2, LN, t + 0.4, True))
    px = wx + ww + 60
    pw = _BX1 - px
    t2 = t + 0.55
    o.append(_fPop(px + pw / 2, wy + wh / 2, t2,
                   _box(px, wy, pw, wh, BD)
                   + f'<line x1="{px + 18}" y1="{wy + 20}" x2="{_BX1 - 18}" y2="{wy + 20}" stroke="{LT}" stroke-width="6" stroke-dasharray="18 12"/>', 1.03))
    shots = _items(c, "shots", 3)
    n = max(len(shots), 1)
    gg = 14
    sw = (pw - 36 - gg * (n - 1)) / n
    sy = wy + 38
    sh = 104
    for i, sh_ in enumerate(shots):
        x = px + 18 + i * (sw + gg)
        ti = t2 + 0.3 + i * 0.22
        o.append(_fPop(x + sw / 2, sy + sh / 2, ti,
                       f'<rect x="{_bR(x)}" y="{sy}" width="{_bR(sw)}" height="{sh}" rx="8" fill="{LT}" opacity=".7"/>'
                       + _line_icon(_g(sh_, "icon", "image"), x + sw / 2, sy + sh / 2, M, LT, 0.9)
                       + _txt(x + sw / 2, sy + sh + 30, _g(sh_, "label", ""), 16, 500, _BGRAY, "middle")))
    fr = (f'<rect x="{_bR(px + 16)}" y="{sy - 3}" width="{_bR(sw + 4)}" height="{sh + 6}" rx="10" '
          f'fill="none" stroke="{M}" stroke-width="3">')
    if n == 3:
        fr += (f'<animateTransform attributeName="transform" type="translate" '
               f'values="0 0;0 0;{_bR(sw + gg)} 0;{_bR(sw + gg)} 0;{_bR(2 * (sw + gg))} 0;{_bR(2 * (sw + gg))} 0;0 0" '
               f'keyTimes="0;0.25;0.33;0.58;0.66;0.92;1" dur="4.5s" begin="{_float_str(_BTT)}s" repeatCount="indefinite"/>')
    fr += "</rect>"
    o.append(f'<g opacity="0">{_Fz("opacity", [0, 0, 1, 1], [0, _BTT - 0.3, _BTT, _BTT])}{fr}</g>')
    t3 = t2 + 0.4 + n * 0.22
    by, bh = 296, 66
    flow = _iter(_g(c, "flow", []))
    spans = ""
    for i, seg in enumerate(flow):
        if not isinstance(seg, str):
            raise TypeError(f'can only concatenate str (not "{type(seg).__name__}") to str')
        spans += (f'<tspan fill-opacity="0">{_Fz("fill-opacity", [0, 0, 1, 1], [0, t3 + 0.3 + 0.18 * i, t3 + 0.5 + 0.18 * i, _BTT])}'
                  f'{_bEsc((" → " if i else "") + seg)}</tspan>')
    o.append(_fPop((_BX0 + _BX1) / 2, by + bh / 2, t3,
                   f'<rect x="{_BX0}" y="{by}" width="{_BX1 - _BX0}" height="{bh}" rx="16" fill="{M}"/>'
                   f'<circle cx="{_BX0 + 38}" cy="{_float_str(by + bh / 2)}" r="6" fill="#FFFFFF" opacity=".6"/>'
                   f'<text x="{_BX0 + 64}" y="{_float_str(by + bh / 2 + 8)}" font-size="22" font-weight="800" '
                   f'fill="#FFFFFF" xml:space="preserve">{spans}</text>', 1.04))
    return o


def _r_chat(c, th, t):
    M, LT, LN, BD = th["main"], th["light"], th["line"], th["border"]
    o = []
    msgs = _items(c, "messages", 4)
    for m in msgs:
        _sub(m, "from")
    wx, ww = _BX0, 372
    wh = 58 + len(msgs) * 54
    wy = 46 + (270 - wh) / 2
    o.append(_fPop(wx + ww / 2, wy + wh / 2, t,
                   _box(wx, wy, ww, wh, BD)
                   + "".join(f'<circle cx="{wx + 20 + i * 12}" cy="{_float_str(wy + 20)}" r="4" fill="#CBD5E1"/>'
                             for i in range(3))
                   + (_txt(wx + ww / 2, wy + 25, _sub(c, "window_title"), 14, 600, _BGRAY2, "middle")
                      if _truthy(_g(c, "window_title")) else ""), 1.03))
    y = wy + 44
    tt = t + 0.3
    for m in msgs:
        s = _g(m, "text", "")
        bw = _tw(s, 16) + 32
        bh = 38
        if _g(m, "from") == "me":
            bx = wx + ww - 18 - bw
            o.append(_fPop(bx + bw, y + bh / 2, tt,
                           f'<rect x="{_bR(bx)}" y="{_float_str(y)}" width="{_bR(bw)}" height="{bh}" '
                           f'rx="19" fill="{M}"/>' + _txt(bx + 16, y + 25, s, 16, 600, "#FFFFFF"), 1.06))
            tt += 0.32
        else:
            bx = wx + 18
            dots = "".join(f'<circle cx="{bx + 18 + i * 11}" cy="{_float_str(y + bh / 2)}" r="3.5" fill="{LN}"/>'
                           for i in range(3))
            o.append(f'<g opacity="0">{_Fz("opacity", [0, 0, 1, 1, 0, 0], [0, tt, tt + 0.05, tt + 0.3, tt + 0.35, _BTT])}'
                     f'<rect x="{bx}" y="{_float_str(y)}" width="58" height="{bh}" rx="19" fill="{LT}"/>{dots}</g>')
            tt += 0.32
            o.append(_fPop(bx, y + bh / 2, tt,
                           f'<rect x="{_bR(bx)}" y="{_float_str(y)}" width="{_bR(bw)}" height="{bh}" '
                           f'rx="19" fill="{LT}"/>' + _txt(bx + 16, y + 25, s, 16, 600, "#1E293B"), 1.06))
            tt += 0.32
        y += bh + 16
    o.append(_bArrow(wx + ww + 12, wx + ww + 50, 181, LN, tt))
    rx = wx + ww + 62
    rw = _BX1 - rx
    ry, rh = 66, 230
    tt += 0.25
    inner = (_box(rx, ry, rw, rh, M, "#FFFFFF", 16, 3)
             + f'<rect x="{rx + 20}" y="{ry + 20}" width="{rw - 40}" height="96" rx="10" fill="{LT}"/>'
             + _line_icon(_g(c, "result_icon", "doc"), rx + rw / 2, ry + 68, M, LT, 0.85)
             + _txt(rx + rw / 2, ry + 156, _g(c, "result_title", ""), 22, 800, M, "middle")
             + _txt(rx + rw / 2, ry + 188, _g(c, "result_sub", ""), 16, 500, _BGRAY, "middle"))
    o.append(_fPop(rx + rw / 2, ry + rh / 2, tt, inner, 1.08))
    o.append(_caption(368, _g(c, "caption", ""), th, tt + 0.3))
    return o


def _r_stats(c, th, t):
    M, LT, LN, BD = th["main"], th["light"], th["line"], th["border"]
    o = []
    o.append(_mono(_BX0, 56, _g(c, "caption", ""), t))
    items = _items(c, "items", 3)
    n = len(items)
    gap = 40
    cw = (_BX1 - _BX0 - gap * (n - 1)) / n
    y0, h = 72, 196
    for i, it in enumerate(items):
        x = _BX0 + i * (cw + gap)
        ti = t + 0.15 + i * 0.35
        cx = x + cw / 2
        hl = _truthy(_g(it, "hl", i == n - 1))
        inner = (_box(x, y0, cw, h, M if hl else BD, LT if hl else "#FFFFFF", 18, 3 if hl else 2)
                 + _txt(cx, y0 + 48, _g(it, "label", ""), 17, 700, "#475569", "middle")
                 + _txt(cx, y0 + 122, _g(it, "value", ""), 52, 800, M if hl else "#334155", "middle")
                 + _txt(cx, y0 + 170, _g(it, "note", ""), 15, 400, _BGRAY2, "middle"))
        if i:
            o.append(f'<g opacity="0">{_fIn(ti - 0.1)}{_txt(x - gap / 2, y0 + h / 2 + 10, "→", 26, 800, LN, "middle")}</g>')
        o.append(_fPop(cx, y0 + h / 2, ti, inner, 1.08))
        o.append(f'<rect x="{_bR(cx - 50)}" y="{y0 + 136}" width="0" height="5" rx="2.5" '
                 f'fill="{M if hl else LN}">{_Fz("width", [0, 0, 100, 100], [0, ti + 0.3, ti + 0.6, _BTT])}</rect>')
    o.append(_button(300, 64, _g(c, "result", ""), th, t + 0.3 + n * 0.35))
    return o


def _r_checklist(c, th, t):
    M, LT, LN, BD = th["main"], th["light"], th["line"], th["border"]
    o = []
    items = _items(c, "items", 5)
    n = len(items)
    x = _BX0
    w = _BX1 - _BX0
    rowh = 40
    h = 74 + n * rowh + 34
    val = (_BCH - h - 60) / 2
    yv, yv_f = (val, True) if val > 44 else (44, False)

    def ys(v):
        return _bS(v, yv_f)

    o.append(_fPop(x + w / 2, yv + h / 2, t,
                   _box(x, yv, w, h, BD) + _txt(x + 26, yv + 40, _g(c, "title", ""), 21, 800, _BINK2)
                   + f'<rect x="{x + 26}" y="{ys(yv + h - 28)}" width="{w - 52}" height="8" rx="4" fill="{LT}"/>', 1.03))
    tt = t + 0.35
    step = min(0.35, 1.8 / max(n, 1))
    for i, s in enumerate(items):
        ry = yv + 66 + i * rowh
        ti = tt + i * step
        o.append(f'<g opacity="0">{_fIn(ti - 0.15)}<rect x="{x + 26}" y="{ys(ry)}" width="24" height="24" '
                 f'rx="6" fill="#FFFFFF" stroke="{LN}" stroke-width="2"/>'
                 + _txt(x + 64, ry + 19, s, 18, 500, "#334155") + "</g>")
        o.append(_fPop(x + 38, ry + 12, ti + 0.1,
                       f'<rect x="{x + 26}" y="{ys(ry)}" width="24" height="24" rx="6" fill="{M}"/>'
                       f'<path d="M{x + 32} {ys(ry + 12)}l5 5l9 -10" fill="none" stroke="#FFFFFF" stroke-width="3" '
                       'stroke-linecap="round" stroke-linejoin="round"/>', 1.3))
    pw = w - 52
    o.append(f'<rect x="{x + 26}" y="{ys(yv + h - 28)}" width="0" height="8" rx="4" fill="{M}">'
             f'{_Fz("width", [0, 0, pw, pw], [0, tt, tt + n * step + 0.2, _BTT])}</rect>')
    o.append(f'<g opacity="0">{_fIn(tt + n * step)}'
             + _txt(x + w - 26, yv + 40, _g(c, "done", f"{n}/{n} 完成"), 17, 800, M, "end") + "</g>")
    o.append(_caption(yv + h + 44, _g(c, "caption", ""), th, tt + n * step + 0.2))
    return o


def _r_terminal(c, th, t):
    LN = th["line"]
    o = []
    lines = _items(c, "lines", 6)
    x = _BX0
    w = _BX1 - _BX0
    h = 70 + (len(lines) + 1) * 34
    val = (_BCH - h - 60) / 2
    yv, yv_f = (val, True) if val > 44 else (44, False)

    def ys(v):
        return _bS(v, yv_f)

    o.append(_fPop(x + w / 2, yv + h / 2, t,
                   f'<rect x="{x}" y="{ys(yv)}" width="{w}" height="{h}" rx="16" fill="#0F172A"/>'
                   + "".join(f'<circle cx="{x + 22 + i * 16}" cy="{ys(yv + 22)}" r="5" fill="{col}"/>'
                             for i, col in enumerate(("#F87171", "#FBBF24", "#34D399")))
                   + _txt(x + w / 2, yv + 27, _g(c, "title", ""), 14, 600, "#64748B", "middle"), 1.03))
    cols = {"cmd": "#E2E8F0", "out": "#94A3B8", "ok": "#34D399"}
    ly = yv + 70
    tt = t + 0.35
    for ln in lines:
        typ = _g(ln, "type", "out")
        s = _g(ln, "text", "")
        pre = "$ " if typ == "cmd" else "✓ " if typ == "ok" else "  "
        if typ not in cols:
            raise KeyError(typ)
        o.append(f'<g opacity="0">{_fIn(tt, 0.12)}<text x="{x + 28}" y="{ys(ly)}" font-size="17" '
                 f'font-weight="{700 if typ != "out" else 400}" fill="{cols[typ]}" '
                 'font-family="Menlo,Consolas,monospace" xml:space="preserve">'
                 f'<tspan fill="{LN}">{_bEsc(pre)}</tspan>{_bEsc(s)}</text></g>')
        _tw(pre + _pyStr(s), 17)
        ly += 34
        tt += 0.4 if typ == "cmd" else 0.22
    o.append(f'<g opacity="0">{_Fz("opacity", [0, 0, 1, 1], [0, tt, tt + 0.05, _BTT])}'
             f'<text x="{x + 28}" y="{ys(ly)}" font-size="17" font-weight="700" fill="{LN}" '
             'font-family="Menlo,Consolas,monospace">$</text>'
             f'<rect x="{x + 46}" y="{ys(ly - 15)}" width="10" height="19" fill="#E2E8F0">'
             '<animate attributeName="opacity" values="1;1;0;0" keyTimes="0;0.5;0.5;1" dur="1s" '
             f'begin="{_float_str(_BTT)}s" repeatCount="indefinite"/></rect></g>')
    o.append(_caption(yv + h + 44, _g(c, "caption", ""), th, tt + 0.1))
    return o


_BANNER_RIGHTS = {
    "steps": _r_steps, "remix": _r_remix, "pick": _r_pick, "timeline": _r_timeline,
    "converge": _r_converge, "tree": _r_tree, "compare": _r_compare,
    "storyboard": _r_storyboard, "chat": _r_chat, "stats": _r_stats,
    "checklist": _r_checklist, "terminal": _r_terminal,
}


def _banner_svg_head(w, h) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="100%" '
            f'style="display:block;width:100%;height:auto;max-width:{w}px;margin:0 auto;'
            'background:transparent;font-family:\'PingFang SC\',\'Helvetica Neue\',sans-serif">')


def _build_card(spec, themes) -> str:
    card = _mapping(_sub(spec, "card"))
    c = {**card, **_mapping(_g(spec, "brand", {}))}
    th = _tget(themes, _g(c, "theme", "blue"), themes["blue"])
    M, LT, BD, BG = th["main"], th["light"], th["border"], th["bg"]
    o = [_banner_svg_head(_BCW, _BCH)]
    o.append(_fPop(_BCW / 2, _BCH / 2, 0,
                   f'<rect x="10" y="10" width="{_BCW - 20}" height="{_BCH - 20}" rx="34" fill="{BG}" stroke="{BD}" stroke-width="2"/>', 1.01))
    L = 70
    dot = (f'<circle cx="{L + 8}" cy="62" r="6" fill="{M}"/>'
           f'<circle cx="{L + 8}" cy="62" r="6" fill="none" stroke="{M}" stroke-width="2" opacity="0">'
           '<animate attributeName="r" values="6;16;16" keyTimes="0;0.5;1" dur="2.4s" '
           f'begin="{_float_str(_BTT)}s" repeatCount="indefinite"/>'
           '<animate attributeName="opacity" values="0.6;0;0" keyTimes="0;0.5;1" dur="2.4s" '
           f'begin="{_float_str(_BTT)}s" repeatCount="indefinite"/></circle>')
    o.append(f'<g opacity="0">{_fIn(0.25)}{dot}<text x="{L + 28}" y="69" font-size="17" font-weight="700" '
             f'letter-spacing="1" fill="{M}">{_bEsc(_g(c, "eyebrow", ""))}</text></g>')
    o.append(_fRise(0.4, f'<text x="{L}" y="150" font-size="46" font-weight="800" fill="{_BINK2}">'
                         f'{_bEsc(_g(c, "title1", ""))}</text>'))
    o.append(_fRise(0.6, f'<text x="{L}" y="218" font-size="46" font-weight="800" fill="{M}">'
                         f'{_bEsc(_g(c, "title2", ""))}</text>'))
    o.append(f'<rect x="{L}" y="250" width="0" height="7" rx="3.5" fill="{M}">'
             f'{_Fz("width", [0, 0, 90, 90], [0, 0.85, 1.2, _BTT], ["0 0 1 1", _BEASE, "0 0 1 1"])}</rect>')
    o.append(_fRise(1.0, f'<text x="{L}" y="302" font-size="21" fill="{_BGRAY}">{_bEsc(_g(c, "sub", ""))}</text>', 10))
    if _truthy(_g(c, "avatar", True)):
        o.append(_fPop(L + 36, 356, 1.2,
                       f'<circle cx="{L + 36}" cy="358" r="34" fill="{LT}"/>'
                       f'<image x="{L - 6}" y="314" width="84" height="80" preserveAspectRatio="xMidYMax meet" href="GIF_URL_HERE"/>'))
        author = _g(c, "author", "林月半子的AI笔记")
        slogan = _g(c, "slogan", "我是林月半子，教你用AI干掉90%的重复劳动")
        o.append(_fRise(1.35, _pt(L + 86, 352, author, _fit(author, 18, 350), "#1F2937", 700)
                        + _pt(L + 86, 378, slogan, _fit(slogan, 14, 350), "#9CA3AF", 400), 8))
    elif _truthy(_g(spec, "brand")):
        o.append(_fRise(1.35, _pt(L, 352, _g(c, "author", ""), _fit(_g(c, "author", ""), 18, 460), "#1F2937", 700)
                        + _pt(L, 378, _g(c, "slogan", ""), _fit(_g(c, "slogan", ""), 14, 460), "#9CA3AF", 400), 8))
    else:
        o.append(_fRise(1.2, f'<text x="{L}" y="372" font-size="15" letter-spacing="1" fill="#9CA3AF">'
                             f'{_bEsc(_g(c, "foot", _g(c, "eyebrow", "")))}</text>', 8))
    o.append(f'<line x1="548" y1="72" x2="548" y2="72" stroke="{LT}" stroke-width="2">'
             f'{_Fz("y2", [72, 72, 372, 372], [0, 0.5, 1.0, _BTT], ["0 0 1 1", _BEASE, "0 0 1 1"])}</line>')
    right = _g(c, "right", "steps")
    if right not in _BANNER_RIGHTS:
        raise KeyError(right)
    o.extend(_BANNER_RIGHTS[right](c, th, 1.1))
    o.append("</svg>")
    return "".join(o)


def _pw(t, size) -> float:
    acc = 0.0
    for ch in _pyStr(t):
        acc += float(size) if ord(ch) > 255 else size * 0.58
    return acc


def _fit(t, size, maxw):
    return math.trunc(min(size, size * maxw / max(_pw(t, size), 1)))


def _pt(x, y, t, size, fill, w=800, anchor="start", extra="") -> str:
    return (f'<text x="{_bR(x)}" y="{_bR(y)}" font-size="{_jsnum(size)}" font-weight="{_jsnum(w)}" '
            f'fill="{fill}" text-anchor="{anchor}"{extra}>{_bEsc(_pyStr(t))}</text>')


def _pcheck(cx, cy, r=28) -> str:
    return (f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="{_BTEAL}" stroke="#FFFFFF" stroke-width="5"/>'
            f'<path d="M{_bR(cx - r * 0.42)} {cy}l{_bR(r * 0.3)} {_bR(r * 0.32)}l{_bR(r * 0.56)} {_bR(-r * 0.6)}" '
            'fill="none" stroke="#FFFFFF" stroke-width="6" stroke-linecap="round" stroke-linejoin="round"/>')


def _build_poster(spec, neo, themes) -> str:
    c = _g(spec, "poster")
    if not _truthy(c):
        c = _g(spec, "card")
    if not _truthy(c):
        c = {}
    th = _tget(themes, _g(c, "theme", "orange"), themes["orange"])
    M, LT, BG = th["main"], th["light"], th["bg"]
    K = "#111827"
    H_ = 484 if neo else 460
    o = [_banner_svg_head(_BCW, H_)]

    def panel(x, y, w, h, fill, rot=0, rx=30):
        if not neo:
            return [f'<rect x="{_jsnum(x)}" y="{_jsnum(y)}" width="{_jsnum(w)}" height="{_jsnum(h)}" '
                    f'rx="{_jsnum(rx)}" fill="{fill}" stroke="{LT if fill != M else M}" stroke-width="3"/>', ""]
        sh = "".join(f'<rect x="{_jsnum(x + 4 - k * 2)}" y="{_jsnum(y + 8 + k * 4)}" '
                     f'width="{_jsnum(w - 8 + k * 4)}" height="{_jsnum(h)}" rx="{_jsnum(rx)}" '
                     'fill="#0F172A" opacity="0.035"/>' for k in range(3))
        return [sh + f'<rect x="{_jsnum(x)}" y="{_jsnum(y)}" width="{_jsnum(w)}" height="{_jsnum(h)}" '
                     f'rx="{_jsnum(rx)}" fill="{fill}" stroke="{M if fill == M else LT}" stroke-width="2"/>', ""]

    t1 = _g(c, "title1", "")
    t2 = _g(c, "title2", "")
    eb = _g(c, "eyebrow", "")
    if neo:
        big = _pyStr(_g(c, "big", ""))
        o.append("".join(f'<rect x="{14 - k * 3}" y="{14 + k * 5}" width="{_BCW - 28 + k * 6}" '
                         f'height="{H_ - 36}" rx="40" fill="#0F172A" opacity="0.03"/>' for k in range(3))
                 + f'<rect x="8" y="3" width="{_BCW - 16}" height="{H_ - 30}" rx="38" fill="#FFFFFF" '
                   f'stroke="{LT}" stroke-width="2.5"/>')
        if big:
            o.append(f'<text x="{_BCW - 64}" y="{H_ - 62}" font-size="{_fit(big, 300, 500)}" font-weight="900" '
                     f'font-style="italic" fill="{LT}" opacity="0.5" text-anchor="end" '
                     f'font-family="Helvetica,Arial,sans-serif" letter-spacing="-10">{_bEsc(big)}</text>')
        ew = _pw(eb, 30) + _pyLen(eb) * 3 + 86
        o.append(f'<rect x="60" y="48" width="{_bR(ew)}" height="58" rx="29" fill="{K}"/>'
                 f'<circle cx="92" cy="77" r="7" fill="{M}"/>'
                 + _pt(60 + ew / 2 + 12, 88, eb, 30, "#FFFFFF", 800, "middle", ' letter-spacing="3"'))
        fs = min(_fit(t1, 82, 640), _fit(t2, 80, 590))
        o.append(_pt(60, 210, t1, fs, K, 900))
        sw = _pw(t2, fs) + 48
        o.append(_fRise(0.3, f'<rect x="56" y="240" width="{_bR(sw)}" height="108" rx="20" fill="{M}"/>'
                             + _pt(80, 294 + fs * 0.34, t2, fs, "#FFFFFF", 900), 24))
        ay = 404
    else:
        o.append(f'<rect x="2" y="2" width="{_BCW - 4}" height="{H_ - 4}" rx="40" fill="{BG}" '
                 f'stroke="{LT}" stroke-width="3"/>')
        o.append(f'<circle cx="76" cy="76" r="10" fill="{M}">'
                 '<animate attributeName="r" values="10;14;10" dur="2s" repeatCount="indefinite"/></circle>'
                 + _pt(100, 88, eb, 32, M, 800, "start", ' letter-spacing="3"'))
        fs = min(_fit(t1, 80, 640), _fit(t2, 80, 640))
        o.append(_pt(60, 196, t1, fs, K, 900))
        o.append(_fRise(0.3, _pt(60, 300, t2, fs, M, 900), 20))
        o.append(f'<rect x="64" y="330" width="0" height="12" rx="6" fill="{M}">'
                 f'{_Fz("width", [0, 0, 110, 110], [0, 0.6, 1.0, _BTT], ["0 0 1 1", _BEASE, "0 0 1 1"])}</rect>')
        ay = 404
    if _truthy(_g(c, "avatar", True)):
        brand = _g(spec, "brand", {})
        author = _g(brand, "author", _g(c, "author", "林月半子的AI笔记"))
        slogan = _g(brand, "slogan")
        o.append(f'<circle cx="96" cy="{ay}" r="34" fill="{LT}"/>'
                 f'<image x="54" y="{ay - 44}" width="84" height="80" preserveAspectRatio="xMidYMax meet" href="GIF_URL_HERE"/>'
                 + _pt(146, ay if _truthy(slogan) else ay + 12, author, _fit(author, 33, 570), K, 900 if neo else 800))
        if _truthy(slogan):
            o.append(_pt(146, ay + 28, slogan, _fit(slogan, 18, 570), K, 400))
    elif _truthy(_g(spec, "brand")):
        brand = _sub(spec, "brand")
        author = _g(brand, "author", "")
        slogan = _g(brand, "slogan", "")
        if _truthy(author):
            o.append(_pt(60, ay if _truthy(slogan) else ay + 12, author, _fit(author, 33, 640), K, 800))
        if _truthy(slogan):
            o.append(_pt(60, ay + 28, slogan, _fit(slogan, 18, 640), K, 400))
    X, W_ = 770, 400
    top = 60 if neo else 56
    r = _g(c, "right")
    if not _truthy(r):
        r = "tags" if (neo and _truthy(_g(c, "tags"))) else "flow"
    if r == "tags":
        tg0 = _g(c, "tags")
        if not _truthy(tg0):
            tg0 = []
        tags = _head(tg0, 3)
        gap = (H_ - 100 - len(tags) * 88) / max(len(tags) - 1, 1) if len(tags) > 1 else 0
        for i, tg in enumerate(tags):
            y = 50 + i * (88 + min(gap, 40)) + (0 if len(tags) == 3 else 60)
            hl = i == 1 or len(tags) == 1
            x = X + 40
            w = 320
            a, b = panel(x, y, w, 88, M if hl else "#FFFFFF", 0, 44)
            o.append(_fPop(x + w / 2, y + 44, 0.9 + i * 0.3,
                           a + _pt(x + w / 2, y + 44 + 15, tg, _fit(tg, 42, w - 50),
                                   "#FFFFFF" if hl else K, 900, "middle") + b))
    elif r == "stat":
        a, b = panel(X, top + 20, W_, H_ - 2 * top - 50, "#FFFFFF", -2)
        v = _pyStr(_g(c, "value", ""))
        cy = H_ / 2
        o.append(_fPop(X + W_ / 2, cy, 0.9,
                       a + _pt(X + W_ / 2, cy - 78, _g(c, "label", ""), 34, _BGRAY, 700, "middle")
                       + _pt(X + W_ / 2, cy + 44, v, _fit(v, 120, W_ - 60), M, 900, "middle")
                       + _pt(X + W_ / 2, cy + 104, _g(c, "note", ""), 30, K, 700, "middle") + b))
    elif r == "steps":
        s0 = _g(c, "steps")
        if not _truthy(s0):
            s0 = []
        st_ = _head(s0, 3)
        hh = 92
        y0 = (H_ - (len(st_) * hh + (len(st_) - 1) * 18)) / 2 - (6 if neo else 0)
        for i, tx in enumerate(st_):
            y = y0 + i * (hh + 18)
            last = i == len(st_) - 1
            a, b = panel(X, round(y), W_, hh, M if last else "#FFFFFF")
            o.append(_fRise(0.9 + i * 0.35,
                            a + f'<circle cx="{X + 52}" cy="{_bR(y + hh / 2)}" r="26" fill="{"#FFFFFF" if last else M}"/>'
                            + _pt(X + 52, y + hh / 2 + 11, i + 1, 30, M if last else "#FFFFFF", 900, "middle")
                            + _pt(X + 96, y + hh / 2 + 14, tx, _fit(tx, 38, W_ - 120),
                                  "#FFFFFF" if last else K, 800) + b, 22))
    else:
        ask = _g(c, "ask", "")
        res = _g(c, "result", "")
        sb = _g(c, "result_sub", "")
        a, b = panel(X, top, W_, 116, "#FFFFFF", -3)
        tail = "" if neo else (f'<path d="M{X + 330} {top + 114}l40 40l-4 -42z" fill="#FFFFFF" '
                               f'stroke="{LT}" stroke-width="3"/>'
                               f'<rect x="{X + 320}" y="{top + 104}" width="60" height="12" fill="#FFFFFF"/>')
        o.append(_fRise(0.9, a + tail + _pt(X + W_ / 2, top + 74, ask, _fit(ask, 44, W_ - 50), K, 800, "middle") + b, 22))
        o.append(_fRise(1.5, f'<path d="M{_float_str(X + W_ / 2)} {top + 142}v52m-18 -20l18 22l18 -22" '
                             f'fill="none" stroke="{M}" stroke-width="7" stroke-linecap="round" stroke-linejoin="round"/>',
                        -14, 0.3))
        y2 = top + 214
        a, b = panel(X, y2, W_, 140, M, 2)
        ic = (f'<circle cx="{X + 80}" cy="{y2 + 70}" r="42" fill="#FFFFFF"/>'
              f'<path d="M{X + 66} {y2 + 46}v48l40 -24z" fill="{M}"/>')
        o.append(_fRise(1.9, a + ic
                        + _pt(X + 146, y2 + (60 if _truthy(sb) else 86), res, _fit(res, 46, W_ - 170), "#FFFFFF", 900)
                        + (f'<text x="{X + 146}" y="{y2 + 106}" font-size="{_fit(sb, 30, W_ - 170)}" '
                           f'font-weight="500" fill="#FFFFFF" opacity="0.88">{_bEsc(sb)}</text>'
                           if _truthy(sb) else "") + b, 26))
        o.append(_fPop(X + W_ - 8, y2 + 6, 2.4, _pcheck(X + W_ - 8, y2 + 6, 30)))
    o.append("</svg>")
    return "".join(o)


def _banner_themes(theme):
    """engine 配色 → banner 键名；theme=None 用 banner.js 自己的 THEMES。"""
    if theme is None:
        return _BANNER_THEMES
    key = theme if theme in THEMES else "orange"
    return {k: {"main": v["main"], "light": v["tint"], "line": v["line"],
                "border": v["tint"], "bg": v["soft"]} for k, v in THEMES.items()}


def build_banner(spec: dict, themes=None) -> str:
    """移植 banner.js 的 build()：返回裸 SVG（不含 section 包装）。

    themes 缺省时用 banner.js 自己的三套配色。scene 模板路径不在移植范围，
    其他 style 返回空串。
    """
    th = _BANNER_THEMES if themes is None else themes
    st = _g(spec, "style")
    if st in ("poster", "neo"):
        return _build_poster(spec, _sub(spec, "style") == "neo", th)
    if st == "card":
        return _build_card(spec, th)
    return ""


def _banner_w_width(t) -> float:
    acc = 0.0
    for ch in _pyStr(t):
        acc += 1 if ord(ch) > 255 else 0.5
    return acc


def _fmt_g(v) -> str:
    f = float(v)
    return str(int(f)) if f.is_integer() else repr(f)


def check_banner(spec, svgs=None) -> list[str]:
    """移植 banner.js 的 checkSpec：返回问题列表，空 = 通过。"""
    out: list[str] = []
    if not isinstance(spec, dict):
        return ["横幅规格不是对象"]

    def lim(v, n, what):
        if v is not None and _banner_w_width(v) > n:
            out.append(f"{what}「{_pyStr(v)}」有 {_fmt_g(_banner_w_width(v))} 个字宽，超过 {n}，"
                       "会挤出去或重叠，缩短一点")

    def cnt(v, lo, hi, what):
        if v is not None:
            length = _pyLen(v)
            if not lo <= length <= hi:
                out.append(f"{what} 有 {length} 个，要在 {lo}–{hi} 个之间，多出来的会画不下")

    def or_(v, d):
        return v if _truthy(v) else d

    st = _g(spec, "style")
    if st in ("poster", "neo"):
        c = _g(spec, "poster", {})
        r = _g(c, "right")
        if not _truthy(r):
            r = "tags" if (_sub(spec, "style") == "neo" and _truthy(_g(c, "tags"))) else "flow"
        lim(_g(c, "eyebrow"), 10, "眉标 eyebrow")
        lim(_g(c, "title1"), 8, "标题第一行")
        lim(_g(c, "title2"), 8, "标题第二行")
        lim(_g(c, "big"), 2, "底纹大字 big")
        if r not in ("flow", "tags", "stat", "steps"):
            out.append(f"没有「{_pyStr(r)}」这个右栏，可选：flow、tags、stat、steps")
        if r == "flow":
            for k, n in (("ask", 9), ("result", 6), ("result_sub", 9)):
                lim(_g(c, k), n, k)
            if not _truthy(_g(c, "ask")) or not _truthy(_g(c, "result")):
                out.append("flow 右栏要写 ask（你说的一句话）和 result（得到的结果）")
        if r == "tags":
            cnt(or_(_g(c, "tags"), []), 2, 3, "tags")
            for x in _iter(or_(_g(c, "tags"), [])):
                lim(x, 6, "贴纸")
        if r == "steps":
            cnt(or_(_g(c, "steps"), []), 2, 3, "steps")
            for x in _iter(or_(_g(c, "steps"), [])):
                lim(x, 8, "步骤")
        if r == "stat":
            lim(_g(c, "value"), 5, "大数字 value")
            lim(_g(c, "label"), 10, "label")
            lim(_g(c, "note"), 12, "note")
    elif st == "card":
        c = _g(spec, "card", {})
        r = _g(c, "right", "steps")
        lim(_g(c, "eyebrow"), 15, "眉标 eyebrow")
        lim(_g(c, "title1"), 9, "标题第一行")
        lim(_g(c, "title2"), 9, "标题第二行")
        lim(_g(c, "sub"), 20, "副标题")
        if r not in _BANNER_RIGHTS:
            out.append(f"没有「{_pyStr(r)}」这个右栏版式，可选：" + "、".join(_BRIGHTS))
        for k in ("caption", "result", "result_title", "result_sub", "head",
                  "box_title", "box_sub", "pick_label", "grid_label"):
            lim(_g(c, k), 18, k)
        for k, (lo, hi) in {"steps": (2, 4), "items": (2, 5), "cards": (2, 4),
                            "shots": (3, 3), "messages": (1, 4), "lines": (1, 6)}.items():
            if k in c:
                cnt(c[k], lo, hi, k)
        allv = list(or_(_g(c, "steps"), [])) + list(or_(_g(c, "cards"), []))
        allv += list(or_(_g(c, "shots"), []))
        allv += [x for x in _iter(or_(_g(c, "items"), [])) if isinstance(x, dict)]
        for it in allv:
            lim(_g(it, "label"), 8, "卡片标题")
            lim(_g(it, "title"), 8, "卡片标题")
            lim(_g(it, "desc"), 14, "卡片描述")
            lim(_g(it, "sub"), 14, "卡片描述")
        for m in _iter(or_(_g(c, "messages"), [])):
            lim(_g(m, "text"), 12, "聊天气泡")
        for ln in _iter(or_(_g(c, "lines"), [])):
            lim(_g(ln, "text"), 17, "终端一行")
        if r == "checklist":
            for x in _iter(or_(_g(c, "items"), [])):
                lim(x, 14, "清单一项")
    else:
        sc = _g(spec, "scene", {})
        lay = _g(sc, "layout", "")
        if lay not in _BLAYOUTS:
            out.append(f"没有「{_pyStr(lay)}」这个场景模板，可选：" + "、".join(_BLAYOUTS))
            return out
        lim(_g(spec, "caption"), 14, "右上角 caption")
        caps = {
            "pipeline": ("items", 1, 5 if _g(sc, "tile") == "card" or not _truthy(_g(sc, "source")) else 4),
            "path": ("nodes", 3, 5), "fly": ("items", 0, 4),
            "before_after": ("items", 1, 4), "hub": ("nodes", 2, 6), "stack": ("blocks", 2, 6),
            "dialog": ("items", 0, 3), "calendar": ("days", 3, 7), "funnel": ("items", 1, 3),
            "chart": ("bars", 2, 6),
        }
        if lay in caps and caps[lay][0] in sc:
            cnt(sc[caps[lay][0]], caps[lay][1], caps[lay][2], caps[lay][0])
        if lay == "pipeline" and _g(sc, "tile") == "film":
            cnt(or_(_g(sc, "items"), []), 1, 4, "胶片帧 items")
        if lay == "before_after":
            cnt(or_(_g(sc, "before"), []), 1, 7, "before")
        if lay == "funnel":
            cnt(or_(_g(sc, "inputs"), []), 1, 10, "inputs")
            lim(_g(sc, "filter_label"), 5, "filter_label")
        if lay == "dialog":
            cnt(or_(_g(sc, "messages"), []), 1, 4, "messages")
            for m in _iter(or_(_g(sc, "messages"), [])):
                lim(_g(m, "text"), 12, "聊天气泡")
        blocks = or_(_g(sc, "blocks"), [])

        def walk(obj):
            if isinstance(obj, dict):
                if "label" in obj:
                    inb = lay == "stack" and any(isinstance(x, dict) and x == obj for x in blocks)
                    lim(obj["label"], 5 if inb else 8, "标签")
                for v in obj.values():
                    walk(v)
            elif isinstance(obj, list):
                for v in obj:
                    walk(v)

        walk(sc)
        for k in ("before_label", "after_label", "input_label", "start_label", "left_label", "device_label"):
            lim(_g(sc, k), 10, k)
    for sv in svgs or ():
        if (_BWS_ATTR.search(sv) or "<style" in sv or "<script" in sv
                or "Gradient" in sv or "<use" in sv):
            out.append("生成的 SVG 里有 id/class/style/script/渐变/use，公众号会吞掉")
            break
    return out


def render_banner(spec: dict, *, theme="orange") -> dict:
    """渲染开头横幅：返回 section 包装后的 HTML 与自查提示。

    入口语义与参考 layout.js 的 renderPage 一致：theme 取自正文配色，映射成
    banner 键名后交给移植后的 build；theme=None 时用 banner.js 自己的 THEMES。
    """
    if not isinstance(spec, dict):
        return {"html": "", "warnings": ["横幅规格不是对象"]}
    themes = _banner_themes(theme)
    # 参考 renderPage 在按主题渲染时会写 body.theme；这里对没写主题的字典 spec 做同样处理，
    # 保持「横幅跟着正文配色走」的产品行为。
    if theme is not None and _g(spec, "style") in ("poster", "neo", "card"):
        body_key = "card" if _g(spec, "style") == "card" else "poster"
        body = _g(spec, body_key)
        if isinstance(body, dict) and "theme" not in body:
            spec = {**spec, body_key: {**body, "theme": theme}}
    try:
        html = build_banner(spec, themes)
        warnings = check_banner(spec, [html] if html else None)
    except Exception as exc:  # noqa: BLE001 - 规格错误要转成可读提示，不能让排版中断
        return {"html": "", "warnings": [f"横幅规格有误：{exc}"]}
    if not html:
        return {"html": "", "warnings": warnings}
    return {"html": f'<section style="margin:0 0 24px">{html}</section>', "warnings": warnings}


# ---------------------------------------------------------------------------
# 完整单文件 HTML 预览页（配色切换 + 复制 + 自查报告）
# ---------------------------------------------------------------------------


def render_studio_document(body: str, options: dict | None = None, image_urls: list | None = None) -> dict:
    """独立工作台正文入口，不依赖旧版 token 排版器。"""
    from .wechat_layout_studio import render_studio_document as render

    return render(body, options, image_urls)


def render_preview_page(article_html: str, *, title="公众号排版预览", style="std", theme="orange", report="") -> str:
    """生成一个自带风格的完整 HTML 页，用于 iframe srcdoc 预览。

    注意：这个页面带 <style>/<script>，只用于本地预览，不进入公众号正文。
    """
    palette = OrderedDict(
        (k, {"main": v["main"], "soft": v["soft"], "tint": v["tint"], "line": v["line"], "mark": v["mark"]})
        for k, v in THEMES.items()
    )
    data = {
        "style": style if style in STYLES else "std",
        "theme": theme if theme in THEMES else "orange",
        "themes": THEMES,
        "palette": palette,
    }
    import json

    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    esc_title = _esc(title)
    esc_report = _esc(report)
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc_title}</title>
<style>
:root{{--main:{THEMES[theme]['main']};--soft:{THEMES[theme]['soft']};--tint:{THEMES[theme]['tint']};--line:{THEMES[theme]['line']};}}
*{{box-sizing:border-box}}body{{margin:0;background:#f4f4f2;font-family:{SFONT};}}
.bar{{position:sticky;top:0;z-index:2;display:flex;flex-wrap:wrap;gap:10px;align-items:center;padding:10px 16px;background:#fff;border-bottom:1px solid #e5e5e5;}}
.bar h1{{font-size:15px;margin:0 12px 0 0;}} .sw{{width:26px;height:26px;border-radius:50%;border:2px solid #fff;box-shadow:0 0 0 1px #ddd;cursor:pointer;}}
.sw.on{{box-shadow:0 0 0 2px var(--main);}} .btn{{padding:7px 14px;border:0;border-radius:8px;background:var(--main);color:#fff;cursor:pointer;font-size:13px;}}
.sheet{{max-width:677px;margin:24px auto 80px;padding:32px 24px;background:#fff;box-shadow:0 6px 30px rgba(0,0,0,.06);}}
.report{{max-width:677px;margin:0 auto;padding:12px 16px;color:#666;font-size:12px;white-space:pre-wrap;}}
</style></head><body>
<div class="bar"><h1>{esc_title}</h1><div id="swatches"></div><span style="flex:1"></span><button class="btn" id="copy">复制全文</button></div>
<div class="sheet" id="sheet">{article_html}</div>
<pre class="report" id="report">{esc_report}</pre>
<script>
var D={payload};
var sheet=document.getElementById('sheet');
var box=document.getElementById('swatches');
Object.keys(D.palette).forEach(function(k){{var b=document.createElement('button');b.className='sw'+(k===D.theme?' on':'');b.style.background=D.palette[k].main;b.title=D.themes[k].name;b.onclick=function(){{
  var p=D.palette[k];['main','soft','tint','line'].forEach(function(c){{document.documentElement.style.setProperty('--'+c,p[c]);}});
  sheet.style.background=p.soft;
  [].forEach.call(box.children,function(x){{x.classList.remove('on')}});b.classList.add('on');
}};box.appendChild(b);}});
document.getElementById('copy').onclick=function(){{
  var range=document.createRange();range.selectNodeContents(sheet);var sel=window.getSelection();sel.removeAllRanges();sel.addRange(range);
  try{{document.execCommand('copy');}}catch(e){{}}sel.removeAllRanges();
}};
</script></body></html>"""
