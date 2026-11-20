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


class CardFormatError(ValueError):
    """卡片内容写法不符合约定，渲染器回退为普通代码框。"""


# ---------------------------------------------------------------------------
# 8 种动画卡
# ---------------------------------------------------------------------------


def _svg_table(title, lines, T):
    rows, quote = _sSplit(lines)
    if not rows:
        raise CardFormatError("表格至少要有表头一行")
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
    if not rows:
        raise CardFormatError("柱状图至少要有数据行")
    vals = []
    for r in rows:
        digits = re.sub(r"[^\d.]", "", r[0][1] if len(r[0]) > 1 else "0") or "0"
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
    if not rows:
        raise CardFormatError("步骤卡至少要有一步")
    o = []
    X0, WID = 40.0, SW - 80
    y, t = 108.0, 0.5
    for step_no, (cells, _) in enumerate(rows, 1):
        tl = _swrap(cells[0], 24, WID - 170)
        sl = _swrap(cells[1] if len(cells) > 1 else "", 19, WID - 170)
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
    if not rows:
        raise CardFormatError("时间线至少要有节点")
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
    if not rows:
        raise CardFormatError("拆解卡至少要有条目")
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
    if not rows or len(rows[0][0]) < 2:
        raise CardFormatError("对比卡首行需要两个表头，用 | 分隔")
    lt, rt = rows[0][0][:2]
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
    old = tracks[0] if tracks else None
    nw = tracks[1] if len(tracks) >= 2 else (tracks[0] if tracks else None)
    if not nw:
        raise CardFormatError("概念卡需要一条「现在」轨道（单行多列）")
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
    if n == 0:
        raise CardFormatError("手机演示至少要有一幕（单行多列）")
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
    if kind not in _CARD_RENDERERS:
        return {"html": "", "warnings": [f"没有「{kind}」这种动画卡"]}
    T = resolve_style_theme(style, theme)
    title = title or SVG_CARD_INFO[kind]["lead"]
    try:
        html = _CARD_RENDERERS[kind](title, lines or [], T)
    except (CardFormatError, ValueError, IndexError, ZeroDivisionError, TypeError) as exc:
        warnings.append(f"{kind}「{title}」的内容格式不对（{type(exc).__name__}），先按普通代码框显示：{exc}")
        body = _esc("\n".join(str(x) for x in (lines or [])))
        html = (
            f'<section style="margin:28px 0;"><pre style="font-size:13px;line-height:1.7;white-space:pre-wrap;'
            f'overflow-wrap:anywhere;background:{T["soft"]};border-radius:{T["r"]}px;padding:12px 14px;">{body}</pre></section>'
        )
    return {"html": html, "warnings": warnings}


# ---------------------------------------------------------------------------
# 开头横幅 banner（移植 poster / card 两种骨架的核心结构）
# ---------------------------------------------------------------------------

_BANNER_THEMES = {
    "blue": {"main": "#2563EB", "light": "#DBEAFE", "line": "#93C5FD", "border": "#BFDBFE", "bg": "#F8FAFC"},
    "orange": {"main": "#C9643E", "light": "#F6E1D6", "line": "#E3A688", "border": "#EBCBB9", "bg": "#FBF8F5"},
    "teal": {"main": "#12A37F", "light": "#D5F2E8", "line": "#7FD1B6", "border": "#BCE7D8", "bg": "#F6FBF9"},
}


def check_banner(spec: dict) -> list[str]:
    """横幅文案规格自检：超字数/缺字段会在公众号里挤出去。"""
    out: list[str] = []
    if not isinstance(spec, dict):
        return ["横幅规格不是对象"]
    st = spec.get("style", "poster")
    if st in ("poster", "neo"):
        c = spec.get("poster") or {}
        for key, limit, label in (
            ("eyebrow", 10, "眉标"),
            ("title1", 8, "标题第一行"),
            ("title2", 8, "标题第二行"),
            ("big", 2, "底纹大字"),
        ):
            v = str(c.get(key) or "")
            if len(v) > limit:
                out.append(f"{label}「{v}」有 {len(v)} 个字，超过 {limit}，会挤出去或重叠，缩短一点")
    elif st == "card":
        c = spec.get("card") or {}
        for key, limit, label in (("eyebrow", 10, "眉标"), ("title", 12, "标题"), ("subtitle", 16, "副标题")):
            v = str(c.get(key) or "")
            if len(v) > limit:
                out.append(f"{label}「{v}」有 {len(v)} 个字，超过 {limit}")
    else:
        out.append(f"没有「{st}」这个横幅样式，可选 poster、neo、card")
    return out


def render_banner(spec: dict, *, theme="orange") -> dict:
    """渲染开头横幅 SVG，输出微信安全的内联属性片段。"""
    if not isinstance(spec, dict):
        return {"html": "", "warnings": ["横幅规格不是对象"]}
    warnings = check_banner(spec)
    palette = THEMES.get(theme, THEMES["orange"])
    T = {"main": palette["main"], "light": palette["tint"], "line": palette["line"], "border": palette["tint"], "bg": palette["soft"]}
    st = spec.get("style", "poster")
    W, H = 750.0, 360.0
    head = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {_sR(W)} {_sR(H)}" width="100%" '
        f'style="display:block;max-width:100%;height:auto;margin:0 auto;font-family:{SFONT}">'
    )
    bg = f'<rect x="0" y="0" width="{_sR(W)}" height="{_sR(H)}" rx="20" fill="{T["bg"]}"/>'
    if st in ("poster", "neo"):
        c = spec.get("poster") or {}
        eyebrow, title1, title2 = str(c.get("eyebrow") or ""), str(c.get("title1") or ""), str(c.get("title2") or "")
        big = str(c.get("big") or "")
        body = (
            f'<rect x="0" y="0" width="14" height="{_sR(H)}" fill="{T["main"]}"/>'
            + (f'<text x="52" y="86" font-size="20" font-weight="700" letter-spacing="3" fill="{T["line"]}">{_esc(eyebrow)}</text>' if eyebrow else "")
            + (f'<text x="52" y="176" font-size="56" font-weight="800" fill="{INK}">{_esc(title1)}</text>' if title1 else "")
            + (f'<text x="52" y="248" font-size="56" font-weight="800" fill="{T["main"]}">{_esc(title2)}</text>' if title2 else "")
        )
        if big:
            body += f'<text x="698" y="316" font-size="150" font-weight="800" text-anchor="end" fill="{T["light"]}">{_esc(big)}</text>'
    else:
        c = spec.get("card") or {}
        body = (
            f'<rect x="40" y="48" width="{_sR(W - 80)}" height="{_sR(H - 96)}" rx="20" fill="#FFFFFF" stroke="{T["border"]}" stroke-width="2"/>'
            + f'<rect x="40" y="48" width="{_sR(W - 80)}" height="8" rx="4" fill="{T["main"]}"/>'
            + (f'<text x="76" y="132" font-size="20" font-weight="700" letter-spacing="2" fill="{T["main"]}">{_esc(str(c.get("eyebrow") or ""))}</text>' if c.get("eyebrow") else "")
            + f'<text x="76" y="212" font-size="46" font-weight="800" fill="{INK}">{_esc(str(c.get("title") or ""))}</text>'
            + (f'<text x="76" y="272" font-size="24" fill="{GRAY}">{_esc(str(c.get("subtitle") or ""))}</text>' if c.get("subtitle") else "")
        )
    return {"html": f'<section style="margin:0 0 24px;">{head}{bg}{body}</svg></section>', "warnings": warnings}


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
