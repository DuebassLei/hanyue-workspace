"""AI 味质检：朱雀 API 检测、报告解析回填，以及无密钥时的本地启发式兜底。

移植自 dsh-wechat-studio：

- `src/zhuque.js`：调朱雀 classify 接口，把返回的片段按文字对回段落；
- `src/report.js`：解析朱雀报告 PDF/文字，优先按片段原文直接对齐，对不上再交模型映射。

与 dsh 的差异（本项目的取舍）：

- 密钥存在 AppSetting（界面可配），不读环境变量里的第三方 key；
- 未配置密钥时不报错空转，而是走本地启发式并标记 `source=local`，
  界面必须如实显示来源，不能把规则打分说成朱雀结论。
"""

from __future__ import annotations

import io
import math
import re
import unicodedata

import httpx

from .article_blocks import is_checkable, plain_of

# 朱雀 API：通过腾讯云 EdgeOne Makers 的内置模型 @makers/zhuque-text 检测文本。
DEFAULT_ENDPOINT = "https://ai-gateway.edgeone.link/v1/providers/zhuque-text/classify"
TIMEOUT_SECONDS = 90.0

# 片段标签：0 人工，1 AI 生成，2 疑似 AI
LEVEL = {1: "ai", 2: "suspect"}
RANK = {"suspect": 1, "ai": 2}

LOCAL_SOURCE = "local"


class QualityError(Exception):
    """质检链路的业务错误；路由层按 status 转成 HTTP 错误。"""

    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


def check_endpoint(value: str) -> str:
    """密钥会随请求发到这个地址，所以只允许 https。"""
    from urllib.parse import urlsplit

    parts = urlsplit(str(value or ""))
    if not parts.scheme or not parts.netloc:
        raise QualityError("接口地址不是有效的网址", 400)
    if parts.scheme != "https":
        raise QualityError("接口地址必须以 https:// 开头", 400)
    return str(value)


async def detect(text: str, *, key: str, endpoint: str | None = None) -> dict:
    """调一次朱雀文本检测，返回总体占比、片段与 token 用量。"""
    if not key:
        raise QualityError("还没有设置朱雀 API Key", 400)
    if not str(text or "").strip():
        raise QualityError("没有可以检测的文字", 400)
    url = check_endpoint(endpoint or DEFAULT_ENDPOINT)
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            response = await client.post(
                url,
                headers={"content-type": "application/json", "authorization": f"Bearer {key}"},
                # is_merge=false 才会逐段给结果；合并模式整篇只有一个结论，没法定位到段落
                json={"text": text, "is_merge": False},
            )
    except httpx.TimeoutException as exc:
        raise QualityError("朱雀接口超时，稍后再试") from exc
    except httpx.HTTPError as exc:
        raise QualityError(f"连不上朱雀接口：{exc}") from exc

    raw = response.text
    try:
        data = response.json()
    except ValueError:
        data = None
    if response.status_code >= 400:
        if response.status_code in (401, 403):
            raise QualityError("朱雀 API Key 无效或没有权限，到设置里检查一下")
        detail = ""
        if isinstance(data, dict):
            error = data.get("error")
            detail = str(
                data.get("msg")
                or (error.get("message") if isinstance(error, dict) else error)
                or data.get("message")
                or ""
            )
        detail = detail or raw[:200]
        if response.status_code == 429:
            raise QualityError(f"朱雀接口限流或额度用完了：{detail}" if detail else "朱雀接口限流或额度用完了")
        raise QualityError(f"朱雀接口报错（{response.status_code}）" + (f"：{detail}" if detail else ""))
    if not isinstance(data, dict):
        raise QualityError("朱雀接口返回的不是 JSON")
    if data.get("status") and data.get("status") != "success":
        raise QualityError(str(data.get("msg") or f"朱雀接口状态异常：{data.get('status')}"))

    def pct(value):
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return math.floor(number * 1000 + 0.5) / 10

    ratio = data.get("labels_ratio") if isinstance(data.get("labels_ratio"), dict) else None
    rows = data.get("segment_labels") if isinstance(data.get("segment_labels"), list) else []
    segments = []
    for row in sorted(rows, key=lambda item: float((item or {}).get("order") or 0))[:2000]:
        if not isinstance(row, dict) or not isinstance(row.get("text"), str):
            continue
        try:
            conf = float(row.get("conf") or 0)
        except (TypeError, ValueError):
            conf = 0.0
        segments.append({
            "text": row["text"],
            "label": int(float(row.get("label") or 0)),
            "conf": max(0.0, min(1.0, conf)),
        })
    usage = data.get("makers_models_usage") or data.get("usage") or {}
    try:
        tokens = int(usage.get("total_tokens"))
    except (TypeError, ValueError, AttributeError):
        tokens = None
    return {
        "overall": (
            {"human": pct(ratio.get("0")), "ai": pct(ratio.get("1")), "suspect": pct(ratio.get("2"))}
            if ratio
            else None
        ),
        "segments": segments,
        "tokens": tokens,
    }


