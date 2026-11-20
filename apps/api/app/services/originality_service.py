"""文章原创度检测服务。

口径约定（前端面板、导出报告、这里三者必须一致）：

- ``similarity_score``：0-100，**越高越像**——取语料库内最高的一篇相似度。
  语料为空时是 0，同时 ``corpus_size = 0`` 表示「无从判断」，不是「完全原创」。
- 其余四维：0-100，**越高越好**。
- ``total_score``：独特性综合分。similarity 维度按 ``100 - similarity`` 计入权重；
  语料为空时该维度不参与，其余四维按原权重归一化。

为什么要这么定：旧实现里 ``similarity_score`` 其实是「越像越低」的独特性分，
但字段名和前端标签都叫「相似度」，于是出现「相似度 100 分」这种自相矛盾的展示，
且语料为空时直接给 95 分，等于凭空判了「很原创」。
"""

from __future__ import annotations

import hashlib
import math
import re
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

import jieba
import jieba.analyse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models
from .structure_analyzer import StructureAnalyzer

# 维度权重（合计 1.0）。similarity 是「越低越好」，计分时用 100 - similarity。
DIMENSION_WEIGHTS = {
    "similarity": 0.40,
    "originality": 0.25,
    "structure": 0.15,
    "data_accuracy": 0.12,
    "style_uniqueness": 0.08,
}

# 展示名按「这一维实际测什么」来写：本地算法量的是结构层次、数据密度、用词丰富度，
# 叫「创新度 / 真实度」会让人以为做过事实核验，名不副实。
DIMENSION_LABELS = {
    "similarity": "相似度",
    "originality": "观点独特性",
    "structure": "结构层次",
    "data_accuracy": "数据支撑度",
    "style_uniqueness": "文风辨识度",
}

# 低于这个分数就该改进（similarity 反过来：高于阈值才提）
_WEAK_THRESHOLD = 70
_SIMILARITY_ALERT = 60

_STOPWORDS = {
    "的", "了", "和", "是", "在", "有", "这", "中", "大", "为", "上", "个", "我",
    "以", "要", "他", "时", "来", "用", "们", "到", "作", "地", "于", "出", "就",
    "分", "对", "成", "会", "可", "发", "年", "动", "同", "工", "也", "能", "下",
    "过", "前", "面", "其", "多", "经", "做", "去", "制", "情", "本", "着", "而",
    "更", "最", "很", "都", "或", "还", "又", "与", "里", "么", "只", "但", "至",
    "则", "把", "被", "让", "给", "从", "向", "并", "等", "一个", "我们", "他们",
    "自己", "这个", "那个", "什么", "因为", "所以", "如果", "但是", "进行", "方面",
}

_SENTENCE_SPLIT = re.compile(r"[。！？!?；;\n]+")
_PUNCT = re.compile(r"[^\w\u4e00-\u9fff]+")
_WS = re.compile(r"\s+")


@dataclass
class AnalysisResult:
    """一次检测的结果。``cached`` 为真时 ``elapsed_ms`` 是命中缓存的耗时，不是分析耗时。"""

    analysis: models.OriginalityAnalysis
    elapsed_ms: int
    cached: bool = False


# ─────────────────────────── 文本工具 ───────────────────────────


def _normalize(text: str) -> str:
    """去掉 URL / HTML / 多余空白，统一小写。"""
    if not text:
        return ""
    text = re.sub(r"http[s]?://\S+", "", text)
    text = re.sub(r"<[^>]+>", "", text)
    return _WS.sub(" ", text).strip().lower()


def _tokenize(text: str) -> list[str]:
    """中文分词 + 停用词过滤。"""
    tokens = []
    for word in jieba.cut(_normalize(text)):
        word = word.strip()
        if len(word) < 2 or word in _STOPWORDS or _PUNCT.fullmatch(word):
            continue
        tokens.append(word)
    return tokens


