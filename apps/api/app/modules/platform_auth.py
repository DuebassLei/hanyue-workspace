"""平台账号接入与校验（设计 §7.3 / §10.4 / §12.1）。

平台收敛说明：仅保留微信公众号（AppID/AppSecret + 草稿箱）。
- 公众号没有面向第三方创作工具的 OAuth 授权码流程，因此不存在 OAuth 回调端点；
- 账号验证统一走连接器真实探针（draft/batchget），演示账号 / 未配置 / 验证失败
  全部如实报错，不伪造「验证通过」。
"""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import select

from .. import models
from ..connectors import base as connectors
from ..connectors import registry
from ..core.deps import ApiError, CurrentUser, DbSession, not_found

router = APIRouter(tags=["platform-auth"])


@router.get("/platforms/connectors/status")
def connectors_status(db: DbSession) -> dict:
    report = connectors.evidence_report()
    return {
        "platforms": [registry.connector_status(code, db) for code in connectors.PLATFORMS],
        "evidence": report,
        "note": "configured 只表示公众号凭据已配置；草稿箱推送不依赖发布能力核验",
    }


@router.get("/platforms/{platform}/maintenance")
def platform_maintenance(platform: str, user: CurrentUser, db: DbSession) -> dict:
    """单平台维护视图：凭据状态、能力、验证能力、绑定账号一次返回。"""
    if platform not in connectors.PLATFORMS:
        raise ApiError("unknown_platform", "未知平台")
    connector = registry.get_connector(platform, db)
    cap = connectors.get_capability(platform)
    accounts = db.scalars(
        select(models.ConnectedAccount).where(
            models.ConnectedAccount.owner_user_id == user.id,
            models.ConnectedAccount.platform == platform,
        )
    ).all()
    status = registry.connector_status(platform, db)
    return {
        "platform": platform,
        "label": cap.label,
        "connector": status,
        "capability": {
            "confirmed": cap.confirmed,
            "publish": cap.publish,
            "draftUpload": cap.draft_upload,
            "contentMetrics": cap.content_metrics,
            "note": cap.note,
            "evidenceDate": cap.evidence_date,
        },
        "accounts": [
            {
                "id": a.id,
                "displayName": a.display_name,
                "status": a.status,
                "authSource": a.auth_source,
                "scopes": a.scopes or [],
                "expiresAt": a.expires_at.isoformat() if a.expires_at else None,
                "lastSyncedAt": a.last_synced_at.isoformat() if a.last_synced_at else None,
                "verifiable": a.auth_source in ("oauth", "app_secret"),
            }
            for a in accounts
        ],
        "verifyCapable": status.get("verifyCapable", False),
        "verifyNote": status.get("verifyNote", ""),
    }


@router.post("/platform-accounts/{account_id}/test")
def test_platform_account(account_id: str, user: CurrentUser, db: DbSession) -> dict:
    """统一真实验证入口：公众号凭据走连接器探针（draft/batchget）。

    演示账号 / 未配置 / 验证失败全部如实报错，不伪造「验证通过」。
    """
    account = db.get(models.ConnectedAccount, account_id)
    if not account or account.owner_user_id != user.id:
        raise not_found("平台账号")
    if account.auth_source == "demo":
        raise ApiError(
            "demo_account",
            "这是历史演示账号，没有平台凭据；请绑定公众号凭据后重新验证",
            status_code=409,
        )
    connector = registry.get_account_connector(account, db)
    if not getattr(connector, "configured", False):
        raise ApiError(
            "connector_unavailable",
            "该账号未绑定凭据，且未配置默认 AppID/AppSecret",
            status_code=409,
        )

    # 公众号（AppSecret 型）：探针只依赖服务端凭据，不需要账号令牌
    from ..connectors.wechat_mp import WeChatMPConnector, WeChatMPError

    try:
        probe = connector.ping(db)
    except WeChatMPError as exc:
        account.status = "reauthorization_required"
        db.commit()
        raise ApiError(exc.code, exc.message, status_code=409, retryable=exc.retryable) from exc
    account.status = "connected"
    account.auth_source = "app_secret"
    db.commit()
    return {
        "status": "connected",
        "verified": True,
        "subject": probe["appId"],
        "detail": {"draftTotal": probe["draftTotal"], "mode": "app_secret"},
        "note": "公众号凭据验证通过（真实调用草稿列表接口）",
    }


@router.get("/platform-accounts/{account_id}/sync-status")
def sync_status(account_id: str, user: CurrentUser, db: DbSession) -> dict:
    account = db.get(models.ConnectedAccount, account_id)
    if not account or account.owner_user_id != user.id:
        raise not_found("平台账号")
    recent = db.scalar(
        select(models.PlatformSyncJob)
        .where(models.PlatformSyncJob.connected_account_id == account.id)
        .order_by(models.PlatformSyncJob.created_at.desc())
        .limit(1)
    )
    jobs = db.scalars(
        select(models.BackgroundJob)
        .where(models.BackgroundJob.queue == "platform-sync")
        .order_by(models.BackgroundJob.created_at.desc())
        .limit(20)
    ).all()
    related = [
        {"id": j.id, "status": j.status, "errorCode": j.error_code}
        for j in jobs
        if (j.payload or {}).get("accountId") == account.id
    ]
    connector = registry.get_connector(account.platform, db)
    return {
        "accountId": account.id,
        "connectorConfigured": bool(getattr(connector, "configured", False)),
        "capabilityConfirmed": connectors.get_capability(account.platform).confirmed,
        "lastSyncedAt": account.last_synced_at.isoformat() if account.last_synced_at else None,
        "latestSyncJob": {
            "id": recent.id,
            "status": recent.status,
            "errorCode": recent.error_code,
            "startedAt": recent.started_at.isoformat() if recent.started_at else None,
            "completedAt": recent.completed_at.isoformat() if recent.completed_at else None,
        } if recent else None,
        "recentJobs": related,
    }