def _squash(text: str) -> str:
    """比对用的归一化：NFKC 把全角标点折成半角，再去掉所有空白。

    朱雀报告里常见半角逗号、全角句号混用；只去空白的话，
    一个标点差异就会让整段对不上。
    """
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(text)))


PROBE = 10


def _probes(flat: str) -> list[str]:
    if len(flat) < PROBE:
        return []
    mid = (len(flat) - PROBE) // 2
    return [flat[at:at + PROBE] for at in (0, mid, len(flat) - PROBE)]


def _overlaps(block: str, segment: str) -> bool:
    """段落和片段是不是同一处文字。

    朱雀的切分和我们的段落不一定对齐，所以除了互相包含，还看一方的头、中、尾
    有没有一截出现在另一方里（一段被切成两个片段时靠这个对上）。
    """
    if not block or not segment:
        return False
    if block in segment:
        return True
    if len(segment) >= 8 and segment in block:
        return True
    return any(part in segment for part in _probes(block)) or any(part in block for part in _probes(segment))


def map_segments(blocks: list[dict], segments: list[dict]) -> tuple[list[dict], set[int]]:
    """把朱雀的片段按文字对回文章段落。一段被多个片段盖住时取最重的结论。

    返回 (标红段列表（段号 1 起）, 所有对上了的段落下标)。
    """
    squashed = [dict(seg, flat=_squash(seg.get("text", ""))) for seg in segments]
    squashed = [seg for seg in squashed if seg["flat"]]
    rows: list[dict] = []
    covered: set[int] = set()
    for index, block in enumerate(blocks):
        if not is_checkable(block["text"]):
            continue
        flat = _squash(plain_of(block["text"]))
        worst = None
        for seg in squashed:
            if not _overlaps(flat, seg["flat"]):
                continue
            covered.add(index)
            level = LEVEL.get(seg.get("label"))
            if not level:
                continue
            if (
                worst is None
                or RANK[level] > RANK[worst["level"]]
                or (level == worst["level"] and seg.get("conf", 0) > worst["score"])
            ):
                worst = {"level": level, "score": seg.get("conf", 0)}
        if worst:
            rows.append({"block": index + 1, **worst})
    return rows, covered


def _heavier(a: dict, b: dict) -> bool:
    return RANK[a["level"]] > RANK[b["level"]] or (a["level"] == b["level"] and a["score"] > b["score"])


