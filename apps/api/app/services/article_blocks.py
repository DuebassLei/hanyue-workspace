"""段落级文章模型：Markdown 按段切块，所有修改都落到某一块上。

移植自 dsh-wechat-studio 的 `src/article.js`，行为逐条对齐：

- 分段只在空行处切，代码块内部的空行不切；
- 一级标题单独抽出来，不进段落；
- 模型给的改稿操作只认白名单，段号越界整条丢弃；
- 快照按段落 id 对齐做版本对比，避免增删导致后续段落错位。

这里只处理纯数据（dict / 基本类型），不碰数据库，便于单测与复用。
"""

from __future__ import annotations

import copy
import re
import uuid
from datetime import datetime, timezone

# 标题钩子类型，来自 skills/headline.md 的四大钩子库。成稿选标题时记下，复盘按它比较。
HOOKS = {"A": "自我打脸", "B": "反转", "C": "被求助", "D": "技术发现", "other": "其他"}
HOOK_IDS = tuple(HOOKS)

HISTORY_MAX = 20
BLOCK_TEXT_MAX = 20000
TITLE_TEXT_MAX = 80


def new_block_id(prefix: str = "b") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clip(value, limit: int) -> str:
    """模型输出可能是任何类型；只接受字符串，按 dsh 的 `text()` 规则裁剪。"""
    if not isinstance(value, str):
        return ""
    return value.replace("\r\n", "\n").replace("\r", "\n").strip()[:limit]


def split_markdown(markdown: str, *, body_only: bool = False) -> dict:
    """把 Markdown 切成 front matter、标题和正文块。代码块内部的空行不切。"""
    text = str(markdown or "").replace("\r\n", "\n").replace("\r", "\n")
    front = ""
    match = re.match(r"^---\n[\s\S]*?\n---\n?", text)
    if match and not body_only:
        front = match.group(0).strip()
        text = text[match.end():]
    blocks: list[str] = []
    current: list[str] = []
    fence: str | None = None

    def flush() -> None:
        nonlocal current
        joined = "\n".join(current).rstrip()
        if joined.strip():
            blocks.append(joined)
        current = []

    for line in text.split("\n"):
        mark = re.match(r"^\s*(```+|~~~+)", line)
        if mark:
            if not fence:
                fence = mark.group(1)[0]
            elif mark.group(1)[0] == fence:
                fence = None
            current.append(line)
            continue
        if not fence and not line.strip():
            flush()
        else:
            current.append(line)
    flush()
    title = ""
    if not body_only:
        head = next(
            (i for i, block in enumerate(blocks) if re.match(r"^#\s+\S", block) and "\n" not in block),
            -1,
        )
        if head != -1:
            title = re.sub(r"^#\s+", "", blocks.pop(head)).strip()
    return {"front": front, "title": title, "blocks": blocks}


def parse_markdown_image(text: str) -> dict | None:
    match = re.match(r'^!\[((?:\\.|[^\]\\])*)\]\(<?([^\s>)]+)>?(?:\s+"([^"]*)")?\)\s*$', str(text or ""))
    if not match:
        return None
    return {
        "caption": re.sub(r"\\([\\\[\]])", r"\1", match.group(1)),
        "src": match.group(2),
        "title": match.group(3) or "",
    }


def block_kind(text: str) -> str:
    """段落类型。顺序有意义：代码块优先于标题，媒体优先于表格。"""
    text = str(text or "")
    if re.match(r"^\s*(```|~~~)", text):
        return "code"
    if re.match(r"^#{2,6}\s", text):
        return "heading"
    if re.match(r"^>\s?", text):
        return "quote"
    if parse_markdown_image(text) or re.match(r"^\[[^\]]+\.(mp4|mov)\]\s*$", text, re.I):
        return "media"
    if re.search(r"^\|.*\|\s*$", text, re.M) and re.search(r"^\|?\s*:?-{2,}", text, re.M):
        return "table"
    if re.match(r"^\s*([-*+]|\d+[.)])\s", text):
        return "list"
    if re.match(r"^(-{3,}|\*{3,})\s*$", text):
        return "rule"
    return "para"


def plain_of(text: str) -> str:
    """去掉 Markdown 记号后的纯文字，用来和朱雀报告对段落。"""
    value = str(text)
    value = re.sub(r"!\[(?:\\.|[^\]\\])*\]\([^)]*\)", "", value)
    value = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", value)
    value = re.sub(r"^\s*>\s?(\[![A-Z]+\]\s*)?", "", value, flags=re.M)
    value = re.sub(r"^\s*([-*+]|\d+[.)])\s+", "", value, flags=re.M)
    value = re.sub(r"^#{1,6}\s+", "", value, flags=re.M)
    value = re.sub(r"(\*\*|==|\+\+|`|</?u>)", "", value)
    return value.strip()


