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

import asyncio
import io
import ipaddress
import math
import re
import unicodedata

import httpx

from ..core.deps import ApiError
from ..core.ssrf import UnsafeUrl, pin_url, validate_base_url
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
    """携带密钥的质检端点：HTTPS + 统一 SSRF 校验，不开放本机模型例外。"""
    from urllib.parse import urlsplit

    try:
        parts = urlsplit(str(value or ""))
        if not parts.scheme or not parts.netloc:
            raise UnsafeUrl("接口地址不是有效的网址")
        if parts.scheme != "https":
            raise UnsafeUrl("接口地址必须以 https:// 开头")
        host = (parts.hostname or "").lower().rstrip(".")
        # core.ssrf 为本机模型显式放行环回地址；质检端点来自普通用户，不适用该例外。
        if host in {"localhost", "localhost.localdomain"}:
            raise UnsafeUrl("禁止访问本机地址")
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            ip = None
        if ip is not None and ip.is_loopback:
            raise UnsafeUrl("禁止访问本机地址")
        validate_base_url(str(value))
    except (UnsafeUrl, ValueError) as exc:
        raise ApiError("unsafe_endpoint", f"质检接口地址不安全：{exc}", 400) from exc
    return str(value)


def _finite_number(value) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


async def detect(text: str, *, key: str, endpoint: str | None = None) -> dict:
    """调一次朱雀文本检测，返回总体占比、片段与 token 用量。"""
    if not key:
        raise QualityError("还没有设置朱雀 API Key", 400)
    if not str(text or "").strip():
        raise QualityError("没有可以检测的文字", 400)
    url = check_endpoint(endpoint or DEFAULT_ENDPOINT)
    try:
        # 请求前解析并校验 DNS，把连接固定到校验过的 IP（防 rebinding），不跟随重定向
        target, pinned_headers, extensions = await asyncio.to_thread(pin_url, url)
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS, follow_redirects=False) as client:
            response = await client.post(
                target,
                headers={
                    "content-type": "application/json",
                    "authorization": f"Bearer {key}",
                    **pinned_headers,
                },
                extensions=extensions,
                # is_merge=false 才会逐段给结果；合并模式整篇只有一个结论，没法定位到段落
                json={"text": text, "is_merge": False},
            )
    except UnsafeUrl as exc:
        raise ApiError("unsafe_endpoint", f"质检接口地址不安全：{exc}", 400) from exc
    except httpx.TimeoutException as exc:
        raise QualityError("朱雀接口超时，稍后再试") from exc
    except httpx.HTTPError as exc:
        raise QualityError("连不上朱雀接口") from exc

    if not response.is_success:
        if response.status_code in (401, 403):
            raise QualityError("朱雀 API Key 无效或没有权限，到设置里检查一下")
        if response.status_code == 429:
            raise QualityError("朱雀接口限流或额度用完了")
        raise QualityError(f"朱雀接口报错（{response.status_code}）")
    try:
        data = response.json()
    except ValueError as exc:
        raise QualityError("朱雀接口返回的不是 JSON") from exc
    if not isinstance(data, dict):
        raise QualityError("朱雀接口返回的不是 JSON")
    if data.get("status") and data.get("status") != "success":
        raise QualityError("朱雀接口状态异常")

    def pct(value):
        number = _finite_number(value)
        if number is None or not 0 <= number <= 1:
            return None
        return math.floor(number * 1000 + 0.5) / 10

    ratio = data.get("labels_ratio") if isinstance(data.get("labels_ratio"), dict) else None
    rows = data.get("segment_labels") if isinstance(data.get("segment_labels"), list) else []
    ordered = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("text"), str) or not row["text"].strip():
            continue
        label = _finite_number(row.get("label"))
        if label not in (0, 1, 2):
            continue
        conf = _finite_number(row.get("conf"))
        if conf is None:
            if label != 0 or row.get("conf") is not None:
                continue
            conf = 0.0
        order = _finite_number(row.get("order")) or 0.0
        ordered.append((order, {"text": row["text"], "label": int(label), "conf": max(0.0, min(1.0, conf))}))
    ordered.sort(key=lambda item: item[0])
    usage = data.get("makers_models_usage") or data.get("usage") or {}
    try:
        tokens = int(usage.get("total_tokens"))
    except (TypeError, ValueError, AttributeError, OverflowError):
        tokens = None
    return {
        "overall": (
            {"human": pct(ratio.get("0")), "ai": pct(ratio.get("1")), "suspect": pct(ratio.get("2"))}
            if ratio
            else None
        ),
        "segments": [segment for _, segment in ordered[:2000]],
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


def mark_blocks(article: dict, parsed: dict, source: str = "pdf", scope: set[int] | None = None) -> dict:
    """把一轮检测结果落到段落上：标红、通过，或者（对不上的）不下结论。

    scope 非空时只重算这些下标对应的段落，scope 外的段落既有结论原样保留；
    文章级 flagged/passed 仍按整篇段落重新计数，避免局部检测后总数失真。
    """
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
        if scope is not None and index not in scope:
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
    unmatched = 0
    for index, block in enumerate(article["blocks"]):
        # 局部检测：scope 外的段落不动，保留上一轮的 check 结论
        if scope is not None and index not in scope:
            continue
        if not is_checkable(block["text"]):
            block["check"] = None
            continue
        hit = found.get(index)
        reasons = (parsed.get("blockHits") or {}).get(index) if source == LOCAL_SOURCE else None
        before = block.get("check") or {}
        if not hit:
            if covered is not None and index not in covered:
                block["check"] = None
                unmatched += 1
                continue
            block["check"] = {"status": "pass", "round": round_no}
            if reasons:
                block["check"]["hits"] = reasons
            continue
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
        if reasons:
            block["check"]["hits"] = reasons

    # 整篇重算：局部检测后其它段落的旧 flag/pass 也要算进总数
    flagged = passed = 0
    for block in article["blocks"]:
        status = (block.get("check") or {}).get("status")
        if status == "flag":
            flagged += 1
        elif status == "pass":
            passed += 1

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


# ---------------- 本地写作质量 ----------------

_LOCAL_AXES = (
    ("wording", "用词自然度"),
    ("structure", "句式多样性"),
    ("sentence_rhythm", "阅读节奏"),
    ("transitions", "衔接自然度"),
    ("specificity", "表达具体度"),
    ("redundancy", "内容去重"),
)
_LOCAL_WORDING = re.compile(r"赋能|抓手|生态闭环|颗粒度|降维打击|沉淀最佳实践|全方位|至关重要|不容忽视")
_LOCAL_TRANSITION = re.compile(
    r"^(首先|其次|再次|最后|总之|综上所述|总的来说|总而言之|由此可见|值得注意的是|更重要的是|"
    r"换句话说|与此同时)(?=[，、,:：\s]|$)"
)
_LOCAL_VAGUE = re.compile(r"具有.{0,3}(?:意义|价值)|发挥.{0,4}作用|带来.{0,4}影响|有助于.{0,5}(?:发展|提升|优化)")
_LOCAL_DETAIL = re.compile(r"\d|[一二三四五六七八九十两百千]+(?:个|件|次|天|月|年|点|元|成)|例如|比如|以.+为例|[「“][^」”]+[」”]")
_LOCAL_TEMPLATE = re.compile(r"不仅[^，,]{1,24}[，,]?\s*而且|不是[^，,]{1,24}[，,]?\s*而是|既[^，,]{1,16}[，,]?\s*又")


def local_check(article: dict, scope: set[int] | None = None) -> dict:
    """只在本次选区内检查，用有上限的段落长度加权汇总六轴。"""
    segments = []
    block_hits = {}
    totals = dict.fromkeys((axis for axis, _ in _LOCAL_AXES), 0)
    total_weight = 0
    seen_sentences: set[str] = set()
    previous_index = -2
    previous_grams: set[str] = set()
    for index, block in enumerate(article["blocks"]):
        raw = block["text"]
        if (scope is not None and index not in scope) or not is_checkable(raw):
            continue
        text = plain_of(raw)
        # 保留小数点和英文缩写，按中文标点、换行或英文句末切句。
        sentences = [part.strip() for part in re.split(r"[。！？!?；;\n]+|\.(?=\s|$)", text) if part.strip()]
        unique = []
        repeated = 0
        normalized = []
        for sentence in sentences:
            key = re.sub(r"[\W_]+", "", unicodedata.normalize("NFKC", sentence).casefold())
            normalized.append(key)
            if len(key) >= 10:
                if key in seen_sentences:
                    repeated += 1
                    continue
                seen_sentences.add(key)
            unique.append(sentence)

        paragraph = "".join(normalized)
        grams = {paragraph[start:start + 4] for start in range(len(paragraph) - 3)} if len(paragraph) >= 40 else set()
        shared = len(grams & previous_grams) if index == previous_index + 1 else 0
        near_duplicate = shared >= 28 and shared >= 0.8 * len(grams | previous_grams)
        previous_index, previous_grams = index, grams

        penalties = {}
        if repeated:
            penalties["redundancy"] = (min(85, 50 + 10 * (repeated - 1)), f"有 {repeated} 句与本段或前文重复")
        elif near_duplicate:
            penalties["redundancy"] = (50, "与上一段存在大段重复表述")
            unique = []

        wording = sum(len(_LOCAL_WORDING.findall(sentence)) for sentence in unique)
        if wording >= 2:
            penalties["wording"] = (min(65, wording * 10), f"有 {wording} 处套话或空泛修饰")

        transitions = sum(bool(_LOCAL_TRANSITION.match(sentence)) for sentence in unique)
        if transitions >= 2 and transitions * 2 >= len(unique):
            penalties["transitions"] = (min(65, (transitions - 1) * 18), f"有 {transitions} 句以固定过渡语开头")

        # 去掉已归为衔接问题的句首。列表逐项使用相同句式很常见。
        bodies = [_LOCAL_TRANSITION.sub("", sentence).lstrip("，、,:： ") for sentence in unique]
        prefixes = [re.match(r"[\u3400-\u9fff]{3}", sentence) for sentence in bodies]
        same_opening = any(a and b and c and a[0] == b[0] == c[0] for a, b, c in zip(prefixes, prefixes[1:], prefixes[2:]))
        templates = sum(len(_LOCAL_TEMPLATE.findall(sentence)) for sentence in bodies)
        if not re.match(r"^\s*(?:[-*+]|\d+[.)])\s", raw) and (same_opening or templates >= 2):
            reason = "连续三句使用相同开头" if same_opening else "多次使用固定对举句式"
            penalties["structure"] = (min(60, max(30, templates * 12)), reason)

        lengths = [len(re.sub(r"\s+", "", sentence)) for sentence in bodies]
        long_sentences = sum(length > 75 for length in lengths)
        if long_sentences:
            penalties["sentence_rhythm"] = (min(65, long_sentences * 22), f"有 {long_sentences} 句超过 75 字")
        elif len(lengths) >= 4 and not same_opening and min(lengths) >= 8 and max(lengths) - min(lengths) <= 3 and "\n" not in text:
            penalties["sentence_rhythm"] = (18, "连续多句长度接近，阅读节奏偏单一")

        vague = sum(bool(_LOCAL_VAGUE.search(sentence)) and not _LOCAL_DETAIL.search(sentence) for sentence in bodies)
        if vague >= 2:
            penalties["specificity"] = (min(65, vague * 14), f"有 {vague} 句泛泛评价，可补充实例或数量")

        weight = min(240, len(text))
        total_weight += weight
        for axis, (points, _) in penalties.items():
            totals[axis] += points * weight
        burden = min(100, sum(points for points, _ in penalties.values()))
        if penalties:
            block_hits[index] = [f"{label}：{penalties[axis][1]}" for axis, label in _LOCAL_AXES if axis in penalties]
        if burden >= 50:
            segments.append({"block": index + 1, "score": burden / 100, "level": "suspect"})

    return {
        "segments": segments,
        "blockHits": block_hits,
        "dimensions": [
            {"id": axis, "label": label, "score": round(100 - totals[axis] / total_weight)}
            for axis, label in _LOCAL_AXES
        ] if total_weight else [],
        "source": LOCAL_SOURCE,
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
