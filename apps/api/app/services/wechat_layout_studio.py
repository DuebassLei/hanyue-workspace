"""dsh-wechat-studio vendor/layout/layout.js 的 Python 逐字节对齐移植。

目标：对参考原生 Markdown 输入，输出与参考 renderPage 中 data.styles（paint 后）逐字节一致的正文 HTML。
产品兼容能力保留在边界：{{imageN}} 占位、:::svg-* 旧围栏、image_urls 注入与 URL 安全校验、
banner 横幅（engine.render_banner）、SVG 动画卡（engine.render_svg_card）。

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
"""
from __future__ import annotations

import html
import re
from urllib.parse import urlsplit

from . import wechat_layout_engine as engine
from . import wechat_svg_cards as cards

# 参考常量（layout.js）
MONO = "Menlo,Consolas,'SF Mono',monospace"
SERIF = "Georgia,'Songti SC','STSong','Source Han Serif SC','Noto Serif SC',serif"
SERIF_CN = "'Songti SC','STSong','Source Han Serif SC','Noto Serif SC','Noto Serif CJK SC',Georgia,serif"
CN_BIG = '壹贰叁肆伍陆柒捌玖拾'
INK, BODY, GRAY, MUTE, BORDER = '#111827', '#3F3F46', '#6B7280', '#94A3B8', '#E5E7EB'
PROMPT_INK = '#52607A'
DK_BG, DK_PANEL, DK_BORDER = '#0F1218', '#151A21', '#2A313C'
NEW_ST = ('note', 'zen', 'bento', 'dark')
CN_NUM = '一二三四五六七八九十'
CJK = '[\\u2e80-\\u9fff\\uff00-\\uffef\\u3000-\\u303f]'
IMAGE_ALT = r'((?:\\.|[^\]\\])*)'
LI_RE = re.compile(r'^(\s*)(?:([-*+])|(\d+)[.)、])\s+(.*)$')
CAP_END = '。！？!?：:；;，,、…'
VIDEO_RE = re.compile(r'^\[([^\]]+\.(?:mp4|mov|m4v|webm|avi|gif))\]$', re.IGNORECASE)
ESC_RE = re.compile(r'\\([\\`*_{}\[\]()#+\-.!>~=|])')
AUTHOR = 'AI小钥的笔记'
SLOGAN = '我是AI小钥，教你学会AI,用好AI~'
CTA = '点个关注，下一篇见'
CALLOUT_LABELS = {'TIP': '小技巧', 'NOTE': '说明', 'WARN': '注意', 'TLDR': '省流版'}


def esc(value):
    """py.js esc：html.escape(s, quote=False)，只转 & < >。"""
    return html.escape(str(value), quote=False)


def pad(n, width=2):
    return str(n).rjust(width, '0')


def r2(v):
    return f'{float(v):.2f}'.rstrip('0').rstrip('.')


def px(v):
    m = re.match(r'(-?[\d.]+)px', str(v).strip())
    return float(m[1]) if m else None


def is_plain_alt(alt):
    return str(alt).lower() in ('image', 'img', '图片')


def image_alt(s):
    return re.sub(r'\\([\\\[\]])', lambda m: m[1], s)


def quote_svg(color, opacity=1, w=92, margin='0'):
    h = round(w * 0.72)
    def mark(dx):
        return (f'<circle cx="{18 + dx}" cy="47" r="15" fill="{color}"/>'
                f'<path d="M{3 + dx} 47C{3 + dx} 26 {13 + dx} 11 {32 + dx} 3L{36 + dx} 10C{24 + dx} 17 {19 + dx} 27 {20 + dx} 37Z" fill="{color}"/>')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 80 64" width="{w}" height="{h}" '
            f'style="display:block;margin:{margin};overflow:visible;"><g opacity="{opacity}">{mark(0)}{mark(40)}</g></svg>')


def deco_svg(style):
    """layout.js decoSvg：把空 span 占位转成公众号安全的 SVG 色块；不适用时返回 None。"""
    d = {}
    for part in style.split(';'):
        if ':' in part:
            k, v = part.split(':', 1)
            d[k.strip()] = v.strip()
    w = px(d.get('width', ''))
    if w is None:
        return None
    keep = [f'{k}:{d[k]}' for k in ('margin', 'margin-left', 'margin-right', 'margin-top', 'margin-bottom', 'vertical-align', 'flex-shrink') if k in d]
    h = px(d.get('height', ''))
    if 'border-top' in d and h is None:
        parts = d['border-top'].split()
        if len(parts) != 3:
            return None
        bw, bs, bc = parts
        t = px(bw)
        if t is None:
            return None
        h = max(2, round(t + 0.5))
        dash = ' stroke-dasharray="4 3"' if bs == 'dashed' else ''
        inner = f'<line x1="0" y1="{r2(h / 2)}" x2="{r2(w)}" y2="{r2(h / 2)}" stroke="{bc}" stroke-width="{r2(t)}"{dash}/>'
    else:
        if h is None:
            return None
        br = d.get('border-radius', '0')
        rx = min(w, h) / 2 if '%' in br else min(px(br) or 0, min(w, h) / 2)
        bg = d.get('background', '')
        stroke, sw = '', 0
        if 'border' in d:
            parts = d['border'].split()
            if len(parts) != 3:
                return None
            sw = px(parts[0])
            stroke = f' stroke="{parts[2]}" stroke-width="{r2(sw)}"'
        gm = re.match(r'linear-gradient\(90deg,\s*(#[0-9A-Fa-f]{6}),\s*(#[0-9A-Fa-f]{6})\)', bg)
        if gm:
            c1, c2 = gm[1], gm[2]
            a, b = rx, w - rx
            n = max(8, int((b - a) / 1.5))
            sw_ = (b - a) / n
            inner = (f'<rect x="0" y="0" width="{r2(w)}" height="{r2(h)}" rx="{r2(rx)}" fill="{c2}"/>'
                     f'<rect x="0" y="0" width="{r2(2 * rx)}" height="{r2(h)}" rx="{r2(rx)}" fill="{c1}"/>')
            for i in range(n):
                inner += (f'<rect x="{r2(a + i * sw_)}" y="0" width="{r2(sw_)}" height="{r2(h)}" fill="{c1}" '
                          f'fill-opacity="{1 - (i + 0.5) / n:.3f}" shape-rendering="crispEdges"/>')
        else:
            if (not bg and not stroke) or '(' in bg:
                return None
            o = sw / 2
            inner = (f'<rect x="{r2(o)}" y="{r2(o)}" width="{r2(w - sw)}" height="{r2(h - sw)}" '
                     f'rx="{r2(max(0, rx - o))}" fill="{bg or "none"}"{stroke}/>')
        tr = re.match(r'rotate\((-?[\d.]+)deg\)', d.get('transform', ''))
        if tr:
            inner = f'<g transform="rotate({tr[1]} {r2(w / 2)} {r2(h / 2)})">{inner}</g>'
        if 'opacity' in d:
            inner = f'<g opacity="{d["opacity"]}">{inner}</g>'
    disp = 'display:block' if d.get('display') == 'block' else 'display:inline-block'
    st = ';'.join([disp, 'overflow:visible', *keep])
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {r2(w)} {r2(h)}" width="{r2(w)}" height="{r2(h)}" style="{st};">{inner}</svg>'


def fix_empty(h):
    """layout.js fixEmpty：没有文字的空 span 换成 SVG（公众号会删空元素）。"""
    return re.sub(r'<span style="([^"]*)"></span>', lambda m: deco_svg(m[1]) or m[0], h)


def dark_fix(h):
    parts = re.split(r'(<svg[\s\S]*?</svg>)', h)
    rep = [
        ('color:#111827', 'color:#FFFFFF'), ('color:#3F3F46', 'color:#C5CCD6'), ('color:#1F2937', 'color:#E6EAF0'),
        ('color:#374151', 'color:#E6EAF0'), ('color:#6B7280', 'color:#8B95A3'), ('color:#94A3B8', 'color:#6B7685'),
        ('background:#FFFFFF', f'background:{DK_PANEL}'), ('background:#F8FAFC', f'background:{DK_PANEL}'),
        ('border:1px solid #E5E7EB', f'border:1px solid {DK_BORDER}'),
        ('border-top:1px solid #F1F2F4', f'border-top:1px solid {DK_BORDER}'),
        ('border-bottom:1px solid #E5E7EB', f'border-bottom:1px solid {DK_BORDER}'),
        ('color:#52607A', 'color:#9FB0C3'),
    ]
    for k in range(0, len(parts), 2):
        for a, b in rep:
            parts[k] = parts[k].replace(a, b)
    return ''.join(parts)


def join_lines(parts):
    s = parts[0]
    for p in parts[1:]:
        s += ('' if re.search(CJK + '$', s) or re.match(CJK, p) else ' ') + p
    return s


def clean_md(md):
    """layout.js cleanMd：去 BOM、统一换行、去飞书转义反斜杠（围栏与行内代码内不动）。"""
    md = str(md or '').lstrip('﻿').replace('\r\n', '\n').replace('\r', '\n')
    out = []
    fence = False
    for ln in md.split('\n'):
        if ln.strip().startswith('```'):
            fence = not fence
            out.append(ln)
            continue
        if fence:
            out.append(ln)
            continue
        out.append(''.join(seg if k % 2 else ESC_RE.sub(lambda m: m[1], seg) for k, seg in enumerate(re.split('(`[^`]*`)', ln))))
    return '\n'.join(out)


def front_matter(text):
    meta = {}
    if text.startswith('---\n'):
        end = text.find('\n---', 4)
        if end > 0:
            for line in text[4:end].splitlines():
                if ':' in line:
                    k, v = line.split(':', 1)
                    meta[k.strip().lower()] = v.strip().strip().strip('"').strip("'")
            text = text[end + 4:].lstrip('\n')
    return meta, text