def is_checkable(text: str) -> bool:
    """这一块要不要参加 AI 味质检：只看成段的文字。"""
    return block_kind(text) in ("para", "quote", "list") and len(plain_of(text)) >= 20


def to_markdown(article: dict) -> str:
    parts: list[str] = []
    if article.get("front"):
        parts.append(article["front"])
    if article.get("title"):
        parts.append(f"# {article['title']}")
    for block in article.get("blocks", []):
        parts.append(block["text"])
    return "\n\n".join(parts) + "\n"


def set_body(article: dict, markdown: str) -> None:
    """整篇替换正文。段落 id 全部重发，插图记账一并清空。"""
    parsed = split_markdown(markdown)
    if parsed["front"]:
        article["front"] = parsed["front"]
    if parsed["title"]:
        article["title"] = parsed["title"]
    article["blocks"] = [_new_block(text) for text in parsed["blocks"]]
    article["inline_diagrams"] = []


def _new_block(text: str, *, block_id: str | None = None, check: dict | None = None,
               prev_text: str | None = None, inserted_diagram_id: str | None = None) -> dict:
    return {
        "id": block_id or new_block_id(),
        "text": text,
        "kind": block_kind(text),
        "check": check,
        "prev_text": prev_text,
        "inserted_diagram_id": inserted_diagram_id,
    }


def build_article(topic: str = "", material: str = "", markdown: str = "") -> dict:
    """新建一篇可编辑稿件（内存结构，落库由仓储层负责）。"""
    parsed = split_markdown(markdown)
    stamp = now_iso()
    return {
        "topic": str(topic).strip()[:400],
        "material": str(material)[:60000],
        "front": parsed["front"],
        "title": parsed["title"],
        "titles": [],
        "summary": "",
        "hook": None,
        "blocks": [_new_block(text) for text in parsed["blocks"]],
        "chat": [],
        "zhuque": {"round": 0, "lastAt": None},
        "inline_diagrams": [],
        "banner_style": "poster",
        "banners": {},
        "layout_options": {},
        "follow_card": False,
        "cover": {},
        "notice": None,
        "archived": False,
        "last_view": "paper",
        "created_at": stamp,
        "updated_at": stamp,
    }


def touch(article: dict) -> None:
    article["updated_at"] = now_iso()


def snapshot_payload(article: dict) -> dict:
    """改动前存一份快照，供撤销与版本对比。"""
    return {
        "title": article.get("title", ""),
        "hook": article.get("hook"),
        "titles": copy.deepcopy(article.get("titles", [])),
        "blocks": [
            {
                "id": block["id"],
                "text": block["text"],
                "kind": block.get("kind") or block_kind(block["text"]),
                "check": copy.deepcopy(block.get("check")),
                "prev_text": block.get("prev_text"),
                "inserted_diagram_id": block.get("inserted_diagram_id"),
            }
            for block in article.get("blocks", [])
        ],
    }


def restore_payload(article: dict, payload: dict) -> None:
    article["title"] = payload.get("title", "")
    article["hook"] = payload.get("hook")
    article["titles"] = copy.deepcopy(payload.get("titles", []))
    article["blocks"] = [
        _new_block(
            row.get("text", ""),
            block_id=row.get("id") or new_block_id(),
            check=copy.deepcopy(row.get("check")),
            prev_text=row.get("prev_text"),
            inserted_diagram_id=row.get("inserted_diagram_id"),
        )
        for row in payload.get("blocks", [])
    ]
    touch(article)


def undo(article: dict, history: list[dict]) -> str | None:
    """退回最近一次快照。history 会被就地 pop 掉最后一条。"""
    if not history:
        return None
    last = history.pop()
    restore_payload(article, last.get("payload", {}))
    return last.get("label")