def mark_blocks(article: dict, parsed: dict, source: str = "pdf") -> dict:
    """把一轮检测结果落到段落上：标红、通过，或者（对不上的）不下结论。"""
    found: dict[int, dict] = {}
    for row in (parsed.get("segments") or [])[:2000]:
        if not isinstance(row, dict):
            continue
        number = row.get("block")
        index = number - 1 if isinstance(number, int) and not isinstance(number, bool) else -1
        try:
            score = float(row.get("score"))
        except (TypeError, ValueError):
            continue
        given = row.get("level") if row.get("level") in ("ai", "suspect") else None
        blocks = article["blocks"]
        if not (0 <= index < len(blocks)) or not is_checkable(blocks[index]["text"]):
            continue
        if not (0 <= score <= 1):
            continue
        if not given and score < 0.5:
            continue
        hit = {"score": score, "level": given or ("ai" if score >= 0.99 else "suspect")}
        if index not in found or _heavier(hit, found[index]):
            found[index] = hit

    covered = parsed.get("covered") if isinstance(parsed.get("covered"), set) else None
    round_no = int((article.get("zhuque") or {}).get("round") or 0) + 1
    flagged = passed = unmatched = 0
    for index, block in enumerate(article["blocks"]):
        if not is_checkable(block["text"]):
            block["check"] = None
            continue
        hit = found.get(index)
        before = block.get("check") or {}
        if not hit:
            if covered is not None and index not in covered:
                block["check"] = None
                unmatched += 1
                continue
            block["check"] = {"status": "pass", "round": round_no}
            passed += 1
            continue
        flagged += 1
        # 上一轮改过、这一轮反而更重：提示退回上一版（zhuque skill 第 5 步）
        worse = (
            before.get("status") == "fixed"
            and isinstance(before.get("score"), (int, float))
            and bool(block.get("prev_text"))
            and _heavier(hit, {"score": before["score"], "level": before.get("level") or "suspect"})
        )
        block["check"] = {
            "status": "flag",
            "score": round(hit["score"] * 100) / 100,
            "level": hit["level"],
            "source": source,
            "round": round_no,
            "fixes": before.get("fixes") or 0,
            "worse": worse,
        }

    overall = parsed.get("overall") if isinstance(parsed.get("overall"), dict) else None
    if overall:
        overall = {key: _num(overall.get(key)) for key in ("human", "suspect", "ai")}
    stamp = _now()
    article["zhuque"] = {
        "round": round_no,
        "lastAt": stamp,
        "overall": overall,
        "overallDerived": parsed.get("overallDerived") is True,
        "flagged": flagged,
        "passed": passed,
        "source": source,
    }
    article["updated_at"] = stamp
    return {"flagged": flagged, "passed": passed, "unmatched": unmatched, "overall": overall}


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


# ---------------- 报告解析 ----------------


def report_overall(report_text: str) -> dict | None:
    """从报告里直接算总体占比（人工 / 疑似 / AI）。"""
    lines = [
        line
        for line in unicodedata.normalize("NFKC", str(report_text)).splitlines()
        if re.match(r"^\s*\d+\s+片段\s*\d+", line)
    ]
    if not lines:
        return None
    rows = [
        re.match(r"^\s*\d+\s+片段\s*\d+\s+(\d+(?:\.\d+)?)%\s+[\d,]+\s+(0(?:\.\d+)?|1(?:\.0+)?)\s*$", line)
        for line in lines
    ]
    if any(row is None for row in rows):
        return None
    totals = {"human": 0.0, "suspect": 0.0, "ai": 0.0}
    total = 0.0
    for row in rows:
        ratio = float(row.group(1))
        score = float(row.group(2))
        if ratio < 0 or ratio > 100:
            return None
        level = "human" if score < 0.5 else "suspect" if score < 0.99 else "ai"
        totals[level] += ratio
        total += ratio
    if total < 99 or total > 101:
        return None
    return {key: math.floor(value * 10 + 0.5) / 10 for key, value in totals.items()}


