"""微信公众号排版：主题 tokens、Markdown 超集解析、内联样式渲染、兼容性检查。

为什么不用通用 Markdown 渲染器：
- 微信正文只保留**内联样式**，`<style>`/class 会被剥离，必须逐元素写 style；
- 微信会重置 `h1~h6`、剥离 `div`，所以标题用 `section + span/strong` 模拟；
- 正文图片只能是微信域名，链接指向外部域会被降级；
- 深色模式下微信会反色，纯白/纯黑背景会出现色块。

边界：预览与推送**共用同一个渲染函数**（`render_document`），保证所见即所得。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# ---------- 主题 ----------

#: 字号/行高/字距等数值型 token 的允许范围，越界直接拒绝（防注入与离谱值）。
NUMERIC_RANGES: dict[str, tuple[float, float]] = {
    "bodySize": (13, 18),
    "bodyLineHeight": (1.4, 2.2),
    "letterSpacing": (0, 2),
    "paragraphGap": (0.8, 3),
    "indent": (0, 2),
    "imageRadius": (0, 20),
}

ENUMS: dict[str, set[str]] = {
    "align": {"left", "justify", "center"},
    "h1Style": {"bar", "block", "number", "center", "underline", "number-divider", "capsule", "banner", "margin-note", "seal", "lead-in", "eyebrow", "kicker", "framed", "rail"},
    "h2Style": {"tag", "underline", "plain", "bar", "strong", "capsule", "hairline"},
    "listMarker": {"dot", "square", "number", "circle"},
}

#: `:::name` 信息块。行式块每行用 | 分列；自由块整段当正文。
ROW_BLOCKS = {
    "stat": 2,
    "steps": 2,
    "compare": 2,
    "layers": 2,
    "checklist": 2,
    "support": 2,
    "cheer": 2,
    "mini-support": 1,
}
FREE_BLOCKS = {"card", "follow", "sign", "lead", "quote-card"}
FENCE_BLOCKS = ROW_BLOCKS.keys() | FREE_BLOCKS

#: 小组件注册表：排版页「组件库」与发布抽屉的区块按钮都从这里取。
#: id 必须与 FENCE_BLOCKS 一一对应（tests/test_wechat_layout.py 有漂移守卫）；
#: snippet 为作者视角的插入语法，sampleBody 为可直接渲染的完整示例（必须零告警）。
LAYOUT_COMPONENTS: tuple[dict, ...] = (
    {
        "id": "lead",
        "label": "导语",
        "description": "主色短条 + 大号开篇，先给结论再展开",
        "category": "开篇",
        "snippet": "\n\n:::lead\n先把结论写在这里。\n:::\n",
        "sampleBody": ":::lead\n排版的尽头是克制：字号、行高、留白定好，内容自然好看。\n:::",
    },
    {
        "id": "card",
        "label": "强调卡",
        "description": "浅主色底卡片，适合本段小结与注意事项",
        "category": "强调",
        "snippet": "\n\n:::card 小结\n把重点收在这里。\n:::\n",
        "sampleBody": ":::card 本节小结\n重点不是花哨，而是稳定与克制：全文样式只由主题 tokens 决定。\n:::",
    },
    {
        "id": "stat",
        "label": "数据",
        "description": "大号数字 + 灰色说明，每行 `数字 | 说明`",
        "category": "数据与对比",
        "snippet": "\n\n:::stat\n12% | 说明\n:::\n",
        "sampleBody": ":::stat\n42% | 平均打开率提升\n3.1 倍 | 转发数增长\n:::",
    },
    {
        "id": "steps",
        "label": "步骤",
        "description": "主色圆圈自动编号，每行 `名称 | 说明`",
        "category": "结构",
        "snippet": "\n\n:::steps\n第一步 | 说明\n第二步 | 说明\n:::\n",
        "sampleBody": ":::steps\n定选题 | 先确认写给谁看\n搭骨架 | 结论放在第一段\n补证据 | 每个判断给出处\n:::",
    },
    {
        "id": "compare",
        "label": "对比",
        "description": "左右两列卡片；标题行写在围栏名后：`:::compare 左 / 右`",
        "category": "数据与对比",
        "snippet": "\n\n:::compare 左边 / 右边\n旧做法 | 新做法\n:::\n",
        "sampleBody": ":::compare 手动排版 / 主题排版\n改样式翻遍全文 | 换个主题整篇换装\n:::",
    },
    {
        "id": "layers",
        "label": "分层",
        "description": "叠放的圆角卡片，每行 `标签 | 正文`",
        "category": "结构",
        "snippet": "\n\n:::layers\n标签 | 说明\n:::\n",
        "sampleBody": ":::layers\n表层 | 读者看到的字号与配色\n中层 | 主题 tokens 决定的骨架\n底层 | 微信白名单约束的渲染\n:::",
    },
    {
        "id": "checklist",
        "label": "清单",
        "description": "✓ / ○ / ! 状态标记，每行 `done|todo|warn | 事项`",
        "category": "结构",
        "snippet": "\n\n:::checklist\ndone | 已完成的事\ntodo | 还没做的事\n:::\n",
        "sampleBody": ":::checklist\ndone | 核对正文数字\ndone | 选好排版主题\ntodo | 补一张封面图\n:::",
    },
    {
        "id": "quote-card",
        "label": "金句",
        "description": "大引号 + 大字重，出处写在围栏名后",
        "category": "强调",
        "snippet": "\n\n:::quote-card 出处\n把金句写在这里。\n:::\n",
        "sampleBody": ":::quote-card 一位资深编辑\n公众号排版的尽头，是把读者的注意力当回事。\n:::",
    },
    {
        "id": "follow",
        "label": "引导关注",
        "description": "文末关注签：主题色标记、关注提示与读者互动文案",
        "category": "互动",
        "snippet": "\n\n:::follow\n点个关注，后续更新就不会错过。\n:::\n",
        "sampleBody": ":::follow\n喜欢这样的内容，就点个关注。\n后续更新与实用技巧，我们会继续整理分享。\n:::",
    },
    {
        "id": "sign",
        "label": "文末署名",
        "description": "细分割线 + 灰色小字，落在最后一屏",
        "category": "互动",
        "snippet": "\n\n:::sign\n作者 | 你的公众号名\n:::\n",
        "sampleBody": ":::sign\n狸知 · 公众号排版样文\n:::",
    },
    {
        "id": "support",
        "label": "支持一下",
        "description": "文末三列互动卡：点赞、转发、推荐。围栏名后写 `标题 / 副标题`，每行 `动作 | 说明`",
        "category": "互动",
        "snippet": "\n\n:::support 喜欢就支持一下吧 / 你的每一次互动，都是我持续创作的动力\n点赞 | 喜欢就点个赞吧，感谢你的支持\n转发 | 分享给更多朋友\n推荐 | 推荐给身边的人\n:::\n",
        "sampleBody": ":::support 喜欢就支持一下吧 / 你的每一次互动，都是我持续创作的动力\n点赞 | 喜欢就点个赞吧，感谢你的支持\n转发 | 分享给更多朋友，让美好一起传递\n推荐 | 推荐给身边的人，让更多人看到\n:::",
    },
    {
        "id": "cheer",
        "label": "暖心支持卡",
        "description": "手绘暖色支持卡：爱心标题 / 胶囊副标题 / 气泡 + 三色互动卡；围栏名后写 `标题 / 副标题 / 气泡`，每行 `动作 | 说明`，可选 `插图 | {{image1}}`",
        "category": "互动",
        "snippet": "\n\n:::cheer 喜欢就支持一下吧！ / 你的每一次互动，都是我持续创作的动力～ / 有你真好！\n点赞 | 喜欢就点个赞吧，感谢你的支持\n转发 | 分享给更多朋友，让美好一起传递\n推荐 | 推荐给身边的人，让更多人看到\n:::\n",
        "sampleBody": ":::cheer 喜欢就支持一下吧！ / 你的每一次互动，都是我持续创作的动力～ / 有你真好！\n点赞 | 喜欢就点个赞吧，感谢你的支持\n转发 | 分享给更多朋友，让美好一起传递\n推荐 | 推荐给身边的人，让更多人看到\n:::",
    },
    {
        "id": "mini-support",
        "label": "轻互动",
        "description": "极简版：只留点赞 / 转发 / 推荐图标与动作名，样式跟随主题；围栏名后可写一行小标题，每行一个动作",
        "category": "互动",
        "snippet": "\n\n:::mini-support\n点赞\n转发\n推荐\n:::\n",
        "sampleBody": ":::mini-support 支持一下\n点赞\n转发\n推荐\n:::",
    },
)

_COLOR_KEYS = (
    "textColor",
    "mutedColor",
    "primaryColor",
    "primarySoft",
    "quoteBg",
    "dividerColor",
    "strongColor",
)
_HEX = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
_RGBA = re.compile(r"^rgba?\(\s*\d{1,3}\s*,\s*\d{1,3}\s*,\s*\d{1,3}\s*(?:,\s*(?:0|1|0?\.\d+)\s*)?\)$")

FONT_STACK = (
    "-apple-system, BlinkMacSystemFont, 'PingFang SC', 'Hiragino Sans GB', "
    "'Microsoft YaHei', sans-serif"
)


def _base_tokens(**overrides) -> dict:
    tokens = {
        "fontFamily": FONT_STACK,
        "bodySize": 16,
        "bodyLineHeight": 1.8,
        "letterSpacing": 0.5,
        "paragraphGap": 1.5,
        "textColor": "#333333",
        "mutedColor": "#888888",
        "primaryColor": "#0F6E56",
        "primarySoft": "#E1F5EE",
        "quoteBg": "#F7F7F7",
        "dividerColor": "#E5E5E5",
        "strongColor": "#333333",
        "align": "justify",
        "indent": 0,
        "imageRadius": 6,
        "h1Style": "bar",
        "h2Style": "tag",
        "listMarker": "dot",
    }
    tokens.update(overrides)
    return tokens


#: 内置预设。id 稳定，历史任务的 layout_snapshot 里记录的是 tokens 快照而非 id。
PRESET_THEMES: dict[str, dict] = {
    "ink-green": {
        "id": "ink-green",
        "name": "墨绿简约",
        "description": "左侧竖线标题，适合知识、职场、干货",
        "tokens": _base_tokens(),
    },
    "warm-orange": {
        "id": "warm-orange",
        "name": "暖橙生活",
        "description": "居中大字标题，适合生活、情感、美食",
        "tokens": _base_tokens(
            primaryColor="#BA7517",
            primarySoft="#FAEEDA",
            textColor="#3A2E22",
            quoteBg="#FCF7EF",
            h1Style="center",
            h2Style="underline",
            align="left",
            bodyLineHeight=1.9,
        ),
    },
    "mono": {
        "id": "mono",
        "name": "极简黑白",
        "description": "序号标题 + 细线，适合深度长文与访谈",
        "tokens": _base_tokens(
            primaryColor="#2C2C2A",
            primarySoft="#F1EFE8",
            textColor="#2C2C2A",
            quoteBg="#F7F7F7",
            h1Style="number",
            h2Style="plain",
            letterSpacing=0.3,
        ),
    },
    "terracotta-warm": {
        "id": "terracotta-warm",
        "name": "暖陶土",
        "description": "陶土主色 + 下划线主标题 + 竖线副标题，适合工具推荐、产品测评",
        "tokens": _base_tokens(
            bodySize=15,
            bodyLineHeight=2.0,
            letterSpacing=0,
            paragraphGap=1.3,
            textColor="#3F3F3F",
            mutedColor="#999999",
            primaryColor="#D97757",
            primarySoft="#F7ECE5",
            quoteBg="#FAF9F5",
            dividerColor="#E4B1A0",
            strongColor="#D97757",
            align="left",
            indent=0,
            imageRadius=8,
            h1Style="underline",
            h2Style="bar",
            listMarker="dot",
        ),
    },
    "blue-business": {
        "id": "blue-business",
        "name": "商务蓝",
        "description": "深蓝编号标题 + 深海军蓝正文强调，适合职场干货、产品教程、商业分析",
        "tokens": _base_tokens(
            bodySize=15,
            bodyLineHeight=1.9,
            letterSpacing=0.3,
            paragraphGap=1.4,
            textColor="#4B5563",
            mutedColor="#758095",
            primaryColor="#003BB3",
            primarySoft="#F6F8FB",
            quoteBg="#F6F8FB",
            dividerColor="#DCE3EE",
            strongColor="#263344",
            align="justify",
            indent=0,
            imageRadius=0,
            h1Style="number-divider",
            h2Style="strong",
            listMarker="dot",
        ),
    },
    # 下面四套对齐参考项目的骨架气质：亲和 / 资讯 / 书卷 / 杂志。
    # 中文字体名在微信里无效，辨识度只放在几何和配色上。
    "news-ink": {
        "id": "news-ink",
        "name": "资讯",
        "description": "大字、松字距、发丝线标题，适合快讯、评测、行业观察",
        "tokens": _base_tokens(
            bodySize=17,
            bodyLineHeight=2.0,
            letterSpacing=0.8,
            paragraphGap=1.6,
            textColor="#111111",
            mutedColor="#8A8F98",
            primaryColor="#1F6F5F",
            primarySoft="#E7F3EF",
            quoteBg="#F4F5F7",
            dividerColor="#E3E6EA",
            strongColor="#1F6F5F",
            align="left",
            imageRadius=12,
            h1Style="underline",
            h2Style="hairline",
            listMarker="dot",
        ),
    },
    "paper-cinnabar": {
        "id": "paper-cinnabar",
        "name": "书卷",
        "description": "居中标题、色条、收紧段距，适合人文、读书、深度长文",
        "tokens": _base_tokens(
            bodySize=15,
            bodyLineHeight=1.95,
            letterSpacing=0.3,
            paragraphGap=1.1,
            textColor="#222222",
            mutedColor="#8A8F98",
            primaryColor="#9E3D30",
            primarySoft="#F8EEEB",
            quoteBg="#F7F5F2",
            dividerColor="#D9DDE1",
            strongColor="#9E3D30",
            align="left",
            imageRadius=0,
            h1Style="center",
            h2Style="plain",
            listMarker="circle",
        ),
    },
    "friendly-pill": {
        "id": "friendly-pill",
        "name": "亲和",
        "description": "胶囊标题、大圆角，适合教程和面向新手的解释",
        "tokens": _base_tokens(
            bodySize=16,
            bodyLineHeight=1.95,
            letterSpacing=0.5,
            paragraphGap=1.5,
            textColor="#1F1F1F",
            mutedColor="#8A8F98",
            primaryColor="#6B5B95",
            primarySoft="#F1EEF6",
            quoteBg="#F5F6F8",
            dividerColor="#E6E4EE",
            strongColor="#1F1F1F",
            align="left",
            imageRadius=16,
            h1Style="capsule",
            h2Style="capsule",
            listMarker="dot",
        ),
    },
    "notebook": {
        "id": "notebook",
        "name": "手帐",
        "description": "短色块贴在标题左侧，方块列表、段距紧，适合教程笔记和清单",
        "tokens": _base_tokens(
            bodySize=15,
            bodyLineHeight=1.75,
            letterSpacing=0.2,
            paragraphGap=0.9,
            textColor="#3D342B",
            mutedColor="#8C8175",
            primaryColor="#C4553A",
            primarySoft="#F8E6DF",
            quoteBg="#F6F1EA",
            dividerColor="#E4D8CC",
            strongColor="#C4553A",
            align="left",
            imageRadius=4,
            h1Style="block",
            h2Style="bar",
            listMarker="square",
        ),
    },
    "dispatch": {
        "id": "dispatch",
        "name": "公文",
        "description": "通栏色条标题、首行缩进、两端对齐，适合通知、报告和政务说明",
        "tokens": _base_tokens(
            bodySize=15,
            bodyLineHeight=1.9,
            letterSpacing=0.4,
            paragraphGap=1.2,
            textColor="#2C2C2A",
            mutedColor="#6F6A62",
            primaryColor="#8C2F2F",
            primarySoft="#F6EEEE",
            quoteBg="#F7F4EF",
            dividerColor="#D9D3C8",
            strongColor="#8C2F2F",
            align="justify",
            indent=2,
            imageRadius=0,
            h1Style="banner",
            h2Style="hairline",
            listMarker="number",
        ),
    },
    "poster": {
        "id": "poster",
        "name": "海报",
        "description": "超大标题几乎不装饰，段距拉得很开，适合封面导语和品牌短文",
        "tokens": _base_tokens(
            bodySize=17,
            bodyLineHeight=1.85,
            letterSpacing=0.6,
            paragraphGap=2.4,
            textColor="#1A1A1A",
            mutedColor="#8A8680",
            primaryColor="#1A1A1A",
            primarySoft="#F2F0EC",
            quoteBg="#F4F2EE",
            dividerColor="#1A1A1A",
            strongColor="#1A1A1A",
            align="left",
            imageRadius=0,
            h1Style="center",
            h2Style="underline",
            listMarker="circle",
        ),
    },
    "margin-note": {
        "id": "margin-note",
        "name": "旁注",
        "description": "章节号竖排在左侧窄色带里，标题靠右，适合连载和分章长文",
        "tokens": _base_tokens(
            bodySize=15,
            bodyLineHeight=1.9,
            letterSpacing=0.4,
            paragraphGap=1.4,
            textColor="#2E2A32",
            mutedColor="#8A8490",
            primaryColor="#5C4B8A",
            primarySoft="#F0ECF6",
            quoteBg="#F6F4F8",
            dividerColor="#DDD6E6",
            strongColor="#5C4B8A",
            align="left",
            imageRadius=2,
            h1Style="margin-note",
            h2Style="plain",
            listMarker="circle",
        ),
    },
    "seal": {
        "id": "seal",
        "name": "印章",
        "description": "标题居中，四角断开的双线方框，适合信笺、书评和题记",
        "tokens": _base_tokens(
            bodySize=15,
            bodyLineHeight=2.0,
            letterSpacing=0.8,
            paragraphGap=1.6,
            textColor="#3A2E24",
            mutedColor="#8C7B68",
            primaryColor="#A63D2F",
            primarySoft="#F8EEEA",
            quoteBg="#F8F3EC",
            dividerColor="#E2D2C4",
            strongColor="#A63D2F",
            align="left",
            indent=2,
            imageRadius=0,
            h1Style="seal",
            h2Style="plain",
            listMarker="circle",
        ),
    },
    "lead-in": {
        "id": "lead-in",
        "name": "拉引",
        "description": "标题几乎不装饰，紧跟的第一段放大成浅色导语，适合开篇和特稿",
        "tokens": _base_tokens(
            bodySize=16,
            bodyLineHeight=1.9,
            letterSpacing=0.3,
            paragraphGap=1.5,
            textColor="#262626",
            mutedColor="#8A8680",
            primaryColor="#1F4E46",
            primarySoft="#E7F1EE",
            quoteBg="#F3F5F4",
            dividerColor="#D5DDD9",
            strongColor="#1F4E46",
            align="left",
            imageRadius=0,
            h1Style="lead-in",
            h2Style="hairline",
            listMarker="dot",
        ),
    },
    "eyebrow": {
        "id": "eyebrow",
        "name": "眉题",
        "description": "标题上方一条主色小标签，正文两端对齐，适合专栏和特稿",
        "tokens": _base_tokens(
            bodySize=16,
            bodyLineHeight=1.95,
            letterSpacing=0.4,
            paragraphGap=1.6,
            textColor="#2A2622",
            mutedColor="#8A8175",
            primaryColor="#8A5A2B",
            primarySoft="#F6EFE4",
            quoteBg="#F7F3EC",
            dividerColor="#E4D8C4",
            strongColor="#8A5A2B",
            align="justify",
            imageRadius=2,
            h1Style="eyebrow",
            h2Style="hairline",
            listMarker="circle",
        ),
    },
    "kicker": {
        "id": "kicker",
        "name": "题签",
        "description": "章节号单独成行，标题居中，段距拉开，适合连载和分章",
        "tokens": _base_tokens(
            bodySize=16,
            bodyLineHeight=2.0,
            letterSpacing=0.6,
            paragraphGap=1.8,
            textColor="#24201C",
            mutedColor="#8C8478",
            primaryColor="#9A3412",
            primarySoft="#F8EDE6",
            quoteBg="#F6F1EA",
            dividerColor="#E6D9CC",
            strongColor="#9A3412",
            align="left",
            imageRadius=0,
            h1Style="kicker",
            h2Style="underline",
            listMarker="circle",
        ),
    },
    "framed": {
        "id": "framed",
        "name": "题框",
        "description": "标题居中，上下两条细线夹住，适合卷首和序言",
        "tokens": _base_tokens(
            bodySize=15,
            bodyLineHeight=2.0,
            letterSpacing=0.5,
            paragraphGap=1.5,
            textColor="#2C2824",
            mutedColor="#8A8278",
            primaryColor="#7A3E2E",
            primarySoft="#F6EEEA",
            quoteBg="#F6F2EC",
            dividerColor="#D9CFC4",
            strongColor="#7A3E2E",
            align="justify",
            indent=2,
            imageRadius=0,
            h1Style="framed",
            h2Style="plain",
            listMarker="circle",
        ),
    },
    "rail": {
        "id": "rail",
        "name": "侧栏",
        "description": "整段标题贴在左侧色带上，适合观点和评论",
        "tokens": _base_tokens(
            bodySize=15,
            bodyLineHeight=1.9,
            letterSpacing=0.3,
            paragraphGap=1.4,
            textColor="#1E2A2E",
            mutedColor="#6E8086",
            primaryColor="#1F6F78",
            primarySoft="#E6F2F3",
            quoteBg="#F2F6F6",
            dividerColor="#D2E0E1",
            strongColor="#1F6F78",
            align="left",
            imageRadius=4,
            h1Style="rail",
            h2Style="bar",
            listMarker="dot",
        ),
    },
}

DEFAULT_THEME_ID = "ink-green"


class ThemeError(ValueError):
    """主题 tokens 不合法。"""


def _check_color(key: str, value: str) -> str:
    raw = (value or "").strip()
    if not (_HEX.match(raw) or _RGBA.match(raw)):
        raise ThemeError(f"{key} 必须是 #rrggbb 或 rgba() 颜色，收到：{value!r}")
    lowered = raw.lower()
    if "#ffffff" in lowered or "#fff" == lowered or "#000" == lowered or "#000000" in lowered:
        # 不是硬错误：给出可用的同时提示深色模式风险（由 lint 呈现）
        pass
    return raw


def known_token_keys() -> set[str]:
    return {"fontFamily", *NUMERIC_RANGES, *ENUMS, *_COLOR_KEYS}


#: 前端主题编辑器展示的中文名、控件参数与分组。校验范围仍以 NUMERIC_RANGES/ENUMS 为准，
#: 这里只是描述元数据——新增 token 时如果漏配标签，测试会抓住（token_schema 必须覆盖全部键）。
_TOKEN_LABELS: dict[str, dict] = {
    "fontFamily": {"label": "字体栈", "group": "文字", "kind": "text"},
    "bodySize": {"label": "正文字号", "group": "文字", "kind": "number", "step": 1},
    "bodyLineHeight": {"label": "行高", "group": "文字", "kind": "number", "step": 0.05},
    "letterSpacing": {"label": "字距", "group": "文字", "kind": "number", "step": 0.1},
    "paragraphGap": {"label": "段间距", "group": "版式", "kind": "number", "step": 0.1},
    "indent": {"label": "首行缩进", "group": "版式", "kind": "number", "step": 0.5},
    "imageRadius": {"label": "图片圆角", "group": "版式", "kind": "number", "step": 1},
    "textColor": {"label": "正文颜色", "group": "配色", "kind": "color"},
    "mutedColor": {"label": "辅助色", "group": "配色", "kind": "color"},
    "primaryColor": {"label": "主色", "group": "配色", "kind": "color"},
    "primarySoft": {"label": "浅主色底", "group": "配色", "kind": "color"},
    "quoteBg": {"label": "引用底色", "group": "配色", "kind": "color"},
    "dividerColor": {"label": "分割线色", "group": "配色", "kind": "color"},
    "strongColor": {"label": "加粗色", "group": "配色", "kind": "color"},
    "align": {"label": "对齐", "group": "版式", "kind": "enum"},
    "h1Style": {"label": "一级标题样式", "group": "标题样式", "kind": "enum"},
    "h2Style": {"label": "二级标题样式", "group": "标题样式", "kind": "enum"},
    "listMarker": {"label": "列表符号", "group": "版式", "kind": "enum"},
}

_ENUM_OPTION_LABELS: dict[str, dict[str, str]] = {
    "align": {"left": "左对齐", "justify": "两端对齐", "center": "居中"},
    "h1Style": {
        "bar": "左侧竖线",
        "block": "色块标签",
        "number": "序号编号",
        "center": "居中大字",
        "underline": "下划线",
        "number-divider": "大号编号+分割线",
        "capsule": "胶囊标题",
        "banner": "通栏色条",
        "margin-note": "旁注色带",
        "seal": "印章方框",
        "lead-in": "标题加导语",
        "eyebrow": "眉题小标签",
        "kicker": "题签编号",
        "framed": "上下线题框",
        "rail": "左侧色带",
    },
    "h2Style": {
        "tag": "标签底色",
        "underline": "下划线",
        "plain": "纯加粗",
        "bar": "左侧竖线",
        "strong": "强调加粗",
        "capsule": "胶囊标题",
        "hairline": "发丝线",
    },
    "listMarker": {"dot": "圆点", "square": "方块", "number": "数字", "circle": "空心圆"},
}


def token_schema() -> list[dict]:
    """主题编辑器的字段描述：key/label/group/kind + 范围或选项 + 默认值。

    数值范围与枚举选项从校验源（NUMERIC_RANGES/ENUMS）派生，不重复维护；
    前端只渲染，不再写第二套校验。
    """
    base = _base_tokens()
    fields: list[dict] = []
    # 保持「配色 → 文字 → 标题样式 → 版式」的分组顺序，组内按注册顺序
    for key, spec in _TOKEN_LABELS.items():
        field: dict = {"key": key, **spec}
        if key in NUMERIC_RANGES:
            low, high = NUMERIC_RANGES[key]
            field["min"], field["max"] = low, high
        elif key in ENUMS:
            option_labels = _ENUM_OPTION_LABELS.get(key, {})
            field["options"] = [
                {"value": value, "label": option_labels.get(value, value)}
                for value in sorted(ENUMS[key])
            ]
        field["default"] = base[key]
        fields.append(field)
    return fields


def normalize_tokens(raw: dict | None) -> dict:
    """校验并归一化主题 tokens；未知键报错（不能静默丢弃，否则用户以为生效了）。"""
    tokens = _base_tokens()
    if not raw:
        return tokens
    unknown = [key for key in raw if key not in known_token_keys()]
    if unknown:
        raise ThemeError(
            f"不认识的排版参数：{'、'.join(sorted(unknown))}；"
            f"可用参数：{'、'.join(sorted(known_token_keys()))}"
        )
    for key, value in raw.items():
        if key == "fontFamily":
            # 只允许系统字体栈，禁止外链字体（微信也不支持 web font）
            if not isinstance(value, str) or "url(" in value.lower() or "@" in value:
                raise ThemeError("fontFamily 只允许系统字体栈，不能引用外部字体")
            tokens[key] = value
            continue
        if key in NUMERIC_RANGES:
            try:
                number = float(value)
            except (TypeError, ValueError) as exc:
                raise ThemeError(f"{key} 必须是数字") from exc
            low, high = NUMERIC_RANGES[key]
            if not (low <= number <= high):
                raise ThemeError(f"{key} 必须在 {low}~{high} 之间，收到 {number}")
            tokens[key] = number
            continue
        if key in ENUMS:
            if value not in ENUMS[key]:
                raise ThemeError(f"{key} 只能是 {'/'.join(sorted(ENUMS[key]))}")
            tokens[key] = value
            continue
        if key in _COLOR_KEYS:
            tokens[key] = _check_color(key, str(value))
            continue
    # 未显式指定加粗色时，回退到正文色，保持现有主题的视觉不变
    if not raw or "strongColor" not in raw:
        tokens["strongColor"] = tokens["textColor"]
    return tokens


def theme_by_id(theme_id: str | None) -> dict:
    """返回预设主题；未知 id 回退默认预设（调用方负责告知用户）。"""
    return PRESET_THEMES.get(theme_id or DEFAULT_THEME_ID) or PRESET_THEMES[DEFAULT_THEME_ID]


# ---------- 行内标记 ----------

_INLINE_RULES: tuple[tuple[re.Pattern, str], ...] = (
    (re.compile(r"\*\*(.+?)\*\*"), "strong"),
    (re.compile(r"`([^`]+)`"), "code"),
    (re.compile(r"==(.+?)=="), "mark"),
    (re.compile(r"~~(.+?)~~"), "del"),
    (re.compile(r"(?<!\*)\*([^*\n]+)\*(?!\*)"), "em"),
)
_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
_IMAGE_PLACEHOLDER = re.compile(r"\{\{\s*image\s*(\d+)\s*(?:\|\s*([^}]*?)\s*)?\}\}", re.IGNORECASE)


def escape_text(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


#: 数值型 token 写进 CSS 时必须补 px 的属性（否则 `font-size:16` 是无效声明）
_LENGTH_KEYS = {
    "font-size",
    "letter-spacing",
    "border-radius",
    "width",
    "height",
    "margin-right",
    "padding-top",
    "padding-bottom",
    "text-indent",
}


def _style(pairs: dict) -> str:
    items: list[str] = []
    for key, value in pairs.items():
        if value is None or value == "":
            continue
        if isinstance(value, (int, float)) and not isinstance(value, bool) and key in _LENGTH_KEYS:
            value = f"{value}px"
        items.append(f"{key}:{value}")
    return ";".join(items)


def render_inline(text: str, tokens: dict, warnings: list[dict], *, where: str = "") -> str:
    """行内标记 → 内联样式 HTML。所有文本先转义，标记替换只作用于纯文本片段。"""
    escaped = escape_text(text)
    # 程序化标签占位，避免后续规则误伤已生成的标签
    slots: list[str] = []

    def stash(html: str) -> str:
        slots.append(html)
        return f"\x00{len(slots) - 1}\x00"

    size = tokens["bodySize"]
    color = tokens["textColor"]
    primary = tokens["primaryColor"]
    strong_color = tokens.get("strongColor", color)

    for pattern, kind in _INLINE_RULES:
        def repl(match: re.Match, kind=kind) -> str:
            content = match.group(1)
            if kind == "strong":
                return stash(f'<strong style="{_style({"font-weight": 600, "color": strong_color})}">{content}</strong>')
            if kind == "em":
                return stash(f'<em style="{_style({"font-style": "italic"})}">{content}</em>')
            if kind == "mark":
                return stash(
                    f'<span style="{_style({"background": tokens["primarySoft"], "padding": "0 3px", "border-radius": 3})}">{content}</span>'
                )
            if kind == "del":
                return stash(f'<span style="{_style({"text-decoration": "line-through", "color": tokens["mutedColor"]})}">{content}</span>')
            return stash(
                f'<span style="{_style({"font-family": "Menlo, Consolas, monospace", "font-size": size - 1, "background": tokens["quoteBg"], "padding": "1px 4px", "border-radius": 3, "color": primary})}">{content}</span>'
            )

        escaped = pattern.sub(repl, escaped)

    # 外链在公众号正文里不可点击，降级为「文本（链接）」并给出告警
    def link_repl(match: re.Match) -> str:
        label, url = match.group(1), match.group(2)
        warnings.append(
            {
                "field": where or "body",
                "level": "warning",
                "message": f"外部链接在公众号正文中不可点击，已降级为文本：{url}",
            }
        )
        return stash(f'{label}<span style="{_style({"color": tokens["mutedColor"], "font-size": size - 2})}">（{url}）</span>')

    escaped = _LINK.sub(link_repl, escaped)

    restored = escaped
    for index, html in enumerate(slots):
        restored = restored.replace(f"\x00{index}\x00", html)
    return restored


# ---------- 区块解析 ----------

@dataclass
class Block:
    kind: str
    text: str = ""
    level: int = 0
    items: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)
    image_index: int = 0
    caption: str = ""
    meta: str = ""


def parse_blocks(body: str, warnings: list[dict]) -> list[Block]:
    """把正文解析成区块。规则保守：不认识的结构保留为段落，不静默丢内容。"""
    text = (body or "").replace("\r\n", "\n").strip()
    if not text:
        return []
    lines = text.split("\n")
    blocks: list[Block] = []
    buffer: list[str] = []
    index = 0

    def flush() -> None:
        if buffer:
            blocks.append(Block(kind="paragraph", text="\n".join(buffer).strip()))
            buffer.clear()

    while index < len(lines):
        line = lines[index]
        stripped = line.strip()

        if stripped.startswith("```"):
            flush()
            fence: list[str] = []
            index += 1
            while index < len(lines) and not lines[index].strip().startswith("```"):
                fence.append(lines[index])
                index += 1
            if index >= len(lines):
                warnings.append(
                    {"field": "body", "level": "warning", "message": "代码块未闭合，已按原样输出"}
                )
            blocks.append(Block(kind="code", text="\n".join(fence)))
            index += 1
            continue

        if stripped.startswith(":::"):
            flush()
            header = stripped[3:].strip()
            name, _, meta = header.partition(" ")
            if name not in FENCE_BLOCKS:
                warnings.append(
                    {
                        "field": "body",
                        "level": "warning",
                        "message": f"未知区块 :::{name}，已按普通段落输出",
                    }
                )
                buffer.append(line)
                index += 1
                continue
            collected: list[str] = []
            index += 1
            closed = False
            while index < len(lines):
                if lines[index].strip() == ":::":
                    closed = True
                    index += 1
                    break
                collected.append(lines[index])
                index += 1
            if not closed:
                warnings.append(
                    {
                        "field": "body",
                        "level": "warning",
                        "message": f"区块 :::{name} 未闭合，内容仍会输出但样式按普通区块处理",
                    }
                )
            body_text = "\n".join(collected).strip()
            if name in ROW_BLOCKS:
                rows = _parse_rows(body_text, ROW_BLOCKS[name], name, warnings)
                blocks.append(Block(kind=name, rows=rows, meta=meta.strip()))
            else:
                blocks.append(Block(kind=name, text=body_text, meta=meta.strip()))
            continue

        if stripped in {"---", "***", "___"}:
            flush()
            blocks.append(Block(kind="divider"))
            index += 1
            continue

        match = _IMAGE_PLACEHOLDER.fullmatch(stripped)
        if match:
            flush()
            blocks.append(
                Block(
                    kind="image",
                    image_index=int(match.group(1)),
                    caption=(match.group(2) or "").strip(),
                )
            )
            index += 1
            continue

        for marker, level in (("### ", 3), ("## ", 2), ("# ", 1)):
            if stripped.startswith(marker):
                flush()
                blocks.append(Block(kind="heading", text=stripped[len(marker):].strip(), level=level))
                index += 1
                break
        else:
            if stripped.startswith("> "):
                flush()
                quote: list[str] = []
                while index < len(lines) and lines[index].strip().startswith("> "):
                    quote.append(lines[index].strip()[2:])
                    index += 1
                blocks.append(Block(kind="quote", text="\n".join(quote)))
                continue

            if re.match(r"^([-*]|\d+\.)\s+", stripped):
                flush()
                items: list[str] = []
                ordered = bool(re.match(r"^\d+\.\s+", stripped))
                while index < len(lines):
                    candidate = lines[index].strip()
                    item = re.match(r"^([-*]|\d+\.)\s+(.*)$", candidate)
                    if not item:
                        break
                    items.append(item.group(2))
                    index += 1
                blocks.append(Block(kind="ordered" if ordered else "list", items=items))
                continue

            if not stripped:
                flush()
                index += 1
                continue

            buffer.append(line)
            index += 1

    flush()
    return blocks


_CHECK_MARKS = {"done": "✓", "todo": "○", "warn": "!"}


def _parse_rows(body_text: str, columns: int, name: str, warnings: list[dict]) -> list[list[str]]:
    """行式区块：每行按 | 分成固定列数。缺列补空，多列并进最后一列，不丢字。"""
    rows: list[list[str]] = []
    for raw in body_text.split("\n"):
        line = raw.strip()
        if not line:
            continue
        parts = [part.strip() for part in line.split("|")]
        if len(parts) < columns:
            warnings.append(
                {
                    "field": "body",
                    "level": "warning",
                    "message": f":::{name} 一行需要 {columns} 列（用 | 分开），已补空列：{line}",
                }
            )
            parts.extend([""] * (columns - len(parts)))
        elif len(parts) > columns:
            parts = parts[: columns - 1] + [" | ".join(parts[columns - 1 :])]
        rows.append(parts)
    if not rows:
        warnings.append(
            {"field": "body", "level": "warning", "message": f":::{name} 没有可渲染的行"}
        )
    return rows


def _check_mark(status: str) -> str:
    return _CHECK_MARKS.get(status.strip().lower(), "○")


# ---------- 区块渲染 ----------


def _section(pairs: dict, inner: str) -> str:
    return f'<section style="{_style(pairs)}">{inner}</section>'


def _render_heading(block: Block, tokens: dict, number: int) -> str:
    # 海报（17px 正文 + 居中标题）的一级标题刻意超大：微信没有自定义字体，字号是它唯一的辨识度。
    # 暖橙、书卷同为居中标题但正文字号更小，不能被这条带上。
    display = block.level == 1 and tokens["h1Style"] == "center" and tokens["bodySize"] >= 17
    size = tokens["bodySize"] + (10 if display else 3 if block.level == 1 else 1)
    color = tokens["textColor"]
    primary = tokens["primaryColor"]
    strong_color = tokens.get("strongColor", color)
    base = {"margin": "2em 0 0.9em", "line-height": 1.5}

    if block.level == 1:
        style = tokens["h1Style"]
        if style == "block":
            return _section(
                {**base},
                f'<span style="{_style({"display": "inline-block", "padding": "4px 12px", "background": primary, "color": "#FFFFFF", "font-size": size, "font-weight": 600, "border-radius": 4})}">{escape_text(block.text)}</span>',
            )
        if style == "number":
            return _section(
                {**base},
                f'<span style="{_style({"color": primary, "font-size": size, "font-weight": 600, "margin-right": 8})}">{number:02d}</span>'
                f'<strong style="{_style({"color": color, "font-size": size, "font-weight": 600})}">{escape_text(block.text)}</strong>',
            )
        if style == "center":
            return _section(
                {**base, "text-align": "center"},
                f'<strong style="{_style({"color": color, "font-size": size, "font-weight": 600, "letter-spacing": 1})}">{escape_text(block.text)}</strong>'
                f'<span style="{_style({"display": "block", "width": 32, "height": 2, "background": primary, "margin": "10px auto 0"})}"></span>',
            )
        if style == "underline":
            return _section(
                {**base, "border-bottom": f"2px solid {tokens['dividerColor']}", "padding-bottom": 6},
                f'<strong style="{_style({"color": primary, "font-size": size, "font-weight": 600, "letter-spacing": 0.5})}">{escape_text(block.text)}</strong>',
            )
        if style == "number-divider":
            # 大号主色编号独占一行，下方紧跟深标题文字，再用分割线收束
            return _section(
                {**base, "border-bottom": f"1px solid {tokens['dividerColor']}", "padding-bottom": 14},
                f'<span style="{_style({"display": "block", "color": primary, "font-size": size + 6, "font-weight": 700, "margin-bottom": 4})}">{number:02d}</span>'
                f'<strong style="{_style({"color": strong_color, "font-size": size, "font-weight": 600, "line-height": 1.6})}">{escape_text(block.text)}</strong>',
            )
        if style == "capsule":
            return _section(
                {**base},
                f'<span style="{_style({"display": "inline-block", "padding": "6px 16px", "background": primary, "color": "#FFFFFF", "font-size": size, "font-weight": 600, "border-radius": 999})}">{escape_text(block.text)}</span>',
            )
        if style == "banner":
            # 通栏色条：文字居中反白。微信会剥掉 h1，只能用 section 铺满一行。
            return _section(
                {**base, "margin": "2em 0 1.1em", "padding": "12px 16px", "background": primary, "text-align": "center"},
                f'<strong style="{_style({"color": "#FFFFFF", "font-size": size, "font-weight": 600, "letter-spacing": 1})}">{escape_text(block.text)}</strong>',
            )
        if style == "margin-note":
            # 章节号竖排在左侧窄色带里。writing-mode 微信不稳定，用逐字换行模拟竖排。
            digits = "<br/>".join(escape_text(char) for char in f"{number:02d}")
            return _section(
                {**base},
                f'<span style="{_style({"display": "inline-block", "width": 26, "padding": "8px 0", "background": primary, "color": "#FFFFFF", "font-size": 13, "font-weight": 700, "line-height": 1.35, "text-align": "center", "vertical-align": "top", "margin-right": 12})}">{digits}</span>'
                f'<strong style="{_style({"display": "inline-block", "color": color, "font-size": size, "font-weight": 600, "line-height": 1.5, "vertical-align": "top"})}">{escape_text(block.text)}</strong>',
            )
        if style == "seal":
            # 四角断开的方框：四条边各留出中间一段，角上才有线。
            edge = f"1px solid {primary}"
            return _section(
                {**base, "text-align": "center"},
                f'<span style="{_style({"display": "inline-block", "padding": "2px"})}">'
                f'<span style="{_style({"display": "inline-block", "border-top": edge, "border-bottom": edge, "padding": "3px 0"})}">'
                f'<span style="{_style({"display": "inline-block", "border-left": edge, "border-right": edge, "padding": "8px 22px"})}">'
                f'<strong style="{_style({"color": primary, "font-size": size, "font-weight": 600, "letter-spacing": 2})}">{escape_text(block.text)}</strong>'
                f"</span></span></span>",
            )
        if style == "lead-in":
            # 标题本身只留一条细下划线，节奏交给紧跟的第一段（见 render_document）。
            return _section(
                {**base, "border-bottom": f"1px solid {tokens['dividerColor']}", "padding-bottom": 8},
                f'<strong style="{_style({"color": color, "font-size": size + 4, "font-weight": 600, "letter-spacing": 0.5})}">{escape_text(block.text)}</strong>',
            )
        if style == "eyebrow":
            # 主色小标签压在标题上方，标签与标题之间不留大空白。
            return _section(
                {**base},
                f'<span style="{_style({"display": "block", "margin": "0 0 8px", "color": primary, "font-size": 12, "font-weight": 700, "letter-spacing": 2})}">本期</span>'
                f'<strong style="{_style({"display": "block", "color": color, "font-size": size + 2, "font-weight": 700, "letter-spacing": 0.5, "line-height": 1.4})}">{escape_text(block.text)}</strong>',
            )
        if style == "kicker":
            # 章节号单独成一行，标题居中，下面一条短线收住。
            return _section(
                {**base, "text-align": "center"},
                f'<span style="{_style({"display": "block", "color": primary, "font-size": 12, "font-weight": 700, "letter-spacing": 3, "margin": "0 0 8px"})}">{number:02d}</span>'
                f'<strong style="{_style({"color": color, "font-size": size + 2, "font-weight": 600, "letter-spacing": 1})}">{escape_text(block.text)}</strong>'
                f'<span style="{_style({"display": "block", "width": 28, "height": 2, "background": primary, "margin": "12px auto 0"})}"></span>',
            )
        if style == "framed":
            # 上下两条细线把标题夹在中间。
            return _section(
                {**base, "text-align": "center", "padding": "14px 0", "border-top": f"1px solid {primary}", "border-bottom": f"1px solid {primary}"},
                f'<strong style="{_style({"color": color, "font-size": size, "font-weight": 600, "letter-spacing": 2})}">{escape_text(block.text)}</strong>',
            )
        if style == "rail":
            # 左侧色带贴着标题，标题可以换行，色带跟着一起高。
            return _section(
                {**base, "padding": "4px 0 4px 14px", "border-left": f"4px solid {primary}"},
                f'<strong style="{_style({"color": color, "font-size": size + 1, "font-weight": 700, "line-height": 1.45})}">{escape_text(block.text)}</strong>',
            )
        return _section(
            {**base},
            f'<span style="{_style({"display": "inline-block", "width": 4, "height": "1em", "background": primary, "vertical-align": "-1px", "margin-right": 8})}"></span>'
            f'<strong style="{_style({"color": color, "font-size": size, "font-weight": 600})}">{escape_text(block.text)}</strong>',
        )

    style = tokens["h2Style"]
    if style == "tag":
        return _section(
            {**base, "margin": "1.6em 0 0.7em"},
            f'<span style="{_style({"display": "inline-block", "padding": "3px 10px", "background": tokens["primarySoft"], "color": primary, "font-size": size, "font-weight": 600, "border-radius": 4})}">{escape_text(block.text)}</span>',
        )
    if style == "underline":
        return _section(
            {**base, "margin": "1.6em 0 0.7em", "border-bottom": f"1px solid {tokens['dividerColor']}", "padding-bottom": 6},
            f'<strong style="{_style({"color": color, "font-size": size, "font-weight": 600})}">{escape_text(block.text)}</strong>',
        )
    if style == "bar":
        return _section(
            {**base, "margin": "1.6em 0 0.7em"},
            f'<span style="{_style({"display": "inline-block", "width": 4, "height": "1em", "background": primary, "vertical-align": "-1px", "margin-right": 8, "border-radius": 2})}"></span>'
            f'<strong style="{_style({"color": color, "font-size": size, "font-weight": 600})}">{escape_text(block.text)}</strong>',
        )
    if style == "strong":
        return _section(
            {**base, "margin": "1.6em 0 0.7em"},
            f'<strong style="{_style({"color": strong_color, "font-size": size, "font-weight": 600, "line-height": 1.6})}">{escape_text(block.text)}</strong>',
        )
    if style == "capsule":
        return _section(
            {**base, "margin": "1.6em 0 0.7em"},
            f'<span style="{_style({"display": "inline-block", "padding": "4px 14px", "background": tokens["primarySoft"], "color": primary, "font-size": size, "font-weight": 600, "border-radius": 999})}">{escape_text(block.text)}</span>',
        )
    if style == "hairline":
        return _section(
            {**base, "margin": "1.6em 0 0.7em", "padding-bottom": 8, "border-bottom": f"0.8px solid {tokens['dividerColor']}"},
            f'<strong style="{_style({"color": color, "font-size": size, "font-weight": 600, "letter-spacing": 0.8})}">{escape_text(block.text)}</strong>',
        )
    return _section(
        {**base, "margin": "1.6em 0 0.7em"},
        f'<strong style="{_style({"color": primary, "font-size": size, "font-weight": 600})}">{escape_text(block.text)}</strong>',
    )


def _render_paragraph(block: Block, tokens: dict, warnings: list[dict], *, lead: bool = False) -> str:
    inner = render_inline(block.text, tokens, warnings).replace("\n", "<br/>")
    # 拉引：一级标题后的第一段放大、改用浅主色，充当导语。其余段落不动。
    size = tokens["bodySize"] + 2 if lead else tokens["bodySize"]
    color = tokens["mutedColor"] if lead else tokens["textColor"]
    return (
        f'<p style="{_style({"margin": f"0 0 {tokens["paragraphGap"]}em", "font-size": size, "line-height": tokens["bodyLineHeight"], "letter-spacing": tokens["letterSpacing"], "color": color, "text-align": tokens["align"], "text-indent": f"{tokens["indent"]}em" if tokens["indent"] else 0})}">'
        f"{inner}</p>"
    )


def _render_list(block: Block, tokens: dict, warnings: list[dict], ordered: bool) -> str:
    marker = tokens["listMarker"]
    if ordered or marker == "number":
        tag = "ol"
        list_style = "decimal"
    else:
        tag = "ul"
        list_style = "square" if marker == "square" else "circle" if marker == "circle" else "disc"
    items = "".join(
        f'<li style="{_style({"margin": "0 0 0.35em", "line-height": tokens["bodyLineHeight"]})}">{render_inline(item, tokens, warnings)}</li>'
        for item in block.items
    )
    return (
        f'<{tag} style="{_style({"margin": f"0 0 {tokens["paragraphGap"]}em", "padding-left": "1.4em", "list-style-type": list_style, "font-size": tokens["bodySize"], "letter-spacing": tokens["letterSpacing"], "color": tokens["textColor"]})}">'
        f"{items}</{tag}>"
    )


def _render_quote(block: Block, tokens: dict, warnings: list[dict]) -> str:
    inner = render_inline(block.text, tokens, warnings).replace("\n", "<br/>")
    return (
        f'<blockquote style="{_style({"margin": f"0 0 {tokens["paragraphGap"]}em", "padding": "10px 14px", "border-left": f"3px solid {tokens["primaryColor"]}", "background": tokens["quoteBg"], "color": tokens["mutedColor"], "font-size": tokens["bodySize"] - 1, "line-height": tokens["bodyLineHeight"], "letter-spacing": tokens["letterSpacing"]})}">'
        f"{inner}</blockquote>"
    )


def _render_image(block: Block, tokens: dict, image_urls: list[str], used: set[int]) -> str:
    if block.image_index < 1 or block.image_index > len(image_urls):
        return ""
    used.add(block.image_index)
    url = escape_text(image_urls[block.image_index - 1])
    caption = (
        f'<span style="{_style({"display": "block", "margin": "6px 0 0", "font-size": 12, "color": tokens["mutedColor"], "text-align": "center"})}">{escape_text(block.caption)}</span>'
        if block.caption
        else ""
    )
    return (
        f'<section style="{_style({"margin": f"0 0 {tokens["paragraphGap"]}em", "text-align": "center"})}">'
        f'<img src="{url}" style="{_style({"max-width": "100%", "display": "block", "margin": "0 auto", "border-radius": tokens["imageRadius"]})}"/>'
        f"{caption}</section>"
    )


def _render_blockquote_like(kind: str, block: Block, tokens: dict, warnings: list[dict]) -> str:
    inner = render_inline(block.text, tokens, warnings).replace("\n", "<br/>")
    if kind == "card":
        head = (
            f'<strong style="{_style({"display": "block", "margin": "0 0 6px", "color": tokens["primaryColor"], "font-size": tokens["bodySize"] - 1, "font-weight": 600})}">{escape_text(block.meta)}</strong>'
            if block.meta
            else ""
        )
        return _section(
            {
                "margin": f"0 0 {tokens['paragraphGap']}em",
                "padding": "14px 16px",
                "background": tokens["primarySoft"],
                "border-radius": tokens["imageRadius"],
                "font-size": tokens["bodySize"] - 1,
                "line-height": tokens["bodyLineHeight"],
                "color": tokens["textColor"],
            },
            head + inner,
        )
    if kind == "follow":
        badge = (
            f'<span style="{_style({"display": "inline-block", "margin": "0 0 10px", "padding": "3px 10px", "border": "1px solid " + tokens["primaryColor"], "border-radius": 999, "color": tokens["primaryColor"], "font-size": 12, "font-weight": 600, "line-height": 1.5})}">关注我们</span>'
        )
        message = (
            f'<span style="{_style({"display": "block", "color": tokens["textColor"], "font-size": tokens["bodySize"] - 1, "line-height": tokens["bodyLineHeight"]})}">{inner}</span>'
        )
        action = (
            f'<strong style="{_style({"display": "inline-block", "margin": "12px 0 0", "padding": "6px 14px", "background": tokens["primaryColor"], "border-radius": 4, "color": "#FFFFFF", "font-size": 13, "font-weight": 600, "line-height": 1.5})}">点个关注，不错过更新</strong>'
        )
        return _section(
            {
                "margin": "2em 0 1em",
                "padding": "18px 16px",
                "background": tokens["quoteBg"],
                "border-left": f'3px solid {tokens["primaryColor"]}',
                "border-radius": tokens["imageRadius"],
                "text-align": "center",
                "line-height": tokens["bodyLineHeight"],
                "color": tokens["mutedColor"],
            },
            badge + message + action,
        )
    if kind == "lead":
        bar = (
            f'<span style="{_style({"display": "block", "width": 36, "height": 4, "background": tokens["primaryColor"], "margin": "0 0 12px", "border-radius": 2})}"></span>'
        )
        return _section(
            {
                "margin": f"0 0 {tokens['paragraphGap']}em",
                "font-size": 17.5,
                "line-height": 1.7,
                "font-weight": 500,
                "color": tokens["textColor"],
                "letter-spacing": tokens["letterSpacing"],
            },
            bar + inner,
        )
    if kind == "quote-card":
        mark = (
            f'<span style="{_style({"display": "block", "font-size": 36, "line-height": 1, "color": tokens["primaryColor"], "font-weight": 700, "margin": "0 0 4px"})}">“</span>'
        )
        source = (
            f'<span style="{_style({"display": "block", "margin": "10px 0 0", "font-size": 13, "color": tokens["mutedColor"]})}">〔{escape_text(block.meta)}〕</span>'
            if block.meta
            else ""
        )
        return _section(
            {
                "margin": f"0 0 {tokens['paragraphGap']}em",
                "padding": "8px 4px 8px 0",
                "font-size": 19,
                "line-height": 1.6,
                "font-weight": 600,
                "color": tokens["textColor"],
            },
            mark + inner + source,
        )
    # sign：文末署名，上面一条细线
    return _section(
        {
            "margin": "2em 0 0",
            "padding-top": 12,
            "border-top": f"1px solid {tokens['dividerColor']}",
            "text-align": "center",
            "font-size": 12,
            "line-height": 1.8,
            "color": tokens["mutedColor"],
        },
        inner,
    )


def _render_code(block: Block, tokens: dict) -> str:
    code = escape_text(block.text)
    return _section(
        {
            "margin": f"0 0 {tokens['paragraphGap']}em",
            "padding": "12px 14px",
            "background": tokens["quoteBg"],
            "border-radius": tokens["imageRadius"],
            "font-family": "Menlo, Consolas, monospace",
            "font-size": 13,
            "line-height": 1.6,
            "color": tokens["textColor"],
            "white-space": "pre-wrap",
            "word-break": "break-all",
        },
        f"<span>{code}</span>",
    )


def _cell(text: str, tokens: dict, warnings: list[dict]) -> str:
    return render_inline(text, tokens, warnings) if text else ""


def _render_stat(block: Block, tokens: dict, warnings: list[dict]) -> str:
    rows = "".join(
        _section(
            {
                "display": "flex",
                "align-items": "baseline",
                "margin": "0 0 10px",
            },
            f'<span style="{_style({"min-width": 96, "font-size": 30, "font-weight": 800, "line-height": 1.1, "color": tokens["primaryColor"], "font-variant-numeric": "tabular-nums", "margin-right": 12})}">{_cell(cols[0], tokens, warnings)}</span>'
            f'<span style="{_style({"font-size": 14, "line-height": 1.5, "color": tokens["mutedColor"]})}">{_cell(cols[1], tokens, warnings)}</span>',
        )
        for cols in block.rows
    )
    return _section({"margin": f"0 0 {tokens['paragraphGap']}em"}, rows)


def _render_steps(block: Block, tokens: dict, warnings: list[dict]) -> str:
    rows = "".join(
        _section(
            {"display": "flex", "align-items": "flex-start", "margin": "0 0 14px"},
            f'<span style="{_style({"flex": "0 0 26px", "width": 26, "height": 26, "line-height": 26, "text-align": "center", "border-radius": 999, "background": tokens["primaryColor"], "color": "#FFFFFF", "font-size": 13, "font-weight": 700, "margin-right": 12})}">{index}</span>'
            f'<span style="{_style({"flex": 1})}">'
            f'<strong style="{_style({"display": "block", "font-size": 16.5, "font-weight": 600, "line-height": 1.4, "color": tokens["textColor"]})}">{_cell(cols[0], tokens, warnings)}</strong>'
            f'<span style="{_style({"display": "block", "margin": "2px 0 0", "font-size": 14, "line-height": 1.6, "color": tokens["mutedColor"]})}">{_cell(cols[1], tokens, warnings)}</span>'
            f"</span>",
        )
        for index, cols in enumerate(block.rows, start=1)
    )
    return _section({"margin": f"0 0 {tokens['paragraphGap']}em"}, rows)


def _render_compare(block: Block, tokens: dict, warnings: list[dict]) -> str:
    heads = [part.strip() for part in block.meta.split("/", 1)] if block.meta else []
    while len(heads) < 2:
        heads.append("")
    header = _section(
        {"display": "flex", "margin": "0 0 8px"},
        f'<strong style="{_style({"flex": 1, "font-size": 13, "font-weight": 600, "color": tokens["mutedColor"], "margin-right": 10})}">{escape_text(heads[0])}</strong>'
        f'<strong style="{_style({"flex": 1, "font-size": 13, "font-weight": 600, "color": tokens["primaryColor"]})}">{escape_text(heads[1])}</strong>',
    )
    rows = "".join(
        _section(
            {"display": "flex", "margin": "0 0 8px"},
            f'<span style="{_style({"flex": 1, "padding": "12px 14px", "background": "#F4F5F7", "border-radius": 12, "font-size": 14, "line-height": 1.6, "color": tokens["textColor"], "margin-right": 10})}">{_cell(cols[0], tokens, warnings)}</span>'
            f'<span style="{_style({"flex": 1, "padding": "12px 14px", "background": tokens["primarySoft"], "border-radius": 12, "font-size": 14, "line-height": 1.6, "color": tokens["textColor"]})}">{_cell(cols[1], tokens, warnings)}</span>',
        )
        for cols in block.rows
    )
    return _section({"margin": f"0 0 {tokens['paragraphGap']}em"}, header + rows)


def _render_layers(block: Block, tokens: dict, warnings: list[dict]) -> str:
    cards = "".join(
        _section(
            {
                "margin": "0 0 8px",
                "padding": "12px 14px",
                "background": tokens["primarySoft"],
                "border-radius": 12,
            },
            f'<strong style="{_style({"display": "block", "margin": "0 0 4px", "font-size": 12, "font-weight": 600, "letter-spacing": 0.4, "color": tokens["primaryColor"]})}">{_cell(cols[0], tokens, warnings)}</strong>'
            f'<span style="{_style({"display": "block", "font-size": tokens["bodySize"] - 1, "line-height": 1.7, "color": tokens["textColor"]})}">{_cell(cols[1], tokens, warnings)}</span>',
        )
        for cols in block.rows
    )
    return _section({"margin": f"0 0 {tokens['paragraphGap']}em"}, cards)


# 动作名对应的色块字。认不出的名字用首字，不引入外部图标。
_SUPPORT_GLYPHS = {"点赞": "赞", "转发": "转", "推荐": "荐", "收藏": "藏", "在看": "看", "评论": "评"}


def _support_glyph(label: str) -> str:
    text = label.strip()
    if text in _SUPPORT_GLYPHS:
        return _SUPPORT_GLYPHS[text]
    return text[:1] or "·"


def _render_support(block: Block, tokens: dict, warnings: list[dict]) -> str:
    """文末互动卡。微信不能放插画，图标用主色圆里的一个字代替。"""
    heads = [part.strip() for part in block.meta.split("/", 1)] if block.meta else []
    title = heads[0] if heads else ""
    subtitle = heads[1] if len(heads) > 1 else ""
    head = ""
    if title:
        head += (
            f'<strong style="{_style({"display": "block", "font-size": 18, "font-weight": 700, "line-height": 1.4, "letter-spacing": 0.5, "color": tokens["textColor"]})}">{escape_text(title)}</strong>'
            f'<span style="{_style({"display": "block", "width": 36, "height": 3, "background": tokens["primaryColor"], "margin": "8px auto 0", "border-radius": 2})}"></span>'
        )
    if subtitle:
        head += (
            f'<span style="{_style({"display": "inline-block", "margin": "12px 0 0", "padding": "4px 12px", "background": tokens["primarySoft"], "border-radius": 999, "font-size": 13, "line-height": 1.6, "color": tokens["mutedColor"]})}">{escape_text(subtitle)}</span>'
        )
    cards = "".join(
        f'<span style="{_style({"display": "inline-block", "width": "31%", "vertical-align": "top", "margin": "0 1% 0", "padding": "12px 6px 10px", "background": tokens["quoteBg"], "border-radius": 12, "text-align": "center"})}">'
        f'<span style="{_style({"display": "block", "width": 32, "height": 32, "line-height": 32, "margin": "0 auto 8px", "border-radius": 999, "background": tokens["primarySoft"], "color": tokens["primaryColor"], "font-size": 14, "font-weight": 700, "text-align": "center"})}">{escape_text(_support_glyph(cols[0]))}</span>'
        f'<strong style="{_style({"display": "inline-block", "padding": "2px 10px", "background": tokens["primaryColor"], "border-radius": 999, "color": "#FFFFFF", "font-size": 12, "font-weight": 600, "line-height": 1.6})}">{_cell(cols[0], tokens, warnings)}</strong>'
        f'<span style="{_style({"display": "block", "margin": "6px 0 0", "font-size": 12, "line-height": 1.5, "color": tokens["mutedColor"]})}">{_cell(cols[1], tokens, warnings)}</span>'
        f"</span>"
        for cols in block.rows
    )
    body = (
        f'<span style="{_style({"display": "block", "margin": "16px 0 0", "text-align": "center"})}">{cards}</span>'
        if cards
        else ""
    )
    return _section(
        {
            "margin": "2em 0 1em",
            "padding": "20px 8px 16px",
            "background": tokens["primarySoft"],
            "border-radius": 16,
            "text-align": "center",
        },
        head + body,
    )


# 暖心支持卡：海报式构图（主视觉徽章 + 三层文案 + 三色互动卡）。
# 图标统一用彩色 emoji，避免彩色 emoji 与单色字形混排造成的风格割裂。
_CHEER_GLYPHS = {"点赞": "👍", "转发": "🔁", "推荐": "❤️", "收藏": "⭐", "在看": "👀", "评论": "💬"}
_CHEER_COLORS = (
    ("#FDECC8", "#F7CE7A", "#8A5E1F"),  # 暖黄
    ("#FBE0E4", "#F2B8C0", "#9C4C5C"),  # 柔粉
    ("#FCE0D6", "#F4B49B", "#A04E36"),  # 蜜桃
)


def _cheer_glyph(label: str) -> str:
    text = label.strip()
    return _CHEER_GLYPHS.get(text, text[:1] or "·")


def _render_cheer(
    block: Block,
    tokens: dict,
    warnings: list[dict],
    image_urls: list[str] | None = None,
    used: set[int] | None = None,
) -> str:
    """暖色支持卡：爱心标题 + 胶囊副标题 + 三色互动卡，可附一张正文图作插画。"""
    heads = [part.strip() for part in block.meta.split("/", 2)] if block.meta else []
    title = heads[0] if heads else ""
    subtitle = heads[1] if len(heads) > 1 else ""
    bubble = heads[2] if len(heads) > 2 else ""

    # 主视觉徽章：红花作焦点，两侧小花点缀，延续参考图的手绘暖色调。
    hero = (
        f'<span style="{_style({"display": "block", "margin": "0 0 12px", "text-align": "center", "font-size": 0})}">'
        f'<span style="{_style({"display": "inline-block", "vertical-align": "middle", "margin": "0 8px", "font-size": 17, "line-height": 1.4})}">🌼</span>'
        f'<span style="{_style({"display": "inline-block", "vertical-align": "middle", "width": 58, "height": 58, "line-height": "58px", "border-radius": 999, "background": "#FCE4DC", "border": "3px solid #FFFFFF", "font-size": 28, "text-align": "center", "box-shadow": "0 2px 8px rgba(200,120,100,0.12)"})}">❤️</span>'
        f'<span style="{_style({"display": "inline-block", "vertical-align": "middle", "margin": "0 8px", "font-size": 17, "line-height": 1.4})}">🌼</span>'
        f"</span>"
    )

    head = hero
    if title:
        head += (
            f'<strong style="{_style({"display": "block", "font-size": 21, "font-weight": 700, "line-height": 1.4, "letter-spacing": 1.5, "color": "#6E4733"})}">{escape_text(title)}</strong>'
            f'<span style="{_style({"display": "block", "width": "52%", "height": 3, "margin": "8px auto 0", "background": "#F0B7A6", "border-radius": 2})}"></span>'
        )
    if subtitle:
        head += (
            f'<span style="{_style({"display": "inline-block", "margin": "12px 0 0", "padding": "5px 14px", "background": "#FFF1CC", "border-radius": 999, "font-size": 12, "line-height": 1.7, "color": "#9C663C"})}">{escape_text(subtitle)}</span>'
        )

    cards: list[str] = []
    art = ""
    for index, cols in enumerate(block.rows):
        if cols[0].strip() in {"插图", "插画"}:
            placeholder = _IMAGE_PLACEHOLDER.fullmatch(cols[1].strip())
            if not placeholder:
                warnings.append(
                    {
                        "field": "body",
                        "level": "warning",
                        "message": ":::cheer 插图行请写成 `插图 | {{image1}}`",
                    }
                )
                continue
            image_index = int(placeholder.group(1))
            if image_urls and 1 <= image_index <= len(image_urls):
                if used is not None:
                    used.add(image_index)
                art = (
                    f'<img src="{escape_text(image_urls[image_index - 1])}" style="{_style({"display": "block", "max-width": "100%", "margin": "16px auto 0", "border-radius": tokens["imageRadius"]})}"/>'
                )
            else:
                warnings.append(
                    {
                        "field": "body",
                        "level": "warning",
                        "message": f":::cheer 插图占位 image{image_index} 超出已选正文图数量，已忽略",
                    }
                )
            continue
        circle, tag, ink = _CHEER_COLORS[index % len(_CHEER_COLORS)]
        cards.append(
            f'<span style="{_style({"display": "inline-block", "box-sizing": "border-box", "width": "31%", "vertical-align": "top", "margin": "0 1%", "padding": "15px 4px 13px", "background": "#FFFCF8", "border": "1px solid #F7E6D9", "border-radius": 16, "text-align": "center"})}">'
            f'<span style="{_style({"display": "block", "width": 56, "height": 56, "line-height": "56px", "margin": "0 auto 10px", "border-radius": 999, "background": circle, "font-size": 28, "text-align": "center"})}">{escape_text(_cheer_glyph(cols[0]))}</span>'
            f'<strong style="{_style({"display": "block", "font-size": 13, "font-weight": 700, "letter-spacing": 0.5, "line-height": 1.5, "color": ink})}">{_cell(cols[0], tokens, warnings)}</strong>'
            f'<span style="{_style({"display": "block", "margin": "5px 2px 0", "font-size": 11, "line-height": 1.6, "color": "#8C735F"})}">{_cell(cols[1], tokens, warnings)}</span>'
            f"</span>"
        )

    body = (
        f'<span style="{_style({"display": "block", "margin": "18px 0 0", "font-size": 0, "text-align": "center"})}">{"".join(cards)}</span>'
        if cards
        else ""
    )
    footer = (
        f'<span style="{_style({"display": "block", "margin": "16px 0 0", "text-align": "center"})}">'
        f'<span style="{_style({"display": "inline-block", "padding": "4px 16px", "background": "#FDEEE9", "border-radius": 999, "font-size": 12, "letter-spacing": 1, "line-height": 1.6, "color": "#C77B69"})}">{escape_text(bubble)}</span>'
        f"</span>"
        if bubble
        else ""
    )
    return _section(
        {
            "margin": "1.6em 0 1em",
            "padding": "20px 14px 18px",
            "background": "#FFFAF4",
            "border": "1px solid #F5E4D4",
            "border-radius": 22,
            "text-align": "center",
        },
        head + body + art + footer,
    )


_MINI_SUPPORT_GLYPHS = {"点赞": "👍", "转发": "🔁", "推荐": "❤️", "收藏": "⭐", "在看": "👀", "评论": "💬"}


def _mini_glyph(label: str) -> str:
    text = label.strip()
    return _MINI_SUPPORT_GLYPHS.get(text, text[:1] or "·")


def _render_mini_support(block: Block, tokens: dict, warnings: list[dict]) -> str:
    """极简互动：只放图标 + 动作名，浅底与文字色都跟随主题 tokens。"""
    title = block.meta.strip()
    head = (
        f'<span style="{_style({"display": "block", "margin": "0 0 10px", "font-size": 12, "letter-spacing": 1, "color": tokens["mutedColor"], "text-align": "center"})}">{escape_text(title)}</span>'
        if title
        else ""
    )
    items = "".join(
        f'<span style="{_style({"display": "inline-block", "box-sizing": "border-box", "width": "30%", "margin": "0 1%", "vertical-align": "top", "text-align": "center", "padding": "10px 2px", "background": tokens["quoteBg"], "border-radius": 14})}">'
        f'<span style="{_style({"display": "block", "width": 46, "height": 46, "line-height": "46px", "margin": "0 auto 8px", "border-radius": 999, "background": tokens["primarySoft"], "font-size": 24, "text-align": "center"})}">{escape_text(_mini_glyph(cols[0]))}</span>'
        f'<strong style="{_style({"display": "block", "font-size": 12, "font-weight": 600, "letter-spacing": 0.5, "line-height": 1.6, "color": tokens["textColor"]})}">{_cell(cols[0], tokens, warnings)}</strong>'
        f"</span>"
        for cols in block.rows
    )
    return _section(
        {"margin": "1.5em 0", "padding": "10px 6px", "text-align": "center", "font-size": 0},
        head + items,
    )


def _render_checklist(block: Block, tokens: dict, warnings: list[dict]) -> str:
    rows = "".join(
        _section(
            {"display": "flex", "align-items": "flex-start", "margin": "0 0 8px"},
            f'<span style="{_style({"flex": "0 0 18px", "width": 18, "font-size": 15, "line-height": 1.6, "font-weight": 700, "color": tokens["primaryColor"], "margin-right": 8})}">{_check_mark(cols[0])}</span>'
            f'<span style="{_style({"flex": 1, "font-size": tokens["bodySize"] - 1, "line-height": 1.6, "color": tokens["textColor"]})}">{_cell(cols[1], tokens, warnings)}</span>',
        )
        for cols in block.rows
    )
    return _section({"margin": f"0 0 {tokens['paragraphGap']}em"}, rows)


_ROW_RENDERERS = {
    "stat": _render_stat,
    "steps": _render_steps,
    "compare": _render_compare,
    "layers": _render_layers,
    "checklist": _render_checklist,
    "support": _render_support,
    "mini-support": _render_mini_support,
}


def _render_divider(tokens: dict) -> str:
    return (
        f'<section style="{_style({"margin": "2em 0", "text-align": "center", "color": tokens["dividerColor"], "font-size": 12, "letter-spacing": 4})}">'
        "— — —</section>"
    )


# ---------- 对外入口 ----------


def render_document(
    body: str,
    tokens: dict | None = None,
    image_urls: list[str] | None = None,
) -> dict:
    """渲染正文：返回 html / warnings / stats。预览与推送必须都调用这里。"""
    theme = normalize_tokens(tokens)
    urls = [str(url) for url in (image_urls or [])]
    warnings: list[dict] = []
    blocks = parse_blocks(body, warnings)
    used: set[int] = set()
    heading_no = 0
    lead_pending = False
    parts: list[str] = []

    for block in blocks:
        if block.kind == "heading":
            if block.level == 1:
                heading_no += 1
                lead_pending = theme["h1Style"] == "lead-in"
            else:
                lead_pending = False
            parts.append(_render_heading(block, theme, heading_no))
        elif block.kind == "paragraph":
            parts.append(_render_paragraph(block, theme, warnings, lead=lead_pending))
            lead_pending = False
        elif block.kind in {"list", "ordered"}:
            parts.append(_render_list(block, theme, warnings, block.kind == "ordered"))
        elif block.kind == "quote":
            parts.append(_render_quote(block, theme, warnings))
        elif block.kind == "image":
            rendered = _render_image(block, theme, urls, used)
            if not rendered:
                warnings.append(
                    {
                        "field": "body",
                        "level": "warning",
                        "message": f"图片占位 image{block.image_index} 超出已选正文图数量，已忽略",
                    }
                )
            parts.append(rendered)
        elif block.kind in FREE_BLOCKS:
            parts.append(_render_blockquote_like(block.kind, block, theme, warnings))
        elif block.kind == "cheer":
            parts.append(_render_cheer(block, theme, warnings, urls, used))
        elif block.kind in _ROW_RENDERERS:
            parts.append(_ROW_RENDERERS[block.kind](block, theme, warnings))
        elif block.kind == "code":
            parts.append(_render_code(block, theme))
        elif block.kind == "divider":
            parts.append(_render_divider(theme))

    # 没有用占位符的图片按顺序追加到文末，避免静默丢图
    for index in range(1, len(urls) + 1):
        if index not in used:
            parts.append(_render_image(Block(kind="image", image_index=index), theme, urls, used))
    html = "".join(part for part in parts if part)

    plain = re.sub(r"<[^>]+>", "", html)
    chars = len(plain.strip())
    return {
        "html": html,
        "warnings": warnings,
        "stats": {
            "sourceChars": len((body or "").strip()),
            "renderedChars": chars,
            "htmlChars": len(html),
            "images": len(urls),
            "readingMinutes": max(1, round(chars / 400)) if chars else 0,
        },
    }


def lint_html(html: str, tokens: dict | None = None) -> list[dict]:
    """渲染后兼容性检查：非白名单标签/属性一旦出现说明渲染器有 bug，必须报出来。"""
    theme = normalize_tokens(tokens)
    issues: list[dict] = []
    for tag in ("<style", "<script", "<div", "<h1", "<h2", "<h3", "<iframe", "<table"):
        if tag in html:
            issues.append(
                {
                    "field": "body",
                    "level": "error",
                    "message": f"渲染结果包含公众号不支持的标签 {tag}>，请反馈此问题",
                }
            )
    if "class=" in html or " id=" in html:
        issues.append(
            {"field": "body", "level": "error", "message": "渲染结果包含 class/id，微信会剥离，请反馈此问题"}
        )
    if "background-image" in html:
        issues.append(
            {"field": "body", "level": "warning", "message": "公众号不渲染 background-image，已改用纯色底"}
        )
    for key in ("textColor", "quoteBg"):
        value = str(theme[key]).lower()
        if value in {"#fff", "#ffffff"}:
            issues.append(
                {
                    "field": "theme",
                    "level": "warning",
                    "message": f"主题 {key} 接近纯白，公众号深色模式下可能显示异常",
                }
            )
    return issues
