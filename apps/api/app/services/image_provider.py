"""AI 生图供应商适配器（设计 §10.2 / §8）。

只做真实调用：没有配置供应商就明确抛 `ImageProviderUnavailable`，
绝不生成占位图、绝不把失败伪装成成功。生成结果经 Pillow 校验后才会入库。
"""

from __future__ import annotations

import base64
import io
import json
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
# 这些状态码配参考图出现，说明供应商根本没实现 /images/edits：
# 404 端点不存在 / 405 方法不支持 / 501 未实现。
# 400/415 不在其中：它们更可能是参考图格式、尺寸或参数不合规，
# 一律当成「不支持」会把可修复的请求误判成永久降级。
EDIT_UNSUPPORTED_STATUS = frozenset({404, 405, 501})


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


def provider_supports_edits(db, owner_user_id: str, model_id: str | None) -> bool | None:
    """该供应商是否支持参考图编辑（/images/edits）。

    True 明确支持 / False 探测后确认不支持 / None 还没探测过。
    环境变量供应商没有可写的 catalog 行，永远返回 None（每次现探）。
    """
    if not model_id:
        return None
    entry = db.get(models.ModelCatalogEntry, model_id)
    if entry is None:
        return None
    connection = db.get(models.ModelConnection, entry.connection_id)
    if not connection or connection.owner_user_id != owner_user_id:
        return None
    value = (entry.options or {}).get("supportsEdits")
    return value if isinstance(value, bool) else None


def mark_edits_unsupported(db, model_id: str | None) -> None:
    """把「不支持 /images/edits」记到模型的 options 上，后续任务直接跳过注定失败的请求。

    环境变量供应商（model_id 为空）无处可记，调用方按每次现探处理。
    """
    if not model_id:
        return
    entry = db.get(models.ModelCatalogEntry, model_id)
    if entry is None:
        return
    entry.options = {**(entry.options or {}), "supportsEdits": False}
    db.commit()


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
                # None 表示尚未探测；前端据此决定参考图提示文案
                "supportsEdits": (entry.options or {}).get("supportsEdits"),
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


def _request_batch(
    provider: dict,
    *,
    prompt: str,
    size: str,
    batch: int,
    reference_images: list[tuple[bytes, str, str]] | None,
):
    """一次请求。多数兼容接口只接受 n=1，调用方按张重试。"""
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
    return response


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

    数量大于 1 时按张请求：gpt-image / 多数兼容网关只接受 n=1，
    一次传 n=4 会直接 400。单张失败不影响已经拿到的图；一张都没有才抛错。
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
    wanted = max(1, min(count, 4))
    collected: list[tuple[bytes, str]] = []
    last_error: ImageProviderUnavailable | None = None
    for _ in range(wanted):
        response = _request_batch(
            provider,
            prompt=prompt,
            size=size,
            batch=1,
            reference_images=reference_images,
        )
        if response.status_code in (401, 403):
            raise ImageProviderUnavailable("unauthorized", "图像供应商拒绝了凭据")
        if response.status_code >= 400:
            # 带参考图却收到「端点不存在/方法不支持」一类状态码：供应商不支持
            # /images/edits。用独立 errorCode 表达，调用方不必去猜 message 文案。
            if reference_images and response.status_code in EDIT_UNSUPPORTED_STATUS:
                raise ImageProviderUnavailable(
                    "edits_unsupported",
                    f"供应商不支持参考图编辑（HTTP {response.status_code}）",
                )
            hint = "（供应商可能不支持参考图编辑 /images/edits）" if reference_images else ""
            last_error = ImageProviderUnavailable(
                "provider_error", f"图像供应商返回 HTTP {response.status_code}{hint}"
            )
            if collected:
                break
            raise last_error
        try:
            items = response.json().get("data") or []
        except ValueError as exc:
            last_error = ImageProviderUnavailable("invalid_provider_response", "图像供应商未返回 JSON")
            if collected:
                break
            raise last_error from exc
        if not items:
            last_error = ImageProviderUnavailable("empty_provider_response", "图像供应商未返回图片")
            if collected:
                break
            raise last_error
        collected.append(_decode_image(items[0]))
    if not collected and last_error:
        raise last_error
    return collected


INTERROGATE_SYSTEM = (
    "You are a professional image description assistant. "
    "Analyze the provided image and generate a concise, detailed English prompt "
    "that could be used to recreate or describe it. "
    "Focus on: subject, style, composition, lighting, colors, mood, and any notable details. "
    "Output ONLY the prompt text — no explanation, no prefix, no extra formatting."
)