def parse_report_segments(report_text: str) -> list[dict]:
    """按「NO.x 片段 y AIGC值 z」切出片段原文。"""
    text = unicodedata.normalize("NFKC", str(report_text))
    markers = list(re.finditer(r"NO\.\s*\d+\s+片段\s*\d+\s+AIGC值\s+(0(?:\.\d+)?|1(?:\.0+)?)", text))
    segments: list[dict] = []
    for i, marker in enumerate(markers):
        conf = float(marker.group(1))
        end = markers[i + 1].start() if i + 1 < len(markers) else len(text)
        content = text[marker.end():end]
        kept = []
        for line in content.splitlines():
            value = line.strip()
            if re.match(r"^\d{1,2}/\d{1,2}/\d{2,4},?\s+\d{1,2}:\d{2}", value):
                continue
            if re.match(r"^https://matrix\.tencent\.com/ai-detect/ai_gen_txt(?:\s+\d+/\d+)?$", value):
                continue
            if re.match(r"^朱雀AI生成检测报告单(?:_\d+)?$", value):
                continue
            kept.append(line)
        cleaned = re.sub(r"\\([\\`*_{}\[\]()#+\-.!|>~])", r"\1", "\n".join(kept)).strip()
        if cleaned:
            segments.append({"text": cleaned, "conf": conf, "label": 1 if conf >= 0.99 else 2 if conf >= 0.5 else 0})
    return segments


REPORT_SYSTEM = """你在帮作者核对一份「朱雀 AI 检测」报告。报告把文章切成若干片段，每个片段有一个 AIGC 值：
0–0.5 是人工特征，0.5–0.99 是疑似 AI，0.99–1 是 AI 特征。片段的切分位置和文章的段落不一定对齐。

你的任务：按文字内容，把报告里 AIGC 值 ≥ 0.5 的片段对回文章的段落编号。
- 一个片段可能盖住好几段，这几段都列出来，分数都用这个片段的 AIGC 值。
- 一段被多个片段盖住时取最高的分数。
- 只按报告里写明的数值填，不要自己判断哪段像 AI，报告里没有的不要列。
- 报告里如果有总体占比（人工特征 / 疑似 AI / AI 特征的百分比），原样填进 overall，没有就填 null。

只输出 JSON，不要解释：
{"overall":{"human":数字或null,"suspect":数字或null,"ai":数字或null},"segments":[{"block":段落编号,"score":0到1的小数}]}"""


async def apply_report(article: dict, report_text: str, *, mapper=None) -> dict:
    """把报告片段对回段落，然后给每个可检测的段落打上「标红」或「通过」。

    mapper(report_text, rows) -> parsed dict | None，由调用方注入（走 ai_gateway）。
    """
    rows = [
        f"【{i + 1}】{plain_of(block['text'])}"
        for i, block in enumerate(article["blocks"])
        if is_checkable(block["text"])
    ]
    if not rows:
        raise QualityError("这篇文章还没有可以检测的正文段落。", 400)

    parsed = None
    direct = parse_report_segments(report_text)
    if direct:
        segments, covered = map_segments(article["blocks"], direct)
        if covered:
            parsed = {"segments": segments, "covered": covered}
    if parsed is None and mapper is not None:
        parsed = await mapper(report_text, rows)
    if parsed is None:
        if mapper is None:
            raise QualityError("报告里没有能直接对上的片段，且没有可用的模型做辅助映射。", 422)
        # 分批：一份报告动辄几十段，一次喂完容易漏
        segments = []
        overall = None
        for start in range(0, len(rows), 20):
            batch = await mapper(report_text, rows[start:start + 20])
            if not batch:
                raise QualityError(
                    f"模型返回的段落映射格式无效，分批处理第 {start // 20 + 1}/{-(-len(rows) // 20)} 批仍失败，请重试。",
                    422,
                )
            segments.extend(batch.get("segments") or [])
            if overall is None and isinstance(batch.get("overall"), dict):
                overall = batch["overall"]
        parsed = {"segments": segments, "overall": overall}

    derived = report_overall(report_text)
    merged = dict(parsed)
    merged["overall"] = derived or parsed.get("overall")
    merged["overallDerived"] = bool(derived)
    return mark_blocks(article, merged, "pdf")


# ---------------- 本地启发式兜底 ----------------
#
# 没有朱雀密钥时的降级路径。规则只覆盖「结构层面」的高置信特征：
# 整齐并列、对仗金句、段尾总结、套路过渡、三连排比。
# 打分仅用于排序与提示，界面必须标注「本地规则」，不能冒充朱雀结论。