def _sentences(text: str, min_len: int = 12) -> list[str]:
    """按标点切句，过滤过短碎片，并去掉 Markdown 标题记号。

    标题和正文被同一个换行切开时，`## 二、把平台当草稿箱` 会粘在句子开头，
    匹配出来的片段就带着 `##`，直接在切句阶段清掉。
    """
    parts = (re.sub(r"^[#\s]+", "", p.strip()) for p in _SENTENCE_SPLIT.split(_normalize(text)))
    return [p for p in parts if len(p) >= min_len]


def _shingles(text: str, n: int = 3) -> set[str]:
    """字符 n-gram 指纹，用于句子级近似匹配（比逐对跑 TF-IDF 快两个数量级）。"""
    chars = _PUNCT.sub("", _normalize(text))
    if len(chars) < n:
        return {chars} if chars else set()
    return {chars[i : i + n] for i in range(len(chars) - n + 1)}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _tfidf_vectors(docs: list[list[str]]) -> list[dict[str, float]]:
    """整批文档一次算出 TF-IDF 稀疏向量（替代逐对 fit，避免 O(n²) 重复建模）。"""
    if not docs:
        return []
    n_docs = len(docs)
    df: Counter = Counter()
    for tokens in docs:
        df.update(set(tokens))
    idf = {term: math.log((n_docs + 1) / (count + 1)) + 1.0 for term, count in df.items()}

    vectors = []
    for tokens in docs:
        if not tokens:
            vectors.append({})
            continue
        tf = Counter(tokens)
        total = len(tokens)
        vec = {term: (count / total) * idf.get(term, 1.0) for term, count in tf.items()}
        norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
        vectors.append({term: v / norm for term, v in vec.items()})
    return vectors


def _cosine(a: dict[str, float], b: dict[str, float]) -> float:
    if not a or not b:
        return 0.0
    if len(a) > len(b):
        a, b = b, a
    return max(0.0, min(1.0, sum(v * b.get(term, 0.0) for term, v in a.items())))


def body_hash(body: str) -> str:
    """正文指纹，用于缓存命中判断（只认正文内容，与标题无关）。"""
    return hashlib.sha256(_normalize(body).encode("utf-8")).hexdigest()[:40]


def warmup() -> None:
    """提前把 jieba 词典与分词缓存加载好（首次调用约 1s）。

    由 API 启动时的后台线程调用；不预热也不影响正确性，只是第一次检测会慢一截。
    """
    jieba.initialize()
    _tokenize("预热分词器，避免第一次检测时卡顿。")
    _sentences("预热句子切分与标点正则。")
    _shingles("预热字符指纹。")


# ─────────────────────────── 语料库 ───────────────────────────


def _load_corpus(
    db: Session,
    *,
    content_item_id: str,
    workspace_id: str,
    owner_user_id: str,
    compare_scope: str,
) -> tuple[list[tuple[str, str, str]], int]:
    """取比对语料。

    返回 ``(语料, 同稿历史版本数)``。语料只包含**其它稿件**的版本——拿同一篇稿件的
    历史版本互比，衡量的是「改了多少」而不是「像不像别人的文章」，放进去会让每篇
    有历史版本的稿件都被判成高相似。
    """
    if compare_scope == "public":
        # 目前没有对外公开的稿件库，如实返回空语料而不是假装有数据
        return [], 0

    rows = db.execute(
        select(
            models.ContentVersion.id,
            models.ContentItem.title,
            models.ContentVersion.body,
            models.ContentItem.id,
        )
        .join(
            models.ContentItem,
            models.ContentVersion.content_item_id == models.ContentItem.id,
        )
        .where(
            models.ContentItem.workspace_id == workspace_id,
            models.ContentItem.owner_user_id == owner_user_id,
            models.ContentVersion.body.isnot(None),
        )
    ).all()

    corpus: list[tuple[str, str, str]] = []
    self_versions = 0
    seen: set[str] = set()
    for version_id, title, body, item_id in rows:
        if not body or len(body.strip()) < 20:
            continue
        digest = body_hash(body)
        if digest in seen:  # 同一篇正文被存成多个版本时只比一次
            continue
        if item_id == content_item_id:
            self_versions += 1
            continue
        seen.add(digest)
        corpus.append((version_id, title or "未命名稿件", body))
    return corpus, self_versions


