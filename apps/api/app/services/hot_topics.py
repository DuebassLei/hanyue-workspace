"""热点选题：采集 → 聚簇 → 评分 → 出稿。

参考 DSH 工作台的 `tools/hot-topics.py`，但数据不再走文件快照，而是直接落库：

    hot_sources.collect()   多路公开榜单（今日头条/百度/抖音/微博/知乎/AIHOT）
      → hot_items           每条标题一个身份（platform + 归一化标题）
      → hot_ranking         同事件合并 + 100 分制评分
      → hot_clusters        事件卡片（分数、档位、证据都在这里）
      → content_items       点「写这篇」直接建稿件（选题库不再作为独立产品）

三条必须守住的约定：

1. **可复现**：评分的时间基准是本次采集时间 `captured_at`，不是 `now()`。
   同一天同一轮重跑，分数完全一致。
2. **幂等**：条目身份是 `platform + title_norm`（名次变化不算新条目），
   名次采样有 `(item_id, captured_at)` 唯一约束，簇按 `fingerprint` 复用。
3. **不伪造**：某个源抓不到就如实记进 notes，少一个平台不是「没有热点」；
   所有源都失败时抛错，不返回空列表冒充成功。

热点是公开数据，不按 owner 隔离；`status` / `content_item_id` 也是全局的。
单人部署下这是最省事的口径，多租户场景再拆表。
"""

from __future__ import annotations

import hashlib
import os
import threading
import time
from datetime import datetime, timedelta
from urllib.parse import urlparse

import httpx
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from .. import models
from ..core import ssrf
from . import hot_ranking as ranking
from . import hot_sources

# 历史簇回看窗口：几天内还活跃的簇可以继续吸收新条目。
# 3 天是经验值——热榜事件生命周期通常 1~3 天，再长就该算「新事件」了。
HISTORY_WINDOW_DAYS = 3
VALID_STATUSES = {"new", "watching", "ignored", "generated"}


class HotTopicsError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# ══════════════════════════════════════════════════════════════════════
# 刷新冷却
# ══════════════════════════════════════════════════════════════════════
#
# 采集一次要打 7 个外部接口。连点会让上游以为我们在刷接口，也让用户以为
# 「点了没反应」。这段状态原本写在路由层（modules/hot_topics.py 的模块级 dict），
# 定时任务走 job_runner 会绕过它——同一个动作两条路径，限速却只管一条。
# 现在下沉到服务层：谁调 refresh 都面对同一份状态。
#
# 注意：定时路径**不**调用 reserve_refresh。理由见 job_runner._handle_hot_topics：
# cron 频率 + 触发互斥已经把并发限住了，若因为「15 秒冷却」被判失败，
# 任务会连续失败三次后自己暂停，这比限速本身更糟。

_COOLDOWN_LOCK = threading.Lock()
_last_refresh: dict[str, float] = {}

# 最近一轮采集的降级说明（单源失败 / selector_may_changed / 源未配置关键词）。
# 不落库（models 不在本 Agent 范围），只作为「最近一次」的进程内视图，
# 供 /hot-topics/degraded 与排障读取；服务重启后自然清空。
_last_source_notes: list[str] = []


def _remember_source_notes(notes: list[str]) -> None:
    global _last_source_notes
    _last_source_notes = list(notes or [])


def source_notes() -> list[str]:
    """最近一轮采集的 notes 副本（供测试/排障，不改内部状态）。"""
    return list(_last_source_notes)


def cooldown_seconds() -> float:
    try:
        return max(0.0, float(os.getenv("HOT_TOPICS_REFRESH_COOLDOWN", "15")))
    except ValueError:
        return 15.0


def reserve_refresh(key: str = "all") -> None:
    """占用一次刷新额度；冷却中抛 too_frequent，由调用方翻成 429。"""
    wait = cooldown_seconds()
    with _COOLDOWN_LOCK:
        if wait and time.monotonic() - _last_refresh.get(key, 0.0) < wait:
            raise HotTopicsError("too_frequent", f"刷新太频繁，请 {int(wait)} 秒后再试")
        _last_refresh[key] = time.monotonic()