_PATTERNS = (
    (r"首先|其次|再次|最后|第一[，、]|第二[，、]|第三[，、]", 0.16, "整齐并列"),
    (r"总之|综上|由此可见|总的来说|总而言之|说白了就是", 0.22, "段尾总结"),
    (r"不仅[^。]{0,20}而且|既[^。]{0,12}又|不是[^。]{0,16}而是|与其[^。]{0,16}不如", 0.2, "对仗金句"),
    (r"更重要的是|值得注意的是|换句话说|与此同时|在这个[^。]{0,8}时代", 0.14, "套路过渡"),
    (r"、[^、。]{1,10}、[^、。]{1,10}、[^、。]{1,10}、", 0.12, "三连排比"),
)


def local_check(article: dict) -> dict:
    """本地规则打分，产出与朱雀同构的 segments（分数 0–1）。"""
    # 复用项目已有的规则词表（写作规则里可配），避免两套「AI 腔」标准
    from .article_workflow import check_ai_tone

    segments = []
    checked_chars = 0.0
    weighted = 0.0
    for index, block in enumerate(article["blocks"]):
        if not is_checkable(block["text"]):
            block["check"] = None
            continue
        text = plain_of(block["text"])
        score = 0.08
        hits = []
        try:
            tone = check_ai_tone(block["text"])
        except Exception:  # noqa: BLE001 - 词表异常不该让整轮检测失败
            tone = {"hits": []}
        tone_hits = tone.get("hits") or []
        if tone_hits:
            score += min(0.36, 0.06 * len(tone_hits))
            hits.extend(sorted({str(hit.get("ruleId")) for hit in tone_hits})[:4])
        for pattern, weight, name in _PATTERNS:
            if re.search(pattern, text):
                score += weight
                hits.append(name)
        # 长段落里句句同构更像 AI：按句号切分后看长度方差
        sentences = [s for s in re.split(r"[。！？]", text) if len(s.strip()) >= 8]
        if len(sentences) >= 3:
            lengths = [len(s) for s in sentences]
            mean = sum(lengths) / len(lengths)
            variance = sum((n - mean) ** 2 for n in lengths) / len(lengths)
            if variance < 12:
                score += 0.12
                hits.append("句长一致")
        score = min(0.99, round(score, 2))
        checked_chars += len(text)
        weighted += score * len(text)
        if score >= 0.5:
            segments.append({
                "block": index + 1,
                "score": score,
                "level": "ai" if score >= 0.99 else "suspect",
                "hits": hits,
            })
    overall_score = round(weighted / checked_chars, 3) if checked_chars else 0.0
    article["zhuque"] = {"round": 0, "lastAt": None}
    return {
        "segments": segments,
        "overall": {
            "human": round((1 - overall_score) * 100, 1),
            "suspect": round(overall_score * 100, 1),
            "ai": 0.0,
        },
        "source": LOCAL_SOURCE,
        "overallScore": overall_score,
    }


# ---------------- PDF ----------------


def pdf_to_text(buffer: bytes) -> str:
    """PDF → 文字。优先 pypdf（可选依赖），没有就给出可执行的替代建议。"""
    if buffer[:5] != b"%PDF-":
        raise QualityError("这不是 PDF 文件。请上传朱雀下载的检测报告 PDF。", 400)
    try:
        from pypdf import PdfReader  # 可选依赖，未安装时走下面的提示
    except ImportError as exc:
        raise QualityError(
            "服务端未安装 PDF 解析库（pypdf）。可以改用「粘贴报告文字」，或在 requirements 里加上 pypdf 后重启。",
            422,
        ) from exc
    try:
        reader = PdfReader(io.BytesIO(buffer))
        text = "\n".join((page.extract_text() or "") for page in reader.pages)
    except Exception as exc:  # noqa: BLE001 - 解析失败原因很多，统一转成业务错误
        raise QualityError(f"PDF 解析失败：{exc}", 422) from exc
    if not text.strip():
        raise QualityError("没能从这份 PDF 里读出文字，可能是扫描件。", 422)
    return text
