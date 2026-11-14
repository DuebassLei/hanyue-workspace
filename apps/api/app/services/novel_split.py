"""按章节标题切分小说原文。纯函数，不碰数据库。"""

from __future__ import annotations

import re

# 行首「第…章/回/节/卷」或 1–3 级 Markdown 标题。
_HEADING = re.compile(
    r"^(?:\s*第[0-9一二三四五六七八九十百千零〇两]+[章回节卷].*|#{1,3}\s+.+)$"
)


def split_chapters(source_text: str) -> list[dict]:
    """返回 [{title, source_text}, ...]。

    标题行本身不进正文。没有任何标题时整篇作为一章，title 为「正文」，
    正文是原文 strip 后的全文。
    """
    text = source_text.replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    chapters: list[dict] = []
    current_title: str | None = None
    body: list[str] = []
    seen_heading = False

    def flush() -> None:
        if current_title is None:
            return
        chapters.append({"title": current_title, "source_text": "\n".join(body).strip()})

    for line in lines:
        if _HEADING.match(line):
            seen_heading = True
            flush()
            current_title = line.strip()
            body = []
            continue
        if current_title is None:
            current_title = ""
        body.append(line)

    if not seen_heading:
        stripped = text.strip()
        return [{"title": "正文", "source_text": stripped}] if stripped else []

    flush()
    return chapters
