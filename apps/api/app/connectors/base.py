"""平台能力矩阵与 Connector 抽象（设计文档第 7 章）。

平台收敛说明：其他内容平台（小红书 / 抖音 / 视频号 / B站）的开放能力未按官方
文档核验前不提供任何入口，已从系统中移除。当前仅保留**微信公众号**，其真实能力：
- 草稿箱推送（cgi-bin/draft/add，低风险动作，凭据 + IP 白名单即可）
- 凭据真实验证（draft/batchget 探针）
- 正式群发未接入：明确报错，绝不伪造发布成功
"""

from __future__ import annotations

from dataclasses import dataclass, field

PLATFORMS = ["wechat_official"]

PLATFORM_LABELS = {
    "wechat_official": "公众号",
}


@dataclass(frozen=True)
class PlatformCapability:
    platform: str
    label: str
    confirmed: bool = False
    account_auth: bool = False
    publish: bool = False
    scheduled_publish: bool = False
    image_publish: bool = False
    draft_upload: bool = False
    account_metrics: bool = False
    content_metrics: bool = False
    note: str = "待核实官方开放范围"
    evidence_date: str | None = None
    evidence_url: str | None = None
    scopes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "platform": self.platform,
            "label": self.label,
            "confirmed": self.confirmed,
            "accountAuth": self.account_auth,
            "publish": self.publish,
            "scheduledPublish": self.scheduled_publish,
            "imagePublish": self.image_publish,
            "draftUpload": self.draft_upload,
            "accountMetrics": self.account_metrics,
            "contentMetrics": self.content_metrics,
            "note": self.note,
            "evidenceDate": self.evidence_date,
            "evidenceUrl": self.evidence_url,
            "scopes": self.scopes,
        }


# 公众号草稿箱是公开接口（凭据 + IP 白名单即用），能力真实存在且已实现；
# 自动群发（publish）未接入，保持 False，绝不伪造对外发布能力。
CAPABILITIES: dict[str, PlatformCapability] = {
    "wechat_official": PlatformCapability(
        platform="wechat_official",
        label=PLATFORM_LABELS["wechat_official"],
        confirmed=True,
        account_auth=True,
        publish=False,
        draft_upload=True,
        note="草稿箱推送已实现（AppID/AppSecret + 真实验证）；正式群发需认证账号且未接入",
    ),
}


def evidence_report() -> dict:
    """供能力矩阵接口返回能力说明，保持接口契约兼容。"""
    confirmed = sum(1 for c in CAPABILITIES.values() if c.confirmed)
    return {
        "verifiedAt": None,
        "warnings": [],
        "verifiedCount": confirmed,
    }


def get_capability(platform: str) -> PlatformCapability:
    return CAPABILITIES.get(platform) or PlatformCapability(
        platform=platform, label=platform, note="未知平台"
    )


def capability_allows_publish(platform: str) -> bool:
    cap = get_capability(platform)
    return cap.confirmed and cap.publish


def manual_publish_required(platform: str) -> bool:
    """不满足自动发布条件时，界面必须降级为导出/手动发布。"""
    return not capability_allows_publish(platform)


def validate_draft(
    platform: str, *, title: str, body: str, asset_count: int
) -> list[dict]:
    """平台侧预检；公众号按官方限制校验。"""
    issues: list[dict] = []
    if not title.strip():
        issues.append({"field": "title", "level": "error", "message": "标题不能为空"})
    if not body.strip():
        issues.append({"field": "body", "level": "error", "message": "正文不能为空"})
    if platform == "wechat_official":
        if len(title) > 64:
            issues.append(
                {"field": "title", "level": "error", "message": "标题超过 64 字（微信限制）"}
            )
        if len(body) > 20000:
            issues.append({"field": "body", "level": "warning", "message": "正文超过 20000 字，请拆分"})
    return issues
