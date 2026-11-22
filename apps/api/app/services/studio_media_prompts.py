"""成稿台：AI 生成封面 / 插图提示词建议。

成稿台的提示词有两个来源：提示词库里的模板（人工维护、可复用）和按当前稿件现拟的 AI 建议。
本模块只负责后者——把稿件标题、正文或某一段交给模型，换回能直接喂给生图模型的画面描述。

结果只回给前端、不落库：建议是「草稿态」，用户还要在面板里改，
真正用掉的那一条才会随封面/插图任务写进稿件。
"""

from __future__ import annotations

from . import ai_gateway, article_blocks as AB
from .studio_pipeline import extract_json

# 提示词上限与生图面板的输入框一致（前端 maxlength=600）
MAX_PROMPT_LEN = 600
MAX_COVER_PROMPTS = 4
MAX_INLINE_PROMPTS = 6

COVER_RULES = """# 本次输出要求
你要为一篇公众号文章写封面图的生图提示词。只输出 JSON，不要解释、不要前言：
{"prompts":[{"prompt":"画面描述","note":"这版画面想表达什么"}]}

要求：
- prompt 是可以直接喂给生图模型的画面描述：主体、场景、构图、光线、氛围、配色。
- 不要写「生成一张封面图」这类指令式的话，画面里也不要出现文字、水印、logo、二维码。
- 至少 3 条，彼此风格不同（写实摄影 / 扁平插画 / 水彩质感 / 国风留白 / 3D 渲染 等）。
- 不要编造文章里没有的事实、品牌与数据，人物与场景保持通用。
- note 用一句话说明这版画面的取向，40 字以内。
"""

INLINE_RULES = """# 本次输出要求
你要为公众号文章的段落写配图的生图提示词。只输出 JSON，不要解释、不要前言：
{"prompts":[{"block":段号,"prompt":"画面描述","note":"这版配图想说明什么"}]}

要求：
- prompt 是可以直接喂给生图模型的画面描述：主体、场景、构图、光线、配色。
- 不要写「为这段配图」这类指令式的话，画面里也不要出现文字、水印、logo、二维码。
- 只挑真正需要画面才能讲清的段落，不要每段都配。
- note 用一句话说明这版配图对应段落的哪个意思，40 字以内。
"""

ONE_BLOCK_RULES = """# 本次输出要求
你要为下面这一段公众号正文写配图的生图提示词。只输出 JSON，不要解释、不要前言：
{"prompts":[{"prompt":"画面描述","note":"这版配图想说明什么"}]}

要求：
- prompt 是可以直接喂给生图模型的画面描述：主体、场景、构图、光线、配色。
- 不要写「为这段配图」这类指令式的话，画面里也不要出现文字、水印、logo、二维码。
- 至少 2 条，彼此风格不同。
- note 用一句话说明这版配图对应段落的哪个意思，40 字以内。
"""


def _clip(text, limit: int = MAX_PROMPT_LEN) -> str:
    return str(text or "").strip()[:limit]


def _title_of(data: dict) -> str:
    return (data.get("title") or data.get("topic") or "文章").strip()


def _blocks_of(data: dict) -> list[dict]:
    return list(data.get("blocks") or [])


def _lead_paragraph(data: dict, limit: int = 800) -> str:
    """取第一段像样的话当内容概要；没有正文就如实说没有。"""
    blocks = _blocks_of(data)
    for block in blocks:
        if block.get("kind") != "para":
            continue
        text = AB.plain_of(block.get("text") or "")
        if len(text) >= 20:
            return text[:limit]
    for block in blocks:
        text = AB.plain_of(block.get("text") or "")
        if text:
            return text[:limit]
    return ""


def _normalize(rows, *, want_block: bool, blocks: list[dict]) -> list[dict]:
    """把模型输出整成可用条目：去重、裁长、段号对齐回真实 blockId。"""
    items: list[dict] = []
    seen: set[str] = set()
    for raw in rows if isinstance(rows, list) else []:
        row = raw if isinstance(raw, dict) else {"prompt": raw}
        prompt = _clip(row.get("prompt"))
        if not prompt or prompt in seen:
            continue
        seen.add(prompt)
        item = {
            "prompt": prompt,
            "note": _clip(row.get("note"), 60),
        }
        if want_block:
            number = row.get("block")
            try:
                number = int(number)
            except (TypeError, ValueError):
                number = 0
            # 段号必须落在正文范围内；模型偶尔会自己编段号，越界的直接丢掉
            if number < 1 or number > len(blocks):
                continue
            item["blockNumber"] = number
            item["blockId"] = blocks[number - 1]["id"]
        items.append(item)
    return items


