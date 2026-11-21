"""成稿流水线：写正文 → 出标题；对话改稿；按质检结论改标红段。

移植自 dsh-wechat-studio 的 `src/pipeline.js`，保留其核心约束：

- 任务在后台线程里跑，关掉页面不会中断；
- 同一篇文章同时只允许一个任务；
- 模型只输出结构化修改，落稿一律走 `article_blocks.apply_ops` 的白名单；
- 一键处理按相邻分组、三组并发，可中途停止，某一组失败不影响其他组；
- 每次改动前存快照，撤销一次退回一步。

与本项目的差异：模型统一走 `ai_gateway.generate_text`（项目已有模型连接管理），
横幅校验复用 `wechat_layout_engine.check_banner`（项目已有移植版）。
"""

from __future__ import annotations

import asyncio
import copy
import json
import re
import threading
from pathlib import Path

from .. import models
from ..core.db import SessionLocal
from . import ai_gateway, article_blocks as AB, article_quality as AQ, studio_repo, wechat_layout_engine

SKILL_DIR = Path(__file__).resolve().parent.parent / "resources" / "studio_skills"
BATCH_CONCURRENCY = 3

DRAFT_RULES = """# 本次输出要求
- 只输出文章本身的 Markdown，不要任何解释、前言或结语说明。
- 第一行是 `# 标题`，小节用 `## 小标题`。
- 只用素材里出现过的事实、数字和经历。缺真实细节的地方写 `[可补：需要什么]` 占位，不要编造。
- 需要截图的地方写 `[截图：内容]` 占位。"""

TITLE_RULES = """# 本次输出要求
为文章写 5 个候选标题和一条摘要。选出最准确、具体且有吸引力的一条放在第一位，给出一句具体的主推理由（40 字以内）。
每个标题标注它用的钩子：A 自我打脸、B 反转、C 被求助、D 技术发现；都不像就填 other。只输出 JSON：
{"titles":[{"text":"标题一","hook":"C"},{"text":"标题二","hook":"A"},{"text":"标题三","hook":"B"},{"text":"标题四","hook":"D"},{"text":"标题五","hook":"other"}],"summary":"摘要","recommendationReason":"主推第一条的理由"}"""

CHAT_RULES = """# 你现在的工作：按作者的一句话改稿
作者会给你一篇分好段、带段号的文章，和一句修改意见。你只输出 JSON，不要解释：
{"reply":"一句话说明改了什么","ops":[操作, ...]}

可用的操作：
- {"op":"rewrite","block":段号,"text":"改写后的整段 Markdown"}
- {"op":"insert_after","block":段号,"text":"新段落"}（block 填 0 表示插在最前面）
- {"op":"delete","block":段号}
- {"op":"title","text":"新标题","hook":"C"}
- {"op":"titles","list":[{"text":"候选一","hook":"C"},{"text":"候选二","hook":"A"}]}

规则：
- 只动作者点名的段落。没点名、但意见明显只针对某几段时，只改那几段。其余段落一个字都不要动。
- 段号用文章里【】中的数字，指的是修改前的位置。
- 改写时保留这一段原有的 Markdown 记号（加粗、引用、列表、代码块）。
- 改标题时给 hook 标注钩子：A 自我打脸、B 反转、C 被求助、D 技术发现，都不像填 other。
- 不编造作者没提供的经历、数字和原话。缺细节就写 [可补：需要什么]。
- 作者只是提问、没让你改时，ops 留空数组，在 reply 里回答。"""

FIX_RULES = """# 本次输出要求
朱雀把下面文章里的某一段标成了 AI 味偏重。按上面两份规则只改写这一段：
- 先找结构问题（整齐的并列、对仗金句、段尾总结、套路过渡），再看用词。
- 不硬加口语，不用排比制造说话感，不编造经历。缺真实细节的地方写 [可补：需要什么]。
- 保留这一段原有的 Markdown 记号，长度和原段大致相当。
- 只输出改写后的这一段，不要解释，不要带段号。"""

