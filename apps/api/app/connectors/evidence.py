"""Phase 0 平台能力核验证据登记表（设计 §15）。

代码不自行宣称任何平台「已核验」。`confirmed=True` 只能来自运维显式提供的证据文件，
且必须通过：核验日期有效性/时效、官方 HTTPS 文档链接的字面安全校验、真实 scope 列表。
证据不全、过期或格式不合法时一律降级为未核验，自动发布入口继续禁用。

注意：域名是否为「官方开放平台」仍需人工核对；代码只能挡住明显伪造与内网地址。
配置：环境变量 `PLATFORM_EVIDENCE_FILE` 指向 JSON，格式见 `infra/platform-evidence.example.json`。
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlparse

from ..core import ssrf

CAPABILITY_FIELDS = (
    "accountAuth",
    "publish",
    "scheduledPublish",
    "imagePublish",
    "draftUpload",
    "accountMetrics",
    "contentMetrics",
)

# 证据有效期：超过一年必须重新核验官方文档
MAX_EVIDENCE_AGE_DAYS = 365
# 模板里的占位文本不能被当成真实证据，防止「复制示例即放开自动发布」
_PLACEHOLDER_MARKERS = ("替换", "示例", "example.com", "placeholder", "todo", "xxx", "（", "填")


def _looks_like_placeholder(value: str) -> bool:
    lowered = value.lower()
    return any(marker.lower() in lowered for marker in _PLACEHOLDER_MARKERS)


def _parse_date(value) -> date | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None
    return parsed.date()


def _valid_date(value, field: str, warnings: list[str]) -> bool:
    parsed = _parse_date(value)
    # 用本地日期：运维按本地日历填写核验日期，UTC 会让当天被误判为「未来」
    today = date.today()
    if parsed is None:
        warnings.append(f"{field} 不是合法的 ISO 日期")
        return False
    if parsed > today:
        warnings.append(f"{field} 晚于当前日期")
        return False
    if (today - parsed).days > MAX_EVIDENCE_AGE_DAYS:
        warnings.append(f"{field} 超过 {MAX_EVIDENCE_AGE_DAYS} 天，需重新核验官方文档")
        return False
    return True


def _valid_evidence_url(value, warnings: list[str]) -> bool:
    if not isinstance(value, str) or not value.strip():
        warnings.append("evidenceUrl 缺失")
        return False
    if _looks_like_placeholder(value):
        warnings.append("evidenceUrl 仍含模板占位文本，请换成真实官方文档链接")
        return False
    try:
        normalized = ssrf.validate_base_url(value)
    except ssrf.UnsafeUrl as exc:
        warnings.append(f"evidenceUrl 不安全：{exc}")
        return False
    parsed = urlparse(normalized)
    if not parsed.netloc:
        warnings.append("evidenceUrl 缺少主机名")
        return False
    return True


def _valid_scopes(value, warnings: list[str]) -> bool:
    if not isinstance(value, list) or not value:
        warnings.append("scopes 必须是非空数组")
        return False
    if not all(isinstance(item, str) and item.strip() for item in value):
        warnings.append("scopes 必须全部是非空字符串")
        return False
    if any(_looks_like_placeholder(item) for item in value):
        warnings.append("scopes 仍含模板占位文本，请换成应用实际获批的 scope")
        return False
    return True


def load_evidence() -> dict:
    """返回 {'verifiedAt': str|None, 'platforms': {code: {...}}, 'warnings': [str]}。

    只做纯数据校验，不 import 能力矩阵，避免循环依赖。
    """
    path = os.getenv("PLATFORM_EVIDENCE_FILE", "").strip()
    result: dict = {"verifiedAt": None, "platforms": {}, "warnings": []}
    if not path:
        result["warnings"].append("未配置 PLATFORM_EVIDENCE_FILE，全部平台保持待核验")
        return result
    file = Path(path)
    if not file.is_file():
        result["warnings"].append(f"证据文件不存在：{file}")
        return result
    try:
        raw = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        result["warnings"].append(f"证据文件无法解析：{exc}")
        return result
    if not isinstance(raw, dict):
        result["warnings"].append("证据文件根节点必须是对象")
        return result

    result["verifiedAt"] = raw.get("verifiedAt")
    entries = raw.get("platforms")
    if not isinstance(entries, dict):
        result["warnings"].append("证据文件缺少 platforms 对象")
        return result

    for code, entry in entries.items():
        if not isinstance(entry, dict):
            result["warnings"].append(f"{code} 的证据不是对象，已忽略")
            continue
        wanted = bool(entry.get("confirmed"))
        if wanted:
            checks: list[str] = []
            _valid_date(result["verifiedAt"], "verifiedAt", checks)
            _valid_date(entry.get("evidenceDate"), "evidenceDate", checks)
            _valid_evidence_url(entry.get("evidenceUrl"), checks)
            _valid_scopes(entry.get("scopes"), checks)
            if checks:
                result["warnings"].append(f"{code} 证据无效，拒绝标记为已核验：{'；'.join(checks)}")
                wanted = False
        normalized = {
            "confirmed": wanted,
            "evidenceDate": entry.get("evidenceDate"),
            "evidenceUrl": entry.get("evidenceUrl"),
            "scopes": list(entry.get("scopes") or []) if isinstance(entry.get("scopes"), list) else [],
            "note": entry.get("note") or "依据官方开放平台文档核验",
        }
        for field in CAPABILITY_FIELDS:
            normalized[field] = bool(entry.get(field)) if wanted else False
        result["platforms"][code] = normalized
    return result