def apply_ops(article: dict, ops: list) -> tuple[list[str], list[int]]:
    """把模型给的修改落到文章上。

    模型输出不可信：只认白名单里的操作和字段，段号越界的直接丢弃。
    段号是 1 起的、按当前顺序数的；为了让一批操作里的段号都指向修改前的位置，
    先把段号解析成段落对象，再执行。
    """
    receipts: list[str] = []
    changed: list[int] = []
    blocks: list[dict] = article["blocks"]
    # 先解析，后执行：插入/删除不会让同一批里后续操作的段号漂移
    resolved: list[tuple[dict, dict | None, int]] = []
    for op in (ops[:40] if isinstance(ops, list) else []):
        if not isinstance(op, dict):
            resolved.append(({}, None, 0))
            continue
        number = op.get("block")
        index = (number - 1) if isinstance(number, int) and not isinstance(number, bool) else -1
        target = blocks[index] if 0 <= index < len(blocks) else None
        resolved.append((op, target, index + 1))

    for op, target, number in resolved:
        kind = op.get("op")
        if kind == "title":
            value = _clip(op.get("text"), TITLE_TEXT_MAX)
            if value and value != article.get("title"):
                article["title"] = value
                article["hook"] = op.get("hook") if op.get("hook") in HOOK_IDS else None
                receipts.append(f"标题改成《{value}》")
        elif kind == "titles":
            values = []
            for row in (op.get("list") if isinstance(op.get("list"), list) else []):
                text = _clip(row if isinstance(row, str) else (row or {}).get("text"), TITLE_TEXT_MAX)
                if not text:
                    continue
                hook = (row or {}).get("hook") if isinstance(row, dict) else None
                values.append({"text": text, "hook": hook if hook in HOOK_IDS else None})
            values = values[:8]
            if values:
                article["titles"] = values
                receipts.append(f"重写了 {len(values)} 个标题候选")
        elif kind == "rewrite" and target is not None:
            value = _clip(op.get("text"), BLOCK_TEXT_MAX)
            if value and value != target["text"]:
                target["text"] = value
                target["kind"] = block_kind(value)
                target["check"] = None
                changed.append(number)
                receipts.append(f"改写第 {number} 段")
        elif kind == "insert_after" and (target is not None or op.get("block") == 0):
            value = _clip(op.get("text"), BLOCK_TEXT_MAX)
            if value:
                at = _index_of(blocks, target) + 1 if target is not None else 0
                blocks.insert(at, _new_block(value))
                receipts.append(f"在第 {number} 段后面加了一段" if target is not None else "在开头加了一段")
        elif kind == "delete" and target is not None:
            at = _index_of(blocks, target)
            if at != -1:
                blocks.pop(at)
                receipts.append(f"删掉第 {number} 段")
    if receipts:
        touch(article)
    return receipts, changed


def _index_of(blocks: list[dict], target: dict) -> int:
    return next((i for i, block in enumerate(blocks) if block is target), -1)


def numbered(blocks: list[dict]) -> str:
    """带段号的正文，喂给模型时用。"""
    return "\n\n".join(f"【{i + 1}】{block['text']}" for i, block in enumerate(blocks))


def normalize_titles(raw, limit: int = 8) -> list[dict]:
    """标题候选规范化：去重、裁剪、钩子白名单。"""
    seen: set[str] = set()
    out: list[dict] = []
    for row in raw if isinstance(raw, list) else []:
        text = _clip(row if isinstance(row, str) else (row or {}).get("text"), TITLE_TEXT_MAX)
        if not text or text in seen:
            continue
        seen.add(text)
        hook = (row or {}).get("hook") if isinstance(row, dict) else None
        out.append({"text": text, "hook": hook if hook in HOOK_IDS else None})
        if len(out) >= limit:
            break
    return out


def readiness(article: dict, *, banner_ready: bool = False) -> dict:
    """发车体检：五项全过才算就绪。横幅是否就绪由调用方传入，避免耦合横幅模块。"""
    pending = any((block.get("check") or {}).get("status") in ("flag", "fixed") for block in article.get("blocks", []))
    items = [
        {"id": "body", "label": "正文", "done": bool(article.get("blocks"))},
        {"id": "title", "label": "标题", "done": bool(article.get("title"))},
        {"id": "quality", "label": "质检", "done": bool((article.get("zhuque") or {}).get("round")) and not pending},
        {"id": "cover", "label": "封面", "done": bool((article.get("cover") or {}).get("id"))},
        {"id": "banner", "label": "横幅", "done": banner_ready},
    ]
    return {"items": items, "done": sum(1 for item in items if item["done"]), "total": len(items)}


def list_meta(article: dict, *, banner_ready: bool = False) -> dict:
    chars = sum(len(plain_of(block["text"])) for block in article.get("blocks", []))
    review = sum(
        1 for block in article.get("blocks", []) if (block.get("check") or {}).get("status") in ("flag", "fixed")
    )
    return {
        "id": article["id"],
        "title": article.get("title") or article.get("topic") or "未命名文章",
        "topic": article.get("topic", ""),
        "updated_at": article.get("updated_at"),
        "chars": chars,
        "blocks": len(article.get("blocks", [])),
        "hook": article.get("hook"),
        "last_view": "layout" if article.get("last_view") == "layout" else "paper",
        "archived_at": article.get("archived_at"),
        "cover_id": (article.get("cover") or {}).get("id"),
        "review": review,
        "checked": bool((article.get("zhuque") or {}).get("round")),
        "scheduled_for": article.get("scheduled_for"),
        "published_at": article.get("published_at"),
        "publish_marked_at": article.get("publish_marked_at"),
        "readiness": readiness(article, banner_ready=banner_ready),
    }


