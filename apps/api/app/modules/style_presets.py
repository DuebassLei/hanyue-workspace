"""统一风格预设：只读接口，任何登录用户可读（与风格库管理权限无关）。"""

from __future__ import annotations

from fastapi import APIRouter, Query
from pydantic import Field

from .. import schemas
from ..core.deps import CurrentUser
from ..services import style_presets as _style_presets

router = APIRouter(prefix="/style-presets", tags=["style-presets"])


class StylePresetOut(schemas.CamelModel):
    id: str
    name: str
    prompt_suffix: str
    negative_prompt: str = ""
    reference_asset_id: str | None = None
    applicable_to: list[str] = Field(default_factory=list)


@router.get("", response_model=list[StylePresetOut])
def list_style_presets(
    user: CurrentUser,
    applicable_to: str | None = Query(default=None, alias="applicableTo"),
) -> list[dict]:
    """列出内置风格预设。user 仅用于鉴权（任何登录用户可读，无需 manage 权限）。"""
    return _style_presets.list_presets(applicable_to)