def reset_cooldown() -> None:
    """测试与排障用：清空冷却状态与缓存的采集 notes。"""
    with _COOLDOWN_LOCK:
        _last_refresh.clear()
    _remember_source_notes([])


# ══════════════════════════════════════════════════════════════════════
# 兼容层：创作空间「灵感」面板
# ══════════════════════════════════════════════════════════════════════

def _configured_source() -> str | None:
    url = os.getenv("HOT_TOPICS_API_URL", "").strip()
    if not url:
        return None
    try:
        ssrf.validate_base_url(url)
    except ssrf.UnsafeUrl:
        return None
    return url


def fetch_hot_topics(db: Session | None = None, limit: int = 20) -> list[dict]:
    """创作空间「灵感」面板的数据源。

    优先用本地已评分的热点簇（比原始榜单更值得看）；一条都没有时才回落到
    环境变量配置的单源，保持老部署可用。

    返回格式：[{"title": str, "hot": str, "url": str|None, "source": str}]
    """
    if db is not None:
        day = latest_day(db)
        if day:
            clusters = list_clusters(db, day=day, limit=limit)
            if clusters:
                return [
                    {
                        "title": cluster["title"],
                        "hot": f"{cluster['score']} 分 · {cluster['grade']}",
                        "url": None,
                        "source": "热点选题",
                    }
                    for cluster in clusters
                ]

    source = _configured_source()
    if not source:
        return []

    try:
        response = ssrf.safe_request(
            "GET", source, headers={"Accept": "application/json"}, timeout=15.0
        )
    except (ssrf.UnsafeUrl, httpx.HTTPError) as exc:
        raise HotTopicsError("fetch_failed", f"热点源请求失败：{type(exc).__name__}") from exc

    if response.status_code >= 400:
        raise HotTopicsError("fetch_failed", f"热点源返回 HTTP {response.status_code}")
    try:
        payload = response.json()
    except ValueError:
        raise HotTopicsError("invalid_response", "热点源未返回 JSON") from None

    return _normalize(payload, limit)


def _normalize(payload, limit: int) -> list[dict]:
    """把不同热搜源的返回格式统一成内部结构。

    支持两种常见格式：
    1. {"data": [{"title":..., "hot":..., "url":...}]}
    2. [{"word":..., "hot":..., "url":...}]  (微博热搜风格)
    """
    raw_list: list = []
    if isinstance(payload, dict):
        for key in ("data", "items", "list", "result"):
            value = payload.get(key)
            if isinstance(value, list):
                raw_list = value
                break
    elif isinstance(payload, list):
        raw_list = payload

    source = _configured_source()
    host = urlparse(source).netloc if source else "hot-topics"
    result: list[dict] = []
    for item in raw_list[:limit]:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or item.get("word") or item.get("name") or "").strip()
        if not title:
            continue
        hot = str(item.get("hot") or item.get("hot_value") or item.get("num") or "")
        url = item.get("url") or item.get("link")
        result.append({
            "title": title,
            "hot": hot,
            "url": url if isinstance(url, str) else None,
            "source": host,
        })
    return result


def source_status() -> dict:
    """热点源配置状态。多路采集默认启用，单源只是回落。"""
    source = _configured_source()
    platforms = hot_sources.enabled_platforms()
    configured = bool(platforms) or bool(source)
    if platforms:
        note = f"已启用 {len(platforms)} 路采集：" + "、".join(platforms)
    elif source:
        note = "多路采集未启用，使用 HOT_TOPICS_API_URL 单源"
    else:
        note = "未配置热点源，当前仅支持手动输入热点关键词"
    return {"configured": configured, "source": source or "", "note": note}