async def _ask(db, user_id: str, system: str, user: str, task: str) -> str:
    """问一次模型；拿不到内容就报错，不给占位文本。"""
    result = await ai_gateway.generate_text(
        db,
        owner_user_id=user_id,
        task_type=task,
        prompt=user,
        system_prompt=system,
    )
    text = str(result.get("text") or "").strip()
    if not text:
        raise RuntimeError(
            f"模型没有返回内容（{result.get('errorCode') or 'provider_unavailable'}），"
            "先到「模型管理」检查连接与默认模型。"
        )
    return text


async def suggest_cover_prompts(db, data: dict, *, user_id: str, count: int = 3) -> list[dict]:
    """按标题 + 内容概要给封面提示词候选。"""
    prompt = (
        f"# 文章标题\n{_title_of(data)}\n\n"
        f"# 内容概要\n{_lead_paragraph(data) or '（还没有正文，只按标题来想画面）'}"
    )
    raw = await _ask(db, user_id, COVER_RULES, prompt, "studio_media_prompt")
    parsed = extract_json(raw)
    rows = parsed.get("prompts") if isinstance(parsed, dict) else parsed
    items = _normalize(rows, want_block=False, blocks=[])
    return items[: max(1, min(count, MAX_COVER_PROMPTS))]


async def suggest_inline_prompts(
    db, data: dict, *, user_id: str, block_id: str | None = None, count: int = 4
) -> list[dict]:
    """给插图提示词候选。

    给了 block_id 就只针对那一段；没给就通读全文，挑出真正需要配图的段落各给一条。
    """
    blocks = _blocks_of(data)
    if block_id:
        index = next((i for i, item in enumerate(blocks) if item["id"] == block_id), -1)
        if index < 0:
            return []
        block = blocks[index]
        user = (
            f"# 文章标题\n{_title_of(data)}\n\n"
            f"# 要配图的段落（第 {index + 1} 段）\n{AB.plain_of(block.get('text') or '')}"
        )
        raw = await _ask(db, user_id, ONE_BLOCK_RULES, user, "studio_media_prompt")
        parsed = extract_json(raw)
        rows = parsed.get("prompts") if isinstance(parsed, dict) else parsed
        items = _normalize(rows, want_block=False, blocks=[])
        for item in items:
            item["blockId"] = block_id
            item["blockNumber"] = index + 1
        return items[: max(1, min(count, MAX_INLINE_PROMPTS))]

    if not blocks:
        return []
    user = (
        f"# 文章标题\n{_title_of(data)}\n\n"
        f"# 正文（段号就是下面的【n】）\n{AB.numbered(blocks)}\n\n"
        "# 请挑出 2-4 个最需要配图的段落"
    )
    raw = await _ask(db, user_id, INLINE_RULES, user, "studio_media_prompt")
    parsed = extract_json(raw)
    rows = parsed.get("prompts") if isinstance(parsed, dict) else parsed
    items = _normalize(rows, want_block=True, blocks=blocks)
    return items[: max(1, min(count, MAX_INLINE_PROMPTS))]


async def suggest(db, data: dict, *, user_id: str, scope: str, block_id: str | None = None,
                  count: int = 3) -> list[dict]:
    """统一入口：scope=cover 走封面，scope=inline 走插图。"""
    if scope == "cover":
        return await suggest_cover_prompts(db, data, user_id=user_id, count=count)
    return await suggest_inline_prompts(db, data, user_id=user_id, block_id=block_id, count=count)


def run_sync(db, data: dict, *, user_id: str, scope: str, block_id: str | None = None,
             count: int = 3) -> list[dict]:
    """给同步路由用的包装：在线程自己的事件循环里跑一次模型调用。

    FastAPI 的同步路由跑在线程池里，线程内没有正在跑的事件循环，
    所以这里可以安全地 asyncio.run；不会占用主事件循环。
    """
    import asyncio

    return asyncio.run(suggest(db, data, user_id=user_id, scope=scope, block_id=block_id, count=count))


__all__ = [
    "MAX_COVER_PROMPTS",
    "MAX_INLINE_PROMPTS",
    "MAX_PROMPT_LEN",
    "run_sync",
    "suggest",
    "suggest_cover_prompts",
    "suggest_inline_prompts",
]
