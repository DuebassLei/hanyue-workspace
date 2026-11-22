"""AI 生图供应商适配器（设计 §10.2 / §8）。

只做真实调用：没有配置供应商就明确抛 `ImageProviderUnavailable`，
绝不生成占位图、绝不把失败伪装成成功。生成结果经 Pillow 校验后才会入库。
"""

from __future__ import annotations

import base64
import io
import json
import logging
import os

import httpx
from PIL import Image
from sqlalchemy import select

from .. import models
from ..core import ssrf
from ..core.security import decrypt_secret

logger = logging.getLogger(__name__)

# 默认生图模型失效的原因文案：设置页与顶栏状态条共用一份，避免两处口径打架。
DEFAULT_IMAGE_IGNORE_MESSAGES = {
    "not_found": "模型不存在或已被删除",
    "connection_missing": "模型所属连接已被删除",
    "foreign": "模型不属于当前用户",
    "disabled": "模型已停用",
    "not_image": "该模型未登记 image.generate 能力",
    "no_base_url": "连接缺少 Base URL",
    "no_credential": "连接缺少 API Key",
}

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

# 供应商能力键（写在 ModelCatalogEntry.options 上）。保守探测：缺省一律视为不支持。
# OpenAI 兼容 /images/generations 网关真正支持独立 negative_prompt/seed 的很少，
# 默认走「负向词拼进 prompt 尾部」的兜底更安全（D2：明示，不静默声称生效）。
CAPABILITY_NEGATIVE_PROMPT = "supportsNegativePrompt"
CAPABILITY_SEED = "supportsSeed"


def _option_is_true(entry, key: str) -> bool:
    """只有 options 里显式为 True 才算支持；缺失/None/False 全部按不支持处理。"""
    return (entry.options or {}).get(key) is True


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


def _default_image_model_id(db, owner_user_id: str) -> str | None:
    """读用户级全局默认生图模型偏好；无记录或未设置返回 None。"""
    value = db.scalar(
        select(models.UserPreference.default_image_model_id).where(
            models.UserPreference.owner_user_id == owner_user_id
        )
    )
    return str(value or "").strip() or None


def _validate_image_entry(db, owner_user_id: str, model_id: str):
    """校验一个 catalog 条目能不能用于生图。

    返回 (connection, entry, reason)；reason 为 None 表示有效。
    与文本侧的 _validate_entry 同风格：只看凭据是否登记，不解密，避免把解密异常
    带进解析路径；真正出图时再解密，失败会明确报错而不是悄悄换供应商。
    """
    entry = db.get(models.ModelCatalogEntry, model_id)
    if entry is None:
        return None, None, "not_found"
    connection = db.get(models.ModelConnection, entry.connection_id)
    if connection is None:
        return None, entry, "connection_missing"
    if connection.owner_user_id != owner_user_id:
        return None, entry, "foreign"
    if not entry.enabled:
        return connection, entry, "disabled"
    if "image.generate" not in (entry.capabilities or []):
        return connection, entry, "not_image"
    if not (connection.base_url or "").strip():
        return connection, entry, "no_base_url"
    if not str(connection.credential_ciphertext or "").strip():
        return connection, entry, "no_credential"
    return connection, entry, None


def default_image_model_reason(db, owner_user_id: str, model_id: str) -> str | None:
    """设置默认生图模型前的校验：返回失效原因，None 表示可以设为默认。"""
    return _validate_image_entry(db, owner_user_id, model_id)[2]


def describe_default_image_model(db, owner_user_id: str) -> dict:
    """给设置页 / 状态条用的默认生图模型展示信息。

    失效时只返回 ignored 原因，**不清除偏好**：模型重新启用、密钥补上后自动恢复。
    """
    model_id = _default_image_model_id(db, owner_user_id)
    if not model_id:
        return {"id": None, "label": None, "ignored": None}
    connection, entry, reason = _validate_image_entry(db, owner_user_id, model_id)
    label = None
    if entry is not None:
        name = (connection.display_name if connection else None) or "已删除的连接"
        label = f"{name} · {entry.model_code}"
    if not reason:
        return {"id": model_id, "label": label, "ignored": None}
    detail = {
        "reason": reason,
        "message": DEFAULT_IMAGE_IGNORE_MESSAGES.get(reason, "默认生图模型当前不可用"),
        "modelId": model_id,
    }
    logger.warning("[image] 默认生图模型 %s 配置失效，本次忽略：%s", model_id, reason)
    return {"id": model_id, "label": label, "ignored": detail}