# ══════════════════════════════════════════════════════════════════════
# 入库前的列宽守卫
# ══════════════════════════════════════════════════════════════════════
#
# 表结构里 title / title_norm 是 VARCHAR(300)，item_uid 是带唯一索引的
# VARCHAR(255)。多数热榜源给的是短标题，但酷安、微信热文这类「标题」其实是
# 整段正文——归一化后动辄两三百字，拼进 item_uid 直接超列宽，PostgreSQL 抛
# StringDataRightTruncation，整轮采集 500（和当年 url 那个坑同型）。
#
# 口径分两种，不能用同一招：
# - 展示/存储字段（title / title_norm）：按列宽裁剪。超宽的部分对选题没价值，
#   砍掉比塞爆列好。
# - 身份键（item_uid）：**不能**简单裁剪——两条长帖很可能前 255 字一样、后面
#   才是差别，裁完就成了同一条。改成「可读前缀 + 归一化标题的全量 SHA-1」，
#   截掉的部分仍然参与判等；短标题保持原样，历史行身份不变。
TITLE_MAX_CHARS = 300
ITEM_UID_MAX_CHARS = 255
_UID_HASH_CHARS = 20


def _clip(text: str, limit: int) -> str:
    """按列宽裁剪。不加省略号：库里存的是能直接判等的前缀。"""
    return text if len(text) <= limit else text[:limit]


def make_item_uid(platform: str, norm: str) -> str:
    """条目身份键 = platform + 归一化标题，长度恒在 item_uid 列宽以内。

    短标题（历史数据里最长 72 字）完全保持原样；只有拼出来超宽时才截前缀，
    尾接归一化标题的 SHA-1 摘要——同一条内容每次算出来的键必须一致，
    否则幂等就废了。
    """
    uid = f"{platform}\x1f{norm}"
    if len(uid) <= ITEM_UID_MAX_CHARS:
        return uid
    digest = hashlib.sha1(norm.encode("utf-8")).hexdigest()[:_UID_HASH_CHARS]
    # 留出分隔符与 '#' 的位置；platform 来自 catalog，最长 32 字，这里只是兜底
    budget = ITEM_UID_MAX_CHARS - len(platform) - 2 - _UID_HASH_CHARS
    return f"{platform}\x1f{norm[:max(0, budget)]}#{digest}"


def now_stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _member_of(item: models.HotItem) -> dict:
    """ORM 行 → 评分函数要的成员结构。"""
    days = item.days_json or []
    return {
        "id": item.id,
        "title": item.title,
        "title_norm": item.title_norm,
        "platform": item.platform,
        "rank": item.rank,
        "hot": item.hot,
        "url": item.url,
        "first_seen_at": item.first_seen_at,
        "prev_rank": item.prev_rank,
        "best_rank": item.best_rank,
        "days_seen": len(days) or 1,
    }


def latest_day(db: Session) -> str:
    """最近一次有数据的日期。没有数据时返回空串。"""
    return db.scalar(select(func.max(models.HotItem.day))) or ""


# ══════════════════════════════════════════════════════════════════════
# 写入：灌条目 + 聚簇 + 评分
# ══════════════════════════════════════════════════════════════════════

