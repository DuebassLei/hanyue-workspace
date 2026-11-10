"""AI 生图供应商适配器（设计 §10.2 / §8）。

只做真实调用：没有配置供应商就明确抛 `ImageProviderUnavailable`，
绝不生成占位图、绝不把失败伪装成成功。生成结果经 Pillow 校验后才会入库。
"""

from __future__ import annotations

import base64
import io
import os

import httpx
from PIL import Image
from sqlalchemy import select

from .. import models
from ..core import ssrf
from ..core.security import decrypt_secret

SIZE_MAP = {
    "1:1": "1024x1024",
    "3:4": "1024x1536",
    "9:16": "1024x1536",
    "4:3": "1536x1024",
    "16:9": "1536x1024",
}
MAX_PIXELS = 16_000_000
MAX_BYTES = 20 * 1024 * 1024


class ImageProviderUnavailable(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def resolve_image_provider(db, owner_user_id: str, model_id: str | None = None) -> dict | None:
    """返回 {baseUrl, apiKey, modelCode}；无可用供应商返回 None。"""
    env_base = os.getenv("IMAGE_PROVIDER_BASE_URL", "").strip()
    env_key = os.getenv("IMAGE_PROVIDER_API_KEY", "").strip()
    env_model = os.getenv("IMAGE_PROVIDER_MODEL", "").strip()
    if env_base and env_key and env_model:
        return {"baseUrl": env_base.rstrip("/"), "apiKey": env_key, "modelCode": env_model}

    stmt = select(models.ModelCatalogEntry).where(
        models.ModelCatalogEntry.enabled.is_(True)
    )
    if model_id:
        stmt = stmt.where(models.ModelCatalogEntry.id == model_id)
    for entry in db.scalars(stmt).all():
        if "image.generate" not in (entry.capabilities or []):
            continue
        connection = db.get(models.ModelConnection, entry.connection_id)
        if not connection or connection.owner_user_id != owner_user_id:
            continue
        key = decrypt_secret(connection.credential_ciphertext)
        if connection.base_url and key:
            return {
                "baseUrl": connection.base_url.rstrip("/"),
                "apiKey": key,
                "modelCode": entry.model_code,
            }
    return None


def status(db, owner_user_id: str) -> dict:
    provider = resolve_image_provider(db, owner_user_id)
    return {
        "available": provider is not None,
        "modelCode": provider["modelCode"] if provider else None,
        "note": "已配置图像生成供应商" if provider else "未接入图像生成供应商，配图需指定已上传素材",
    }


def _decode_image(item: dict) -> tuple[bytes, str]:
    encoded = item.get("b64_json")
    if encoded:
        raw = base64.b64decode(encoded, validate=True)
        mime = "image/png"
    else:
        url = item.get("url")
        if not url:
            raise ImageProviderUnavailable("invalid_provider_response", "供应商未返回图片数据")
        try:
            response = ssrf.safe_request("GET", url, timeout=20.0)
        except (ssrf.UnsafeUrl, httpx.HTTPError) as exc:
            raise ImageProviderUnavailable("image_download_failed", f"下载生成图失败：{exc}") from exc
        if response.status_code >= 400:
            raise ImageProviderUnavailable("image_download_failed", f"图片下载返回 HTTP {response.status_code}")
        raw = response.content
        mime = response.headers.get("content-type", "image/png").split(";")[0]
    if not raw or len(raw) > MAX_BYTES:
        raise ImageProviderUnavailable("invalid_provider_response", "图片大小不合法")
    try:
        with Image.open(io.BytesIO(raw)) as image:
            image.verify()
        with Image.open(io.BytesIO(raw)) as image:
            if image.width * image.height > MAX_PIXELS:
                raise ImageProviderUnavailable("invalid_provider_response", "图片像素超出上限")
    except (OSError, ValueError) as exc:
        raise ImageProviderUnavailable("invalid_provider_response", "供应商返回的不是有效图片") from exc
    return raw, mime if mime.startswith("image/") else "image/png"


def generate_images(
    db,
    owner_user_id: str,
    *,
    prompt: str,
    aspect_ratio: str = "3:4",
    count: int = 1,
    model_id: str | None = None,
) -> list[tuple[bytes, str]]:
    provider = resolve_image_provider(db, owner_user_id, model_id)
    if not provider:
        raise ImageProviderUnavailable(
            "image_provider_unavailable",
            "未接入图像生成供应商；请在设置中配置支持 image.generate 能力的模型，或上传素材后指定 assetId",
        )
    payload = {
        "model": provider["modelCode"],
        "prompt": prompt,
        "n": max(1, min(count, 4)),
        "size": SIZE_MAP.get(aspect_ratio, "1024x1536"),
        "response_format": "b64_json",
    }
    try:
        response = ssrf.safe_request(
            "POST",
            f"{provider['baseUrl']}/images/generations",
            headers={"Authorization": f"Bearer {provider['apiKey']}", "Accept": "application/json"},
            json_body=payload,
            timeout=60.0,
        )
    except (ssrf.UnsafeUrl, httpx.HTTPError) as exc:
        raise ImageProviderUnavailable("provider_unreachable", f"图像供应商请求失败：{exc}") from exc
    if response.status_code in (401, 403):
        raise ImageProviderUnavailable("unauthorized", "图像供应商拒绝了凭据")
    if response.status_code >= 400:
        raise ImageProviderUnavailable("provider_error", f"图像供应商返回 HTTP {response.status_code}")
    try:
        items = response.json().get("data") or []
    except ValueError as exc:
        raise ImageProviderUnavailable("invalid_provider_response", "图像供应商未返回 JSON") from exc
    if not items:
        raise ImageProviderUnavailable("empty_provider_response", "图像供应商未返回图片")
    return [_decode_image(item) for item in items]
