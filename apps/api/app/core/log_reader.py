"""日志读取基础设施：直读 storage/logs 下的轮转日志文件，供 /logs 接口查询。

设计约束（见 core/logging.py）：
- 不入库、不建索引：按需反向 tail + 内存过滤，零迁移零存储成本。
- 每个服务一个逻辑日志流（api / worker），物理上是 `<service>.log` 加轮转备份
  `<service>.log.1..N`（RotatingFileHandler 语义：编号越大越旧）。
- 增量拉取用「字节偏移」表达（跨备份+当前文件按时间序累计）：文件比 offset 小
  （轮转/清空）时 nextOffset 归零，客户端自动回退为重新全量拉取尾部。
- 只暴露元信息（大小/修改时间/条数计数），不回显绝对路径，避免泄露部署目录结构。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .config import Settings, get_settings

# core/logging.py 的 Formatter 行首：2026-11-15 06:53:13 INFO    [name] [reqid] message
_LINE_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) (?P<level>[A-Z]+)\s+\[(?P<logger>[^\]]*)\](?: \[(?P<rid>[^\]]*)\])?(?P<message>.*)$"
)
_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
_LEVEL_SET = set(_LEVELS)


@dataclass
class LogEntry:
    ts: str
    level: str
    logger: str
    request_id: str
    message: str
    raw: str


@dataclass
class LogPage:
    entries: list[LogEntry] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    truncated: bool = False
    has_more: bool = False
    file_size: int = 0
    modified_at: float | None = None
    next_offset: int = 0


def log_dir(settings: Settings | None = None) -> Path:
    settings = settings or get_settings()
    return Path(settings.log_dir) if settings.log_dir else settings.storage_dir / "logs"


def available_services(settings: Settings | None = None) -> list[str]:
    """扫描日志目录，返回存在日志文件的服务名（api / worker / …）。"""
    directory = log_dir(settings)
    services: set[str] = set()
    if directory.is_dir():
        for path in directory.glob("*.log"):
            services.add(path.stem)
    return sorted(services)


def _ordered_files(directory: Path, service: str, backup_count: int) -> list[Path]:
    """从最旧备份到当前文件排序：service.log.N … service.log.1, service.log。"""
    current = directory / f"{service}.log"
    backups = [
        (n, directory / f"{service}.log.{n}")
        for n in range(1, backup_count + 1)
        if (directory / f"{service}.log.{n}").is_file()
    ]
    backups.sort(key=lambda item: -item[0])
    files = [path for _, path in backups]
    if current.is_file():
        files.append(current)
    return files


def _parse_line(raw: str) -> LogEntry:
    match = _LINE_RE.match(raw)
    if not match:
        # 异常堆栈续行等非标准行：无级别，前端按上下文展示
        return LogEntry(ts="", level="", logger="", request_id="", message=raw, raw=raw)
    return LogEntry(
        ts=match["ts"],
        level=match["level"].upper(),
        logger=match["logger"],
        request_id=match["rid"] or "",
        message=match["message"].strip(),
        raw=raw,
    )


def _matches(entry: LogEntry, min_rank: int | None, needle: str | None, request_id: str | None) -> bool:
    if entry.level:
        if min_rank is not None and (_LEVELS.index(entry.level) if entry.level in _LEVEL_SET else -1) < min_rank:
            return False
    elif min_rank is not None and min_rank > _LEVELS.index("INFO"):
        # 只看 ERROR+ 时不必带上无级别的堆栈续行
        return False
    if needle and needle not in entry.raw.lower():
        return False
    if request_id and entry.request_id != request_id:
        return False
    return True


def _count(page: LogPage, level: str) -> None:
    page.counts[level] = page.counts.get(level, 0) + 1


def _safe_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def read_logs(
    service: str,
    *,
    settings: Settings | None = None,
    offset: int | None = None,
    limit: int = 200,
    min_level: str | None = None,
    keyword: str | None = None,
    request_id: str | None = None,
    max_scan_bytes: int = 4 * 1024 * 1024,
) -> LogPage:
    """读取服务日志并过滤。

    - offset=None：tail 模式，返回满足过滤条件的最近 limit 条；扫描窗口内各级别计数。
    - offset=N：forward 模式，从第 N 字节向后读到 EOF（轮询新日志）；命中超过 limit
      置 truncated=True（next_offset 仍推进到 EOF，避免死循环）。
    - 文件总长小于 offset（轮转/清空）时 next_offset 归零，提示客户端重置。

    max_scan_bytes 按 limit 放大（约每行 300 字节，上限 64MB），否则导出大 limit
    （如近 1 万行）会被默认 4MB 扫描窗口卡住，只能拿到尾部一小截。
    """
    settings = settings or get_settings()
    files = _ordered_files(log_dir(settings), service, settings.log_backup_count)
    page = LogPage(counts={level: 0 for level in _LEVELS})
    if not files:
        return page
    min_rank = _LEVELS.index(min_level.upper()) if min_level and min_level.upper() in _LEVEL_SET else None
    needle = keyword.lower() if keyword else None
    budget = max(max_scan_bytes, min(limit * 300, 64 * 1024 * 1024))

    if offset is None:
        return _read_tail(files, page, limit=limit, min_rank=min_rank, needle=needle,
                          request_id=request_id, max_scan_bytes=budget)
    return _read_forward(files, page, offset=offset, limit=limit, min_rank=min_rank,
                         needle=needle, request_id=request_id)


def _read_tail(
    files: list[Path],
    page: LogPage,
    *,
    limit: int,
    min_rank: int | None,
    needle: str | None,
    request_id: str | None,
    max_scan_bytes: int,
) -> LogPage:
    collected: list[LogEntry] = []
    scanned_total = 0
    budget = max_scan_bytes
    exhausted = True
    current = files[-1]
    # 从最新文件往旧回溯
    for path in reversed(files):
        if budget <= 0:
            exhausted = False
            break
        try:
            size = path.stat().st_size
            take = min(size, budget)
            head_trim = size > take  # 窗口起点可能落在半行上
            with path.open("rb") as fh:
                fh.seek(size - take)
                chunk = fh.read(take)
        except OSError:
            continue
        scanned_total += take
        budget -= take
        lines = chunk.decode("utf-8", errors="replace").splitlines()
        if head_trim and lines:
            lines = lines[1:]  # 丢弃可能被截断的首行
        for raw in reversed(lines):
            entry = _parse_line(raw.rstrip("\r"))
            if entry.level:
                _count(page, entry.level)
            if _matches(entry, min_rank, needle, request_id):
                collected.append(entry)
                if len(collected) >= limit:
                    break
        if path is current:
            page.file_size = size
            try:
                page.modified_at = path.stat().st_mtime
            except OSError:
                pass
        if len(collected) >= limit:
            break
    page.entries = list(reversed(collected))
    page.has_more = not exhausted or (len(collected) >= limit and not _scan_complete(scanned_total, files))
    return page


def _scan_complete(scanned: int, files: list[Path]) -> bool:
    total = sum(_safe_size(f) for f in files)
    return scanned >= total


def _read_forward(
    files: list[Path],
    page: LogPage,
    *,
    offset: int,
    limit: int,
    min_rank: int | None,
    needle: str | None,
    request_id: str | None,
) -> LogPage:
    """从累计字节偏移（跨备份+当前文件，按时间序）向后读到 EOF。"""
    current = files[-1]
    try:
        sizes = [f.stat().st_size for f in files]
        page.file_size = current.stat().st_size
        page.modified_at = current.stat().st_mtime
    except OSError:
        return page
    if offset > sum(sizes):
        # 轮转或清空导致偏移失效：重置，让客户端从头重新拉尾部
        page.next_offset = 0
        return page
    consumed = 0
    pending_partial = ""
    for path, size in zip(files, sizes):
        start = max(offset - consumed, 0)
        consumed += size
        if start >= size:
            continue
        try:
            with path.open("rb") as fh:
                fh.seek(start)
                chunk = fh.read()
        except OSError:
            continue
        text = pending_partial + chunk.decode("utf-8", errors="replace")
        lines = text.split("\n")
        # 非末尾文件的最后一段可能没有换行结尾，留给下一个文件拼接
        pending_partial = lines.pop() if path is not current else ""
        for raw in lines:
            raw = raw.rstrip("\r")
            if not raw:
                continue
            entry = _parse_line(raw)
            if entry.level:
                _count(page, entry.level)
            if _matches(entry, min_rank, needle, request_id):
                if len(page.entries) < limit:
                    page.entries.append(entry)
                else:
                    page.truncated = True
    new_total = sum(_safe_size(f) for f in files)
    page.next_offset = new_total if new_total >= offset else 0
    return page


def summary(settings: Settings | None = None, *, scan_bytes: int = 1024 * 1024) -> dict:
    """各服务最近一段日志（默认 INFO 及以上）的级别计数概览，供前端角标提示。"""
    settings = settings or get_settings()
    result: dict = {"services": {}}
    for service in available_services(settings):
        page = _read_tail(
            _ordered_files(log_dir(settings), service, settings.log_backup_count),
            LogPage(counts={level: 0 for level in _LEVELS}),
            limit=10**9,
            min_rank=_LEVELS.index("INFO"),
            needle=None,
            request_id=None,
            max_scan_bytes=scan_bytes,
        )
        result["services"][service] = {
            "counts": page.counts,
            "fileSize": page.file_size,
            "modifiedAt": page.modified_at,
        }
    return result