def _ingest(db: Session, day: str, rows: list[dict], captured_at: str) -> list[dict]:
    """把采集行写成 hot_items，并返回内存里的成员视图。

    身份键是 `platform + title_norm`，**不是名次**：同一条新闻一天内从 #3 掉到 #7，
    事件没变，不能算成两条。
    """
    members: list[dict] = []
    for row in rows:
        title = (row.get("title") or "").strip()
        if not title:
            continue
        norm = ranking.normalize_title(title)
        if not norm:
            continue
        platform = row.get("platform") or ""
        # 身份键用**完整**归一化标题（超长时取前缀+哈希），落库字段按列宽裁剪：
        # 酷安/微信热文的标题可能是整段正文，直接入库会 StringDataRightTruncation。
        uid = make_item_uid(platform, norm)
        title = _clip(title, TITLE_MAX_CHARS)
        norm = _clip(norm, TITLE_MAX_CHARS)
        rank = row.get("rank")
        hot = row.get("hot")
        hot = hot if isinstance(hot, int) and hot > 0 else None
        url = row.get("url") or ""

        item = db.scalar(select(models.HotItem).where(models.HotItem.item_uid == uid))
        if item is None:
            item = models.HotItem(
                item_uid=uid, day=day, platform=platform, rank=rank,
                title=title, title_norm=norm, hot=hot, url=url,
                first_seen_at=captured_at, last_seen_at=captured_at,
                first_seen_day=day, days_json=[day], seen_count=1,
                prev_rank=None, best_rank=rank if rank is not None else 999,
            )
            db.add(item)
            db.flush()
        else:
            prev_rank = item.rank
            # 同一次采集重复跑：prev_rank 不能自我污染成同一个值
            if prev_rank == rank:
                prev_rank = item.prev_rank
            best_rank = item.best_rank
            if rank is not None and (best_rank is None or rank < best_rank):
                best_rank = rank
            days = set(item.days_json or [])
            days.add(day)
            item.rank = rank
            item.hot = hot if hot is not None else item.hot
            item.url = url or item.url
            item.last_seen_at = captured_at
            item.seen_count = (item.seen_count or 0) + 1
            item.prev_rank = prev_rank
            item.best_rank = best_rank
            item.days_json = sorted(days)
            db.flush()

        if rank is not None:
            sampled = db.scalar(
                select(models.HotRankHistory).where(
                    models.HotRankHistory.item_id == item.id,
                    models.HotRankHistory.captured_at == captured_at,
                )
            )
            if sampled is None:
                db.add(
                    models.HotRankHistory(
                        item_id=item.id, captured_at=captured_at, rank=rank, hot=hot
                    )
                )
        members.append(_member_of(item))

    db.flush()
    return members


def _upsert_clusters(
    db: Session, day: str, items: list[dict], captured_at: str
) -> tuple[int, int]:
    """聚簇并写库。返回 (新建簇数, 并入已有簇数)。"""
    # 排序固定：聚簇结果必须与采集回来的物理顺序无关
    items.sort(
        key=lambda it: (
            it["platform"],
            it["rank"] if it["rank"] is not None else 999,
            it["title_norm"],
        )
    )
    groups = ranking.cluster_items(items)
    hot_base = ranking.hot_base_by_platform(items)

    # 每天重算成员归属。不清空的话，上一次误合并留下的 cluster_id 会一直挂着：
    # 本轮即使已经拆开，旧成员仍然指向同一个簇，成员数只增不减。
    db.execute(
        update(models.HotItem)
        .where(models.HotItem.day == day)
        .values(cluster_id=None)
    )

    created = matched = 0
    used: set[str] = set()
    for members in groups:
        best = ranking.best_title(members)
        fp = ranking.fingerprint(best["title_norm"])

        # 先按 fingerprint 精确命中（最常见），再退到相似度（标题措辞变了）。
        # 同一轮里一个已有簇只能被一个分组认领，否则多个分组会把成员堆进同一张卡片。
        stmt = select(models.HotCluster).where(models.HotCluster.fingerprint == fp)
        if used:
            stmt = stmt.where(models.HotCluster.id.notin_(used))
        cluster = db.scalar(stmt)
        if cluster is None:
            window_start = (
                datetime.strptime(day, "%Y-%m-%d") - timedelta(days=HISTORY_WINDOW_DAYS)
            ).strftime("%Y-%m-%d")
            candidates = db.scalars(
                select(models.HotCluster)
                .where(models.HotCluster.last_seen_day >= window_start)
                .order_by(models.HotCluster.created_at)
            ).all()
            for candidate in candidates:
                if candidate.id in used:
                    continue
                candidate_norm = candidate.title_norm or ranking.normalize_title(candidate.title)
                if ranking.similar(best["title_norm"], candidate_norm):
                    cluster = candidate
                    break

        total, detail = ranking.score_cluster(members, captured_at, hot_base)
        platforms = sorted({m["platform"] for m in members})
        days_seen = max((m.get("days_seen") or 1) for m in members)
        risk = detail.get("risk_flags") or []

        if cluster is None:
            cluster = models.HotCluster(
                fingerprint=fp,
                title_norm=best["title_norm"],
                title=best["title"],
                track=detail.get("track") or "",
                first_seen_day=day,
                last_seen_day=day,
                days_seen=days_seen,
                best_rank=detail.get("best_rank"),
                platform_count=len(platforms),
                item_count=len(members),
                platforms=platforms,
                score=total,
                grade=ranking.score_grade(total),
                score_detail=detail,
                risk_flags=risk,
                status="new",
                captured_at=captured_at,
            )
            db.add(cluster)
            db.flush()
            created += 1
        else:
            # status 不覆盖用户设置：ignored / watching / generated 都是人工或流程结果
            cluster.fingerprint = fp
            cluster.title_norm = best["title_norm"]
            cluster.title = best["title"]
            cluster.track = detail.get("track") or cluster.track
            cluster.first_seen_day = min(cluster.first_seen_day or day, day)
            cluster.last_seen_day = max(cluster.last_seen_day or day, day)
            cluster.days_seen = max(cluster.days_seen or 1, days_seen)
            cluster.best_rank = detail.get("best_rank")
            cluster.platform_count = len(platforms)
            cluster.item_count = len(members)
            cluster.platforms = platforms
            cluster.score = total
            cluster.grade = ranking.score_grade(total)
            cluster.score_detail = detail
            cluster.risk_flags = risk
            cluster.captured_at = captured_at
            matched += 1

        used.add(cluster.id)
        for member in members:
            item = db.get(models.HotItem, member["id"])
            if item is not None:
                item.cluster_id = cluster.id
        db.flush()

    return created, matched