# ─────────────────────────── 五维打分 ───────────────────────────


def _score_similarity(
    body: str, corpus: list[tuple[str, str, str]]
) -> tuple[Optional[int], list[dict], float]:
    """返回 ``(相似度分值, 最相似的稿件, 最高相似度 0-1)``。"""
    if not corpus:
        return None, [], 0.0

    docs = [_tokenize(body)] + [_tokenize(text) for _, _, text in corpus]
    vectors = _tfidf_vectors(docs)
    current = vectors[0]

    scored = []
    for idx, (version_id, title, _text) in enumerate(corpus, start=1):
        sim = _cosine(current, vectors[idx])
        if sim > 0:
            scored.append((sim, version_id, title))
    scored.sort(reverse=True)
    if not scored:
        return 0, [], 0.0

    top = scored[:3]
    score = int(round(top[0][0] * 100))
    # 相似度低于 10% 基本是「同一话题下的无关文章」，列出来只会干扰判断
    similar = [
        {"id": vid, "title": title, "similarity": round(sim, 3)}
        for sim, vid, title in top
        if sim >= 0.1
    ]
    return score, similar, top[0][0]


def _matching_segments(
    body: str, corpus: list[tuple[str, str, str]], threshold: float = 0.45
) -> list[dict]:
    """抽取与语料重合的句子。

    对「当前稿件的每一句 × 语料中前几篇的句子」做字符 n-gram 比对，同一句只保留
    最高分的一条——旧实现会把同一句话按 段落 1-2 / 1-4 / 1-6 重复列出好几条。
    """
    current_sents = _sentences(body)
    if not current_sents or not corpus:
        return []

    best_per_sentence: dict[str, dict] = {}
    for _vid, title, other_body in corpus[:3]:
        other_sents = _sentences(other_body)
        if not other_sents:
            continue
        other_shingles = [(s, _shingles(s)) for s in other_sents]
        for index, sent in enumerate(current_sents, start=1):
            sent_shingles = _shingles(sent)
            for other, other_set in other_shingles:
                sim = _jaccard(sent_shingles, other_set)
                if sim < threshold:
                    continue
                prev = best_per_sentence.get(sent)
                if prev and prev["similarity"] >= sim:
                    continue
                best_per_sentence[sent] = {
                    "position": f"第 {index} 句",
                    "source_text": other[:120],
                    "target_text": sent[:120],
                    "similarity": round(sim, 2),
                    "matched_title": title,
                }

    return sorted(
        best_per_sentence.values(), key=lambda x: x["similarity"], reverse=True
    )[:5]


def _score_originality(
    body: str, corpus: list[tuple[str, str, str]]
) -> tuple[Optional[int], list[dict]]:
    """以关键词在语料中的覆盖率衡量观点独特性，同时产出「关键观点」列表。"""
    keywords = [
        word
        for word, _weight in jieba.analyse.extract_tags(body, topK=24, withWeight=True)
        if word not in _STOPWORDS
        and len(re.findall(r"[\u4e00-\u9fffA-Za-z]", word)) >= 2
    ]
    if not keywords:
        return None, []

    if not corpus:
        # 没有对比语料就没法谈「新颖」——旧实现把每个关键词标成 novelty=100，
        # 等于凭空宣布「这些观点全网首创」，索性不给关键观点。
        return None, []

    lowered = [text.lower() for _, _, text in corpus]
    points = []
    covered_total = 0.0
    for kw in keywords:
        hits = sum(1 for text in lowered if kw in text)
        coverage = hits / len(corpus)
        covered_total += min(coverage, 1.0)
        if len(points) < 8:
            points.append(
                {"point": kw, "novelty": int(round(100 * (1 - coverage))), "sources": hits}
            )
    avg_coverage = covered_total / len(keywords)
    # 关键词全都被语料覆盖 → 25 分；一个都没覆盖 → 100 分
    score = int(round(100 - 75 * avg_coverage))
    return max(25, min(100, score)), points


