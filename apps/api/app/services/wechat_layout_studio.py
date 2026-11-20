"""参考 dsh-wechat-studio vendor/layout/layout.js（MIT）的纯标准库正文渲染器。

颜色、组件骨架和十种风格来自参考实现；URL 在输入边界校验，不接收原始 HTML。
来源：dsh-wechat-studio 的 vendor/layout/layout.js 及 skills/wechat-svg-cards/scripts/layout.py。

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

MONO = "Menlo,Consolas,'SF Mono',monospace"
SERIF = "Georgia,'Songti SC','STSong','Source Han Serif SC','Noto Serif SC',serif"
INK, BODY, GRAY = '#111827', '#3F3F46', '#6B7280'
DK_BG, DK_PANEL, DK_BORDER = '#0F1218', '#151A21', '#2A313C'
NEW = ('note', 'zen', 'bento', 'dark')


def esc(value):
    return html.escape(str(value), quote=True)


def tag(name, content, css=''):
    return f'<{name} style="{css}">{content}</{name}>'


def section(content, css=''):
    return tag('section', content, css)


def quote_svg(color, width=40, opacity=1):
    marks = ''.join(f'<circle cx="{18 + dx}" cy="47" r="15" fill="{color}"/><path d="M{3 + dx} 47C{3 + dx} 26 {13 + dx} 11 {32 + dx} 3L{36 + dx} 10C{24 + dx} 17 {19 + dx} 27 {20 + dx} 37Z" fill="{color}"/>' for dx in (0, 40))
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 80 64" width="{width}" height="{round(width * .72)}" style="display:block;margin:14px 0 8px;"><g opacity="{opacity}">{marks}</g></svg>'


def decoration(color, width=36, height=3):
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}" style="display:inline-block;vertical-align:middle;"><rect width="{width}" height="{height}" rx="{min(height / 2, 3)}" fill="{color}"/></svg>'


class StudioRenderer:
    def __init__(self, options, image_urls):
        self.options = options
        self.style = options.get('style', 'std')
        self.theme = options.get('theme', 'orange')
        self.warnings = []
        if self.style not in engine.STYLES:
            self.warn('未知风格，已使用标准风格')
            self.style = 'std'
        if self.theme not in engine.THEMES:
            self.warn('未知配色，已使用品牌橙')
            self.theme = 'orange'
        self.T = engine.resolve_style_theme(self.style, self.theme)
        self.ink = '#FFFFFF' if self.style == 'dark' else INK
        self.body = '#C5CCD6' if self.style == 'dark' else BODY
        self.accent = self.T['line'] if self.style == 'dark' else self.T['main']
        self.notes = []
        self.image_urls = image_urls
        self.consumed_images = set()
        self.stats = dict(title='', characters=0, paragraphs=0, headings=0, images=0, tables=0, lists=0, quotes=0, codeBlocks=0, svgCards=0, links=0)

    def warn(self, message):
        if message not in self.warnings:
            self.warnings.append(message)

    def url(self, value, image=False):
        value = html.unescape(str(value or '')).strip()
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

    def p_css(self):
        variants = {
            'mag': 'font-size:16px;line-height:2;letter-spacing:1px;color:#333333;text-align:justify;margin:0 0 24px;',
            'min': 'font-size:15px;line-height:1.85;letter-spacing:0.3px;color:#2B2B2B;text-align:left;margin:0 0 20px;',
            'geek': f'font-size:15px;line-height:1.9;letter-spacing:0.4px;color:{BODY};text-align:justify;margin:0 0 20px;',
            'note': 'font-size:16px;line-height:1.95;letter-spacing:0.5px;color:#3D3A36;text-align:justify;margin:0 0 22px;',
            'zen': 'font-size:16px;line-height:2.05;letter-spacing:1px;color:#333333;text-align:justify;margin:0 0 24px;',
            'bento': 'font-size:16px;line-height:1.85;letter-spacing:0.3px;color:#1D1D1F;text-align:justify;margin:0 0 22px;',
        }
        return variants.get(self.style, f'margin:0 0 22px;font-size:16px;line-height:1.9;letter-spacing:0.5px;color:{self.body};text-align:justify;')

    def strong_css(self):
        T = self.T
        return {
            'std': f'color:{INK};background:linear-gradient(transparent 58%,{T["mark"]} 58%);padding:0 2px;',
            'mag': f'color:{T["main"]};', 'min': 'color:#000000;',
            'geek': f'color:{T["main"]};background:{T["soft"]};padding:1px 5px;border-radius:3px;',
            'bold': f'color:{INK};background:{T["mark"]};padding:2px 4px;border-radius:3px;',
            'neo': f'color:{INK};background:linear-gradient(100deg,{T["mark"]},{T["tint"]});padding:2px 6px;border-radius:4px;',
            'note': f'color:#1F1F1F;background:{T["mark"]};padding:2px 4px;border-radius:3px;',
            'zen': f'color:#1F1F1F;background:linear-gradient(transparent 66%,{T["tint"]} 66%);padding:0 2px;',
            'bento': f'color:#1D1D1F;background:{T["tint"]};padding:2px 7px;border-radius:7px;',
            'dark': 'color:#FFFFFF;background:#252C37;padding:2px 6px;border-radius:5px;',
        }[self.style] + 'font-weight:700;'

    def placeholder_image(self, number, caption='', inline=False):
        index = int(number) - 1
        self.consumed_images.add(index)
        if index < 0 or index >= len(self.image_urls):
            self.warn(f'图片占位 image{number} 缺少对应图片地址')
            return tag('span', esc(caption or '图片待补充'), 'color:#94A3B8;')
        value = self.image_urls[index]
        url = value.get('url', '') if isinstance(value, dict) else value
        if not url:
            self.warn(f'图片占位 image{number} 缺少对应图片地址')
            return tag('span', esc(caption or '图片待补充'), 'color:#94A3B8;')
        return self.image(url, caption.strip(), inline=inline)

    def inline(self, source):
        # 占位符只由本次调用产生，原文的控制字符先删除，避免伪造索引。
        source = str(source).replace('\x00', '')
        keep = []
        def stash(value):
            keep.append(value)
            return f'\x00{len(keep)-1}\x00'
        source = re.sub(r'`([^`]+)`', lambda m: stash(tag('code', esc(m[1]), f'font-family:{MONO};font-size:0.9em;background:{DK_PANEL if self.style == "dark" else self.T["soft"]};color:{self.accent};padding:2px 5px;border-radius:4px;')), source)
        source = re.sub(r'\{\{image(\d+)(?:\|([^}]*))?\}\}', lambda m: stash(self.placeholder_image(m[1], m[2] or '', inline=True)), source, flags=re.IGNORECASE)
        def image(m):
            return stash(self.image(m[2], m[1], inline=True))
        source = re.sub(r'!\[((?:\\.|[^\]\\])*)\]\(<?([^\s)>]+)>?(?:\s+"[^"]*")?\)', image, source)
        def link(m):
            url = self.url(m[2])
            text = esc(m[1])
            if not url:
                return stash(text)
            self.stats['links'] += 1
            css = f'color:{self.accent};text-decoration:none;border-bottom:1px solid {self.T["line"]};'
            if (urlsplit(url).hostname or '').lower() == 'mp.weixin.qq.com':
                return stash(f'<a href="{esc(url)}" style="{css}">{text}</a>')
            self.notes.append((m[1], url))
            return stash(tag('span', text, css) + tag('sup', f'[{len(self.notes)}]', f'color:{self.accent};font-size:11px;'))
        source = re.sub(r'\[([^\]]+)\]\(<?([^\s)>]+)>?\)', link, source)
        source = esc(source)
        source = re.sub(r'\*\*(?=\S)(.+?)(?<=\S)\*\*', lambda m: tag('strong', m[1], self.strong_css()), source)
        source = re.sub(r'__(?=\S)(.+?)(?<=\S)__', lambda m: tag('strong', m[1], self.strong_css()), source)
        chip = f'background:{self.accent};color:{DK_BG if self.style == "dark" else "#FFFFFF"};font-weight:700;padding:2px 7px;border-radius:{9 if self.style == "bento" else 3}px;'
        source = re.sub(r'==(?=\S)(.+?)(?<=\S)==', lambda m: tag('span', m[1], chip), source)
        border = 'dashed' if self.style in ('geek', 'note') else 'solid'
        source = re.sub(r'\+\+(?=\S)(.+?)(?<=\S)\+\+|&lt;u&gt;(.+?)&lt;/u&gt;', lambda m: tag('span', m[1] or m[2], f'border-bottom:{1 if self.style in ("zen", "mag") else 2}px {border} {self.accent};padding-bottom:2px;'), source)
        source = re.sub(r'~~(?=\S)(.+?)(?<=\S)~~', lambda m: tag('span', m[1], 'text-decoration:line-through;color:#9CA3AF;'), source)
        source = re.sub(r'(?<![*\w])\*(?![\s*])(.+?)(?<![\s*])\*(?![*\w])', lambda m: tag('em', m[1], f'font-style:normal;color:{self.accent};'), source)
        for marker in ('**', '=='):
            if marker in re.sub(r'<[^>]+>', '', source):
                self.warn(f'有一处 {marker} 没配成对，已按原文显示')
        return re.sub(r'\x00(\d+)\x00', lambda m: keep[int(m[1])], source)

    def h2(self, number, title, sub=''):
        T, st, M = self.T, self.style, self.T['main']
        n = f'{number:02d}'
        title = self.inline(title)
        subp = tag('p', esc(sub.upper()), f'margin:6px 0 0;font-size:11px;letter-spacing:2px;color:{self.accent};line-height:1.4;') if sub else ''
        if st == 'std':
            badge = section(tag('p', 'PART', 'margin:0;font-size:12px;letter-spacing:2px;color:#94A3B8;') + tag('p', n, f'margin:0;font-size:40px;line-height:1.05;font-weight:800;color:{M};'), 'padding-right:18px;margin-right:18px;border-right:1px solid #E5E7EB;text-align:center;')
            return section(badge + section(tag('p', title, f'margin:0;font-size:21px;font-weight:800;color:{INK};line-height:1.45;') + subp), 'margin:46px 0 26px;display:flex;align-items:center;')
        if st == 'mag':
            return section(tag('p', n, f'margin:0;font-size:34px;color:{M};font-family:{SERIF};') + tag('p', title, f'margin:14px 0 0;font-size:22px;font-weight:700;color:{INK};font-family:{SERIF};letter-spacing:1px;') + subp + decoration(M, 32, 1), 'margin:54px 0 30px;text-align:center;')
        if st == 'min':
            return section(section(tag('p', title, f'margin:0;font-size:20px;font-weight:800;color:{INK};') + subp, 'flex:1;') + tag('span', n, f'font-size:13px;color:{M};font-family:{MONO};'), f'margin:48px 0 24px;padding-bottom:10px;border-bottom:2px solid {INK};display:flex;align-items:flex-end;')
        if st == 'geek':
            return section(tag('p', '// PART_' + n + (' · ' + esc(sub.upper()) if sub else ''), f'margin:0 0 8px;font-size:12px;color:{M};font-family:{MONO};') + tag('span', n, 'background:#0B0B0C;color:#FFFFFF;font-size:14px;padding:3px 9px;border-radius:5px;margin-right:12px;') + tag('span', title, f'font-size:20px;font-weight:800;color:{INK};'), f'margin:46px 0 24px;padding-bottom:12px;border-bottom:1.5px dashed {T["line"]};')
        if st == 'bold':
            return section(tag('span', n, 'font-size:42px;font-weight:800;color:#FFFFFF;opacity:0.45;margin-right:16px;') + section(tag('p', title, 'margin:0;font-size:20px;font-weight:800;color:#FFFFFF;') + subp), f'margin:46px 0 26px;background:{M};border-radius:16px;padding:16px 20px;display:flex;align-items:center;')
        if st == 'neo':
            return section(tag('p', tag('span', n, f'font-size:92px;line-height:0.82;font-weight:900;font-style:italic;color:{T["tint"]};') + tag('span', esc(sub.upper()) if sub else 'PART ' + n, f'font-size:11px;letter-spacing:3px;color:{M};margin-left:14px;vertical-align:30px;'), 'margin:0;line-height:0.82;height:70px;') + section(tag('p', title, f'margin:0;font-size:23px;font-weight:900;color:{INK};') + decoration(M, 72, 6), 'margin:-20px 0 0 10px;'), 'margin:56px 0 30px;')
        if st == 'note':
            return section(tag('span', 'NO.' + n, f'display:inline-block;background:{T["mark"]};color:#1F1F1F;font-size:12px;font-weight:800;padding:6px 14px;transform:rotate(-3deg);font-family:{MONO};') + tag('p', tag('span', title, f'background:linear-gradient(transparent 62%,{T["tint"]} 62%);padding:0 4px;'), 'margin:14px 0 0;font-size:23px;font-weight:800;color:#1F1F1F;') + subp, f'margin:52px 0 26px;padding-bottom:16px;border-bottom:1.5px dashed {T["line"]};')
        if st == 'zen':
            seal = '壹贰叁肆伍陆柒捌玖拾'[number - 1] if number <= 10 else str(number)
            return section(tag('span', seal, f'display:inline-block;width:42px;height:42px;line-height:42px;background:{M};color:#FFFFFF;font-size:21px;border-radius:5px;font-family:{SERIF};') + tag('p', title, f'margin:18px 0 0;font-size:23px;font-weight:700;letter-spacing:3px;color:#1F1F1F;font-family:{SERIF};') + subp, 'margin:60px 0 34px;text-align:center;')
        if st == 'bento':
            return section(tag('span', n, f'display:inline-block;background:{M};color:#FFFFFF;font-size:13px;font-weight:800;padding:6px 13px;border-radius:20px;') + subp + tag('p', title, 'margin:14px 0 0;font-size:24px;font-weight:800;color:#1D1D1F;'), f'margin:52px 0 26px;background:{T["soft"]};border-radius:22px;padding:22px 22px 24px;')
        return section(tag('p', n, 'margin:0;font-size:68px;line-height:0.9;height:46px;font-weight:800;color:#1D242E;') + subp + tag('p', title, 'margin:8px 0 0;font-size:23px;font-weight:800;color:#FFFFFF;') + decoration(T['line'], 48, 3), 'margin:54px 0 28px;')

    def h3(self, title, level=3):
        st, T = self.style, self.T
        text = self.inline(title)
        if level > 3:
            return tag('p', text, f'margin:24px 0 10px;font-size:16px;font-weight:800;color:{self.ink};line-height:1.6;')
        step = re.match(r'^(第[一二三四五六七八九十\d]+步|Step\s*\d+)\s*[：:、.，,]?\s*(.*)$', title)
        if step:
            chip = tag('span', esc(step[1]), f'display:inline-block;font-size:13px;padding:4px 14px;border-radius:20px;margin-right:12px;background:{self.accent};color:{DK_BG if st == "dark" else "#FFFFFF"};')
            return section(chip + tag('span', self.inline(step[2]), f'font-size:18px;font-weight:800;color:{self.ink};'), 'margin:30px 0 14px;display:flex;align-items:center;')
        css = {
            'std': f'border-left:4px solid {self.accent};padding-left:10px;',
            'mag': f'font-family:{SERIF};padding-bottom:4px;border-bottom:1px solid {self.accent};',
            'min': 'font-size:17px;', 'geek': f'font-family:{MONO};',
            'bold': f'background:{T["tint"]};color:{T["main"]};padding:5px 14px;border-radius:10px;',
            'neo': f'border-left:18px solid {T["main"]};padding-left:10px;',
            'note': f'background:linear-gradient(transparent 60%,{T["mark"]} 60%);padding:0 3px;',
            'zen': f'font-family:{SERIF};letter-spacing:1px;',
            'bento': f'background:{T["soft"]};padding:6px 14px;border-radius:12px;',
            'dark': f'border-left:3px solid {T["line"]};padding-left:10px;',
        }[st]
        return section(tag('span', ('## ' if st == 'geek' else '') + text, f'font-size:18px;font-weight:800;color:{self.ink};' + css), 'margin:32px 0 14px;')

    def quote(self, content, kind=''):
        st, T = self.style, self.T
        self.stats['quotes'] += 1
        body = '<br/>'.join(self.inline(line) for line in content)
        golden = kind in ('GOLDEN', 'QUOTE')
        label = {'TIP': '小技巧', 'NOTE': '说明', 'WARN': '注意', 'WARNING': '注意', 'TLDR': '省流版'}.get(kind, '')
        col = '#D97706' if kind in ('WARN', 'WARNING') else self.accent
        boxes = {
            'std': f'background:{T["soft"]};border-left:4px solid {T["main"]};border-radius:4px 12px 12px 4px;padding:18px 22px 20px;',
            'mag': f'border-top:1px solid {T["line"]};border-bottom:1px solid {T["line"]};padding:22px 14px 16px;font-family:{SERIF};',
            'min': 'padding:2px 0 2px 16px;border-left:2px solid #D4D4D8;',
            'geek': f'padding:12px 16px 10px;background:{T["soft"]};border:1px dashed {T["line"]};border-radius:6px;',
            'bold': f'padding:18px 20px 14px;background:{T["tint"]};border-radius:16px;border-left:6px solid {T["main"]};',
            'neo': f'padding:14px 24px 22px;background:{T["soft"]};border:1px solid {T["tint"]};border-radius:18px;',
            'note': f'padding:16px 20px;background:#FFFFFF;border:1.5px dashed {T["line"]};border-radius:6px;',
            'zen': f'padding:6px 0 6px 20px;border-left:2px solid {T["main"]};font-family:{SERIF};letter-spacing:1px;',
            'bento': f'padding:6px 22px 20px;background:{T["soft"]};border-radius:20px;',
            'dark': f'padding:6px 20px 18px;background:{DK_PANEL};border:1px solid {DK_BORDER};border-radius:14px;',
        }
        css = 'margin:28px 0;' + boxes[st]
        pre = ''
        if label:
            pre = tag('p', esc(label), f'margin:0 0 10px;font-size:13px;font-weight:800;letter-spacing:2px;color:{col};')
            if st in ('min', 'zen', 'mag'):
                css = f'margin:28px 0;padding:16px 4px;border-top:1px solid {col};'
            elif st == 'neo':
                css = f'margin:28px 0;padding:18px 20px;border:1px solid {T["tint"]};border-radius:16px;box-shadow:0 10px 30px rgba(17,24,39,0.06);'
        elif st not in ('min', 'note', 'zen'):
            pre = tag('p', '&gt; quote', f'margin:0 0 6px;font-size:12px;color:{col};font-family:{MONO};') if st == 'geek' else quote_svg(T['line'], 58 if st == 'neo' else 36, .55 if st in ('neo', 'dark') else 1)
        if golden:
            css = 'margin:40px 0;padding:24px 22px;text-align:center;' + boxes[st]
            if st == 'zen':
                css = f'margin:46px 0;padding:28px 10px;border-top:3px double {T["line"]};border-bottom:3px double {T["line"]};text-align:center;font-family:{SERIF};'
                body = '「' + body + '」'
            if st == 'note':
                pre = decoration(T['mark'], 84, 24)
            if st == 'neo':
                pre = quote_svg(T['line'], 96, .45)
                css = f'margin:40px 0;padding:1px 28px 30px;background:{T["soft"]};border-radius:22px;'
        return section(pre + section(body, f'font-size:{21 if golden else 15 if label else 16}px;line-height:1.9;color:{self.body};' + ('font-weight:800;' if golden else '')), css)

    def image(self, src, alt, inline=False):
        src = self.url(src, image=True)
        if not src:
            return tag('span', esc(alt or '图片地址无效'), f'color:{GRAY};')
        self.stats['images'] += 1
        picture = f'<img src="{esc(src)}" alt="{esc(alt)}" style="display:block;width:100%;max-width:100%;border-radius:{self.T["r"]}px;"/>'
        if inline:
            return picture
        cap = tag('p', esc(alt), 'margin:8px 0 0;font-size:13px;color:#94A3B8;line-height:1.6;') if alt.lower() not in ('', 'image', 'img', '图片') else ''
        return section(picture + cap, 'margin:24px 0;text-align:center;')

    def list_block(self, items):
        self.stats['lists'] += 1
        rows = []
        st, T = self.style, self.T
        for level, num, text in items:
            if num is None:
                marker = '—' if st == 'mag' else '&gt;' if st == 'geek' else decoration(self.accent, 8 if st in ('bold', 'neo') else 6, 6)
                css = f'color:{self.accent};margin-right:12px;line-height:1.9;'
            else:
                marker = f'{num:02d}' if st in ('geek', 'neo') else f'{num}.' if st in ('mag', 'min') else str(num)
                css = f'min-width:22px;line-height:22px;text-align:center;margin:4px 10px 0 0;font-size:12px;font-weight:800;color:{self.accent};'
                if st in ('std', 'bold', 'note', 'zen', 'bento', 'dark'):
                    css += f'background:{DK_PANEL if st == "dark" else T["tint"]};border-radius:{50 if st == "bold" else 6}px;'
                if st in ('geek', 'neo'):
                    css += f'font-family:{MONO};font-size:{20 if st == "neo" else 13}px;'
            rows.append(section(tag('span', marker, 'flex-shrink:0;' + css) + tag('p', self.inline(text), f'margin:0;flex:1;min-width:0;font-size:16px;line-height:1.9;color:{self.body};'), f'display:flex;margin:0 0 10px {level * 22}px;'))
        return section(''.join(rows), 'margin:0 0 22px;')

    def table(self, rows):
        self.stats['tables'] += 1
        T, st = self.T, self.style
        if st in ('min', 'mag'):
            hs, cs = f'border-bottom:2px solid {INK};color:{INK};', 'border-bottom:1px solid #E5E7EB;'
        elif st in ('geek', 'bold'):
            col = '#0B0B0C' if st == 'geek' else T['main']
            hs, cs = f'background:{col};color:#FFFFFF;', f'border:1px solid {T["tint"]};'
        elif st == 'dark':
            hs, cs = f'background:#1A2029;color:{T["line"]};', f'border-top:1px solid {DK_BORDER};'
        else:
            hs, cs = f'background:{T["soft"]};color:{T["main"]};', f'border:1px solid {T["tint"]};'
        head = '<thead><tr>' + ''.join(tag('th', self.inline(c), 'padding:11px 13px;text-align:left;font-weight:800;' + hs) for c in rows[0]) + '</tr></thead>'
        body = '<tbody>' + ''.join('<tr>' + ''.join(tag('td', self.inline(c), f'padding:11px 13px;color:{self.body};' + cs) for c in (row + [''] * len(rows[0]))[:len(rows[0])]) + '</tr>' for row in rows[1:]) + '</tbody>'
        return section(tag('table', head + body, 'width:100%;border-collapse:collapse;font-size:14px;line-height:1.7;'), f'margin:26px 0;overflow-x:auto;border-radius:{T["r"]}px;' + (f'background:{DK_PANEL};border:1px solid {DK_BORDER};' if st == 'dark' else ''))

    def code(self, lang, title, lines, fence_text=''):
        self.stats['codeBlocks'] += 1
        if lang in engine.SVG_CARD_KINDS:
            # 渲染期防幻觉提示：柱状图数值必须能在正文（去掉本围栏后）中找到。
            source_text = self.source_body.replace(fence_text, '', 1) if fence_text else self.source_body
            for warning in cards.card_warnings(lang, title, lines, source_text=source_text):
                self.warn(warning)
            result = engine.render_svg_card(lang, title, lines, style=self.style, theme=self.theme)
            for warning in result['warnings']:
                self.warn(warning)
            self.stats['svgCards'] += 1
            return result['html']
        if lang == 'longimage':
            source = next((line.strip() for line in lines if line.strip()), '')
            return self.dashed_box(title or '滑动查看长图', self.image(source, ''), height=420)
        if lang == 'promptbox':
            return self.dashed_box(title or '滑动查看完整提示词', tag('pre', esc('\n'.join(lines)), f'margin:0;white-space:pre-wrap;font-size:13px;line-height:1.85;font-family:{MONO};color:{self.body};'))
        if lang in ('prompt', 'text'):
            return section(tag('p', 'PROMPT' + (' · ' + esc(title) if title else ''), f'margin:0;padding:12px 16px;font-family:{MONO};font-size:12px;font-weight:800;color:{self.accent};') + tag('pre', esc('\n'.join(lines)), f'margin:0;padding:14px 18px;max-height:320px;overflow:auto;white-space:pre-wrap;font-family:{MONO};font-size:13px;line-height:1.85;color:{self.body};'), f'margin:26px 0;border:1px solid {DK_BORDER if self.style == "dark" else self.T["line"]};border-radius:{self.T["r"]}px;background:{DK_PANEL if self.style == "dark" else self.T["soft"]};')
        dots = ''.join(decoration(c, 11, 11) for c in ('#FF5F57', '#FEBC2E', '#28C840'))
        return section(dots + tag('pre', esc('\n'.join(lines)), f'margin:10px 0 0;overflow-x:auto;white-space:pre;font-family:{MONO};font-size:14px;line-height:1.9;color:#E5E7EB;'), 'margin:24px 0;background:#0B0B0C;border-radius:10px;padding:14px 18px 16px;')

    def dashed_box(self, title, content, height=320):
        border = DK_BORDER if self.style == 'dark' else self.T['line']
        background = DK_PANEL if self.style == 'dark' else '#FFFFFF'
        return section(tag('p', esc(title) + ' · ↕ 滑动', f'margin:0;padding:12px 16px;color:{self.accent};font-size:14px;font-weight:700;border-bottom:1.5px dashed {border};') + section(content, f'max-height:{height}px;overflow-y:auto;padding:16px 18px;'), f'margin:26px 0;border:1.5px dashed {border};border-radius:{self.T["r"]}px;background:{background};overflow:hidden;')

    def author(self):
        T, st, o = self.T, self.style, self.options
        boxes = {
            'note': f'background:#FFFFFF;border:1.5px dashed {T["line"]};border-radius:6px;',
            'zen': f'border-top:1px solid {T["line"]};border-bottom:1px solid {T["line"]};',
            'bento': f'background:{T["soft"]};border-radius:24px;',
            'dark': f'background:{DK_PANEL};border:1px solid {DK_BORDER};border-radius:16px;',
            'neo': f'background:#FFFFFF;border:1px solid {T["tint"]};border-radius:18px;box-shadow:0 12px 32px rgba(17,24,39,0.07);',
        }
        box = boxes.get(st, f'border-top:1px solid {INK};' if st in ('min', 'mag') else f'background:{T["soft"]};border:1px solid {T["tint"]};border-radius:{max(T["r"], 6) + 2}px;')
        avatar = self.url(o.get('avatarUrl'), image=True) if o.get('avatarUrl') else ''
        picture = f'<img src="{esc(avatar)}" alt="作者头像" style="display:block;width:72px;height:72px;object-fit:cover;"/>' if avatar else tag('span', esc(str(o.get('brandAuthor') or '作者')[:1]), f'font-size:28px;font-weight:800;color:{self.accent};')
        content = section(picture, f'flex-shrink:0;width:72px;height:72px;line-height:72px;text-align:center;border-radius:50%;background:{"#232A35" if st == "dark" else T["tint"]};overflow:hidden;margin-right:16px;')
        content += section(tag('p', esc(o.get('brandAuthor') or '作者'), f'margin:0;font-size:17px;font-weight:800;color:{self.ink};') + tag('p', esc(o.get('brandSlogan') or '分享知识，记录灵感'), f'margin:4px 0 0;font-size:13px;line-height:1.7;color:{self.body};') + tag('p', esc(o.get('outroCta') or '点个关注，下一篇见 →'), f'margin:8px 0 0;font-size:13px;font-weight:700;color:{self.accent};'), 'flex:1;min-width:0;')
        return section(content, 'margin:48px 0 12px;padding:20px;display:flex;align-items:center;' + box)

    def render(self, body):
        body = str(body or '').lstrip('﻿').replace('\r\n', '\n').replace('\r', '\n').replace('\x00', '')
        # Reference parser treats YAML front matter as metadata and never synthesizes a title heading.
        meta = {}
        if body.startswith('---\n'):
            end = body.find('\n---', 4)
            if end > 0:
                for line in body[4:end].splitlines():
                    if ':' in line:
                        key, value = line.split(':', 1)
                        meta[key.strip().lower()] = value.strip().strip('"').strip("'")
                body = body[end + 4:].lstrip('\n')
        self.source_body = body  # 渲染期防幻觉校验的对照原文（卡片数值必须出自这里）
        self.stats['characters'] = len(re.sub(r'\s', '', body))
        lines = body.split('\n')
        title_match = next((re.match(r'^#\s+(.+)', ln.strip()) for ln in lines if re.match(r'^#\s+(.+)', ln.strip())), None)
        title = str(meta.get('title') or self.options.get('title') or (title_match[1] if title_match else ''))
        self.stats['title'] = title or '未命名文章'
        if not title:
            self.warn('正文未提供一级标题，已使用默认标题「未命名文章」')
        meta.update({key.lower(): str(value).lower() if isinstance(value, bool) else value for key, value in self.options.items() if value is not None})
        meta['author'] = meta.get('brandauthor', meta.get('author', '林月半子的AI笔记'))
        meta['slogan'] = meta.get('brandslogan', meta.get('slogan', '我是林月半子，教你用AI干掉90%的重复劳动'))
        meta['outro_cta'] = meta.get('outrocta', meta.get('outro_cta', '点个关注，下一篇见 →'))
        result = []
        banner = self.options.get('banner', 'none')
        if banner and banner != 'none':
            if isinstance(banner, str):
                spec = {'style': banner, 'poster': {'eyebrow': str(self.options.get('brandAuthor') or '文章精选')[:10], 'title1': title[:8], 'title2': title[8:16], 'big': title[:2]}, 'card': {'eyebrow': '文章精选', 'title': title[:12], 'subtitle': str(self.options.get('brandSlogan') or '')[:16]}}
                if len(title) > (12 if banner == 'card' else 16):
                    self.warn('标题过长，开头横幅使用了缩略标题')
            else:
                spec = banner
            rendered = engine.render_banner(spec, theme=self.theme)
            result.append(rendered['html'])
            for warning in rendered['warnings']:
                self.warn(warning)
        i, part = 0, 0
        list_re = re.compile(r'^(\s*)(?:([-+*])\s+|(\d+)[.)]\s+)(.+)$')
        def block_start(line):
            s = line.strip()
            return not s or bool(re.match(r'^(#{1,6}\s|>|```|~~~|:::|!\[|\^|\[!(?:TIP|NOTE|WARN|WARNING|TLDR|GOLDEN|QUOTE)\])', s)) or bool(list_re.match(line)) or s in ('---', '***', '___')
        while i < len(lines):
            raw, s = lines[i], lines[i].strip()
            i += 1
            if not s:
                continue
            fence = re.match(r'^(`{3,}|~{3,})\s*([\w-]*)\s*(.*)$', s)
            legacy = re.match(r'^:::(svg-[\w-]+)\s*(.*)$', s)
            if fence or legacy:
                lang, caption = (fence[2].lower(), fence[3]) if fence else (legacy[1], legacy[2])
                buf = []
                closed = False
                while i < len(lines):
                    end = lines[i].strip()
                    if (legacy and end == ':::') or (fence and re.fullmatch(re.escape(fence[1][0]) + '{' + str(len(fence[1])) + ',}', end)):
                        i += 1
                        closed = True
                        break
                    buf.append(lines[i]); i += 1
                if not closed:
                    self.warn('代码围栏未闭合，已安全渲染到文末')
                fence_start = i - len(buf) - (2 if closed else 1)
                fence_text = '\n'.join(lines[fence_start:i])
                result.append(self.code(lang, caption, buf, fence_text=fence_text)); continue
            heading = re.match(r'^(#{1,6})\s+(.+?)\s*#*$', s)
            if heading:
                level, text = len(heading[1]), heading[2]
                self.stats['headings'] += 1
                if level == 1:
                    continue
                if level == 2:
                    part += 1
                    pieces = [x.strip() for x in text.split('|', 1)]
                    heading_text = re.sub(r'^(?:[一二三四五六七八九十]+[、.．]|\d+[、.．]\s*|PART\s*\d+\s*[:：]?\s*)', '', pieces[0]).strip() or pieces[0]
                    result.append(self.h2(part, heading_text, pieces[1] if len(pieces) > 1 else ''))
                else:
                    result.append(self.h3(text, level))
                continue
            if s.startswith('>'):
                quote_lines = [re.sub(r'^>\s?', '', s)]
                while i < len(lines) and lines[i].lstrip().startswith('>'):
                    quote_lines.append(re.sub(r'^>\s?', '', lines[i].strip())); i += 1
                call = re.match(r'^\[!(TIP|NOTE|WARN|WARNING|TLDR|GOLDEN|QUOTE)\]\s*(.*)', quote_lines[0])
                kind = call[1] if call else ''
                if call:
                    quote_lines[0] = call[2]
                result.append(self.quote([x for x in quote_lines if x], kind)); continue
            if s in ('---', '***', '___'):
                if self.style == 'geek':
                    inner = tag('span', '/ / /', f'font-family:{MONO};letter-spacing:6px;color:{self.T["line"]};')
                else:
                    inner = decoration(self.accent, 56 if self.style == 'bold' else 40, 1 if self.style in ('min', 'mag', 'zen') else 5)
                result.append(section(inner, 'margin:40px 0;text-align:center;')); continue
            match = list_re.match(raw)
            if match:
                items = []
                while match:
                    items.append((len(match[1].expandtabs(4)) // 2, int(match[3]) if match[3] else None, match[4]))
                    if i >= len(lines): break
                    match = list_re.match(lines[i])
                    if match: i += 1
                result.append(self.list_block(items)); continue
            if '|' in s and i < len(lines) and re.fullmatch(r'\s*\|?\s*:?-{2,}:?\s*(?:\|\s*:?-{2,}:?\s*)+\|?\s*', lines[i]):
                rows = [[x.strip() for x in s.strip('|').split('|')]]
                i += 1
                while i < len(lines) and '|' in lines[i] and lines[i].strip():
                    rows.append([x.strip() for x in lines[i].strip().strip('|').split('|')]); i += 1
                result.append(self.table(rows)); continue
            placeholder = re.fullmatch(r'\{\{image(\d+)(?:\|([^}]*))?\}\}', s, flags=re.IGNORECASE)
            if placeholder:
                result.append(self.placeholder_image(placeholder[1], placeholder[2] or '')); continue
            picture = re.fullmatch(r'!\[((?:\\.|[^\]\\])*)\]\(<?([^\s)>]+)>?(?:\s+"[^"]*")?\)', s)
            if picture:
                result.append(self.image(picture[2], picture[1])); continue
            if s.startswith('^'):
                result.append(tag('p', self.inline(s[1:].strip()), 'margin:-12px 0 24px;font-size:13px;color:#94A3B8;text-align:center;')); continue
            paragraph = [s]
            while i < len(lines) and not block_start(lines[i]):
                if i + 1 < len(lines) and '|' in lines[i] and re.match(r'^\s*\|?\s*:?-{2,}', lines[i + 1]): break
                paragraph.append(lines[i].strip()); i += 1
            text = ''.join((' ' if j and re.search(r'[A-Za-z0-9]$', paragraph[j-1]) and re.match(r'[A-Za-z0-9]', line) else '') + line for j, line in enumerate(paragraph))
            self.stats['paragraphs'] += 1
            result.append(tag('p', self.inline(text), self.p_css()))
        for image_index, url in enumerate(self.image_urls):
            value = url.get('url', '') if isinstance(url, dict) else url
            if image_index not in self.consumed_images and value and value not in body:
                result.append(self.image(value, ''))
        if meta.get('outro', 'true').lower() != 'false':
            result.append(self.author())
        if self.notes:
            refs = ''.join(tag('p', f'[{index}] {esc(text)}：{esc(url)}', 'margin:0 0 6px;font-size:12px;line-height:1.7;color:#94A3B8;word-break:break-all;') for index, (text, url) in enumerate(self.notes, 1))
            result.append(section(tag('p', '参考链接', f'font-size:13px;font-weight:700;color:{self.body};') + refs, 'margin:36px 0 0;padding-top:14px;border-top:1px solid #E5E7EB;'))
        wrap = f'padding:26px 18px 30px;background:{DK_BG};border-radius:18px;' if self.style == 'dark' else 'padding:0 4px;'
        output = section(''.join(result), "font-family:-apple-system,BlinkMacSystemFont,'PingFang SC','Helvetica Neue',sans-serif;" + wrap)
        self.stats['htmlBytes'] = len(output.encode('utf-8'))
        return {'html': output, 'warnings': self.warnings, 'stats': self.stats}


def render_studio_document(body: str, options: dict | None = None, image_urls: list | None = None) -> dict:
    return StudioRenderer(dict(options or {}), list(image_urls or [])).render(body)