def refresh(
    db: Session,
    day: str | None = None,
    captured_at: str | None = None,
    platforms: list[str] | None = None,
) -> dict:
    """采集 → 灌库 → 聚簇 → 评分。返回本轮统计。

    任何单源失败都只记 notes；所有源都失败时由 `hot_sources.collect` 抛错，
    这里原样转成 HotTopicsError，不返回「0 个事件」的假成功。
    """
    day = day or today_str()
    captured_at = captured_at or now_stamp()
    try:
        rows, notes = hot_sources.collect(platforms)
    except hot_sources.HotSourcesError as exc:
        _remember_source_notes(exc.notes or [exc.message])
        raise HotTopicsError("all_sources_failed", exc.message) from exc
    _remember_source_notes(notes)

    items = _ingest(db, day, rows, captured_at)
    db.flush()
    created, matched = _upsert_clusters(db, day, items, captured_at)
    db.commit()

    clusters = list_clusters(db, day=day, limit=500)
    return {
        "day": day,
        "capturedAt": captured_at,
        "items": len(items),
        "clusters": len(clusters),
        "newClusters": created,
        "matchedClusters": matched,
        "recommended": sum(1 for c in clusters if c["score"] >= 70),
        "notes": notes,
    }


# ══════════════════════════════════════════════════════════════════════
# 读取
# ══════════════════════════════════════════════════════════════════════

def _cluster_out(cluster: models.HotCluster) -> dict:
    """统一的出参结构。分数与档位原样给前端，前端不重算。"""
    detail = cluster.score_detail or {}
    return {
        "id": cluster.id,
        "title": cluster.title,
        "track": cluster.track,
        "score": cluster.score,
        "grade": cluster.grade,
        "platforms": cluster.platforms or [],
        "platformCount": cluster.platform_count,
        "itemCount": cluster.item_count,
        "bestRank": cluster.best_rank,
        "daysSeen": cluster.days_seen,
        "firstSeenAt": detail.get("first_seen_at") or "",
        "lastSeenDay": cluster.last_seen_day,
        "riskFlags": cluster.risk_flags or [],
        "status": cluster.status,
        "capturedAt": cluster.captured_at,
        "contentItemId": cluster.content_item_id,
        "scoreDetail": detail,
    }