BATCH_RULES = """# 本次输出要求
朱雀把下面文章里相邻的几段标成了 AI 味偏重，它们通常被判成同一个片段。按上面两份规则把这几段一起改写：
- 先找跨段的结构问题：整齐的并列、对仗金句、每段一句总结、套路过渡。几段之间要详略不齐，不要每段长得一样。
- 再看用词。不硬加口语，不用排比制造说话感，不编造经历。缺真实细节的地方写 [可补：需要什么]。
- 段数不变，每段保留原有的 Markdown 记号，总长度和原来大致相当。没有问题的段可以原样交回。
- 只输出 JSON，不要解释：{"paragraphs":[{"block":段号,"text":"改写后的整段"}]}，段号用下面给出的编号。"""

BANNER_STYLES = {
    "poster": {"name": "大字海报", "from": "## 风格三", "to": "## 设计规则"},
    "card": {"name": "标题卡", "from": "## 风格二", "to": "## 风格三"},
    "scene": {"name": "动画场景", "from": "## 十种场景模板", "to": "## 风格二"},
}
DEFAULT_BANNER = "poster"

BANNER_SHAPE = {
    "poster": '{"slug":"英文短名","title":"开头横幅 · 大字海报 · 主题","style":"poster","poster":{...}}',
    "card": '{"slug":"英文短名","title":"开头横幅 · 标题卡 · 主题","style":"card","card":{...}}',
    "scene": '{"slug":"英文短名","title":"开头横幅 · 主题","caption":"产品名 实测","scene":{"layout":"模板名", ...}}',
}


class PipelineError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


# ---------------- skill 与模型 ----------------


def load_skill(name: str) -> str:
    path = SKILL_DIR / f"{name}.md"
    if not path.is_file():
        raise PipelineError(f"找不到 skill 文件：{name}.md", 500)
    raw = path.read_text(encoding="utf-8")
    # 去掉 front matter，只把正文喂给模型
    return re.sub(r"\A---\n[\s\S]*?\n---\n", "", raw).strip()


def clean_markdown(text: str) -> str:
    value = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    fence = re.match(r"^\s*```(?:markdown|md)?\n([\s\S]*?)\n```\s*$", value)
    return fence.group(1).strip() if fence else value