def _outline_of(body: str) -> list[dict]:
    """抽取小标题大纲；没有 Markdown / 序号标题时返回空列表。"""
    return StructureAnalyzer().extract_outline(body)


def _outline_structure_score(outline: list[dict]) -> int:
    """有标题时的结构分。

    旧实现用 `calculate_structure_diversity`，它按「最多 6 级标题」折算层级丰富度，
    于是一篇标准的两级结构文章只能拿到 48 分（被判为结构偏平）——但那是最常见的
    写法。这里改成「标题数量落在合理区间 + 有层级加分」，最多 4 级不扣分。
    """
    count = len(outline)
    levels = {item.get("level", 1) for item in outline}
    if count < 3:
        base = 62
    elif count <= 5:
        base = 78
    elif count <= 20:
        base = 88
    elif count <= 30:
        base = 72
    else:
        base = 52
    if len(levels) >= 2:
        base += 6
    if len(levels) > 4:
        base -= 8
    return max(45, min(95, base))


def _score_structure(body: str, outline: list[dict]) -> int:
    """结构分。

    没有小标题时旧实现直接给 0，但很多短稿/口播稿本来就不带 Markdown 标题，
    这里改看段落节奏：段落数合理 + 句长有起伏即算合格。
    """
    if not body.strip():
        return 0
    if outline:
        return _outline_structure_score(outline)

    paragraphs = [p for p in (x.strip() for x in body.split("\n")) if p]
    sent_lengths = [len(s) for s in _sentences(body, min_len=5)]
    if len(sent_lengths) < 3:
        return 45

    if 3 <= len(paragraphs) <= 12:
        para_score = 72
    elif 13 <= len(paragraphs) <= 20 or paragraphs and len(paragraphs) < 3:
        para_score = 65
    else:
        para_score = 55

    avg = sum(sent_lengths) / len(sent_lengths)
    variance = sum((x - avg) ** 2 for x in sent_lengths) / len(sent_lengths)
    rhythm = 8 if variance ** 0.5 >= avg * 0.35 else 0
    return min(85, para_score + rhythm)


def _score_data_accuracy(body: str) -> int:
    """数据密度：有没有数字、百分比、年份、引用来源。"""
    if not body.strip():
        return 0
    density_base = len(body) / 1000 + 1
    numbers = len(re.findall(r"\d+(?:\.\d+)?", body))
    percents = len(re.findall(r"\d+(?:\.\d+)?%", body))
    years = len(re.findall(r"(?:19|20)\d{2}\s*年", body))
    citations = len(re.findall(r"(?:据|引用|来源|数据来自|报告显示|研究指出)", body))

    density = (numbers + percents * 1.5 + years * 1.5) / density_base
    score = 45 + min(35, density * 12) + min(12, citations * 3)
    return int(round(max(35, min(95, score))))


def _score_style(body: str) -> int:
    """语言风格独特度：词汇丰富度 + 句长起伏。"""
    tokens = _tokenize(body)
    if not tokens:
        return 0
    richness = len(set(tokens)) / len(tokens)
    sent_lengths = [len(s) for s in _sentences(body, min_len=5)]
    variability = 0.0
    if len(sent_lengths) > 1:
        avg = sum(sent_lengths) / len(sent_lengths)
        std = (sum((x - avg) ** 2 for x in sent_lengths) / len(sent_lengths)) ** 0.5
        variability = min(1.0, std / avg) if avg else 0.0

    score = 40 + richness * 110 + variability * 25
    return int(round(max(35, min(96, score))))