# 画风提取：只输出可迁移的「画法」，刻意排除主体/场景/文字内容——
# 给贴图批量生成做一致性锚点时，主体由各张自己的画面描述负责。
STYLE_SIGNATURE_SYSTEM = (
    "You extract the STYLE SIGNATURE of an image so another generator can match its look. "
    "Describe ONLY transferable visual style: rendering technique (photo/film stock/lens cues "
    "for photography; medium and line treatment for illustration), color palette with hue names, "
    "lighting character, contrast and grain/texture, composition tendencies (framing, perspective, "
    "depth of field), overall mood. "
    "NEVER mention the specific subject, people, place, objects, readable text, or brand names. "
    "Write one dense comma-separated phrase in English, 25-60 words, lowercase, no sentences, "
    "no prefixes, no quotes — pure style tokens only."
)

INTERROGATE_TIMEOUT = httpx.Timeout(connect=10.0, read=120.0, write=30.0, pool=10.0)


def interrogate_image(
    db,
    owner_user_id: str,
    *,
    image_bytes: bytes,
    mime_type: str,
    model_id: str | None = None,
    system_prompt: str | None = None,
    user_text: str = "请分析这张图片并生成一段描述性提示词。",
) -> str:
    """用视觉语言模型反推图片提示词；返回提示词字符串。

    优先使用 model_id 指定的文本模型，否则取用户第一个可用文本连接。
    走 OpenAI 兼容 /chat/completions，携带 image_url content block（base64）。
    system_prompt/user_text 可换成画风提取等专用指令（见 STYLE_SIGNATURE_SYSTEM）。
    """
    from .ai_gateway import resolve_connection

    connection: models.ModelConnection | None = None
    model_code: str = "unconfigured"

    if model_id:
        # 通过 catalog id 找指定模型
        entry = db.get(models.ModelCatalogEntry, model_id)
        if entry and entry.enabled:
            conn = db.get(models.ModelConnection, entry.connection_id)
            if conn and conn.owner_user_id == owner_user_id and conn.base_url:
                key = decrypt_secret(conn.credential_ciphertext)
                if key:
                    connection, model_code = conn, entry.model_code
        if connection is None:
            raise ImageProviderUnavailable(
                "text_provider_unavailable",
                "指定的模型不可用，请重新选择或清空 modelId 自动使用默认文本模型",
            )
    else:
        connection, model_code = resolve_connection(db, owner_user_id)

    if connection is None or model_code == "unconfigured":
        raise ImageProviderUnavailable(
            "text_provider_unavailable",
            "未配置可用的文本/视觉模型；请在「设置 → 模型连接」中添加支持 text.generate 的模型",
        )

    api_key = decrypt_secret(connection.credential_ciphertext)
    if not api_key:
        raise ImageProviderUnavailable("text_provider_unavailable", "模型连接缺少 API Key")

    # 编码图片为 base64 data URI
    b64 = base64.b64encode(image_bytes).decode()
    data_url = f"data:{mime_type};base64,{b64}"

    url = connection.base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": model_code,
        "stream": False,
        "messages": [
            {"role": "system", "content": system_prompt or INTERROGATE_SYSTEM},
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": data_url, "detail": "high"},
                    },
                    {
                        "type": "text",
                        "text": user_text,
                    },
                ],
            },
        ],
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    try:
        response = ssrf.safe_request(
            "POST",
            url,
            headers=headers,
            json_body=payload,
            timeout=120.0,
        )
    except (ssrf.UnsafeUrl, httpx.HTTPError) as exc:
        raise ImageProviderUnavailable("provider_unreachable", f"文本供应商请求失败：{exc}") from exc

    if response.status_code in (401, 403):
        raise ImageProviderUnavailable("unauthorized", "文本供应商拒绝了凭据")
    if response.status_code >= 400:
        body = response.text[:300]
        raise ImageProviderUnavailable(
            "provider_error",
            f"文本供应商返回 HTTP {response.status_code}：{body}",
        )

    try:
        data = response.json()
    except (ValueError, json.JSONDecodeError) as exc:
        raise ImageProviderUnavailable("invalid_provider_response", "文本供应商未返回 JSON") from exc

    choices = data.get("choices") or []
    if not choices:
        raise ImageProviderUnavailable("empty_provider_response", "文本供应商未返回内容")

    prompt_text: str = (choices[0].get("message") or {}).get("content") or ""
    prompt_text = prompt_text.strip()
    if not prompt_text:
        raise ImageProviderUnavailable("empty_provider_response", "模型未生成任何提示词")

    return prompt_text