def extract_json(text: str):
    """从模型输出里抠出第一个 JSON 对象。模型常在前后带解释或代码围栏。"""
    value = str(text or "")
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", value)
    if fence:
        value = fence.group(1)
    start = value.find("{")
    while start != -1:
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(value)):
            char = value[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(value[start:index + 1])
                    except ValueError:
                        break
        start = value.find("{", start + 1)
    return None


async def _model(owner_user_id: str, system: str, user: str, task: str) -> str:
    with SessionLocal() as db:
        result = await ai_gateway.generate_text(
            db,
            owner_user_id=owner_user_id,
            task_type=task,
            prompt=user,
            system_prompt=system,
        )
    text = str(result.get("text") or "").strip()
    if not text:
        raise PipelineError(
            f"模型没有返回内容（{result.get('errorCode') or 'provider_unavailable'}），先到模型管理检查连接与默认模型。",
            502,
        )
    return text


# ---------------- 任务注册表 ----------------


class StudioJobs:
    """同一篇文章只允许一个后台任务；进度、目标段、停止标志都挂在这里。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, dict] = {}

    def get(self, article_id: str) -> dict | None:
        with self._lock:
            job = self._jobs.get(article_id)
            if not job:
                return None
            return {
                "kind": job["kind"],
                "label": job["label"],
                "text": job["text"],
                "target": job.get("target"),
                "targets": sorted(job["targets"]),
                "progress": job.get("progress"),
                "stopping": bool(job.get("stopping")),
            }

    def busy(self, article_id: str) -> bool:
        with self._lock:
            return article_id in self._jobs

    def stop(self, article_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(article_id)
            if not job or job["kind"] != "fixall":
                return False
            job["stopping"] = True
            return True

    def start(self, article_id: str, kind: str, label: str, work, *, target: str | None = None) -> dict:
        with self._lock:
            if article_id in self._jobs:
                raise PipelineError("这篇文章还有任务在跑，等它结束再试。", 409)
            job = {
                "kind": kind,
                "label": label,
                "text": "",
                "target": target,
                "targets": set(),
                "progress": None,
                "stopping": False,
            }
            self._jobs[article_id] = job

        def runner() -> None:
            try:
                asyncio.run(work(job))
            except Exception as exc:  # noqa: BLE001 - 后台任务要把失败写回文章
                _write_notice(article_id, f"{label}失败：{exc}", error=True)
            finally:
                with self._lock:
                    self._jobs.pop(article_id, None)

        thread = threading.Thread(target=runner, daemon=True, name=f"studio-{article_id[:8]}")
        job["thread"] = thread
        thread.start()
        return self.get(article_id) or {}

    def run_now(self, kind: str, label: str, work, *, target: str | None = None) -> None:
        """测试与同步调用用：直接跑完，不走线程。"""
        job = {"kind": kind, "label": label, "text": "", "target": target, "targets": set(),
               "progress": None, "stopping": False}
        asyncio.run(work(job))


def _write_notice(article_id: str, text: str, *, error: bool = False) -> None:
    from datetime import datetime, timezone

    with SessionLocal() as db:
        row = db.get(models.StudioArticle, article_id)
        if not row:
            return
        row.notice = {"error": error, "text": text, "at": datetime.now(timezone.utc).isoformat()}
        db.commit()


JOBS = StudioJobs()


def _session_article(db, article_id: str) -> models.StudioArticle:
    row = db.get(models.StudioArticle, article_id)
    if not row:
        raise PipelineError("稿件不存在", 404)
    return row


# ---------------- 流水线 ----------------


class StudioPipeline:
    def __init__(self, jobs: StudioJobs | None = None) -> None:
        self.jobs = jobs or JOBS

    # ---- 写正文 ----

    def draft(self, article_id: str) -> dict:
        return self.jobs.start(article_id, "draft", "写正文", lambda job: self._draft(article_id, job))

    def draft_now(self, article_id: str) -> None:
        self.jobs.run_now("draft", "写正文", lambda job: self._draft(article_id, job))

    async def _draft(self, article_id: str, job: dict) -> None:
        with SessionLocal() as db:
            row = _session_article(db, article_id)
            data = studio_repo.row_to_dict(row)
            owner = row.owner_user_id
        system = f"{load_skill('writer')}\n\n{DRAFT_RULES}"
        user = f"选题：{data.get('topic') or '（作者没有写选题，请从素材里提炼）'}\n\n素材：\n{data.get('material') or '（没有额外素材）'}"
        text = await _model(owner, system, user, "studio_draft")
        with SessionLocal() as db:
            row = _session_article(db, article_id)
            data = studio_repo.row_to_dict(row)
            AB.set_body(data, clean_markdown(text))
            data["notice"] = None
            studio_repo.save(db, row, data)
            db.commit()
        job["kind"] = "titles"
        job["label"] = "出标题"
        job["text"] = ""
        await self._titles(article_id, job)

    # ---- 出标题 ----

    def titles(self, article_id: str) -> dict:
        return self.jobs.start(article_id, "titles", "出标题", lambda job: self._titles(article_id, job))

    def titles_now(self, article_id: str) -> None:
        self.jobs.run_now("titles", "出标题", lambda job: self._titles(article_id, job))

    async def _titles(self, article_id: str, job: dict) -> None:
        with SessionLocal() as db:
            row = _session_article(db, article_id)
            data = studio_repo.row_to_dict(row)
            owner = row.owner_user_id
        system = f"{load_skill('headline')}\n\n{TITLE_RULES}"
        raw = await _model(owner, system, AB.to_markdown(data), "studio_title")
        parsed = extract_json(raw)
        titles = AB.normalize_titles(parsed.get("titles") if isinstance(parsed, dict) else None)
        if not titles:
            raise PipelineError("模型没有给出可用的标题", 502)
        reason = parsed.get("recommendationReason") if isinstance(parsed, dict) else None
        reason = reason.strip()[:80] if isinstance(reason, str) else ""
        with SessionLocal() as db:
            row = _session_article(db, article_id)
            data = studio_repo.row_to_dict(row)
            studio_repo.snapshot(db, row, data, "出标题")
            data["titles"] = [
                {**item, **({"recommended": True, "reason": reason} if index == 0 and reason else {})}
                for index, item in enumerate(titles)
            ]
            summary = parsed.get("summary") if isinstance(parsed, dict) else None
            if isinstance(summary, str):
                data["summary"] = summary.strip()[:300]
            if not data.get("title"):
                data["title"] = titles[0]["text"]
                data["hook"] = titles[0]["hook"]
            AB.touch(data)
            studio_repo.save(db, row, data)
            db.commit()

    # ---- 对话改稿 ----

    def chat(self, article_id: str, message: str) -> dict:
        return self.jobs.start(article_id, "chat", "改稿", lambda job: self._chat(article_id, job, message))

    def chat_now(self, article_id: str, message: str) -> None:
        self.jobs.run_now("chat", "改稿", lambda job: self._chat(article_id, job, message))

    async def _chat(self, article_id: str, job: dict, message: str) -> None:
        with SessionLocal() as db:
            row = _session_article(db, article_id)
            data = studio_repo.row_to_dict(row)
            owner = row.owner_user_id
        history = "\n".join(
            f"{'作者' if turn.get('role') == 'user' else '你'}：{turn.get('text', '')}"
            for turn in (data.get("chat") or [])[-6:]
        )
        system = f"{load_skill('writer')}\n\n{CHAT_RULES}"
        user = (
            f"# 当前标题\n{data.get('title') or '（还没有）'}\n\n"
            f"# 标题候选\n" + ("\n".join(f"- {t['text']}" for t in data.get("titles") or []) or "（还没有）") + "\n\n"
            f"# 正文\n{AB.numbered(data['blocks'])}\n\n"
            f"# 之前的对话\n{history or '（无）'}\n\n"
            f"# 作者这次说\n{message}"
        )
        raw = await _model(owner, system, user, "studio_chat")
        parsed = extract_json(raw)
        with SessionLocal() as db:
            row = _session_article(db, article_id)
            data = studio_repo.row_to_dict(row)
            if not isinstance(parsed, dict):
                data["chat"] = (data.get("chat") or []) + [{
                    "role": "assistant",
                    "text": "这次没能读懂模型的回复，稿子没有改动。换个说法再试一次。",
                    "error": True,
                }]
                studio_repo.save(db, row, data)
                db.commit()
                return
            revision = studio_repo.snapshot(db, row, data, message[:30])
            receipts, _changed = AB.apply_ops(data, parsed.get("ops"))
            if not receipts:
                db.delete(revision)
                db.flush()
            reply = parsed.get("reply")
            reply = reply.strip()[:600] if isinstance(reply, str) else ""
            chat = data.get("chat") or []
            chat.append({
                "role": "assistant",
                "text": reply or ("改好了。" if receipts else "这次没有改动。"),
                "receipts": receipts,
                "undoable": bool(receipts),
            })
            data["chat"] = chat[-60:]
            studio_repo.save(db, row, data)
            db.commit()

    # ---- 单段修复 ----

    def fix(self, article_id: str, block_id: str) -> dict:
        return self.jobs.start(
            article_id, "fix", "改标红段", lambda job: self._fix(article_id, job, block_id), target=block_id
        )

    def fix_now(self, article_id: str, block_id: str) -> None:
        self.jobs.run_now("fix", "改标红段", lambda job: self._fix(article_id, job, block_id), target=block_id)

    async def _fix(self, article_id: str, job: dict, block_id: str) -> None:
        with SessionLocal() as db:
            row = _session_article(db, article_id)
            data = studio_repo.row_to_dict(row)
            owner = row.owner_user_id
        index = next((i for i, block in enumerate(data["blocks"]) if block["id"] == block_id), -1)
        if index == -1:
            raise PipelineError("这一段已经不在了", 404)
        block = data["blocks"][index]
        system = f"{load_skill('humanizer')}\n\n---\n\n{load_skill('zhuque')}\n\n{FIX_RULES}"
        check = block.get("check") or {}
        score = f"朱雀给它的 AIGC 值是 {check['score']}。" if isinstance(check.get("score"), (int, float)) else ""
        user = (
            f"# 全文（供你了解上下文，不要改别的段）\n{AB.numbered(data['blocks'])}\n\n"
            f"# 要改写的是第 {index + 1} 段\n{score}\n{block['text']}"
        )
        text = re.sub(r"^【\d+】", "", clean_markdown(await _model(owner, system, user, "studio_fix"))).strip()
        if not text:
            raise PipelineError("模型没有给出改写结果", 502)
        with SessionLocal() as db:
            row = _session_article(db, article_id)
            data = studio_repo.row_to_dict(row)
            current = next((item for item in data["blocks"] if item["id"] == block_id), None)
            if not current:
                return
            studio_repo.snapshot(db, row, data, f"改写第 {index + 1} 段")
            before = current.get("check") or {}
            current["prev_text"] = current["text"]
            current["text"] = text
            current["kind"] = AB.block_kind(text)
            current["check"] = {
                "status": "fixed",
                "score": before.get("score"),
                "level": before.get("level"),
                "source": before.get("source"),
                "round": before.get("round") or (data.get("zhuque") or {}).get("round") or 0,
                "fixes": (before.get("fixes") or 0) + 1,
            }
            AB.touch(data)
            studio_repo.save(db, row, data)
            db.commit()

    # ---- 一键处理 ----

    def fix_all(self, article_id: str) -> dict:
        return self.jobs.start(article_id, "fixall", "一键处理", lambda job: self._fix_all(article_id, job))

    def fix_all_now(self, article_id: str) -> None:
        self.jobs.run_now("fixall", "一键处理", lambda job: self._fix_all(article_id, job))

    def stop(self, article_id: str) -> bool:
        return self.jobs.stop(article_id)

    async def _fix_all(self, article_id: str, job: dict) -> None:
        with SessionLocal() as db:
            row = _session_article(db, article_id)
            data = studio_repo.row_to_dict(row)
            owner = row.owner_user_id
        picked, skipped = AB.fix_plan(data["blocks"])
        if not picked:
            raise PipelineError("没有需要处理的标红段", 400)
        groups = AB.fix_groups(data["blocks"], picked)
        ids = [[data["blocks"][i]["id"] for i in group] for group in groups]
        job["progress"] = {"done": 0, "total": len(picked)}
        with SessionLocal() as db:
            row = _session_article(db, article_id)
            data = studio_repo.row_to_dict(row)
            studio_repo.snapshot(db, row, data, f"一键处理 {len(picked)} 段")
            db.commit()

        system = f"{load_skill('humanizer')}\n\n---\n\n{load_skill('zhuque')}\n\n{BATCH_RULES}"
        stats = {"changed": 0, "failed": 0}
        semaphore = asyncio.Semaphore(BATCH_CONCURRENCY)

        async def run_group(group_ids: list[str]) -> None:
            if job.get("stopping"):
                return
            for block_id in group_ids:
                job["targets"].add(block_id)
            try:
                async with semaphore:
                    with SessionLocal() as db:
                        row = _session_article(db, article_id)
                        current = studio_repo.row_to_dict(row)
                    pairs = []
                    for block_id in group_ids:
                        number = next(
                            (i + 1 for i, block in enumerate(current["blocks"]) if block["id"] == block_id), 0
                        )
                        if number > 0:
                            pairs.append((number, block_id))
                    numbers = [number for number, _ in pairs]
                    if not numbers:
                        return
                    parts = []
                    for number in numbers:
                        check = current["blocks"][number - 1].get("check") or {}
                        note = (
                            f"朱雀判为{'AI 特征' if check.get('level') == 'ai' else '疑似 AI'}"
                            if check.get("source") == "api"
                            else f"AIGC 值 {check.get('score')}"
                        )
                        parts.append(f"【{number}】（{note}）\n{current['blocks'][number - 1]['text']}")
                    user = (
                        f"# 全文（供你了解上下文，只改下面点名的段）\n{AB.numbered(current['blocks'])}"
                        f"\n\n# 要一起改写的段\n" + "\n\n".join(parts)
                    )
                    parsed = extract_json(await _model(owner, system, user, "studio_fixall"))
                    rows = parsed.get("paragraphs") if isinstance(parsed, dict) else None
                    by_number = {}
                    for item in rows if isinstance(rows, list) else []:
                        if not isinstance(item, dict):
                            continue
                        number = item.get("block")
                        if not isinstance(number, int) or isinstance(number, bool):
                            continue
                        text = item.get("text")
                        if isinstance(text, str):
                            by_number[number] = re.sub(r"^【\d+】", "", clean_markdown(text)).strip()
                    if not by_number:
                        raise PipelineError("模型没有给出可用的改写", 502)
                with SessionLocal() as db:
                    row = _session_article(db, article_id)
                    latest = studio_repo.row_to_dict(row)
                    for number, block_id in pairs:
                        target = next((item for item in latest["blocks"] if item["id"] == block_id), None)
                        text = by_number.get(number)
                        if not target or not text or len(text) > 20000:
                            continue
                        rewritten = text != target["text"]
                        if rewritten:
                            target["prev_text"] = target["text"]
                            target["text"] = text
                            target["kind"] = AB.block_kind(text)
                            stats["changed"] += 1
                        before = target.get("check") or {}
                        target["check"] = {
                            "status": "fixed",
                            "score": before.get("score"),
                            "level": before.get("level"),
                            "source": before.get("source"),
                            "round": before.get("round") or (latest.get("zhuque") or {}).get("round") or 0,
                            "fixes": (before.get("fixes") or 0) + (1 if rewritten else 0),
                        }
                    AB.touch(latest)
                    studio_repo.save(db, row, latest)
                    db.commit()
            except Exception:  # noqa: BLE001 - 单组失败不影响其他组
                stats["failed"] += len(group_ids)
            finally:
                for block_id in group_ids:
                    job["targets"].discard(block_id)
                if job.get("progress"):
                    job["progress"]["done"] += len(group_ids)

        await asyncio.gather(*(run_group(group) for group in ids))

        with SessionLocal() as db:
            row = _session_article(db, article_id)
            data = studio_repo.row_to_dict(row)
            left = len(picked) - (job.get("progress") or {}).get("done", 0)
            todo = [
                i + 1
                for i, block in enumerate(data["blocks"])
                if re.search(r"\[可补[：:]", block["text"]) and (block.get("check") or {}).get("status") == "fixed"
            ]
            notes = []
            if job.get("stopping"):
                notes.append(f"已停止：处理了 {(job.get('progress') or {}).get('done', 0)} 段，改写 {stats['changed']} 段，还有 {left} 段没动。")
            else:
                notes.append(f"一键处理完：改写了 {stats['changed']} 段。")
            if stats["failed"]:
                notes.append(f"{stats['failed']} 段这次没改成，可以再点一次。")
            skip = [
                f"{skipped['twice']} 段改过两轮，留给你自己重说" if skipped["twice"] else "",
                f"{skipped['worse']} 段建议退回上一版" if skipped["worse"] else "",
                f"{skipped['noise']} 段在噪声带" if skipped["noise"] else "",
            ]
            skip = [item for item in skip if item]
            if skip:
                notes.append("跳过：" + "，".join(skip) + "。")
            if todo:
                notes.append(f"第 {'、'.join(str(n) for n in todo)} 段留了 [可补]，要你补真实细节。")
            notes.append("再检测一轮看效果。")
            data["notice"] = {
                "error": bool(stats["changed"] == 0 and stats["failed"]),
                "text": "".join(notes),
                "at": AB.now_iso(),
            }
            studio_repo.save(db, row, data)
            db.commit()

    # ---- 开头横幅 ----

    def banner(self, article_id: str) -> dict:
        return self.jobs.start(article_id, "banner", "做开头横幅", lambda job: self._banner(article_id, job))

    def banner_now(self, article_id: str) -> None:
        self.jobs.run_now("banner", "做开头横幅", lambda job: self._banner(article_id, job))

    async def _banner(self, article_id: str, job: dict) -> None:
        with SessionLocal() as db:
            row = _session_article(db, article_id)
            data = studio_repo.row_to_dict(row)
            owner = row.owner_user_id
        style = data.get("banner_style") or DEFAULT_BANNER
        if style not in BANNER_STYLES:
            raise PipelineError(f"未知的横幅风格：{style}", 400)
        guide = _banner_guide(load_skill("banner"), style)
        system = f"{guide}\n\n{_banner_rules(style)}"
        body = AB.to_markdown(data)[:30000]

        async def ask(user: str):
            return _normalize_spec(extract_json(await _model(owner, system, user, "studio_banner")), style)

        spec = await ask(body)
        if not spec:
            raise PipelineError("模型没有给出可用的横幅规格", 502)
        warnings = _check_banner(spec)
        if warnings:
            retry = await ask(f"{body}\n\n# 你上一次写的规格\n{json.dumps(spec, ensure_ascii=False)}\n\n# 横幅脚本检查出的问题，逐条改掉，其余不动\n" + "\n".join(f"- {w}" for w in warnings))
            if retry:
                again = _check_banner(retry)
                if len(again) < len(warnings):
                    spec, warnings = retry, again
        with SessionLocal() as db:
            row = _session_article(db, article_id)
            data = studio_repo.row_to_dict(row)
            banners = dict(data.get("banners") or {})
            banners[style] = {"spec": spec, "warnings": warnings, "at": AB.now_iso()}
            data["banners"] = banners
            AB.touch(data)
            studio_repo.save(db, row, data)
            db.commit()

    # ---- 质检：API / 报告 / 本地兜底 ----

    def detect(self, article_id: str, *, key: str | None, endpoint: str | None, fixed_lines: list[str] | None = None) -> dict:
        return self.jobs.start(
            article_id, "report", "朱雀检测", lambda job: self._detect(article_id, job, key, endpoint, fixed_lines or [])
        )

    def detect_now(self, article_id: str, *, key: str | None, endpoint: str | None, fixed_lines: list[str] | None = None) -> None:
        self.jobs.run_now(
            "report", "朱雀检测", lambda job: self._detect(article_id, job, key, endpoint, fixed_lines or [])
        )

    async def _detect(self, article_id: str, job: dict, key: str | None, endpoint: str | None, fixed_lines: list[str]) -> None:
        with SessionLocal() as db:
            row = _session_article(db, article_id)
            data = studio_repo.row_to_dict(row)

        if not key:
            # 没有密钥：走本地启发式，标记来源，不冒充朱雀结论
            result = AQ.local_check(data)
            with SessionLocal() as db:
                row = _session_article(db, article_id)
                latest = studio_repo.row_to_dict(row)
                summary = AQ.mark_blocks(
                    latest,
                    {"segments": result["segments"], "overall": result["overall"]},
                    AQ.LOCAL_SOURCE,
                )
                latest["zhuque"]["note"] = "未配置朱雀 API Key，本次为本地规则打分，不能与朱雀结论直接比较"
                latest["notice"] = {
                    "text": _round_notice(summary, latest["zhuque"].get("round", 0), "（本地规则，未调用朱雀）"),
                    "at": AB.now_iso(),
                }
                studio_repo.save(db, row, latest)
                db.commit()
            return

        text = _detection_text(AB.to_markdown(data), fixed_lines)
        found = await AQ.detect(text, key=key, endpoint=endpoint)
        segments, covered = AQ.map_segments(data["blocks"], found["segments"])
        if not covered:
            raise PipelineError("朱雀返回的片段和文章段落一段都没对上，这次结果没有采用", 502)
        with SessionLocal() as db:
            row = _session_article(db, article_id)
            latest = studio_repo.row_to_dict(row)
            summary = AQ.mark_blocks(
                latest,
                {"segments": segments, "covered": covered, "overall": found.get("overall")},
                "api",
            )
            extra = f"本次用了 {found['tokens']} token。" if found.get("tokens") is not None else ""
            latest["notice"] = {
                "text": _round_notice(summary, latest["zhuque"]["round"], extra),
                "at": latest["zhuque"]["lastAt"],
            }
            studio_repo.save(db, row, latest)
            db.commit()

    def report(self, article_id: str, report_text: str) -> dict:
        return self.jobs.start(article_id, "report", "读朱雀报告", lambda job: self._report(article_id, job, report_text))

    def report_now(self, article_id: str, report_text: str) -> None:
        self.jobs.run_now("report", "读朱雀报告", lambda job: self._report(article_id, job, report_text))

    async def _report(self, article_id: str, job: dict, report_text: str) -> None:
        with SessionLocal() as db:
            row = _session_article(db, article_id)
            data = studio_repo.row_to_dict(row)
            owner = row.owner_user_id

        async def mapper(text: str, rows: list[str]):
            system = AQ.REPORT_SYSTEM
            user = f"# 朱雀报告\n{text[:60000]}\n\n# 文章段落\n" + "\n\n".join(rows)
            return extract_json(await _model(owner, system, user, "studio_report"))

        summary = await AQ.apply_report(data, report_text, mapper=mapper)
        with SessionLocal() as db:
            row = _session_article(db, article_id)
            latest = studio_repo.row_to_dict(row)
            latest["blocks"] = data["blocks"]
            latest["zhuque"] = data["zhuque"]
            latest["notice"] = {
                "text": _round_notice(summary, latest["zhuque"]["round"], ""),
                "at": latest["zhuque"].get("lastAt"),
            }
            studio_repo.save(db, row, latest)
            db.commit()

    # ---- 提示条 ----

    def clear_notice(self, article_id: str) -> None:
        with SessionLocal() as db:
            row = _session_article(db, article_id)
            row.notice = None
            db.commit()


PIPELINE = StudioPipeline()


# ---------------- 横幅辅助 ----------------


def _section(text: str, start_marker: str, end_marker: str) -> str:
    start = text.find(start_marker)
    if start == -1:
        return ""
    end = text.find(end_marker, start + len(start_marker))
    return text[start:end if end != -1 else None].strip()


def _banner_guide(skill: str, style: str) -> str:
    meta = BANNER_STYLES[style]
    body = _section(skill, meta["from"], meta["to"])
    if not body:
        raise PipelineError("横幅 skill 里找不到这种风格的说明", 500)
    return f"{body}\n\n{_section(skill, '## 设计规则', '## 公众号限制')}"


def _banner_rules(style: str) -> str:
    return f"""# 你现在的工作
读下面这篇公众号文章，按上面的规则为它写一份开头横幅的规格。

- 内容全部取自原文：起点（作者交代了什么）、谁在干活、产出了什么。不编数字、不编功能、不编产品名。
- 严格遵守上面写的字数上限，宁可短，不要超。
- 只输出一个 JSON 对象，不要解释。形状是：
{BANNER_SHAPE[style]}"""


def _normalize_spec(raw, style: str) -> dict | None:
    """模型给的规格不可信：只认对应风格的结构，风格字段由我们定。"""
    if not isinstance(raw, dict):
        return None
    key = "scene" if style == "scene" else style
    body = raw.get(key)
    if not isinstance(body, dict):
        return None
    if style == "scene" and not isinstance(body.get("layout"), str):
        return None
    slug = raw.get("slug")
    title = raw.get("title")
    spec = {
        "slug": slug if isinstance(slug, str) and re.fullmatch(r"[\w-]{1,40}", slug) else "banner",
        "title": title[:60] if isinstance(title, str) else "开头横幅",
        key: copy.deepcopy(body),
    }
    if style != "scene":
        spec["style"] = style
        # 配色交给排版页：不写 theme，横幅才会跟着正文的配色一起换
        spec[key].pop("theme", None)
    elif isinstance(raw.get("caption"), str):
        spec["caption"] = raw["caption"][:30]
    return spec


def _check_banner(spec: dict) -> list[str]:
    try:
        return wechat_layout_engine.check_banner(copy.deepcopy(spec), [wechat_layout_engine.build_banner(copy.deepcopy(spec))])
    except Exception as exc:  # noqa: BLE001 - 画不出来本身就是一条问题
        return [f"规格画不出来：{type(exc).__name__}: {exc}"]


def _round_notice(summary: dict, round_no: int, extra: str = "") -> str:
    head = f"朱雀第 {round_no} 轮：" if round_no else "检测结果："
    if summary.get("flagged"):
        body = f"{summary['flagged']} 段要处理，{summary['passed']} 段通过。"
    else:
        body = f"{summary['passed']} 段全部通过。"
    loose = f"另有 {summary['unmatched']} 段没对上检测结果，没有下结论。" if summary.get("unmatched") else ""
    return head + body + loose + (extra or "")


def _detection_text(markdown: str, fixed_lines: list[str]) -> str:
    """送检前把图片、代码、围栏块等噪点去掉，并按设置剔除固定行。"""
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", markdown)
    text = re.sub(r"```[\s\S]*?```", "", text)
    lines = [line.strip() for line in text.splitlines()]
    drop = {line.strip() for line in fixed_lines if line.strip()}
    kept = [line for line in lines if line and line not in drop]
    return "\n".join(kept)