def _combine(scores: dict[str, Optional[int]]) -> tuple[int, str]:
    """加权求总分；缺失（None）的维度剔除后把剩余权重归一化。"""
    effective = {}
    for key, weight in DIMENSION_WEIGHTS.items():
        value = scores.get(key)
        if value is None:
            continue
        effective[key] = (100 - value) if key == "similarity" else value
    if not effective:
        return 0, "poor"

    total_weight = sum(DIMENSION_WEIGHTS[k] for k in effective)
    weighted = sum(effective[k] * DIMENSION_WEIGHTS[k] for k in effective) / total_weight
    total = int(round(weighted))
    if total >= 90:
        return total, "excellent"
    if total >= 75:
        return total, "good"
    if total >= 60:
        return total, "fair"
    return total, "poor"


def _build_risks(
    body: str,
    scores: dict[str, Optional[int]],
    segments: list[dict],
    corpus_size: int,
) -> list[dict]:
    risks: list[dict] = []
    if segments:
        top = segments[0]
        risks.append(
            {
                "type": "high_similarity_paragraph",
                "position": top["position"],
                "severity": "error" if top["similarity"] >= 0.6 else "warning",
                "reason": (
                    f"该句与《{top.get('matched_title', '库内稿件')}》中的表述重合度 "
                    f"{int(top['similarity'] * 100)}%，建议改写句式或补充自己的判断。"
                ),
            }
        )

    similarity = scores.get("similarity")
    if similarity is not None and similarity >= _SIMILARITY_ALERT:
        risks.append(
            {
                "type": "high_overall_similarity",
                "position": "全文",
                "severity": "error" if similarity >= 80 else "warning",
                "reason": f"全文与库内最相似稿件相似度 {similarity}%，已进入判重敏感区间。",
            }
        )

    data = scores.get("data_accuracy")
    if data is not None and data < 60:
        risks.append(
            {
                "type": "missing_data",
                "position": "全文",
                "severity": "warning",
                "reason": "缺少具体数字、百分比或来源标注，观点说服力偏弱。",
            }
        )

    structure = scores.get("structure")
    if structure is not None and structure < 60:
        risks.append(
            {
                "type": "weak_structure",
                "position": "全文",
                "severity": "warning",
                "reason": "段落层级或小标题缺失，读者难以快速抓取重点。",
            }
        )

    if len(body.strip()) < 300:
        risks.append(
            {
                "type": "short_text",
                "position": "全文",
                "severity": "warning",
                "reason": "正文不足 300 字，各维度评分波动较大，建议成稿后再测。",
            }
        )

    if corpus_size == 0:
        risks.append(
            {
                "type": "no_corpus",
                "position": "全文",
                "severity": "warning",
                "reason": "本次没有可比对稿件，相似度与观点独特性无法判定，仅其余三维有效。",
            }
        )
    return risks


def _build_recommendations(
    scores: dict[str, Optional[int]],
    segments: list[dict],
    corpus_size: int,
    outline: list[dict],
    body_length: int,
) -> list[str]:
    """按「短板优先」生成中文建议，最多 5 条。"""
    recs: list[str] = []

    if body_length < 300:
        # 短文本各维度都是噪声，先让用户补全正文，别拿「观点重合」误导人
        recs.append("正文不足 300 字，各维度评分波动很大，建议写成完整稿件后再检测。")

    similarity = scores.get("similarity")
    if similarity is None:
        if corpus_size == 0:
            recs.append(
                "库里还没有其它稿件，本次只评估了结构、数据与文风；积累几篇后再测相似度才有意义。"
            )
    elif similarity >= _SIMILARITY_ALERT:
        title = segments[0].get("matched_title") if segments else ""
        target = f"《{title}》" if title else "库内稿件"
        recs.append(
            f"与{target}相似度 {similarity}%，建议把重合段落换一种叙述顺序，并补上自己的判断和案例。"
        )
    elif similarity >= 35:
        recs.append(
            f"与库内稿件存在 {similarity}% 的话题重叠，属于正常选题重合，注意别沿用对方的论证顺序。"
        )

    originality = scores.get("originality")
    # 正文过短时不提「观点重合」——那点关键词覆盖率说明不了任何问题
    if originality is not None and originality < _WEAK_THRESHOLD and body_length >= 300:
        recs.append(
            "观点与库内文章重合度较高，试着在每一节结尾补一句「我的判断是……」，把结论落到自己的立场上。"
        )

    structure = scores.get("structure")
    if structure is not None and structure < _WEAK_THRESHOLD:
        if not outline:
            recs.append("正文没有小标题，建议按「现象 → 原因 → 结论」拆成 3-5 个小节并加上标题。")
        else:
            recs.append("结构层次偏平，建议给每个小节配一个能概括观点的短标题，而不是只有编号。")

    data = scores.get("data_accuracy")
    if data is not None and data < _WEAK_THRESHOLD:
        recs.append("补充具体数据：至少 2 个百分比或数字，并写明来源（机构 + 年份）。")

    style = scores.get("style_uniqueness")
    if style is not None and style < _WEAK_THRESHOLD:
        recs.append("句式偏单一，长短句交替使用，去掉「进行 / 方面 / 相关」这类填充词。")

    if not recs:
        recs.append("各维度均达标，可以进入发布流程；发布前再通读一遍事实性表述即可。")
    return recs[:5]