def degraded_reasons(db: Session, day: str | None = None) -> list[dict]:
    """这一轮哪些维度没被真实计算，以及原因。

    判据是**采样轮数**（同一天有几个不同的 captured_at）：
      1 轮 → 升降、日内新鲜度都不可算
      2 轮 → 升降可算；新鲜度要 3 轮才有意义（首见可能落在两轮之间）

    这不是保守，是数据形态的客观限制：一次刷新只覆盖一个时间点。
    """
    stmt = select(func.count(func.distinct(models.HotRankHistory.captured_at))).select_from(
        models.HotRankHistory
    )
    if day:
        stmt = stmt.join(models.HotItem, models.HotItem.id == models.HotRankHistory.item_id).where(
            models.HotItem.day == day
        )
    rounds = db.scalar(stmt) or 0

    labels = {
        "rise": "名次升降（需要同一天 ≥2 次采集）",
        "fresh": "日内新鲜度（需要 ≥2 次采集的时间点）",
        "hot": "跨平台热度对比（各平台口径不同，只做同平台归一）",
    }
    out: list[dict] = []
    if rounds < 2:
        out.append({"dim": "rise", "reason": labels["rise"] + f"，当前 {rounds} 轮"})
        out.append({"dim": "fresh", "reason": labels["fresh"] + f"，当前 {rounds} 轮"})
    elif rounds < 3:
        out.append({"dim": "fresh", "reason": labels["fresh"] + f"，当前 {rounds} 轮"})
    out.append({"dim": "hot", "reason": labels["hot"]})

    # 采集侧的降级说明也进同一接口：单源失败、解析 0 行（selector_may_changed）、
    # 源未配置关键词都要让前端看到，不能「悄悄少一个平台」。
    for note in _last_source_notes:
        out.append({"dim": "source", "reason": note})
    return out


def list_clusters(
    db: Session,
    day: str | None = None,
    min_score: int = 0,
    track: str | None = None,
    platform: str | None = None,
    status: str | None = None,
    q: str | None = None,
    sort: str = "score",
    limit: int = 60,
) -> list[dict]:
    """事件列表。默认按分数排序（**不按 id**）——热点的意义就是「最值得写的在最前」。

    platform 在 Python 侧过滤：JSON 列的 LIKE 在 SQLite/PostgreSQL 行为不一致，
    当天簇数量在几百以内，多扫一遍比写方言分支便宜。
    """
    day = day if day is not None else latest_day(db)
    stmt = select(models.HotCluster)
    if day:
        stmt = stmt.join(
            models.HotItem, models.HotItem.cluster_id == models.HotCluster.id
        ).where(models.HotItem.day == day).group_by(models.HotCluster.id)
    if min_score:
        stmt = stmt.where(models.HotCluster.score >= int(min_score))
    if track:
        stmt = stmt.where(models.HotCluster.track == track)
    if status:
        stmt = stmt.where(models.HotCluster.status == status)
    if q:
        stmt = stmt.where(models.HotCluster.title.contains(q))

    clusters = db.scalars(stmt).all()
    if platform:
        clusters = [c for c in clusters if platform in (c.platforms or [])]

    if sort == "time":
        clusters.sort(key=lambda c: (c.last_seen_day or "", c.score or 0), reverse=True)
    elif sort == "resonance":
        clusters.sort(key=lambda c: (c.platform_count or 0, c.score or 0), reverse=True)
    else:
        clusters.sort(key=lambda c: (-(c.score or 0), c.id))
    return [_cluster_out(c) for c in clusters[: max(1, min(int(limit), 500))]]


def get_cluster(db: Session, cluster_id: str) -> dict | None:
    cluster = db.get(models.HotCluster, cluster_id)
    if cluster is None:
        return None
    out = _cluster_out(cluster)
    items = db.scalars(
        select(models.HotItem)
        .where(models.HotItem.cluster_id == cluster.id)
        .order_by(models.HotItem.platform, models.HotItem.rank)
    ).all()
    out["items"] = [
        {
            "id": item.id,
            "platform": item.platform,
            "rank": item.rank,
            "title": item.title,
            "hot": item.hot,
            "url": item.url,
            "firstSeenAt": item.first_seen_at,
            "lastSeenAt": item.last_seen_at,
            "history": [
                {"capturedAt": sample.captured_at, "rank": sample.rank, "hot": sample.hot}
                for sample in db.scalars(
                    select(models.HotRankHistory)
                    .where(models.HotRankHistory.item_id == item.id)
                    .order_by(models.HotRankHistory.captured_at)
                ).all()
            ],
        }
        for item in items
    ]
    if cluster.content_item_id:
        content = db.get(models.ContentItem, cluster.content_item_id)
        out["contentItemTitle"] = content.title if content else ""
    else:
        out["contentItemTitle"] = ""
    return out


