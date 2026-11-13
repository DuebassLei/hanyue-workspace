"""Skill manifest 结构与取值校验。

manifest 是用户/内置资源直接写进模型 prompt 的配置，写错类型不会报错，
只会在生成时静默回退默认值（历史上主题拼错就是这样）。这里把校验提前到写入时。

保留 extra="allow"：manifest 里还有 input / output / riskLevel 等字段，
以及未来扩展，校验只收窄已知字段，不阻断未知字段。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .wechat_layout import PRESET_THEMES

MAX_PROMPT_TEMPLATE = 4000
MAX_TONE = 400
MAX_RULES = 40
MAX_RULE_LENGTH = 500

MAX_WORKFLOW_STEPS = 12
MAX_REVIEW_RULES = 20
MAX_REVIEW_RULE_ID = 64
MAX_REVIEW_RULE_MESSAGE = 200
MAX_REVIEW_RULE_VALUE = 200
MAX_FACT_INSTRUCTIONS = 10
MAX_FACT_INSTRUCTION_LENGTH = 300
MAX_IMAGE_PROMPT_SUFFIX = 200
IMAGE_ASPECT_RATIOS = ("1:1", "3:4", "4:3", "16:9", "9:16")
REVIEW_KINDS = ("contains", "not_contains", "min_chars", "max_chars")
REVIEW_FIELDS = ("markdown", "title", "summary")


class ManifestInvalid(ValueError):
    """manifest 校验失败；调用方转成 422，不把 pydantic 细节直接抛给前端。"""


class WorkflowStep(BaseModel):
    """Agent 规划读取的单步：工具 + 静态参数；不在这里判断工具是否已登记。"""

    model_config = ConfigDict(extra="forbid")

    tool: str = Field(min_length=1, max_length=64)
    args: dict[str, str | int | float | bool] = Field(default_factory=dict)


class ReviewRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=MAX_REVIEW_RULE_ID)
    kind: Literal["contains", "not_contains", "min_chars", "max_chars"]
    value: str | int
    field: Literal["markdown", "title", "summary"] = "markdown"
    message: str | None = Field(default=None, max_length=MAX_REVIEW_RULE_MESSAGE)

    @field_validator("value")
    @classmethod
    def _check_value(cls, value: str | int, info) -> str | int:
        kind = info.data.get("kind")
        if kind in ("contains", "not_contains"):
            if isinstance(value, bool) or not isinstance(value, str):
                raise ValueError(f"{kind} 的 value 必须是字符串")
            if not value.strip():
                raise ValueError(f"{kind} 的 value 不能为空")
            if len(value) > MAX_REVIEW_RULE_VALUE:
                raise ValueError(f"{kind} 的 value 不能超过 {MAX_REVIEW_RULE_VALUE} 字")
            return value
        if kind in ("min_chars", "max_chars"):
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{kind} 的 value 必须是整数")
            if value < 0:
                raise ValueError(f"{kind} 的 value 不能为负数")
            return value
        return value


class FactPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requireSources: bool = False
    forbidUnverifiedQuotes: bool = True
    placeholder: str = Field(default="〔待核实〕", min_length=1, max_length=20)
    extraInstructions: list[str] = Field(default_factory=list, max_length=MAX_FACT_INSTRUCTIONS)

    @field_validator("extraInstructions")
    @classmethod
    def _check_instructions(cls, value: list[str]) -> list[str]:
        for item in value:
            if len(item) > MAX_FACT_INSTRUCTION_LENGTH:
                raise ValueError(f"单条事实规则不能超过 {MAX_FACT_INSTRUCTION_LENGTH} 字")
        return value


class ImagePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    coverAspectRatio: Literal["1:1", "3:4", "4:3", "16:9", "9:16"] = "3:4"
    inlineAspectRatio: Literal["1:1", "3:4", "4:3", "16:9", "9:16"] = "1:1"
    maxInlineImages: int = Field(default=3, ge=0, le=6)
    allowAiImages: bool = True
    promptStyleSuffix: str | None = Field(default=None, max_length=MAX_IMAGE_PROMPT_SUFFIX)


class RepairPolicy(BaseModel):
    """本批只做校验与存储，不接入模型重试。"""

    model_config = ConfigDict(extra="forbid")

    retries: int = Field(default=0, ge=0, le=1)
    requireContract: bool = False


class SkillManifest(BaseModel):
    model_config = ConfigDict(extra="allow")

    promptTemplate: str | None = Field(default=None, max_length=MAX_PROMPT_TEMPLATE)
    tone: str | None = Field(default=None, max_length=MAX_TONE)
    styleRules: list[str] | None = None
    defaultTheme: str | None = None
    input: dict | None = None
    output: dict | None = None
    riskLevel: str | None = None
    # 以下五个策略字段可选：不写即 None，落库时被 exclude_none 去掉，checksum 与旧数据一致。
    workflow: list[WorkflowStep] | None = Field(default=None, max_length=MAX_WORKFLOW_STEPS)
    reviewRules: list[ReviewRule] | None = Field(default=None, max_length=MAX_REVIEW_RULES)
    factPolicy: FactPolicy | None = None
    imagePolicy: ImagePolicy | None = None
    repairPolicy: RepairPolicy | None = None

    @field_validator("reviewRules")
    @classmethod
    def _check_review_ids(cls, value: list[ReviewRule] | None) -> list[ReviewRule] | None:
        if value is None:
            return None
        seen: set[str] = set()
        for rule in value:
            if rule.id in seen:
                raise ValueError(f"reviewRules 的 id 重复：{rule.id}")
            seen.add(rule.id)
        return value

    @field_validator("styleRules")
    @classmethod
    def _check_rules(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        cleaned: list[str] = []
        for item in value:
            if not isinstance(item, str):
                raise ValueError("styleRules 必须是字符串数组")
            text = item.strip()
            if not text:
                continue
            if len(text) > MAX_RULE_LENGTH:
                raise ValueError(f"单条风格规则不能超过 {MAX_RULE_LENGTH} 字")
            cleaned.append(text)
        if len(cleaned) > MAX_RULES:
            raise ValueError(f"风格规则最多 {MAX_RULES} 条")
        return cleaned

    @field_validator("defaultTheme")
    @classmethod
    def _check_theme(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if value not in PRESET_THEMES:
            allowed = "、".join(PRESET_THEMES)
            raise ValueError(f"未知排版主题「{value}」，可选：{allowed}")
        return value


def validate_manifest(raw: dict | None) -> dict:
    """校验并规范化 manifest；返回可直接落库的 dict（去掉 None）。"""
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ManifestInvalid("manifest 必须是对象")
    try:
        model = SkillManifest.model_validate(raw)
    except ValidationError as exc:
        details = "；".join(
            f"{'.'.join(str(part) for part in err['loc']) or 'manifest'}：{err['msg']}"
            for err in exc.errors()
        )
        raise ManifestInvalid(details) from exc
    return model.model_dump(exclude_none=True)