def _summary(
    total: int,
    level: str,
    scores: dict[str, Optional[int]],
    corpus_size: int,
    self_versions: int,
    segment_count: int,
    body_length: int,
) -> str:
    """一句话结论，必须能解释总分是怎么来的。

    旧版只报「综合 52 分」+「最高相似度 0%」，读起来像自相矛盾——相似度没失分
    却只有 52 分，用户根本不知道分扣在哪。这里补上短板项。
    """
    level_text = {
        "excellent": "优秀",
        "good": "良好",
        "fair": "中等",
        "poor": "偏低",
    }.get(level, "未知")
    parts = [f"综合 {total} 分（{level_text}）"]
    if corpus_size:
        parts.append(
            f"与库内 {corpus_size} 篇稿件比对，最高相似度 {scores.get('similarity')}%"
        )
    else:
        parts.append("本次无可比对稿件，相似度与观点独特性未参与计分")
    if segment_count:
        parts.append(f"{segment_count} 处句子存在明显重合")
    elif corpus_size:
        parts.append("未发现成句重合")

    weak: list[str] = []
    for key in ("similarity", "originality", "structure", "data_accuracy", "style_uniqueness"):
        value = scores.get(key)
        if value is None:
            continue
        label = DIMENSION_LABELS[key]
        if key == "similarity":
            if value >= _SIMILARITY_ALERT:
                weak.append(f"{label} {value}%")
        elif value < _WEAK_THRESHOLD:
            weak.append(f"{label} {value} 分")
    if weak:
        parts.append("主要短板是" + "、".join(weak[:2]))
    else:
        parts.append("各维度均达标")

    if body_length < 300:
        parts.append("正文不足 300 字，结论仅供参考")
    if self_versions:
        parts.append(f"同稿另有 {self_versions} 个版本未计入比对")
    return "；".join(parts) + "。"


# ─────────────────────────── 对外入口 ───────────────────────────


def find_cached_analysis(
    db: Session,
    *,
    content_version_id: str,
    digest: str,
    compare_scope: str,
    max_age_hours: int = 24,
) -> Optional[models.OriginalityAnalysis]:
    """同版本、同正文、同比对范围且未过期的结果直接复用。"""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    stmt = (
        select(models.OriginalityAnalysis)
        .where(
            models.OriginalityAnalysis.content_version_id == content_version_id,
            models.OriginalityAnalysis.compare_scope == compare_scope,
            models.OriginalityAnalysis.analyzed_at >= cutoff,
        )
        .order_by(models.OriginalityAnalysis.analyzed_at.desc())
        .limit(5)
    )
    for row in db.scalars(stmt).all():
        if getattr(row, "body_hash", None) == digest:
            return row
    return None