def summary(db: Session, day: str | None = None) -> dict:
    """顶部概要条。一次查询给全，前端不用自己拼。"""
    day = day if day is not None else latest_day(db)
    items_stmt = select(func.count(models.HotItem.id))
    if day:
        # 只数当前挂在簇上的条目：掉榜的旧条目还留在 hot_items 里，
        # 算进来会让「素材数」和事件卡片的原文数合计对不上。
        items_stmt = items_stmt.where(
            models.HotItem.day == day, models.HotItem.cluster_id.isnot(None)
        )
    items = db.scalar(items_stmt) or 0

    # 与 list_clusters 同口径：只算今天有成员条目的簇，
    # 否则被拆掉/放弃的旧簇会让概要条比列表多出几十个「幽灵事件」。
    cluster_stmt = select(models.HotCluster)
    if day:
        cluster_stmt = (
            cluster_stmt.join(
                models.HotItem, models.HotItem.cluster_id == models.HotCluster.id
            )
            .where(models.HotItem.day == day)
            .group_by(models.HotCluster.id)
        )
    clusters = db.scalars(cluster_stmt).all()

    tracks: dict[str, int] = {}
    platform_counts: dict[str, int] = {}
    for cluster in clusters:
        name = cluster.track or "其他"
        tracks[name] = tracks.get(name, 0) + 1
        # 一个事件可以横跨多个平台，所以每个平台各计一次：这里回答的是
        # 「切到某个平台能看到几个事件」，不是把事件按平台分摊。
        for platform in cluster.platforms or []:
            platform_counts[platform] = platform_counts.get(platform, 0) + 1

    latest = max((c.captured_at or "" for c in clusters), default="")
    return {
        "day": day,
        "items": items,
        "clusters": len(clusters),
        "recommended": sum(1 for c in clusters if (c.score or 0) >= 70),
        "generated": sum(1 for c in clusters if c.status == "generated"),
        "tracks": tracks,
        "platforms": platform_counts,
        "capturedAt": latest,
    }


def dashboard_highlights(db: Session, limit: int = 5) -> dict:
    """首页「今日热点」：复用当日高分簇与概要，不引入第二套查询口径。

    没有数据时返回空 topics 而非报错——首页必须永远能打开。
    """
    day = latest_day(db)
    if not day:
        return {"day": "", "capturedAt": "", "clusters": 0, "topics": []}
    stats = summary(db, day=day)
    clusters = list_clusters(db, day=day, sort="score", limit=limit)
    return {
        "day": stats["day"],
        "capturedAt": stats["capturedAt"],
        "clusters": stats["clusters"],
        "topics": [
            {
                "id": row["id"],
                "title": row["title"],
                "score": row["score"],
                "grade": row["grade"],
                "platformCount": row["platformCount"],
            }
            for row in clusters
        ],
    }


def set_status(db: Session, cluster_id: str, status: str) -> dict | None:
    if status not in VALID_STATUSES:
        raise HotTopicsError(
            "invalid_status", f"不支持的状态：{status}（可用：{'、'.join(sorted(VALID_STATUSES))}）"
        )
    cluster = db.get(models.HotCluster, cluster_id)
    if cluster is None:
        return None
    # generated 只能由「成稿」写入；手工改回 generated 会让「已出稿」统计失真
    if status == "generated" and not cluster.content_item_id:
        raise HotTopicsError("invalid_status", "还没有成稿，不能标记为已出稿")
    cluster.status = status
    db.commit()
    return _cluster_out(cluster)


