"""Agent 工具风险分级与授权合并（设计文档 5.7 / 12.3）。

原则：模型永远不决定“是否允许”；工具网关按服务端策略强制。
"""

from __future__ import annotations

READ_ONLY = "read_only"
REVERSIBLE_WRITE = "reversible_write"
EXTERNAL_SIDE_EFFECT = "external_side_effect"
FORBIDDEN = "forbidden"

RISK_ORDER = {READ_ONLY: 0, REVERSIBLE_WRITE: 1, EXTERNAL_SIDE_EFFECT: 2, FORBIDDEN: 3}

TOOL_CATALOG: dict[str, dict] = {
    "read_content": {"risk": READ_ONLY, "label": "读取稿件"},
    "list_assets": {"risk": READ_ONLY, "label": "浏览素材"},
    "sync_metrics_read": {"risk": READ_ONLY, "label": "读取平台指标"},
    "create_outline": {"risk": REVERSIBLE_WRITE, "label": "生成大纲"},
    "generate_article": {"risk": REVERSIBLE_WRITE, "label": "生成文章草稿"},
    "generate_image": {"risk": REVERSIBLE_WRITE, "label": "生成配图任务"},
    "prepare_platform_variant": {"risk": REVERSIBLE_WRITE, "label": "生成平台版本"},
    "create_publish_preview": {"risk": REVERSIBLE_WRITE, "label": "创建发布预览"},
    "publish_content": {"risk": EXTERNAL_SIDE_EFFECT, "label": "发布内容到平台"},
    "delete_asset": {"risk": EXTERNAL_SIDE_EFFECT, "label": "删除素材"},
    "unbind_platform_account": {"risk": EXTERNAL_SIDE_EFFECT, "label": "解绑平台账号"},
    "update_model_credential": {"risk": EXTERNAL_SIDE_EFFECT, "label": "修改模型凭据"},
    "run_shell": {"risk": FORBIDDEN, "label": "执行命令"},
    "raw_sql": {"risk": FORBIDDEN, "label": "执行 SQL"},
    "read_local_files": {"risk": FORBIDDEN, "label": "读取本机文件"},
}


class ToolNotAllowed(Exception):
    pass


def tool_risk(tool_id: str) -> str:
    return TOOL_CATALOG.get(tool_id, {}).get("risk", FORBIDDEN)


def resolve_allowed_tools(
    *,
    skill_tools: list[str],
    workspace_granted: list[str],
    policy_max_risk: str = EXTERNAL_SIDE_EFFECT,
) -> list[str]:
    """取交集：Skill 声明 ∩ 工作区授权，且不超过策略上限；禁用项直接剔除。"""
    limit = RISK_ORDER.get(policy_max_risk, RISK_ORDER[EXTERNAL_SIDE_EFFECT])
    granted = set(workspace_granted)
    allowed: list[str] = []
    for tool in skill_tools:
        if tool not in granted:
            continue
        risk = tool_risk(tool)
        if risk == FORBIDDEN or RISK_ORDER[risk] > limit:
            continue
        allowed.append(tool)
    return allowed


def requires_approval(tool_id: str) -> bool:
    risk = tool_risk(tool_id)
    if risk == FORBIDDEN:
        return True
    return risk == EXTERNAL_SIDE_EFFECT


def assert_tool_callable(tool_id: str, allowed_tools: list[str]) -> None:
    if tool_id not in TOOL_CATALOG:
        raise ToolNotAllowed(f"未知工具：{tool_id}")
    if tool_risk(tool_id) == FORBIDDEN:
        raise ToolNotAllowed(f"工具 {tool_id} 属于禁止级操作，已由工具层拒绝")
    if tool_id not in allowed_tools:
        raise ToolNotAllowed(f"工具 {tool_id} 未在本次运行授权范围内")


def budget_exceeded(rounds_used: int, budget: dict) -> bool:
    max_rounds = int(budget.get("maxRounds", 8) or 8)
    return rounds_used >= max_rounds


RISK_LABELS = {
    READ_ONLY: "只读",
    REVERSIBLE_WRITE: "可回滚写入",
    EXTERNAL_SIDE_EFFECT: "外部副作用",
    FORBIDDEN: "禁止",
}


def policy_summary() -> dict:
    """把服务端真实策略导出给前端展示，避免界面另写一份文案后与代码漂移。"""
    groups: dict[str, list[dict]] = {risk: [] for risk in RISK_ORDER}
    for tool_id, spec in TOOL_CATALOG.items():
        groups[spec["risk"]].append({"toolId": tool_id, "label": spec["label"]})
    return {
        "riskLabels": RISK_LABELS,
        "groups": [
            {
                "risk": risk,
                "label": RISK_LABELS[risk],
                "requiresApproval": risk == EXTERNAL_SIDE_EFFECT,
                "tools": groups[risk],
            }
            for risk in (READ_ONLY, REVERSIBLE_WRITE, EXTERNAL_SIDE_EFFECT, FORBIDDEN)
        ],
    }