def _provider_from_entry(connection, entry) -> dict | None:
    """把 catalog 条目翻译成调用所需的供应商参数；密钥解不出来就当不可用。"""
    key = decrypt_secret(connection.credential_ciphertext)
    if not connection.base_url or not key:
        return None
    return {
        "modelId": entry.id,
        "baseUrl": connection.base_url.rstrip("/"),
        "apiKey": key,
        "modelCode": entry.model_code,
        CAPABILITY_NEGATIVE_PROMPT: _option_is_true(entry, CAPABILITY_NEGATIVE_PROMPT),
        CAPABILITY_SEED: _option_is_true(entry, CAPABILITY_SEED),
        "supportedImageSizes": (entry.options or {}).get("supportedImageSizes"),
    }


def exact_image_sizes(provider: dict | None) -> dict[str, str]:
    """仅使用模型明确登记、且宽高与比例一致的尺寸。"""
    sizes = (provider or {}).get("supportedImageSizes")
    if not isinstance(sizes, dict):
        return {}
    result = {}
    for ratio, size in sizes.items():
        try:
            rw, rh = (float(part) for part in ratio.split(":"))
            width, height = (int(part) for part in size.split("x"))
            if rw > 0 and rh > 0 and width > 0 and height > 0 and abs(width / height - rw / rh) < 0.001:
                result[ratio] = size
        except (ValueError, TypeError, AttributeError, ZeroDivisionError):
            continue
    return result


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

    返回项：{modelId, modelCode, label, source, isDefault}；modelId 为 None 表示环境变量供应商。
    全局默认生图模型排在最前：图片生成页 / 创作空间默认取第一项，这样「设了默认」
    与「页面默认选中」才是同一个东西，不会出现切换后页面还是老供应商的错位。
    """
    providers: list[dict] = []
    described = describe_default_image_model(db, owner_user_id)
    default_id = described["id"] if not described["ignored"] else None
    env = _env_provider()
    if env:
        providers.append(
            {
                "modelId": None,
                "modelCode": env["modelCode"],
                "label": f"环境变量 · {env['modelCode']}",
                "source": "env",
                "isDefault": False,
                # 环境变量供应商无从探测，保守按不支持处理，走兜底拼接。
                CAPABILITY_NEGATIVE_PROMPT: False,
                CAPABILITY_SEED: False,
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
                CAPABILITY_NEGATIVE_PROMPT: _option_is_true(entry, CAPABILITY_NEGATIVE_PROMPT),
                CAPABILITY_SEED: _option_is_true(entry, CAPABILITY_SEED),
                "label": f"{connection.display_name} · {entry.display_name or entry.model_code}",
                "source": "connection",
                "isDefault": entry.id == default_id,
            }
        )
    if default_id:
        # 稳定排序：默认项提到第一位，其余保持原有顺序（环境变量退到其后）。
        providers.sort(key=lambda item: 0 if item.get("isDefault") else 1)
    return providers


def resolve_image_provider(db, owner_user_id: str, model_id: str | None = None) -> dict | None:
    """返回 {baseUrl, apiKey, modelCode}；无可用供应商返回 None。

    指定 model_id 时优先使用对应连接（支持多供应商动态切换），否则回退环境变量。

    未指定时的优先级：用户级全局默认生图模型 → 环境变量供应商 → 第一个可用连接。
    显式默认排最前是为了让「设为默认」真的生效：否则运营环境配了 IMAGE_PROVIDER_*，
    用户在界面里怎么切都会被环境变量压住。
    """
    if model_id:
        entry = db.get(models.ModelCatalogEntry, model_id)
        if entry and entry.enabled and "image.generate" in (entry.capabilities or []):
            connection = db.get(models.ModelConnection, entry.connection_id)
            if connection and connection.owner_user_id == owner_user_id and connection.base_url:
                provider = _provider_from_entry(connection, entry)
                if provider:
                    return provider
        # 明确指定了供应商却不可用时不静默回退，避免「切了没生效」的错觉
        return None

    default_id = _default_image_model_id(db, owner_user_id)
    if default_id:
        connection, entry, reason = _validate_image_entry(db, owner_user_id, default_id)
        if not reason:
            provider = _provider_from_entry(connection, entry)
            if provider:
                return provider
            reason = "no_credential"
        logger.warning("[image] 默认生图模型 %s 配置失效，本次按原顺序选择：%s", default_id, reason)

    env = _env_provider()
    if env:
        return {
            "baseUrl": env["baseUrl"],
            "apiKey": env["apiKey"],
            "modelCode": env["modelCode"],
            "modelId": None,
            CAPABILITY_NEGATIVE_PROMPT: False,
            CAPABILITY_SEED: False,
        }

    stmt = select(models.ModelCatalogEntry).where(
        models.ModelCatalogEntry.enabled.is_(True)
    )
    for entry in db.scalars(stmt).all():
        if "image.generate" not in (entry.capabilities or []):
            continue
        connection = db.get(models.ModelConnection, entry.connection_id)
        if not connection or connection.owner_user_id != owner_user_id:
            continue
        provider = _provider_from_entry(connection, entry)
        if provider:
            return provider
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
    negative_prompt: str | None = None,
    seed: int | None = None,
    merge_meta: dict | None = None,
):
    """一次请求。多数兼容接口只接受 n=1，调用方按张重试。

    负向词/种子按能力探测决定走独立字段还是文本兜底；兜底结果写进 merge_meta，
    由调用方透传到 job.result，绝不静默声称参数已生效。
    """
    headers = {"Authorization": f"Bearer {provider['apiKey']}", "Accept": "application/json"}
    effective_prompt = prompt
    negative_field: str | None = None
    if negative_prompt and negative_prompt.strip():
        if provider.get(CAPABILITY_NEGATIVE_PROMPT):
            negative_field = negative_prompt.strip()
        else:
            # 供应商无独立负向字段：按约定拼进正向尾部，并明确标注为文本兜底。
            effective_prompt = f"{prompt}, avoid: {negative_prompt.strip()}"
            if merge_meta is not None:
                merge_meta["negative_prompt_merged"] = True
    seed_field: int | None = None
    if seed is not None:
        if provider.get(CAPABILITY_SEED):
            seed_field = seed
            if merge_meta is not None:
                merge_meta["seed_applied"] = True
        elif merge_meta is not None:
            merge_meta["seed_ignored"] = True
    extra_json = {}
    extra_form = {}
    if negative_field is not None:
        extra_json["negative_prompt"] = negative_field
        extra_form["negative_prompt"] = negative_field
    if seed_field is not None:
        extra_json["seed"] = seed_field
        extra_form["seed"] = str(seed_field)
    if reference_images:
        endpoint = f"{provider['baseUrl']}/images/edits"
        files = [
            ("image[]", (filename, raw, mime))
            for raw, mime, filename in reference_images
        ]
        form_body = {
            "model": provider["modelCode"],
            "prompt": effective_prompt,
            "n": str(batch),
            "size": size,
            "response_format": "b64_json",
            **extra_form,
        }
        json_body = None
    else:
        endpoint = f"{provider['baseUrl']}/images/generations"
        files = None
        form_body = None
        json_body = {
            "model": provider["modelCode"],
            "prompt": effective_prompt,
            "n": batch,
            "size": size,
            "response_format": "b64_json",
            **extra_json,
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
    negative_prompt: str | None = None,
    seed: int | None = None,
    merge_meta: dict | None = None,
    exact_aspect: bool = False,
) -> list[tuple[bytes, str]]:
    """文生图走 /images/generations；带参考图时改走 /images/edits（multipart）。

    reference_images 为 [(raw, mime, filename)]，调用方负责校验素材归属。

    数量大于 1 时按张请求：gpt-image / 多数兼容网关只接受 n=1，
    一次传 n=4 会直接 400。单张失败不影响已经拿到的图；一张都没有才抛错。

    negative_prompt/seed：供应商按 options 能力键探测；负向词不被支持时拼进
    prompt 尾部（`, avoid: ...`），seed 不被支持时忽略。两者都会写进 merge_meta，
    供调用方如实告知用户（不传新参数时行为与改动前完全一致）。
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
    if exact_aspect:
        size = exact_image_sizes(provider).get(aspect_ratio)
        if not size:
            raise ImageProviderUnavailable("unsupported_aspect_ratio", f"当前模型未确认支持 {aspect_ratio} 比例，请在模型管理配置支持的精确尺寸。")
    else:
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
            negative_prompt=negative_prompt,
            seed=seed,
            merge_meta=merge_meta,
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
        decoded = _decode_image(items[0])
        if exact_aspect:
            with Image.open(io.BytesIO(decoded[0])) as image:
                rw, rh = (float(part) for part in aspect_ratio.split(":"))
                if abs(image.width / image.height - rw / rh) >= 0.001:
                    raise ImageProviderUnavailable("unexpected_aspect_ratio", f"供应商返回的图片比例不符合 {aspect_ratio}，请检查模型尺寸支持。")
        collected.append(decoded)
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
    from . import ai_gateway
    from .ai_gateway import resolve_connection

    connection: models.ModelConnection | None = None
    model_code: str = "unconfigured"

    if model_id:
        # 显式选择：统一走网关校验（越权/停用/无文本能力一律拒绝），并钉死单模型
        try:
            selection = ai_gateway.resolve_selection(db, owner_user_id, model_id)
        except ai_gateway.InvalidModelSelection as exc:
            raise ImageProviderUnavailable(exc.code, exc.message) from exc
        connection, model_code = selection["candidates"][0]
    else:
        # 不传 modelId：resolve_connection 会读用户级默认模型（未设置时行为同原来）
        connection, model_code = resolve_connection(db, owner_user_id)

    if connection is None or model_code == "unconfigured":
        raise ImageProviderUnavailable(
            "text_provider_unavailable",
            "未配置可用的文本/视觉模型；请在「系统 → 模型管理」中添加支持 text.generate 的模型",
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