def _new_draft(
    db: Session, cluster: models.HotCluster, user: models.User, workspace: models.Workspace
) -> models.ContentItem:
    """建一篇空草稿（标题=热点标题），不 commit、不碰簇状态。

    「热点 → 稿件」和「定时选题简报」共用这一处，避免两条路径的字段口径分叉。
    """
    item = models.ContentItem(
        workspace_id=workspace.id,
        owner_user_id=user.id,
        title=cluster.title[:200],
        type="article",
    )
    db.add(item)
    db.flush()
    version = models.ContentVersion(
        content_item_id=item.id, version_no=1, title=item.title, body=""
    )
    db.add(version)
    db.flush()
    item.current_version_id = version.id
    return item


def create_draft(
    db: Session, cluster_id: str, user: models.User, workspace: models.Workspace
) -> tuple[models.ContentItem, dict] | None:
    """热点 → 稿件。重复点击返回同一篇，不重复建。"""
    cluster = db.get(models.HotCluster, cluster_id)
    if cluster is None:
        return None

    if cluster.content_item_id:
        existing = db.get(models.ContentItem, cluster.content_item_id)
        if existing is not None:
            return existing, _cluster_out(cluster)

    item = _new_draft(db, cluster, user, workspace)
    cluster.content_item_id = item.id
    cluster.status = "generated"
    db.commit()
    return item, _cluster_out(cluster)


# 简报扫描上限：簇按天分，一天几百条以内，多扫一遍比写多赛道 SQL 便宜
DIGEST_SCAN_LIMIT = 200


def _owner_has_draft(db: Session, user: models.User, title: str) -> bool:
    """同一个人已有同名未归档稿件 → 视为建过，不重复建。

    不能用 `cluster.content_item_id` 判重：那一列是全局单一指针（热点数据不分用户），
    按它判重会让第二个用户永远拿不到自己的草稿。
    """
    exists = db.scalar(
        select(models.ContentItem.id)
        .where(
            models.ContentItem.owner_user_id == user.id,
            models.ContentItem.title == title[:200],
            models.ContentItem.archived.is_(False),
        )
        .limit(1)
    )
    return exists is not None


def build_digest(
    db: Session,
    user: models.User,
    workspace: models.Workspace,
    *,
    tracks: list[str] | None = None,
    min_score: int = 60,
    limit: int = 3,
    create_drafts: bool = True,
) -> dict:
    """把当天的高分热点整理成「选题简报」，可选给每条建一篇草稿。

    产出只落在**这个用户**的创作空间里，不改簇的 `status` / `content_item_id`
    —— 那是热点页的全局状态，定时任务不该替所有人改。
    """
    day = latest_day(db)
    if not day:
        # 一条数据都没有说明采集侧坏了，不能报「简报已生成（0 条）」把问题藏起来
        raise HotTopicsError("no_hot_data", "库里还没有任何热点数据，请先跑一次热点采集")

    wanted = {str(track).strip() for track in (tracks or []) if str(track).strip()}
    scanned = list_clusters(db, day=day, min_score=int(min_score), limit=DIGEST_SCAN_LIMIT)
    picks = [row for row in scanned if not wanted or row["track"] in wanted]
    picks = picks[: max(1, min(int(limit), 20))]

    drafts, reused = 0, 0
    for row in picks:
        if not create_drafts:
            continue
        cluster = db.get(models.HotCluster, row["id"])
        if cluster is None:
            continue
        if _owner_has_draft(db, user, cluster.title):
            reused += 1
            continue
        _new_draft(db, cluster, user, workspace)
        drafts += 1
    db.commit()

    return {
        "day": day,
        "total": len(picks),
        "draftsCreated": drafts,
        "draftsReused": reused,
        "tracks": sorted(wanted),
        "minScore": int(min_score),
        "items": [
            {
                "clusterId": row["id"],
                "title": row["title"],
                "track": row["track"],
                "score": row["score"],
                "grade": row["grade"],
                "platforms": row["platforms"],
            }
            for row in picks
        ],
    }


def sources_status() -> dict:
    return {
        "platforms": hot_sources.source_catalog(),
        "enabled": hot_sources.enabled_platforms(),
        "fallbackConfigured": bool(_configured_source()),
    }
