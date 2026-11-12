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


def _env_provider() -> dict | None:
    env_base = os.getenv("IMAGE_PROVIDER_BASE_URL", "").strip()
    env_key = os.getenv("IMAGE_PROVIDER_API_KEY", "").strip()
    env_model = os.getenv("IMAGE_PROVIDER_MODEL", "").strip()
    if env_base and env_key and env_model:
        return {"modelId": None, "baseUrl": env_base.rstrip("/"), "apiKey": env_key, "modelCode": env_model}
    return None


def list_providers(db, owner_user_id: str) -> list[dict]:
    """列出当前用户可用于生图的供应商（环境变量 + 本人连接里带 image.generate 的模型）。

    返回项：{modelId, modelCode, label, source}；modelId 为 None 表示环境变量供应商。
    """
    providers: list[dict] = []
    env = _env_provider()
    if env:
        providers.append(
            {
                "modelId": None,
                "modelCode": env["modelCode"],
                "label": f"环境变量 · {env['modelCode']}",
                "source": "env",
            }
        )
    stmt = select(models.ModelCatalogEntry).where(models.ModelCatalogEntry.enabled.is_(True))
    for entry in db.scalars(stmt).all():
        if "image.generate" not in (entry.capabilities or []):
            continue
        connection = db.get(models.ModelConnection, entry.connection_id)
        if not connection or connection.owner_user_id != owner_user_id:
            continue
        if not connection.base_url or not decrypt_secret(connection.credential_ciphertext):
            continue
        providers.append(
            {
                "modelId": entry.id,
                "modelCode": entry.model_code,
                "label": f"{connection.display_name} · {entry.display_name or entry.model_code}",
                "source": "connection",
            }
        )
    return providers


def resolve_image_provider(db, owner_user_id: str, model_id: str | None = None) -> dict | None:
    """返回 {baseUrl, apiKey, modelCode}；无可用供应商返回 None。

    指定 model_id 时优先使用对应连接（支持多供应商动态切换），否则回退环境变量。
    """
    if model_id:
        entry = db.get(models.ModelCatalogEntry, model_id)
        if entry and entry.enabled and "image.generate" in (entry.capabilities or []):
            connection = db.get(models.ModelConnection, entry.connection_id)
            if connection and connection.owner_user_id == owner_user_id and connection.base_url:
                key = decrypt_secret(connection.credential_ciphertext)
                if key:
                    return {
                        "baseUrl": connection.base_url.rstrip("/"),
                        "apiKey": key,
                        "modelCode": entry.model_code,
                    }
        # 明确指定了供应商却不可用时不静默回退，避免「切了没生效」的错觉
        return None
    env = _env_provider()
    if env:
        return {"baseUrl": env["baseUrl"], "apiKey": env["apiKey"], "modelCode": env["modelCode"]}

    stmt = select(models.ModelCatalogEntry).where(
        models.ModelCatalogEntry.enabled.is_(True)
    )
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
    providers = list_providers(db, owner_user_id)
    provider = resolve_image_provider(db, owner_user_id)
    return {
        "available": provider is not None,
        "modelCode": provider["modelCode"] if provider else None,
        "note": "已配置图像生成供应商" if provider else "未接入图像生成供应商，配图需指定已上传素材",
        "providers": providers,
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
    reference_images: list[tuple[bytes, str, str]] | None = None,
) -> list[tuple[bytes, str]]:
    """文生图走 /images/generations；带参考图时改走 /images/edits（multipart）。

    reference_images 为 [(raw, mime, filename)]，调用方负责校验素材归属。
    """
    provider = resolve_image_provider(db, owner_user_id, model_id)
    if not provider:
        if model_id:
            raise ImageProviderUnavailable(
                "image_provider_unavailable",
                "指定的图像供应商不可用或已被移除，请重新选择",
            )
        raise ImageProviderUnavailable(
            "image_provider_unavailable",
            "未接入图像生成供应商；请在设置中配置支持 image.generate 能力的模型，或上传素材后指定 assetId",
        )
    size = SIZE_MAP.get(aspect_ratio, "1024x1536")
    batch = max(1, min(count, 4))
    headers = {"Authorization": f"Bearer {provider['apiKey']}", "Accept": "application/json"}
    if reference_images:
        endpoint = f"{provider['baseUrl']}/images/edits"
        files = [
            ("image[]", (filename, raw, mime))
            for raw, mime, filename in reference_images
        ]
        form_body = {
            "model": provider["modelCode"],
            "prompt": prompt,
            "n": str(batch),
            "size": size,
            "response_format": "b64_json",
        }
        json_body = None
    else:
        endpoint = f"{provider['baseUrl']}/images/generations"
        files = None
        form_body = None
        json_body = {
            "model": provider["modelCode"],
            "prompt": prompt,
            "n": batch,
            "size": size,
            "response_format": "b64_json",
        }
    try:
        response = ssrf.safe_request(
            "POST",
            endpoint,
            headers=headers,
            json_body=json_body,
            form_body=form_body,
            files=files,
            timeout=90.0,
        )
    except (ssrf.UnsafeUrl, httpx.HTTPError) as exc:
        raise ImageProviderUnavailable("provider_unreachable", f"图像供应商请求失败：{exc}") from exc
    if response.status_code in (401, 403):
        raise ImageProviderUnavailable("unauthorized", "图像供应商拒绝了凭据")
    if response.status_code >= 400:
        hint = "（供应商可能不支持参考图编辑 /images/edits）" if reference_images else ""
        raise ImageProviderUnavailable(
            "provider_error", f"图像供应商返回 HTTP {response.status_code}{hint}"
        )
    try:
        items = response.json().get("data") or []
    except ValueError as exc:
        raise ImageProviderUnavailable("invalid_provider_response", "图像供应商未返回 JSON") from exc
    if not items:
        raise ImageProviderUnavailable("empty_provider_response", "图像供应商未返回图片")
    return [_decode_image(item) for item in items]