def analyze_originality(
    db: Session,
    *,
    content_version_id: str,
    content_version: models.ContentVersion,
    workspace_id: str,
    owner_user_id: str,
    compare_scope: str = "all",
    use_cache: bool = True,
) -> AnalysisResult:
    """执行一次检测，返回结果与耗时。"""
    started = time.perf_counter()
    body = content_version.body or ""
    digest = body_hash(body)

    if use_cache:
        cached = find_cached_analysis(
            db,
            content_version_id=content_version_id,
            digest=digest,
            compare_scope=compare_scope,
        )
        if cached is not None:
            elapsed = int((time.perf_counter() - started) * 1000)
            return AnalysisResult(analysis=cached, elapsed_ms=elapsed, cached=True)

    corpus, self_versions = _load_corpus(
        db,
        content_item_id=content_version.content_item_id,
        workspace_id=workspace_id,
        owner_user_id=owner_user_id,
        compare_scope=compare_scope,
    )

    similarity, top_docs, _top_sim = _score_similarity(body, corpus)
    segments = _matching_segments(body, corpus)
    originality, key_points = _score_originality(body, corpus)
    outline = _outline_of(body)
    scores = {
        "similarity": similarity,
        "originality": originality,
        "structure": _score_structure(body, outline),
        "data_accuracy": _score_data_accuracy(body),
        "style_uniqueness": _score_style(body),
    }

    similar_articles = [
        {
            **doc,
            "matching_snippets": [
                seg for seg in segments if seg.get("matched_title") == doc["title"]
            ],
        }
        for doc in top_docs
    ]

    total, level = _combine(scores)
    risks = _build_risks(body, scores, segments, len(corpus))
    recommendations = _build_recommendations(
        scores, segments, len(corpus), outline, len(body.strip())
    )

    analysis = models.OriginalityAnalysis(
        id=models.new_id(),
        content_version_id=content_version_id,
        workspace_id=workspace_id,
        analyzed_at=datetime.now(timezone.utc),
        similarity_score=similarity or 0,
        originality_score=originality or 0,
        structure_score=scores["structure"] or 0,
        data_accuracy_score=scores["data_accuracy"] or 0,
        style_uniqueness_score=scores["style_uniqueness"] or 0,
        total_score=total,
        level=level,
        similar_articles={"items": similar_articles, "count": len(similar_articles)},
        key_points={"items": key_points, "count": len(key_points)},
        risk_flags={"items": risks, "count": len(risks)},
        recommendations="\n".join(recommendations),
        compare_scope=compare_scope,
        model_version="v3.0-local" if corpus else "v3.0-local-nocorpus",
        body_hash=digest,
        corpus_size=len(corpus),
        self_version_count=self_versions,
        summary=_summary(
            total, level, scores, len(corpus), self_versions, len(segments), len(body.strip())
        ),
    )
    db.add(analysis)
    db.commit()
    db.refresh(analysis)

    elapsed = int((time.perf_counter() - started) * 1000)
    return AnalysisResult(analysis=analysis, elapsed_ms=elapsed, cached=False)


def get_latest_analysis(
    db: Session, content_version_id: str
) -> Optional[models.OriginalityAnalysis]:
    """取某版本最近一次检测结果。"""
    return db.scalar(
        select(models.OriginalityAnalysis)
        .where(models.OriginalityAnalysis.content_version_id == content_version_id)
        .order_by(models.OriginalityAnalysis.analyzed_at.desc())
        .limit(1)
    )


def list_analyses(
    db: Session, content_version_id: str, limit: int = 10
) -> list[models.OriginalityAnalysis]:
    """历史检测记录，最新在前。"""
    return list(
        db.scalars(
            select(models.OriginalityAnalysis)
            .where(models.OriginalityAnalysis.content_version_id == content_version_id)
            .order_by(models.OriginalityAnalysis.analyzed_at.desc())
            .limit(max(1, min(limit, 50)))
        ).all()
    )