class StudioRenderer:
    """参考 render() 的忠实移植；产品扩展只放在边界。"""

    def __init__(self, options, image_urls):
        self.options = options
        self.warnings = []
        self.notes = []
        self.image_urls = image_urls
        self.consumed_images = set()
        self.stats = dict(title='', characters=0, paragraphs=0, headings=0, images=0, tables=0, lists=0, quotes=0, codeBlocks=0, svgCards=0, links=0, captions=0)

    # ---------------- 产品边界 ----------------
    def warn(self, message):
        if message not in self.warnings:
            self.warnings.append(message)

    def url(self, value, image=False):
        """产品安全边界：校验 URL；仅用于 {{imageN}}、image_urls 与头像，不影响参考原生 markdown。"""
        value = str(value or '').strip()
        if image and re.fullmatch(r'asset:\/\/[\w./%-]+', value):
            return value
        try:
            parsed = urlsplit(value)
            valid = parsed.scheme.lower() in ('http', 'https') and bool(parsed.hostname) and not parsed.username and not parsed.password
        except ValueError:
            valid = False
        if not valid or re.search(r'[\x00-\x20<>"\x7f]', value):
            self.warn('已移除不安全或无效的图片/链接地址')
            return ''
        return value

    def placeholder_image(self, number, caption='', inline=False, T=None):
        index = int(number) - 1
        self.consumed_images.add(index)
        if index < 0 or index >= len(self.image_urls):
            self.warn(f'图片占位 image{number} 缺少对应图片地址')
            return f'<span style="color:{MUTE};">{esc(caption or "图片待补充")}</span>'
        value = self.image_urls[index]
        url = value.get('url', '') if isinstance(value, dict) else value
        url = self.url(url, image=True)
        if not url:
            self.warn(f'图片占位 image{number} 缺少对应图片地址')
            return f'<span style="color:{MUTE};">{esc(caption or "图片待补充")}</span>'
        return self.image(url, caption.strip(), T or self._T, inline=inline)

    # ---------------- inline ----------------
    def inline(self, source, T):
        keep = []
        def stash(value):
            keep.append(value)
            return f'\x00{len(keep) - 1}\x00'
        st = T['st']
        s = str(source).replace('\x00', '')
        # 产品兼容：{{imageN}} 占位（参考实现没有该语法）
        s = re.sub(r'\{\{image(\d+)(?:\|([^}]*))?\}\}', lambda m: stash(self.placeholder_image(m[1], m[2] or '', inline=True)), s, flags=re.IGNORECASE)
        s = re.sub(r'`([^`]+)`', lambda m: stash(f'<code style="font-family:{MONO};font-size:0.9em;background:{T["soft"]};color:{T["main"]};padding:2px 5px;border-radius:4px;">{esc(m[1])}</code>'), s)
        def inline_image(m):
            src = self.safe_src(m[2])
            if not src:
                return stash(f'<span style="color:{GRAY};">{esc(m[1] or "图片地址无效")}</span>')
            return stash(f'<img src="{src}" style="max-width:100%;vertical-align:middle;"/>')
        s = re.sub(r'!\[' + IMAGE_ALT + r'\]\(<?([^)\s>]+)>?(?:\s+"[^"]*")?\)', inline_image, s)
        def link(m):
            text, url = esc(m[1]), self.safe_src(m[2])
            if url and (urlsplit(url).hostname or '').lower() == 'mp.weixin.qq.com':
                return stash(f'<a href="{url}" style="color:{T["main"]};text-decoration:none;border-bottom:1px solid {T["line"]};">{text}</a>')
            self.notes.append((m[1], url))
            return stash(f'<span style="color:{T["main"]};border-bottom:1px solid {T["line"]};">{text}</span>'
                         f'<sup style="color:{T["main"]};font-size:11px;">[{len(self.notes)}]</sup>')
        s = re.sub(r'\[([^\]]+)\]\(<?([^)\s>]+)>?\)', link, s)
        s = esc(s)
        s = re.sub(r'\*\*(?=\S)(.+?)(?<=\S)\*\*', lambda m: f'<strong style="{self.strong_css(T)}">{m[1]}</strong>', s)
        chip = INK if st == 'min' else T['main']
        chip_css = self.x_chip(T) if st in NEW_ST else f'background:{chip};color:#FFFFFF;font-weight:700;padding:2px 6px;border-radius:3px;'
        s = re.sub(r'==(?=\S)(.+?)(?<=\S)==', lambda m: f'<span style="{chip_css}">{m[1]}</span>', s)
        s = re.sub(r'\+\+(?=\S)(.+?)(?<=\S)\+\+|&lt;u&gt;(.+?)&lt;/u&gt;', lambda m: f'<span style="{self.underline_css(T)}">{m[1] or m[2]}</span>', s)
        s = re.sub(r'~~(?=\S)(.+?)(?<=\S)~~', lambda m: f'<span style="text-decoration:line-through;color:#9CA3AF;">{m[1]}</span>', s)
        s = re.sub(r'(?<![*\w])\*(?![\s*])(.+?)(?<![\s*])\*(?![*\w])', lambda m: f'<em style="font-style:normal;color:{T["main"]};">{m[1]}</em>', s)
        plain = re.sub(r'<[^>]+>|\x00\d+\x00', '', s)
        for mk in ('**', '=='):
            if mk in plain:
                self.warn(f'有一处 {mk} 没配成对，已按原文显示')
        return re.sub(r'\x00(\d+)\x00', lambda m: keep[int(m[1])], s)

    def safe_src(self, url):
        """产品安全边界：markdown 里的链接/图片地址做安全过滤（非法地址移除并告警）。"""
        return self.url(url)

    def strong_css(self, T):
        st = T['st']
        if st in NEW_ST:
            if st == 'note':
                return f'font-weight:700;color:#1F1F1F;background:{T["mark"]};padding:2px 4px;border-radius:3px;'
            if st == 'zen':
                return f'font-weight:700;color:#1F1F1F;background:linear-gradient(transparent 66%,{T["tint"]} 66%);padding:0 2px;'
            if st == 'bento':
                return f'font-weight:700;color:#1D1D1F;background:{T["tint"]};padding:2px 7px;border-radius:7px;'
            return 'font-weight:700;color:#FFFFFF;background:#252C37;padding:2px 6px;border-radius:5px;'
        if st == 'neo':
            return f'font-weight:800;color:{INK};background:linear-gradient(100deg,{T["mark"]},{T["tint"]});padding:2px 6px;border-radius:4px;'
        if st == 'mag':
            return f'font-weight:700;color:{T["main"]};'
        if st == 'min':
            return 'font-weight:700;color:#000000;'
        if st == 'geek':
            return f'font-weight:700;color:{T["main"]};background:{T["soft"]};padding:1px 5px;border-radius:3px;'
        if st == 'bold':
            return f'font-weight:700;color:{INK};background:{T["mark"]};padding:2px 4px;border-radius:3px;'
        return f'font-weight:700;color:{INK};background:linear-gradient(transparent 58%,{T["mark"]} 58%);padding:0 2px;'

    def x_chip(self, T):
        st = T['st']
        if st == 'zen':
            return f'font-family:{SERIF_CN};font-weight:700;color:#FFFFFF;background:{T["main"]};padding:3px 9px;letter-spacing:1px;'
        if st == 'bento':
            return f'background:{T["main"]};color:#FFFFFF;font-weight:700;padding:4px 10px;border-radius:9px;'
        if st == 'dark':
            return f'background:{T["line"]};color:{DK_BG};font-weight:800;padding:2px 8px;border-radius:4px;'
        return f'background:{T["main"]};color:#FFFFFF;font-weight:700;padding:2px 7px;border-radius:3px;'

    def underline_css(self, T):
        st, M = T['st'], T['main']
        return {
            'mag': f'border-bottom:1px solid {M};padding-bottom:3px;', 'min': f'border-bottom:2px solid {INK};padding-bottom:1px;',
            'geek': f'border-bottom:2px dashed {M};padding-bottom:2px;', 'bold': f'border-bottom:3px solid {M};padding-bottom:1px;',
            'neo': f'border-bottom:2px solid {T["line"]};padding-bottom:2px;', 'note': f'border-bottom:2px dashed {M};padding-bottom:2px;',
            'zen': f'border-bottom:1px solid {M};padding-bottom:3px;', 'bento': f'border-bottom:2px solid {M};padding-bottom:1px;',
            'dark': f'color:#E6EAF0;border-bottom:2px solid {T["line"]};padding-bottom:1px;',
        }.get(st) or f'border-bottom:2px solid {M};padding-bottom:1px;'

    def p_css(self, T):
        st = T['st']
        if st in NEW_ST:
            return {
                'note': 'margin:0 0 22px;font-size:16px;line-height:1.95;letter-spacing:0.5px;color:#3D3A36;text-align:justify;',
                'zen': 'margin:0 0 24px;font-size:16px;line-height:2.05;letter-spacing:1px;color:#333333;text-align:justify;',
                'bento': 'margin:0 0 22px;font-size:16px;line-height:1.85;letter-spacing:0.3px;color:#1D1D1F;text-align:justify;',
                'dark': 'margin:0 0 22px;font-size:16px;line-height:1.9;letter-spacing:0.5px;color:#C5CCD6;text-align:justify;',
            }[st]
        if st == 'mag':
            return 'margin:0 0 24px;font-size:16px;line-height:2;letter-spacing:1px;color:#333333;text-align:justify;'
        if st == 'min':
            return 'margin:0 0 20px;font-size:15px;line-height:1.85;letter-spacing:0.3px;color:#2B2B2B;text-align:left;'
        if st == 'geek':
            return f'margin:0 0 20px;font-size:15px;line-height:1.9;letter-spacing:0.4px;color:{BODY};text-align:justify;'
        return f'margin:0 0 22px;font-size:16px;line-height:1.9;letter-spacing:0.5px;color:{BODY};text-align:justify;'

    # ---------------- blocks ----------------
    def h2(self, n, title, sub, T):
        st, M = T['st'], T['main']
        if st == 'neo':
            return self.neo_h2(n, title, sub, T)
        if st in NEW_ST:
            return self.x_h2(n, title, sub, T)

        def subp(col, extra=''):
            return f'<p style="margin:6px 0 0;font-size:11px;letter-spacing:2px;color:{col};line-height:1.4;{extra}">{esc(sub.upper())}</p>' if sub else ''
        if st == 'mag':
            return ('<section style="margin:54px 0 30px;text-align:center;">'
                    f'<p style="margin:0;font-size:34px;line-height:1;color:{M};font-family:{SERIF};font-weight:400;">{pad(n)}</p>'
                    f'<p style="margin:14px 0 0;font-size:22px;font-weight:700;color:{INK};line-height:1.5;font-family:{SERIF};letter-spacing:1px;">{title}</p>'
                    + subp(MUTE)
                    + f'<span style="display:inline-block;width:32px;height:1px;background:{M};margin-top:14px;"></span></section>')
        if st == 'min':
            return ('<section style="margin:48px 0 24px;padding-bottom:10px;border-bottom:2px solid ' + INK + ';display:flex;align-items:flex-end;">'
                    f'<section style="flex:1;min-width:0;"><p style="margin:0;font-size:20px;font-weight:800;color:{INK};line-height:1.45;">{title}</p>' + subp(MUTE) + '</section>'
                    f'<span style="flex-shrink:0;margin-left:12px;font-size:13px;font-weight:700;color:{M};font-family:{MONO};line-height:1.8;">{pad(n)}</span></section>')
        if st == 'geek':
            return ('<section style="margin:46px 0 24px;padding-bottom:12px;border-bottom:1.5px dashed ' + T['line'] + ';">'
                    f'<p style="margin:0 0 8px;font-size:12px;letter-spacing:1px;color:{M};font-family:{MONO};line-height:1.4;">// PART_{pad(n)}'
                    + (f' · {esc(sub.upper())}' if sub else '') + '</p>'
                    '<section style="display:flex;align-items:center;">'
                    f'<span style="flex-shrink:0;display:inline-block;background:#0B0B0C;color:#FFFFFF;font-size:14px;font-weight:700;font-family:{MONO};padding:3px 9px;border-radius:5px;margin-right:12px;">{pad(n)}</span>'
                    f'<span style="font-size:20px;font-weight:800;color:{INK};line-height:1.45;">{title}</span></section></section>')
        if st == 'bold':
            return ('<section style="margin:46px 0 26px;background:' + M + ';border-radius:16px;padding:16px 20px;display:flex;align-items:center;">'
                    f'<span style="flex-shrink:0;font-size:42px;line-height:1;font-weight:800;color:#FFFFFF;opacity:0.45;font-family:Helvetica,Arial,sans-serif;margin-right:16px;">{pad(n)}</span>'
                    f'<section style="flex:1;min-width:0;"><p style="margin:0;font-size:20px;font-weight:800;color:#FFFFFF;line-height:1.45;">{title}</p>' + subp('#FFFFFF', 'opacity:0.75;') + '</section></section>')
        return ('<section style="margin:46px 0 26px;display:flex;align-items:center;">'
                f'<section style="flex-shrink:0;padding-right:18px;margin-right:18px;border-right:1px solid {BORDER};text-align:center;">'
                f'<p style="margin:0;font-size:12px;letter-spacing:2px;font-weight:600;color:{MUTE};line-height:1.4;">PART</p>'
                f'<p style="margin:0;font-size:40px;line-height:1.05;font-weight:800;color:{M};font-family:Helvetica,Arial,sans-serif;">{pad(n)}</p></section>'
                f'<section style="flex:1;min-width:0;"><p style="margin:0;font-size:21px;font-weight:800;color:{INK};line-height:1.45;">{title}</p>' + subp(MUTE) + '</section></section>')

    def h2_pill(self, n, title, T):
        num = CN_NUM[n - 1] if n <= 10 else n
        return ('<section style="margin:46px 0 28px;text-align:center;">'
                f'<span style="display:inline-block;background:{T["main"]};color:#FFFFFF;font-size:18px;font-weight:800;line-height:1.5;'
                f'padding:10px 24px;border-radius:10px;box-shadow:0 4px 0 {T["tint"]};">{num}、{title}</span></section>')

    def x_h2(self, n, title, sub, T):
        st, M = T['st'], T['main']
        S, TT, L, K = T['soft'], T['tint'], T['line'], T['mark']
        if st == 'note':
            return ('<section style="margin:52px 0 26px;padding-bottom:16px;border-bottom:1.5px dashed ' + L + ';">'
                    f'<p style="margin:0;line-height:1;"><span style="display:inline-block;background:{K};color:#1F1F1F;font-size:12px;font-weight:800;letter-spacing:2px;line-height:1;padding:6px 14px;transform:rotate(-3deg);font-family:{MONO};">NO.{pad(n)}</span></p>'
                    f'<p style="margin:14px 0 0;font-size:23px;font-weight:800;color:#1F1F1F;line-height:1.55;"><span style="background:linear-gradient(transparent 62%,{TT} 62%);padding:0 4px;">{title}</span></p>'
                    + (f'<p style="margin:8px 0 0 4px;font-size:11px;letter-spacing:3px;color:#A8A29E;line-height:1.4;">— {esc(sub.upper())}</p>' if sub else '') + '</section>')
        if st == 'zen':
            ln = f'<span style="display:inline-block;width:28px;height:1px;background:{L};vertical-align:middle;margin:0 10px;"></span>'
            num = CN_BIG[n - 1] if n <= 10 else n
            return ('<section style="margin:60px 0 34px;text-align:center;">'
                    f'<p style="margin:0;line-height:1;"><span style="display:inline-block;width:42px;height:42px;line-height:42px;background:{M};color:#FFFFFF;font-size:21px;font-weight:700;border-radius:5px;font-family:{SERIF_CN};">{num}</span></p>'
                    f'<p style="margin:18px 0 0;font-size:23px;font-weight:700;letter-spacing:3px;color:#1F1F1F;line-height:1.6;font-family:{SERIF_CN};">{title}</p>'
                    + (f'<p style="margin:12px 0 0;font-size:10px;letter-spacing:4px;color:#A8A29E;line-height:1.4;">{ln}{esc(sub.upper())}{ln}</p>' if sub else '') + '</section>')
        if st == 'bento':
            return ('<section style="margin:52px 0 26px;background:' + S + ';border-radius:22px;padding:22px 22px 24px;">'
                    f'<p style="margin:0;line-height:1;"><span style="display:inline-block;background:{M};color:#FFFFFF;font-size:13px;font-weight:800;line-height:1;padding:6px 13px;border-radius:20px;font-family:Helvetica,Arial,sans-serif;">{pad(n)}</span>'
                    + (f'<span style="margin-left:10px;font-size:11px;font-weight:600;letter-spacing:2px;color:#86868B;">{esc(sub.upper())}</span>' if sub else '') + '</p>'
                    f'<p style="margin:14px 0 0;font-size:24px;font-weight:800;color:#1D1D1F;line-height:1.4;">{title}</p></section>')
        return ('<section style="margin:54px 0 28px;">'
                f'<p style="margin:0 0 0 -2px;font-size:68px;line-height:0.9;height:46px;font-weight:800;color:#1D242E;font-family:Helvetica,Arial,sans-serif;">{pad(n)}</p>'
                + (f'<p style="margin:0;font-size:11px;font-weight:700;letter-spacing:3px;color:{L};line-height:1.4;">{esc(sub.upper())}</p>' if sub else '')
                + f'<p style="margin:8px 0 0;font-size:23px;font-weight:800;color:#FFFFFF;line-height:1.45;">{title}</p>'
                + f'<p style="margin:16px 0 0;line-height:0;"><span style="display:inline-block;width:48px;height:3px;border-radius:2px;background:{L};"></span></p></section>')

    def neo_h2(self, n, title, sub, T):
        M = T['main']
        return ('<section style="margin:56px 0 30px;">'
                '<p style="margin:0 0 0 -3px;line-height:0.82;height:70px;white-space:nowrap;overflow:hidden;">'
                f'<span style="font-size:92px;line-height:0.82;font-weight:900;font-style:italic;letter-spacing:-3px;color:{T["tint"]};font-family:Helvetica,Arial,sans-serif;">{pad(n)}</span>'
                f'<span style="font-size:11px;font-weight:800;letter-spacing:3px;color:{M};margin-left:14px;vertical-align:30px;">{esc(sub.upper()) if sub else "PART " + pad(n)}</span></p>'
                '<section style="margin:-20px 0 0 10px;">'
                f'<p style="margin:0;font-size:23px;font-weight:900;color:{INK};line-height:1.4;">{title}</p>'
                f'<section style="margin-top:12px;line-height:0;"><span style="display:inline-block;width:72px;height:6px;border-radius:3px;background:linear-gradient(90deg,{M},{T["line"]});"></span>'
                f'<span style="display:inline-block;width:6px;height:6px;border-radius:3px;background:{INK};margin-left:5px;"></span></section></section></section>')

    def h3(self, title, T):
        st, M = T['st'], T['main']
        m = re.match(r'^(第[一二三四五六七八九十\d]+步|Step\s*\d+)\s*[：:、.，,]?\s*(.*)$', title)
        if st == 'neo':
            return self.neo_h3(m, title, T)
        if st in NEW_ST:
            return self.x_h3(m, title, T)
        if m:
            chip = {'mag': f'border:1px solid {M};color:{M};',
                    'geek': f'background:{T["soft"]};color:{M};font-family:{MONO};border-radius:5px;',
                    'bold': f'background:{M};color:#FFFFFF;'}.get(st) or f'background:{INK};color:#FFFFFF;'
            return ('<section style="margin:30px 0 14px;display:flex;align-items:center;">'
                    f'<span style="flex-shrink:0;display:inline-block;font-size:13px;font-weight:700;padding:4px 14px;border-radius:20px;margin-right:12px;{chip}">{m[1]}</span>'
                    f'<span style="font-size:18px;font-weight:800;color:{INK};line-height:1.5;">{m[2]}</span></section>')
        if st == 'mag':
            return f'<section style="margin:34px 0 14px;"><span style="font-size:18px;font-weight:700;color:{INK};font-family:{SERIF};padding-bottom:4px;border-bottom:1px solid {M};">{title}</span></section>'
        if st == 'min':
            return '<section style="margin:30px 0 12px;"><span style="font-size:17px;font-weight:800;color:#000000;">' + title + '</span></section>'
        if st == 'geek':
            return ('<section style="margin:30px 0 14px;">'
                    f'<span style="font-size:16px;font-weight:700;color:{M};font-family:{MONO};margin-right:8px;">##</span>'
                    f'<span style="font-size:18px;font-weight:800;color:{INK};">{title}</span></section>')
        if st == 'bold':
            return f'<section style="margin:32px 0 14px;"><span style="display:inline-block;background:{T["tint"]};color:{M};font-size:17px;font-weight:800;padding:5px 14px;border-radius:10px;">{title}</span></section>'
        return (f'<section style="margin:32px 0 14px;"><span style="display:inline-block;width:4px;height:18px;background:{M};border-radius:2px;vertical-align:-2px;margin-right:10px;"></span>'
                f'<span style="font-size:18px;font-weight:800;color:{INK};">{title}</span></section>')

    def x_h3(self, m, title, T):
        st, M = T['st'], T['main']
        ink = '#FFFFFF' if st == 'dark' else '#1F1F1F'
        if m:
            chip = {'note': f'background:{T["mark"]};color:#1F1F1F;transform:rotate(-2deg);',
                    'zen': f'border:1px solid {M};color:{M};font-family:{SERIF_CN};',
                    'bento': f'background:{M};color:#FFFFFF;',
                    'dark': f'background:{T["line"]};color:{DK_BG};'}[st]
            return ('<section style="margin:30px 0 14px;display:flex;align-items:center;">'
                    f'<span style="flex-shrink:0;display:inline-block;font-size:13px;font-weight:700;padding:4px 14px;border-radius:20px;margin-right:12px;{chip}">{m[1]}</span>'
                    f'<span style="font-size:18px;font-weight:800;color:{ink};line-height:1.5;">{m[2]}</span></section>')
        if st == 'zen':
            return f'<section style="margin:34px 0 14px;"><span style="font-size:18px;font-weight:700;color:{ink};font-family:{SERIF_CN};letter-spacing:1px;">{title}</span></section>'
        if st == 'note':
            return f'<section style="margin:32px 0 14px;"><span style="font-size:18px;font-weight:800;color:{ink};background:linear-gradient(transparent 60%,{T["mark"]} 60%);padding:0 3px;">{title}</span></section>'
        if st == 'bento':
            return f'<section style="margin:32px 0 14px;"><span style="display:inline-block;background:{T["soft"]};color:#1D1D1F;font-size:17px;font-weight:800;padding:6px 14px;border-radius:12px;">{title}</span></section>'
        return f'<section style="margin:32px 0 14px;"><span style="font-size:18px;font-weight:800;color:{ink};border-left:3px solid {T["line"]};padding-left:10px;">{title}</span></section>'

    def neo_h3(self, m, title, T):
        M = T['main']
        if m:
            return ('<section style="margin:32px 0 16px;display:flex;align-items:center;">'
                    f'<span style="flex-shrink:0;display:inline-block;background:{INK};color:#FFFFFF;font-size:13px;font-weight:800;padding:5px 14px;border-radius:20px;letter-spacing:1px;margin-right:12px;">{m[1]}</span>'
                    f'<span style="font-size:18px;font-weight:900;color:{INK};line-height:1.5;">{m[2]}</span></section>')
        return ('<section style="margin:34px 0 14px;display:flex;align-items:center;">'
                f'<span style="flex-shrink:0;display:inline-block;width:18px;height:3px;background:{M};border-radius:2px;margin-right:10px;"></span>'
                f'<span style="font-size:18px;font-weight:900;color:{INK};line-height:1.5;">{title}</span></section>')

    def h4(self, title, T):
        color = '#FFFFFF' if T['st'] == 'dark' else INK
        return f'<p style="margin:24px 0 10px;font-size:16px;font-weight:800;color:{color};line-height:1.6;">{title}</p>'

    def terminal(self, body):
        rows = ''.join(f'<p style="margin:0;white-space:pre;font-size:14px;line-height:1.9;color:#E5E7EB;font-family:{MONO};">{esc(l) or " "}</p>' for l in body)
        dots = ''.join(f'<span style="display:inline-block;width:11px;height:11px;border-radius:50%;background:{c};margin-right:7px;"></span>'
                       for c in ('#FF5F57', '#FEBC2E', '#28C840'))
        return ('<section style="margin:24px 0;background:#0B0B0C;border-radius:10px;padding:14px 18px 16px;">'
                f'<section style="margin-bottom:10px;line-height:0;">{dots}</section>'
                f'<section style="overflow-x:auto;">{rows}</section></section>')

    def prompt_box(self, body, title, T):
        st = T['st']
        def mark(t):
            return re.sub(r'(【[^】]+】)', lambda m: f'<strong style="color:{T["main"]};">{m[1]}</strong>', t)
        if st in NEW_ST:
            inner = ''.join(f'<p style="margin:0;white-space:pre-wrap;word-break:break-all;font-size:13px;line-height:1.9;color:#5B5A57;font-family:{MONO};">{mark(esc(l) or " ")}</p>' for l in body)
            return self.x_prompt(body, mark, title, T)
        inner = ''.join(f'<p style="margin:0;white-space:pre-wrap;word-break:break-all;font-size:13px;line-height:1.85;color:{PROMPT_INK};font-family:{MONO};">{mark(esc(l) or " ")}</p>' for l in body)
        return ('<section style="margin:26px 0;border:1px solid ' + BORDER + f';border-radius:{T["r"]}px;background:#F8FAFC;overflow:hidden;">'
                '<section style="display:flex;align-items:center;justify-content:space-between;padding:12px 16px;border-bottom:1px solid ' + BORDER + ';background:#FFFFFF;">'
                f'<span style="display:inline-block;background:{INK};color:#FFFFFF;font-size:12px;font-weight:800;letter-spacing:1px;padding:4px 12px;border-radius:20px;font-family:{MONO};">PROMPT</span>'
                f'<span style="font-size:12px;color:{MUTE};">{esc(title or "提示词")} · 上下滑动查看 ↕</span></section>'
                f'<section style="max-height:320px;overflow-y:auto;padding:14px 18px;">{inner}</section></section>')

    def x_prompt(self, body, mark, title, T):
        st, L = T['st'], T['line']
        lab = f'{esc(title or "提示词")} · 上下滑动查看 ↕'
        def p(col, lh):
            return ''.join(f'<p style="margin:0;white-space:pre-wrap;word-break:break-all;font-size:13px;line-height:{lh};color:{col};font-family:{MONO};">{mark(esc(l) or " ")}</p>' for l in body)
        if st == 'note':
            return ('<section style="margin:28px 0;border:1.5px solid ' + L + ';border-radius:6px;background:#FFFFFF;">'
                    f'<section style="display:flex;align-items:center;justify-content:space-between;padding:12px 16px;border-bottom:1.5px dashed {L};">'
                    f'<span style="display:inline-block;background:{T["mark"]};color:#1F1F1F;font-size:12px;font-weight:800;letter-spacing:1px;padding:4px 12px;transform:rotate(-2deg);font-family:{MONO};">PROMPT</span>'
                    f'<span style="font-size:12px;color:#A8A29E;">{lab}</span></section>'
                    f'<section style="max-height:320px;overflow-y:auto;padding:14px 18px;">{p("#5B5A57", "1.9")}</section></section>')
        if st == 'zen':
            return ('<section style="margin:30px 0;border:1px solid ' + L + ';padding:4px;"><section style="border:1px solid ' + T['tint'] + ';background:#FFFFFF;">'
                    f'<section style="display:flex;align-items:center;justify-content:space-between;padding:12px 16px;border-bottom:1px solid {T["tint"]};">'
                    f'<span style="font-size:13px;font-weight:700;letter-spacing:4px;color:{T["main"]};font-family:{SERIF_CN};">PROMPT</span><span style="font-size:12px;color:#A8A29E;">{lab}</span></section>'
                    f'<section style="max-height:320px;overflow-y:auto;padding:16px 18px;">{p("#5B5A57", "2")}</section></section></section>')
        if st == 'bento':
            return ('<section style="margin:26px 0;border-radius:20px;background:#F5F5F7;overflow:hidden;">'
                    '<section style="display:flex;align-items:center;justify-content:space-between;padding:14px 18px 0;">'
                    '<span style="display:inline-block;background:#1D1D1F;color:#FFFFFF;font-size:12px;font-weight:800;letter-spacing:1px;padding:4px 12px;border-radius:20px;font-family:' + MONO + ';">PROMPT</span>'
                    f'<span style="font-size:12px;color:#86868B;">{lab}</span></section>'
                    f'<section style="max-height:320px;overflow-y:auto;padding:14px 18px 18px;">{p("#515154", "1.85")}</section></section>')
        return ('<section style="margin:26px 0;border:1px solid ' + DK_BORDER + f';border-radius:14px;background:{DK_PANEL};overflow:hidden;">'
                f'<section style="display:flex;align-items:center;justify-content:space-between;padding:12px 16px;border-bottom:1px solid {DK_BORDER};background:#1A2029;">'
                f'<span style="display:inline-block;background:{L};color:{DK_BG};font-size:12px;font-weight:800;letter-spacing:1px;padding:4px 12px;border-radius:20px;font-family:{MONO};">PROMPT</span>'
                f'<span style="font-size:12px;color:#6B7685;">{lab}</span></section>'
                f'<section style="max-height:320px;overflow-y:auto;padding:14px 18px;">{p("#9FB0C3", "1.85")}</section></section>')

    def dashed_box(self, title, icon, inner, T, height=320):
        ic = {'list': ('<span style="display:block;width:14px;height:2px;background:' + T['main'] + ';margin:3px auto;"></span>') * 3,
              'image': f'<span style="display:block;width:16px;height:12px;border:2px solid {T["main"]};border-radius:3px;margin:3px auto;"></span>'}[icon]
        return ('<section style="margin:26px 0;border:1.5px dashed ' + T['line'] + f';border-radius:{T["r"]}px;background:#FFFFFF;overflow:hidden;">'
                f'<section style="display:flex;align-items:center;padding:12px 16px;background:{T["soft"]};border-bottom:1.5px dashed {T["line"]};">'
                f'<span style="flex-shrink:0;display:inline-block;width:30px;height:30px;border:1px solid {T["tint"]};background:#FFFFFF;border-radius:8px;padding-top:6px;box-sizing:border-box;margin-right:10px;">{ic}</span>'
                f'<span style="flex:1;font-size:15px;font-weight:700;color:#374151;">{esc(title)}</span>'
                f'<span style="font-size:12px;color:{T["main"]};">↕ 滑动</span></section>'
                f'<section style="max-height:{height}px;overflow-y:auto;padding:16px 18px;">{inner}</section></section>')

    def quote(self, text_html, T):
        st, M = T['st'], T['main']
        if st == 'neo':
            return self.neo_quote(text_html, T)
        if st in NEW_ST:
            return self.x_quote(text_html, T)
        if st == 'mag':
            return ('<section style="margin:32px 0;padding:22px 14px 16px;border-top:1px solid ' + T['line'] + ';border-bottom:1px solid ' + T['line'] + ';text-align:center;">'
                    + quote_svg(M, 1, 30, '0 auto 8px')
                    + f'<section style="font-size:16px;line-height:2;color:#444444;letter-spacing:1px;font-family:{SERIF};text-align:left;">{text_html}</section></section>')
        if st == 'min':
            return f'<section style="margin:26px 0;padding:2px 0 2px 16px;border-left:2px solid #D4D4D8;"><section style="font-size:15px;line-height:1.85;color:{GRAY};">{text_html}</section></section>'
        if st == 'geek':
            return ('<section style="margin:26px 0;padding:12px 16px 10px;background:' + T['soft'] + f';border:1px dashed {T["line"]};border-radius:6px;">'
                    f'<p style="margin:0 0 6px;font-size:12px;color:{M};font-family:{MONO};line-height:1.4;">&gt; quote</p>'
                    f'<section style="font-size:15px;line-height:1.9;color:#1F2937;">{text_html}</section></section>')
        if st == 'bold':
            return (f'<section style="margin:28px 0;padding:18px 20px 14px;background:{T["tint"]};border-radius:16px;border-left:6px solid {M};">'
                    f'<section style="font-size:16px;line-height:1.9;color:{INK};font-weight:600;">{text_html}</section></section>')
        return (f'<section style="margin:28px 0;padding:18px 22px 20px;background:{T["soft"]};border-left:4px solid {M};border-radius:4px 12px 12px 4px;">'
                + quote_svg(T['line'], 1, 30, '2px 0 6px')
                + f'<section style="font-size:16px;line-height:1.9;color:#1F2937;letter-spacing:0.5px;">{text_html}</section></section>')

    def x_quote(self, text_html, T):
        st, M = T['st'], T['main']
        S, TT, L = T['soft'], T['tint'], T['line']
        if st == 'note':
            return f'<section style="margin:30px 6px;padding:16px 20px;background:#FFFFFF;border:1.5px dashed {L};border-radius:6px;"><section style="font-size:16px;line-height:1.95;color:#3D3A36;">{text_html}</section></section>'
        if st == 'zen':
            return f'<section style="margin:32px 0;padding:6px 0 6px 20px;border-left:2px solid {M};"><section style="font-size:16px;line-height:2;color:#444444;letter-spacing:1px;font-family:{SERIF_CN};">{text_html}</section></section>'
        if st == 'bento':
            return f'<section style="margin:28px 0;padding:6px 22px 20px;background:{S};border-radius:20px;">' + quote_svg(TT, 1, 40, '14px 0 4px') + f'<section style="font-size:16px;line-height:1.9;color:#1D1D1F;">{text_html}</section></section>'
        return f'<section style="margin:28px 0;padding:6px 20px 18px;background:{DK_PANEL};border:1px solid {DK_BORDER};border-radius:14px;">' + quote_svg(L, 0.55, 36, '14px 0 4px') + f'<section style="font-size:16px;line-height:1.9;color:#E6EAF0;">{text_html}</section></section>'

    def neo_quote(self, text_html, T):
        return (f'<section style="margin:32px 0;padding:14px 24px 22px;background:{T["soft"]};border:1px solid {T["tint"]};border-radius:18px;overflow:hidden;">'
                + quote_svg(T['line'], 0.55, 58, '18px 0 8px -2px')
                + f'<section style="margin-top:0;font-size:16px;line-height:1.95;color:{INK};letter-spacing:0.5px;">{text_html}</section>'
                + f'<section style="margin-top:10px;text-align:right;line-height:0;"><span style="display:inline-block;width:28px;height:4px;border-radius:2px;background:{T["main"]};"></span></section></section>')

    def golden(self, text_html, T):
        st, M = T['st'], T['main']
        if st == 'neo':
            return (f'<section style="margin:40px 0;padding:1px 28px 30px;background:{T["soft"]};border-radius:22px;overflow:hidden;">'
                    + quote_svg(T['line'], 0.45, 96, '26px 0 12px -2px')
                    + f'<p style="margin:0;font-size:22px;line-height:1.7;font-weight:900;color:{INK};letter-spacing:0.5px;">{text_html}</p>'
                    + f'<section style="margin-top:18px;line-height:0;"><span style="display:inline-block;width:56px;height:5px;border-radius:3px;background:linear-gradient(90deg,{M},{T["line"]});vertical-align:middle;"></span>'
                    + f'<span style="display:inline-block;width:5px;height:5px;border-radius:3px;background:{INK};margin-left:5px;vertical-align:middle;"></span></section></section>')
        if st in NEW_ST:
            return self.x_golden(text_html, T)
        if st == 'mag':
            return ('<section style="margin:40px 0;text-align:center;">' + quote_svg(M, 1, 36, '0 auto')
                    + f'<p style="margin:10px 12px 0;font-size:21px;line-height:1.8;font-weight:700;color:{INK};font-family:{SERIF};letter-spacing:1px;">{text_html}</p></section>')
        if st == 'min':
            return f'<section style="margin:34px 0;padding:18px 0;border-top:1px solid {INK};border-bottom:1px solid {INK};"><p style="margin:0;font-size:19px;line-height:1.7;font-weight:800;color:#000000;">{text_html}</p></section>'
        if st == 'geek':
            return (f'<section style="margin:34px 0;padding:18px 18px;border:1.5px solid {M};border-radius:6px;text-align:center;">'
                    f'<p style="margin:0 0 8px;font-size:12px;color:{M};font-family:{MONO};line-height:1.4;">/* key point */</p>'
                    f'<p style="margin:0;font-size:19px;line-height:1.7;font-weight:800;color:{INK};">{text_html}</p></section>')
        if st == 'bold':
            return f'<section style="margin:34px 0;padding:24px 20px;background:{T["soft"]};border:2px solid {M};border-radius:16px;text-align:center;"><p style="margin:0;font-size:21px;line-height:1.7;font-weight:800;color:{M};">{text_html}</p></section>'
        return ('<section style="margin:36px 0;text-align:center;">'
                f'<span style="display:inline-block;width:36px;height:3px;background:{M};border-radius:2px;"></span>'
                f'<p style="margin:16px 8px;font-size:20px;line-height:1.7;font-weight:800;color:{INK};">{text_html}</p>'
                f'<span style="display:inline-block;width:36px;height:3px;background:{M};border-radius:2px;"></span></section>')

    def x_golden(self, text_html, T):
        st = T['st']
        S, TT, L, K = T['soft'], T['tint'], T['line'], T['mark']
        if st == 'note':
            return (f'<section style="margin:44px 6px 40px;background:{S};border:1.5px dashed {L};border-radius:6px;padding:0 22px 24px;text-align:center;">'
                    f'<p style="margin:-12px 0 16px;line-height:0;"><span style="display:inline-block;width:84px;height:24px;background:{K};opacity:0.9;transform:rotate(-2deg);"></span></p>'
                    f'<p style="margin:0;font-size:19px;line-height:1.75;font-weight:800;color:#1F1F1F;">{text_html}</p></section>')
        if st == 'zen':
            return (f'<section style="margin:46px 0;padding:28px 10px;border-top:3px double {L};border-bottom:3px double {L};text-align:center;">'
                    f'<p style="margin:0;font-size:20px;line-height:1.9;font-weight:700;letter-spacing:2px;color:#1F1F1F;font-family:{SERIF_CN};"><span style="color:{L};">「</span>{text_html}<span style="color:{L};">」</span></p></section>')
        if st == 'bento':
            return (f'<section style="margin:40px 0;background:{S};border-radius:26px;padding:10px 26px 30px;overflow:hidden;">'
                    + quote_svg(TT, 1, 74, '22px 0 14px')
                    + f'<p style="margin:0;font-size:22px;line-height:1.6;font-weight:800;color:#1D1D1F;">{text_html}</p></section>')
        return (f'<section style="margin:40px 0;background:{DK_PANEL};border:1px solid {DK_BORDER};border-radius:18px;padding:8px 24px 28px;overflow:hidden;">'
                + quote_svg(L, 0.55, 64, '22px 0 14px')
                + f'<p style="margin:0;font-size:21px;line-height:1.65;font-weight:800;color:#FFFFFF;">{text_html}</p></section>')

    def callout(self, kind, text_html, T):
        st = T['st']
        lab = CALLOUT_LABELS.get(kind, kind)
        wn = kind == 'WARN'
        col = '#D97706' if wn else T['main']
        body = f'<section style="font-size:15px;line-height:1.85;color:{BODY};">{text_html}</section>'
        if st in NEW_ST:
            return self.x_callout(kind, text_html, T)
        if st == 'neo':
            return (f'<section style="margin:28px 0;border:1px solid {"#F3DDB4" if wn else T["tint"]};border-radius:16px;background:{"#FFFCF5" if wn else "#FFFFFF"};padding:18px 20px 16px;box-shadow:0 10px 30px rgba(17,24,39,0.06);">'
                    f'<p style="margin:0 0 10px;line-height:1;"><span style="display:inline-block;width:6px;height:6px;border-radius:50%;background:{col};vertical-align:middle;margin-right:8px;"></span>'
                    f'<span style="font-size:12px;font-weight:800;letter-spacing:3px;color:{col};vertical-align:middle;">{esc(lab)}</span></p>{body}</section>')
        if st == 'mag':
            return f'<section style="margin:28px 0;padding:14px 0 4px;border-top:2px solid {col};"><p style="margin:0 0 8px;font-size:13px;font-weight:700;letter-spacing:3px;color:{col};">{esc(lab)}</p>{body}</section>'
        if st == 'min':
            return f'<section style="margin:26px 0;padding:2px 0 2px 16px;border-left:2px solid {col};"><p style="margin:0 0 4px;font-size:13px;font-weight:800;color:{col};">{esc(lab)}</p>{body}</section>'
        if st == 'geek':
            return (f'<section style="margin:26px 0;border:1px dashed {"#F0B45C" if wn else T["line"]};border-radius:6px;background:{"#FFF7E6" if wn else "#FFFFFF"};padding:12px 16px;">'
                    f'<p style="margin:0 0 6px;font-size:12px;font-weight:700;color:{col};font-family:{MONO};">// {esc(lab)}</p>{body}</section>')
        if st == 'bold':
            return (f'<section style="margin:26px 0;border-radius:16px;background:{"#FFF1D6" if wn else T["tint"]};padding:16px 18px;">'
                    f'<span style="display:inline-block;background:{col};color:#FFFFFF;font-size:13px;font-weight:800;padding:4px 12px;border-radius:20px;margin-bottom:10px;">{esc(lab)}</span>{body}</section>')
        return (f'<section style="margin:26px 0;border:1px solid {"#FCD9A5" if wn else T["tint"]};border-radius:12px;background:{"#FFF7E6" if wn else "#FFFFFF"};padding:16px 18px;">'
                f'<span style="display:inline-block;background:{col};color:#FFFFFF;font-size:12px;font-weight:700;padding:3px 10px;border-radius:6px;margin-bottom:10px;">{esc(lab)}</span>{body}</section>')

    def x_callout(self, kind, text_html, T):
        st = T['st']
        lab = CALLOUT_LABELS.get(kind, kind)
        wn = kind == 'WARN'
        col = '#D97706' if wn else T['main']
        if st == 'note':
            return (f'<section style="margin:30px 6px;border:1.5px dashed {"#F0B45C" if wn else T["line"]};border-radius:6px;background:#FFFFFF;padding:22px 18px 14px;">'
                    f'<p style="margin:-34px 0 10px;line-height:1;"><span style="display:inline-block;background:{"#FDE3B0" if wn else T["mark"]};color:#1F1F1F;font-size:12px;font-weight:800;padding:5px 12px;transform:rotate(-2deg);">{esc(lab)}</span></p>'
                    f'<section style="font-size:15px;line-height:1.85;color:#3D3A36;">{text_html}</section></section>')
        if st == 'zen':
            return (f'<section style="margin:30px 0;padding:16px 4px 6px;border-top:1px solid {col};border-bottom:1px solid {T["tint"]};">'
                    f'<p style="margin:0 0 8px;font-size:13px;font-weight:700;letter-spacing:4px;color:{col};font-family:{SERIF_CN};">{esc(lab)}</p>'
                    f'<section style="font-size:15px;line-height:1.95;color:#444444;">{text_html}</section></section>')
        if st == 'bento':
            return (f'<section style="margin:26px 0;border-radius:20px;background:{"#FFF4E0" if wn else T["soft"]};padding:18px 20px;">'
                    f'<span style="display:inline-block;background:{col};color:#FFFFFF;font-size:12px;font-weight:800;padding:4px 12px;border-radius:20px;margin-bottom:10px;">{esc(lab)}</span>'
                    f'<section style="font-size:15px;line-height:1.85;color:#1D1D1F;">{text_html}</section></section>')
        return (f'<section style="margin:26px 0;border-radius:14px;background:{DK_PANEL};border:1px solid {"#6B4A1A" if wn else DK_BORDER};padding:16px 18px;">'
                f'<span style="display:inline-block;background:{"#F0B45C" if wn else T["line"]};color:{DK_BG};font-size:12px;font-weight:800;padding:3px 10px;border-radius:6px;margin-bottom:10px;">{esc(lab)}</span>'
                f'<section style="font-size:15px;line-height:1.85;color:#C5CCD6;">{text_html}</section></section>')

    def caption(self, text_html, T, top=-12, bottom=24):
        st = T['st']
        if st == 'neo':
            return (f'<p style="margin:{top + 2}px 0 {bottom}px;text-align:center;line-height:1.6;">'
                    f'<span style="display:inline-block;width:14px;height:1px;background:{T["line"]};vertical-align:middle;margin-right:8px;"></span>'
                    f'<span style="color:{MUTE};font-size:12px;letter-spacing:0.5px;vertical-align:middle;">{text_html}</span>'
                    f'<span style="display:inline-block;width:14px;height:1px;background:{T["line"]};vertical-align:middle;margin-left:8px;"></span></p>')
        pre = (f'<span style="color:{T["main"]};font-family:{MONO};">// </span>' if st == 'geek'
               else f'<span style="color:{T["main"]};">▲ </span>' if st == 'mag' else '')
        return (f'<p style="margin:{top}px 0 {bottom}px;font-size:13px;color:{MUTE};text-align:{"left" if st == "min" else "center"};line-height:1.6;">{pre}{text_html}</p>')

    def image(self, src, alt, T, inline=False):
        st = T['st']
        if not src:
            # 产品安全边界：无效地址不输出 img（参考原生输入的合法地址不受影响）
            return f'<span style="color:{GRAY};">{esc(alt or "图片地址无效")}</span>'
        self.stats['images'] += 1
        if inline:
            return f'<img src="{src}" style="max-width:100%;vertical-align:middle;"/>'
        if st == 'neo':
            cap = self.caption(esc(alt), T, 14, 0) if alt and not is_plain_alt(alt) else ''
            return (f'<section style="margin:26px 0;text-align:center;">'
                    f'<img src="{src}" style="display:block;width:100%;max-width:100%;border-radius:14px;box-shadow:0 12px 32px rgba(17,24,39,0.10);"/>{cap}</section>')
        fr = {'mag': 'border-radius:0;', 'min': 'border-radius:0;',
              'geek': f'border-radius:6px;border:1px solid {BORDER};box-sizing:border-box;',
              'bold': 'border-radius:14px;box-shadow:0 6px 20px rgba(0,0,0,0.10);'}.get(st) or 'border-radius:8px;'
        cap = self.caption(esc(alt), T, 8, 0) if alt and not is_plain_alt(alt) else ''
        return (f'<section style="margin:24px 0;text-align:center;">'
                f'<img src="{src}" style="display:block;width:100%;max-width:100%;margin:0 auto;{fr}"/>{cap}</section>')

    def list_block(self, items, T):
        st, M = T['st'], T['main']
        fs = '15px' if st in ('min', 'geek') else '16px'
        rows = []
        for lv, num, text in items:
            if num is None:
                if st == 'neo' and not lv:
                    mk = f'<span style="flex-shrink:0;display:inline-block;width:8px;height:8px;background:{M};border-radius:2px;margin:12px 12px 0 3px;"></span>'
                elif lv:
                    mk = f'<span style="flex-shrink:0;display:inline-block;width:5px;height:5px;border-radius:50%;border:1px solid {M};margin:12px 11px 0 4px;"></span>'
                elif st == 'mag':
                    mk = f'<span style="flex-shrink:0;color:{M};margin-right:10px;line-height:1.9;font-size:{fs};">—</span>'
                elif st == 'min':
                    mk = f'<span style="flex-shrink:0;display:inline-block;width:5px;height:5px;background:{INK};margin:12px 12px 0 4px;"></span>'
                elif st == 'geek':
                    mk = f'<span style="flex-shrink:0;color:{M};font-family:{MONO};margin-right:10px;line-height:1.9;font-size:13px;">&gt;</span>'
                elif st == 'bold':
                    mk = f'<span style="flex-shrink:0;display:inline-block;width:8px;height:8px;border-radius:50%;background:{M};margin:11px 12px 0 2px;"></span>'
                else:
                    mk = f'<span style="flex-shrink:0;display:inline-block;width:6px;height:6px;border-radius:50%;background:{M};margin:12px 12px 0 4px;"></span>'
            else:
                if st == 'neo':
                    mk = f'<span style="flex-shrink:0;color:{M};font-family:Helvetica,Arial,sans-serif;font-style:italic;font-size:20px;font-weight:900;margin-right:10px;line-height:1.5;min-width:28px;">{pad(num)}</span>'
                elif st == 'mag':
                    mk = f'<span style="flex-shrink:0;color:{M};font-family:{SERIF};font-size:17px;font-weight:700;margin-right:10px;line-height:1.75;">{num}.</span>'
                elif st == 'min':
                    mk = f'<span style="flex-shrink:0;color:#000000;font-size:{fs};font-weight:800;margin-right:8px;line-height:1.9;">{num}.</span>'
                elif st == 'geek':
                    mk = f'<span style="flex-shrink:0;color:{M};font-family:{MONO};font-size:13px;font-weight:700;margin-right:10px;line-height:2.1;">{pad(num)}</span>'
                elif st == 'bold':
                    mk = f'<span style="flex-shrink:0;display:inline-block;min-width:22px;height:22px;line-height:22px;text-align:center;border-radius:50%;background:{M};color:#FFFFFF;font-size:12px;font-weight:800;margin:4px 10px 0 0;">{num}</span>'
                else:
                    mk = f'<span style="flex-shrink:0;display:inline-block;min-width:22px;height:22px;line-height:22px;text-align:center;border-radius:6px;background:{T["tint"]};color:{M};font-size:12px;font-weight:800;margin:4px 10px 0 0;">{num}</span>'
            rows.append(f'<section style="display:flex;margin:0 0 10px {lv * 22}px;">{mk}'
                        f'<p style="margin:0;flex:1;min-width:0;font-size:{fs};line-height:1.9;color:{BODY};">{self.inline(text, T)}</p></section>')
        return '<section style="margin:0 0 22px;">' + ''.join(rows) + '</section>'

    def table(self, rows, T):
        st = T['st']
        head = rows[0] if rows else []
        n = len(head)
        body = [(r + [''] * n)[:n] for r in rows[1:]]
        if st == 'neo':
            th = ''.join(f'<th style="padding:12px 14px;font-weight:800;text-align:left;font-size:12px;letter-spacing:1px;background:{T["soft"]};color:{T["main"]};border-bottom:1px solid {T["tint"]};">{self.inline(c, T)}</th>' for c in head)
            tb = ''.join('<tr>' + ''.join(f'<td style="padding:12px 14px;color:{"#111827" if j == 0 else BODY};font-weight:{"700" if j == 0 else "400"};border-top:1px solid #F1F2F4;">{self.inline(c, T)}</td>' for j, c in enumerate(r)) + '</tr>' for r in body)
            return (f'<section style="margin:28px 0;border:1px solid {T["tint"]};border-radius:14px;overflow:hidden;background:#FFFFFF;box-shadow:0 10px 30px rgba(17,24,39,0.06);"><section style="overflow-x:auto;">'
                    f'<table style="width:100%;border-collapse:collapse;font-size:14px;line-height:1.7;"><thead><tr>{th}</tr></thead><tbody>{tb}</tbody></table></section></section>')
        if st == 'dark':
            th = ''.join(f'<th style="padding:11px 13px;font-weight:800;text-align:left;font-size:13px;background:#1A2029;color:{T["line"]};border-bottom:1px solid {DK_BORDER};">{self.inline(c, T)}</th>' for c in head)
            tb = ''.join('<tr>' + ''.join(f'<td style="padding:11px 13px;color:{"#FFFFFF" if j == 0 else "#C5CCD6"};font-weight:{"700" if j == 0 else "400"};border-top:1px solid {DK_BORDER};">{self.inline(c, T)}</td>' for j, c in enumerate(r)) + '</tr>' for r in body)
            return (f'<section style="margin:26px 0;border:1px solid {DK_BORDER};border-radius:14px;overflow:hidden;background:{DK_PANEL};"><section style="overflow-x:auto;">'
                    f'<table style="width:100%;border-collapse:collapse;font-size:14px;line-height:1.7;"><thead><tr>{th}</tr></thead><tbody>{tb}</tbody></table></section></section>')
        if st in ('mag', 'min'):
            hs, cs = f'border-bottom:2px solid {INK};color:{INK};', f'border-bottom:1px solid {BORDER};'
        elif st == 'geek':
            hs, cs = f'background:#0B0B0C;color:#FFFFFF;border:1px solid #0B0B0C;font-family:{MONO};', f'border:1px solid {BORDER};'
        elif st == 'bold':
            hs, cs = f'background:{T["main"]};color:#FFFFFF;border:1px solid {T["main"]};', f'border:1px solid {T["tint"]};'
        else:
            hs, cs = f'background:{T["soft"]};color:{INK};border:1px solid {BORDER};', f'border:1px solid {BORDER};'
        th = ''.join(f'<th style="padding:10px 12px;font-weight:700;text-align:left;{hs}">{self.inline(c, T)}</th>' for c in head)
        tb = ''.join('<tr>' + ''.join(f'<td style="padding:10px 12px;color:{BODY};{cs}">{self.inline(c, T)}</td>' for c in r) + '</tr>' for r in body)
        return (f'<section style="margin:24px 0;overflow-x:auto;"><table style="width:100%;border-collapse:collapse;font-size:14px;line-height:1.7;">'
                f'<thead><tr>{th}</tr></thead><tbody>{tb}</tbody></table></section>')

    def hr(self, T):
        st, M = T['st'], T['main']
        box = '<section style="margin:40px 0;text-align:center;line-height:1;">'
        if st in NEW_ST:
            return self.x_hr(T)
        if st == 'mag':
            ln = f'<span style="display:inline-block;width:44px;height:1px;background:{T["line"]};vertical-align:middle;"></span>'
            return box + ln + f'<span style="display:inline-block;width:5px;height:5px;border-radius:50%;background:{M};margin:0 10px;vertical-align:middle;"></span>' + ln + '</section>'
        if st == 'min':
            return box + f'<span style="display:inline-block;width:40px;height:1px;background:{INK};"></span></section>'
        if st == 'neo':
            sq = lambda c: f'<span style="display:inline-block;width:8px;height:8px;background:{c};border-radius:2px;margin:0 4px;vertical-align:middle;"></span>'
            ln = lambda a, b: f'<span style="display:inline-block;width:70px;height:3px;border-radius:2px;background:linear-gradient(90deg,{a},{b});vertical-align:middle;"></span>'
            return box + ln('#FFFFFF', M) + sq(T['line']) + sq(INK) + sq(T['line']) + ln(M, '#FFFFFF') + '</section>'
        if st == 'geek':
            return box + f'<span style="font-size:13px;letter-spacing:6px;color:{T["line"]};font-family:{MONO};">/ / /</span></section>'
        if st == 'bold':
            return box + f'<span style="display:inline-block;width:56px;height:5px;border-radius:3px;background:{M};"></span></section>'
        return box + ''.join(f'<span style="display:inline-block;width:6px;height:6px;border-radius:50%;background:{c};margin:0 6px;"></span>' for c in (T['tint'], M, T['tint'])) + '</section>'

    def x_hr(self, T):
        st, M = T['st'], T['main']
        L, K, TT = T['line'], T['mark'], T['tint']
        if st == 'note':
            return ('<section style="margin:40px 0;display:flex;align-items:center;justify-content:center;">'
                    f'<span style="display:inline-block;width:70px;border-top:1.5px dashed {L};"></span>'
                    f'<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:{K};border:1.5px solid {L};margin:0 12px;"></span>'
                    f'<span style="display:inline-block;width:70px;border-top:1.5px dashed {L};"></span></section>')
        if st == 'zen':
            ln = f'<span style="display:inline-block;width:56px;height:1px;background:{L};vertical-align:middle;"></span>'
            return ('<section style="margin:44px 0;text-align:center;line-height:1;">' + ln
                    + f'<span style="display:inline-block;width:7px;height:7px;background:{M};transform:rotate(45deg);vertical-align:middle;margin:0 14px;"></span>' + ln + '</section>')
        if st == 'bento':
            return f'<section style="margin:40px 0;text-align:center;line-height:0;"><span style="display:inline-block;width:44px;height:5px;border-radius:3px;background:{TT};"></span></section>'
        return ('<section style="margin:40px 0;text-align:center;line-height:1;">'
                + ''.join(f'<span style="display:inline-block;width:6px;height:6px;border-radius:50%;background:{c};margin:0 6px;"></span>' for c in (DK_BORDER, L, DK_BORDER))
                + '</section>')

    def video_slot(self, name, T):
        return (f'<section data-video-slot="{esc(name)}" style="margin:20px 0;border:1.5px dashed {T["line"]};border-radius:12px;background:{T["soft"]};padding:18px 16px;text-align:center;">'
                f'<p style="margin:0 0 4px;font-size:15px;font-weight:700;color:{T["main"]};">此处放视频：{esc(name)}</p>'
                '<p style="margin:0;font-size:13px;color:#888888;">在公众号编辑器里插入视频或动图，然后删掉这个方框</p></section>')

    def author_card(self, T, meta):
        st = T['st']
        author = esc(meta.get('author') or AUTHOR)
        slogan = esc(meta.get('slogan') or SLOGAN)
        cta = esc(meta.get('outro_cta') or CTA)
        avatar_url = self.options.get('avatarUrl')
        avatar = (self.url(avatar_url, image=True) if avatar_url else '') or 'GIF_URL_HERE'
        avatar_img = f'<img src="{avatar}" style="display:block;width:72px;height:72px;"/>'
        avatar_box = f'<section style="flex-shrink:0;width:72px;height:72px;border-radius:50%;background:' + '{av}' + f';overflow:hidden;margin-right:16px;">{avatar_img}</section>'
        if st in NEW_ST:
            box = {'note': f'border-radius:6px;background:#FFFFFF;border:1.5px dashed {T["line"]};',
                   'zen': f'border-top:1px solid {T["line"]};border-bottom:1px solid {T["line"]};',
                   'bento': f'border-radius:24px;background:{T["soft"]};',
                   'dark': f'border-radius:16px;background:{DK_PANEL};border:1px solid {DK_BORDER};'}[st]
            c1, c2, c3, av = ('#FFFFFF', '#8B95A3', T['line'], '#232A35') if st == 'dark' else ('#1F1F1F', '#78716C', T['main'], T['tint'])
            return ('<section style="margin:48px 0 12px;padding:20px;' + box + 'display:flex;align-items:center;">'
                    + avatar_box.replace('{av}', av)
                    + f'<section style="flex:1;"><p style="margin:0;font-size:17px;font-weight:800;color:{c1};">{author}</p>'
                    + f'<p style="margin:4px 0 0;font-size:13px;line-height:1.7;color:{c2};">{slogan}</p>'
                    + f'<p style="margin:8px 0 0;font-size:13px;font-weight:700;color:{c3};">{cta}</p></section></section>')
        if st == 'neo':
            return ('<section style="margin:52px 0 16px;padding:22px;border:1px solid ' + T['tint'] + ';border-radius:18px;background:#FFFFFF;box-shadow:0 12px 32px rgba(17,24,39,0.07);display:flex;align-items:center;">'
                    + avatar_box.replace('{av}', T['tint'])
                    + f'<section style="flex:1;"><p style="margin:0;font-size:17px;font-weight:900;color:{INK};">{author}</p>'
                    + f'<p style="margin:4px 0 0;font-size:13px;line-height:1.7;color:{GRAY};">{slogan}</p>'
                    + f'<p style="margin:10px 0 0;line-height:1;"><span style="display:inline-block;color:{T["main"]};font-size:12px;font-weight:800;letter-spacing:0.5px;padding:5px 12px;border-radius:20px;border:1px solid {T["line"]};">{cta}</span></p></section></section>')
        box = (f'margin:48px 0 12px;padding:20px 0 0;border-top:1px solid {INK};' if st in ('min', 'mag')
               else f'margin:48px 0 12px;padding:20px;border-radius:{max(T["r"], 6) + 2}px;background:{T["soft"]};border:1px solid {T["tint"]};')
        return ('<section style="' + box + 'display:flex;align-items:center;">'
                + avatar_box.replace('{av}', T['tint'])
                + f'<section style="flex:1;"><p style="margin:0;font-size:17px;font-weight:800;color:{INK};">{author}</p>'
                + f'<p style="margin:4px 0 0;font-size:13px;line-height:1.7;color:{GRAY};">{slogan}</p>'
                + f'<p style="margin:8px 0 0;font-size:13px;font-weight:700;color:{T["main"]};">{cta}</p></section></section>')

    def notes_block(self, T):
        if not self.notes:
            return ''
        items = ''.join(f'<p style="margin:0 0 6px;font-size:12px;line-height:1.7;color:{MUTE};word-break:break-all;">[{i}] {esc(t)}：{esc(u)}</p>'
                        for i, (t, u) in enumerate(self.notes, 1))
        return (f'<section style="margin:36px 0 0;padding-top:14px;border-top:1px solid {BORDER};">'
                f'<p style="margin:0 0 8px;font-size:13px;font-weight:700;color:{GRAY};">参考链接</p>{items}</section>')

    # ---------------- 主渲染循环（参考 render()） ----------------
    def render_body(self, md, T, meta):
        lines = md.split('\n')
        out = []
        state = {'media': False}
        para = []
        autocap = str(meta.get('caption', 'auto')).lower() not in ('off', 'false', 'no')
        pill = meta.get('h2') == 'pill' and T['st'] == 'std'

        def emit(h, media=False):
            out.append(h)
            state['media'] = media

        def bump(key):
            self.stats[key] = self.stats.get(key, 0) + 1

        def flush():
            if not para:
                return
            text = join_lines(para)
            n = len(para)
            para.clear()
            if text.startswith('^'):
                bump('captions')
                emit(self.caption(self.inline(text[1:].strip(), T), T))
                return
            if (autocap and state['media'] and n == 1 and len(text) <= 46 and text and text[-1] not in CAP_END
                    and not re.search(r'\*\*|==|\+\+|^[「“\"]', text)):
                bump('captions')
                emit(self.caption(self.inline(text, T), T))
                return
            self.stats['paragraphs'] += 1
            emit(f'<p style="{self.p_css(T)}">{self.inline(text, T)}</p>')

        i = 0
        while i < len(lines):
            ln = lines[i]
            s = ln.strip()
            if not s:
                flush()
                i += 1
                continue
            mf = re.match(r'^(`{3,}|~{3,})\s*([\w][\w-]*)?\s*(.*)$', s)
            if mf:
                flush()
                fence = mf[1][:3]
                lang = (mf[2] or '').lower()
                arg = mf[3].strip()
                fence_start = i
                body = []
                i += 1
                while i < len(lines) and not lines[i].strip().startswith(fence):
                    body.append(lines[i])
                    i += 1
                if i >= len(lines):
                    self.warn('代码围栏未闭合，已安全渲染到文末')
                i += 1
                fence_text = '\n'.join(lines[fence_start:i])
                while body and not body[-1].strip():
                    body.pop()
                while body and not body[0].strip():
                    body.pop(0)
                self.stats['codeBlocks'] += 1
                if lang in engine.SVG_CARD_KINDS:
                    bump('svgCards')
                    emit(self._svg_card(lang, arg, body, T, fence_text))
                elif lang == 'prompt':
                    emit(self.prompt_box(body, arg, T))
                elif lang == 'promptbox':
                    inner = ''.join(f'<p style="margin:0;white-space:pre-wrap;word-break:break-all;font-size:13px;line-height:1.85;color:{PROMPT_INK};font-family:{MONO};">{esc(b) or " "}</p>' for b in body)
                    emit(self.dashed_box(arg or '滑动查看完整提示词', 'list', inner, T))
                elif lang == 'longimage':
                    first = next((b for b in body if b.strip()), '')
                    src = first.strip()
                    emit(self.dashed_box(arg or '滑动查看长图', 'image', f'<img src="{src}" style="display:block;width:100%;"/>', T, 420), True)
                else:
                    emit(self.terminal(body))
                continue
            # 产品兼容：旧版 :::svg-* 围栏（参考原生语法没有该分支）
            legacy = re.match(r'^:::(svg-[\w-]+)\s*(.*)$', s)
            if legacy:
                flush()
                lang, arg = legacy[1], legacy[2]
                legacy_start = i
                buf = []
                i += 1
                while i < len(lines) and lines[i].strip() != ':::':
                    buf.append(lines[i])
                    i += 1
                i += 1
                legacy_text = '\n'.join(lines[legacy_start:i])
                self.stats['codeBlocks'] += 1
                if lang in engine.SVG_CARD_KINDS:
                    bump('svgCards')
                    emit(self._svg_card(lang, arg, buf, T, legacy_text))
                else:
                    emit(self.terminal(buf))
                continue
            mh = re.match(r'^(#{1,6})\s+(.+?)\s*#*$', s)
            if mh:
                flush()
                lv = len(mh[1])
                t = mh[2].strip()
                self.stats['headings'] += 1
                if lv == 1:
                    if not meta.get('title'):
                        meta['title'] = t
                elif lv == 2:
                    self._h2n += 1
                    sub = ''
                    if '|' in t:
                        t, sub = t.split('|', 1)[0].strip(), t.split('|', 1)[1].strip()
                    t2 = re.sub(r'^(?:[一二三四五六七八九十]+[、.．]|\d+[、.．]\s*|PART\s*\d+\s*[:：]?\s*)', '', t, count=1).strip() or t
                    inl = self.inline(t2, T)
                    emit(self.h2_pill(self._h2n, inl, T) if pill else self.h2(self._h2n, inl, sub, T))
                elif lv == 3:
                    emit(self.h3(self.inline(t, T), T))
                else:
                    emit(self.h4(self.inline(t, T), T))
                i += 1
                continue
            if re.match(r'^(-{3,}|\*{3,}|_{3,})$', s):
                flush()
                emit(self.hr(T))
                i += 1
                continue
            if s.startswith('>'):
                flush()
                q = []
                while i < len(lines) and lines[i].strip().startswith('>'):
                    q.append(lines[i].strip()[1:].strip())
                    i += 1
                kind = None
                mm = re.match(r'^\[!(\w+)\]\s*(.*)$', q[0]) if q else None
                if mm:
                    kind = mm[1].upper()
                    q[0] = mm[2]
                if kind == 'QUOTE':
                    body_inl = self.inline(join_lines([x for x in q if x]) if any(x for x in q) else '', T)
                    self.stats['quotes'] += 1
                    emit(self.golden(body_inl, T))
                elif kind:
                    self.stats['quotes'] += 1
                    paras = [join_lines(x.split('\n')) for x in '\n'.join(q).split('\n\n') if x.strip()]
                    body = ''.join(f'<p style="margin:0 0 6px;">{self.inline(x, T)}</p>' for x in paras)
                    emit(self.callout(kind, body, T))
                else:
                    self.stats['quotes'] += 1
                    paras = [join_lines(x.split('\n')) for x in '\n'.join(q).split('\n\n') if x.strip()]
                    body = ''.join(f'<p style="margin:0 0 6px;">{self.inline(x, T)}</p>' for x in paras)
                    emit(self.quote(body, T))
                continue
            mv = VIDEO_RE.match(s)
            if mv:
                flush()
                bump('videos')
                emit(self.video_slot(mv[1], T), True)
                i += 1
                continue
            mi = re.match(r'^!\[' + IMAGE_ALT + r'\]\(<?([^)\s>]+)>?(?:\s+"([^"]*)")?\)$', s)
            if mi:
                flush()
                src = self.safe_src(mi[2])
                alt = image_alt(mi[1])
                if (mi[3] or '').lower() == 'long':
                    emit(self.dashed_box(alt and alt.lower() != 'image' and alt or '滑动查看长图', 'image', f'<img src="{src}" style="display:block;width:100%;"/>', T, 420), True)
                else:
                    emit(self.image(src, alt, T), True)
                i += 1
                continue
            ml = LI_RE.match(ln)
            if ml:
                flush()
                items = []
                base = len(ml[1].expandtabs(4))
                ordered = ml[3] is not None
                while i < len(lines):
                    x = LI_RE.match(lines[i])
                    if x:
                        lv = min(2, max(0, (len(x[1].expandtabs(4)) - base) // 2))
                        if lv == 0 and (x[3] is not None) != ordered:
                            break
                        items.append((lv, int(x[3]) if x[3] is not None else None, x[4]))
                        i += 1
                        continue
                    if lines[i].strip() and lines[i][:1] in (' ', '\t') and items:
                        items[-1] = (items[-1][0], items[-1][1], join_lines([items[-1][2], lines[i].strip()]))
                        i += 1
                        continue
                    j = i
                    while j < len(lines) and not lines[j].strip():
                        j += 1
                    nxt = LI_RE.match(lines[j]) if j > i and j < len(lines) else None
                    if nxt and (len(nxt[1]) > 0 or (nxt[3] is not None) == ordered):
                        i = j
                        continue
                    break
                self.stats['lists'] += 1
                emit(self.list_block(items, T))
                continue
            if '|' in s and i + 1 < len(lines) and re.match(r'^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?$', lines[i + 1].strip()) and '|' in lines[i + 1]:
                flush()
                rows = []
                while i < len(lines) and '|' in lines[i] and lines[i].strip():
                    r = [c.strip() for c in re.sub(r'^\||\|$', '', lines[i].strip()).split('|')]
                    if not all(re.match(r'^:?-+:?$', c) for c in r):
                        rows.append(r)
                    i += 1
                self.stats['tables'] += 1
                emit(self.table(rows, T))
                continue
            # 产品兼容：整行 {{imageN}} 占位（参考原生语法当普通段落）
            placeholder = re.fullmatch(r'\{\{image(\d+)(?:\|([^}]*))?\}\}', s, flags=re.IGNORECASE)
            if placeholder:
                flush()
                emit(self.placeholder_image(placeholder[1], placeholder[2] or ''))
                i += 1
                continue
            self.stats['paragraphs'] += 1
            para.append(s)
            i += 1
        flush()
        # 产品兼容：未使用的图片地址追加到文末（参考实现没有 image_urls 注入）
        for index, value in enumerate(self.image_urls):
            url = value.get('url', '') if isinstance(value, dict) else value
            if index not in self.consumed_images and url and url not in md:
                self.stats['images'] += 1
                emit(self.image(self.url(url, image=True), '', T))
        if str(meta.get('outro', 'true')).lower() != 'false':
            emit(self.author_card(T, meta))
        emit(self.notes_block(T))
        wrap = f'padding:26px 18px 30px;background:{DK_BG};border-radius:18px;' if T['st'] == 'dark' else 'padding:0 4px;'
        return f'<section style="font-family:-apple-system,BlinkMacSystemFont,\'PingFang SC\',\'Helvetica Neue\',sans-serif;{wrap}">' + ''.join(out) + '</section>'

    def _svg_card(self, lang, arg, body, T, fence_text=''):
        # 渲染期防幻觉提示：柱状图数值必须能在正文（去掉本围栏后）中找到。
        source_text = self.source_body.replace(fence_text, '', 1) if fence_text else self.source_body
        for warning in cards.card_warnings(lang, arg, body, source_text=source_text):
            self.warn(warning)
        result = engine.render_svg_card(lang, arg, body, style=T['st'], theme=self.theme)
        for warning in result['warnings']:
            self.warn(warning)
        return result['html']

    def render(self, body):
        self._h2n = 0
        self._T = dict(engine.THEMES['orange'], st='std', r=engine.STYLES['std']['r'])
        source = clean_md(str(body or ''))
        meta, md = front_matter(source)
        self.source_body = md
        self.stats['characters'] = len(re.sub(r'\s', '', md))
        # 产品覆盖：品牌信息、outro（参考 renderPage 的同名参数语义）
        if self.options.get('brandAuthor'):
            meta['author'] = self.options['brandAuthor']
        if self.options.get('brandSlogan'):
            meta['slogan'] = self.options['brandSlogan']
        if self.options.get('outroCta'):
            meta['outro_cta'] = self.options['outroCta']
        if self.options.get('outro') is not None:
            meta['outro'] = self.options['outro']
        for key in ('style', 'theme'):
            if self.options.get(key):
                meta[key] = self.options[key]
        # 标题链与参考 renderPage 一致：meta.title || H1，再以产品 API 的稿件标题兜底
        title = str(meta.get('title') or '')
        m1 = re.search(r'^#\s+(.+)$', md, re.MULTILINE)
        title = title or (m1[1].strip() if m1 else '')
        title = title or str(self.options.get('title') or '')
        self.stats['title'] = title or '未命名文章'
        if not title:
            self.warn('正文未提供一级标题，已使用默认标题「未命名文章」')
        style = meta.get('style') or 'std'
        theme = meta.get('theme') or 'orange'
        if style not in engine.STYLES:
            self.warn('未知风格，已使用标准风格')
            style = 'std'
        if theme not in engine.THEMES:
            self.warn('未知配色，已使用品牌橙')
            theme = 'orange'
        self.style, self.theme = style, theme
        T = dict(engine.THEMES[theme])
        T['st'] = style
        T['r'] = engine.STYLES[style]['r']
        self._T = T
        rendered = self.render_body(md, T, meta)
        rendered = fix_empty(rendered)
        if style == 'dark':
            rendered = dark_fix(rendered)
        # 产品兼容：开头横幅（spec 字段与参考 banner.js 对齐；brand 注入同 renderPage）
        result = [rendered]
        banner = self.options.get('banner', 'none')
        if banner and banner != 'none':
            brand = {}
            if self.options.get('brandAuthor'):
                brand['author'] = str(self.options['brandAuthor'])
            if self.options.get('brandSlogan'):
                brand['slogan'] = str(self.options['brandSlogan'])
            if isinstance(banner, str):
                if banner == 'card':
                    t1 = title[:9] or '文章精选'
                    t2 = title[9:18]
                    spec = {
                        'style': 'card',
                        'card': {
                            'theme': theme,
                            'eyebrow': '文章精选',
                            'title1': t1,
                            'title2': t2,
                            'sub': str(self.options.get('brandSlogan') or '')[:20],
                            'right': 'steps',
                            'steps': [{'label': t1[:8], 'icon': 'doc'},
                                      {'label': (t2 or '一键复制')[:8], 'icon': 'spark'}],
                            'result': '读完这篇',
                        },
                    }
                    if len(title) > 18:
                        self.warn('标题过长，开头横幅使用了缩略标题')
                else:
                    t1, t2 = title[:8], title[8:16]
                    spec = {
                        'style': banner,
                        'poster': {
                            'theme': theme,
                            'eyebrow': (brand.get('author') or '文章精选')[:10],
                            'title1': t1,
                            'title2': t2,
                            'big': title[:2],
                            'right': 'flow',
                            'ask': title[:9] or '帮我排好这篇',
                            'result': (t2 or '出稿了')[:6],
                            'result_sub': (brand.get('slogan') or '直接贴进公众号')[:9],
                        },
                    }
                    if len(title) > 16:
                        self.warn('标题过长，开头横幅使用了缩略标题')
            else:
                spec = banner
            if brand:
                spec = {**spec, 'brand': brand}
            rendered_banner = engine.render_banner(spec, theme=theme)
            result.insert(0, rendered_banner['html'])
            for warning in rendered_banner['warnings']:
                self.warn(warning)
        html_output = ''.join(result)
        self.stats['htmlBytes'] = len(html_output.encode('utf-8'))
        return {'html': html_output, 'warnings': self.warnings, 'stats': self.stats}


def render_studio_document(body: str, options: dict | None = None, image_urls: list | None = None) -> dict:
    return StudioRenderer(dict(options or {}), list(image_urls or [])).render(body)
