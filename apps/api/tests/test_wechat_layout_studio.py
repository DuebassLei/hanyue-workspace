"""独立工作台正文、风格、安全性与确定性测试。"""
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser

import pytest

from app.services import wechat_layout_engine as engine


BODY = '''# 排版示例

这是**重点**、==色块==、++下划线++和 `print("ok")`。

## 开始使用 | getting started

### 第一步：准备

- 一级条目
  - 二级条目
3. 第三个步骤

> [!TIP] 提示内容
> **重要说明**

> 引用内容

> [!GOLDEN] 好内容值得分享

| 名称 | 数值 |
| --- | --- |
| 示例 | **42** |

![图注](https://example.com/image.png)

[公众号](https://mp.weixin.qq.com/s/abc) 与 [参考](https://example.com/docs)

```python
print('<script>')
```

```prompt 示例提示词
【要求】清晰表达
```

---
'''


class SafetyParser(HTMLParser):
    def handle_starttag(self, tag, attrs):
        assert tag not in ('script', 'style', 'iframe', 'object', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6')
        for name, value in attrs:
            assert not name.startswith('on')
            assert name not in ('id', 'class')
            if name in ('src', 'href'):
                # GIF_URL_HERE 是参考实现的页面占位符，预览页脚本会替换成用户头像地址
                assert value == 'GIF_URL_HERE' or value.startswith(('https://', 'http://', 'asset://'))


@pytest.mark.parametrize('style', engine.STYLES)
def test_style_complete_document(style):
    rendered = engine.render_studio_document(BODY, {'style': style, 'theme': 'violet'})
    SafetyParser().feed(rendered['html'])
    assert '<table' in rendered['html']
    assert '<strong' in rendered['html']
    assert '<code' in rendered['html']
    assert rendered['stats']['tables'] == 1
    assert rendered['stats']['images'] == 1
    assert rendered['stats']['lists'] == 2  # 无序与有序列表切换时按参考语义分成两个列表块
    assert rendered['stats']['codeBlocks'] == 2
    assert rendered['stats']['headings'] == 3
    assert rendered['stats']['title'] == '排版示例'
    assert '参考链接' in rendered['html']


def test_title_chain_follows_reference_order():
    # 参考 renderPage：meta.title || H1；API 稿件标题只做兜底，不抢占 H1
    assert engine.render_studio_document('# 正文标题\n\n正文', {'title': '接口标题', 'outro': False})['stats']['title'] == '正文标题'
    assert engine.render_studio_document('---\ntitle: 前置标题\n---\n# 正文标题\n\n正文', {'title': '接口标题', 'outro': False})['stats']['title'] == '前置标题'
    assert engine.render_studio_document('正文', {'title': '接口标题', 'outro': False})['stats']['title'] == '接口标题'


def test_ten_styles_differ_without_banner_or_author():
    documents = [engine.render_studio_document('普通**正文**\n\n## 标题\n\n> 引用', {'style': style, 'outro': False})['html'] for style in engine.STYLES]
    assert len(set(documents)) == 10
    dark = documents[-1]
    assert 'background:#0F1218' in dark
    assert 'color:#C5CCD6' in dark
    assert 'color:#FFFFFF' in dark


@pytest.mark.parametrize('theme', engine.THEMES)
@pytest.mark.parametrize('banner', ['poster', 'neo', 'card'])
def test_banner_and_author_share_theme(theme, banner):
    result = engine.render_studio_document('正文', {'title': '测试标题', 'theme': theme, 'banner': banner, 'brandAuthor': '测试作者', 'brandSlogan': '测试标语'})
    assert engine.THEMES[theme]['main'] in result['html']
    assert engine.THEMES[theme]['tint'] in result['html']
    assert '测试作者' in result['html']
    SafetyParser().feed(result['html'])


@pytest.mark.parametrize('kind,lines', [
    ('svg-table', '名称|数值\nA|12'), ('svg-bars', 'A|12\nB|42'),
    ('svg-steps', '第一步|说明'), ('svg-timeline', '2026|说明'),
    ('svg-breakdown', '名称|说明'), ('svg-compare', '以前|现在\n慢|快'),
    ('svg-concept', '旧|A|B\n现在|C|D'), ('svg-phone', '场景|我|你好'),
])
def test_eight_fenced_svg_cards(kind, lines):
    result = engine.render_studio_document(f'```{kind} 卡片标题\n{lines}\n```', {'outro': False})
    assert '<svg' in result['html']
    assert '<animate' in result['html']
    assert result['stats']['svgCards'] == 1
    SafetyParser().feed(result['html'])


def test_untrusted_source_and_urls_are_safe():
    body = '''# <script>alert(1)</script>

<img src=x onerror=alert(1)>

[x](javascript:alert) ![图](data:image/png;base64,abc)

[x](https://example.com/\"onload=evil)

```html
<script>alert(1)</script>
```
'''
    result = engine.render_studio_document(body, {'brandAuthor': '<img onerror=x>', 'avatarUrl': 'javascript:alert(1)'}, ['javascript:bad', 'asset://example/image'])
    SafetyParser().feed(result['html'])
    assert '&lt;script&gt;' in result['html']
    assert '&lt;img' in result['html']
    assert 'javascript:' not in result['html']
    assert result['warnings']


def test_deterministic_and_thread_isolated():
    expected = engine.render_studio_document(BODY, {'style': 'dark', 'theme': 'rose'})
    with ThreadPoolExecutor(max_workers=4) as pool:
        outputs = list(pool.map(lambda _: engine.render_studio_document(BODY, {'style': 'dark', 'theme': 'rose'}), range(12)))
    assert all(output == expected for output in outputs)
    assert not engine.render_studio_document('# 正常标题\n\n正常正文', {'outro': False})['warnings']


def test_missing_title_unclosed_fence_and_legacy_syntax():
    result = engine.render_studio_document(':::svg-bars 标题\nA|10\n:::')
    assert result['stats']['title'] == '未命名文章'
    assert result['stats']['svgCards'] == 1
    assert result['warnings']
    result = engine.render_studio_document('```python\n<script>')
    assert '&lt;script&gt;' in result['html']
    assert any('未闭合' in warning for warning in result['warnings'])


@pytest.mark.parametrize('style', engine.STYLES)
def test_old_image_placeholders_and_caption_are_consumed(style):
    body = '# 图片文章\n\n{{image1|第一张图注}}\n\n正文后{{image2}}结束'
    urls = ['https://example.com/one.png', {'url': 'https://example.com/two.png'}]
    result = engine.render_studio_document(body, {'style': style, 'outro': False}, urls)
    assert '{{image' not in result['html']
    assert result['html'].count('src="https://example.com/one.png"') == 1
    assert result['html'].count('src="https://example.com/two.png"') == 1
    assert '第一张图注' in result['html']
    assert result['stats']['images'] == 2
    SafetyParser().feed(result['html'])


def test_missing_or_unsafe_image_placeholder_warns_without_raw_marker():
    result = engine.render_studio_document('{{image1|缺失图}}\n\n{{image2}}\n\n{{image0}}', {'outro': False}, ['javascript:evil'])
    assert '{{image' not in result['html']
    assert 'javascript:' not in result['html']
    assert result['warnings']
    assert result['stats']['images'] == 0
    SafetyParser().feed(result['html'])


def test_reference_quote_and_scrolling_cards():
    body = '# 示例\n\n## 一、主标题|subtitle\n\n> [!QUOTE] 金句内容\n\n```promptbox 提示词\n内容\n```\n\n```longimage 长图\nhttps://example.com/long.png\n```'
    result = engine.render_studio_document(body, {'style': 'zen', 'outro': False})
    assert '3px double' in result['html']
    assert 'SUBTITLE' in result['html']
    assert '一、主标题' not in result['html']
    assert 'max-height:320px' in result['html']
    assert 'max-height:420px' in result['html']
    SafetyParser().feed(result['html'])