def fix_plan(blocks: list[dict]) -> tuple[list[int], dict]:
    """一键处理要改哪些段。

    照 zhuque skill 的规矩跳过三种：改过两轮的（留给作者自己重说）、
    改完结论反而更重的（该退回上一版）、PDF 报告里 0.5–0.6 噪声带的。
    """
    picked: list[int] = []
    skipped = {"twice": 0, "worse": 0, "noise": 0}
    for index, block in enumerate(blocks):
        check = block.get("check") or {}
        if check.get("status") != "flag":
            continue
        fixes = check.get("fixes") or 0
        score = check.get("score")
        score = score if isinstance(score, (int, float)) else 0
        if check.get("worse"):
            skipped["worse"] += 1
        elif fixes >= 2:
            skipped["twice"] += 1
        elif check.get("source") != "api" and check.get("level") != "ai" and score < 0.6:
            skipped["noise"] += 1
        else:
            picked.append(index)
    return picked, skipped


def fix_groups(blocks: list[dict], picked: list[int], *, max_blocks: int = 5, max_chars: int = 900) -> list[list[int]]:
    """把要改的段按相邻关系分组。

    朱雀常把几段短段落判成一个片段，并列、对仗这类问题也常跨段，一起改才看得到。
    中间隔着一个标题或图片就断开（只按相邻下标判断）。每组最多 5 段、约 900 字。
    排序照 skill 第 3 步：含 AI 特征的组先改，再按分数从高到低。
    """
    groups: list[list[int]] = []
    current: list[int] = []
    chars = 0
    for index in picked:
        size = len(plain_of(blocks[index]["text"]))
        adjacent = bool(current) and index == current[-1] + 1
        if current and (not adjacent or len(current) >= max_blocks or chars + size > max_chars):
            groups.append(current)
            current = []
            chars = 0
        current.append(index)
        chars += size
    if current:
        groups.append(current)

    def weight(group: list[int]) -> float:
        return max(
            (2 if (blocks[i].get("check") or {}).get("level") == "ai" else 1)
            + ((blocks[i].get("check") or {}).get("score") or 0)
            for i in group
        )

    return sorted(groups, key=weight, reverse=True)


def revision_changes(history: list[dict], article: dict) -> list[dict]:
    """相邻快照就是一次修改前后的内容；按段落 id 对齐，避免增删导致后续段落错位。"""
    out: list[dict] = []
    for index, entry in enumerate(history):
        # 仓储层给的是 {label, at, payload}，纯函数层也接受裸快照
        before = entry.get("payload", entry)
        label = entry.get("label") or before.get("label", "")
        at = entry.get("at") or before.get("at")
        after = history[index + 1].get("payload", history[index + 1]) if index + 1 < len(history) else article
        rows: list[dict] = []
        if before.get("title") != after.get("title"):
            rows.append({
                "kind": "title",
                "before": {"text": before.get("title")} if before.get("title") else None,
                "after": {"text": after.get("title")} if after.get("title") else None,
            })
        old_blocks = before.get("blocks", [])
        new_blocks = after.get("blocks", [])
        old_map = {block["id"]: {"text": block["text"], "number": i + 1} for i, block in enumerate(old_blocks)}
        new_ids = {block["id"] for block in new_blocks}
        old_rank = {block["id"]: rank for rank, block in enumerate(row for row in old_blocks if row["id"] in new_ids)}
        new_rank = {block["id"]: rank for rank, block in enumerate(row for row in new_blocks if row["id"] in old_map)}
        cursor = 0
        for i, block in enumerate(new_blocks):
            old = old_map.get(block["id"])
            if old:
                while cursor < old["number"] - 1:
                    pending = old_blocks[cursor]
                    cursor += 1
                    if pending["id"] not in new_ids:
                        rows.append({"kind": "delete", "before": old_map[pending["id"]], "after": None})
                cursor = max(cursor, old["number"])
            if not old or old["text"] != block["text"]:
                rows.append({
                    "kind": "rewrite" if old else "insert",
                    "before": old,
                    "after": {"text": block["text"], "number": i + 1},
                })
            elif old_rank.get(block["id"]) != new_rank.get(block["id"]):
                rows.append({"kind": "move", "before": old, "after": {"text": block["text"], "number": i + 1}})
        for block in old_blocks[cursor:]:
            if block["id"] not in new_ids:
                rows.append({"kind": "delete", "before": old_map[block["id"]], "after": None})
        if rows:
            out.append({"index": index, "label": label, "at": at, "rows": rows})
    return list(reversed(out))
