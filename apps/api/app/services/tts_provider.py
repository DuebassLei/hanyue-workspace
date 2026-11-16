"""可插拔 TTS 供应商（视频创作 · 图文成片）。

默认实现是 edge-tts（免费、无需密钥，中文音色质量够用）；接口抽象出来，
将来可无缝接入付费云 TTS。与 image_provider 同构的双路启用：

1. 环境变量 ``TTS_PROVIDER_*`` 三件套齐全时优先（预留 HTTP 兼容供应商通道）；
2. 否则回退 edge-tts 内置实现——因此 TTS 天然「可用」，不需要配置。

做不了的事明确抛 ``TTSUnavailable``，绝不返回静音占位音频。
edge-tts 走微软在线服务，不可达 / 403 都如实报错，不伪造成功。
"""

from __future__ import annotations

import asyncio
import os
import re

VOICE_PATTERN = re.compile(r"^[a-zA-Z]{2,3}(?:-[A-Za-z0-9]{2,8}){1,2}(-[A-Za-z]+Neural)?$")
RATE_PATTERN = re.compile(r"^[+-]\d{1,3}%$")
DEFAULT_VOICE = "zh-CN-XiaoxiaoNeural"
# 单镜头旁白长度上限：edge-tts 内部会分块，但过长文本失败率高且无法试听
MAX_NARRATION_CHARS = 600
# 单次合成硬超时：edge-tts aiohttp 流半开挂起的兜底（正常一段 <10s）
EDGE_TIMEOUT_SECONDS = 60


class TTSUnavailable(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def normalize_voice(voice: str | None) -> str:
    value = (voice or "").strip() or DEFAULT_VOICE
    if not VOICE_PATTERN.match(value):
        raise TTSUnavailable("invalid_voice", f"音色名不合法：{value}")
    return value


def normalize_rate(rate: str | None) -> str:
    value = (rate or "").strip() or "+0%"
    if not RATE_PATTERN.match(value):
        raise TTSUnavailable("invalid_rate", f"语速偏移不合法（应为 +N% / -N%）：{value}")
    return value


def _env_provider() -> dict | None:
    base = os.getenv("TTS_PROVIDER_BASE_URL", "").strip()
    key = os.getenv("TTS_PROVIDER_API_KEY", "").strip()
    model = os.getenv("TTS_PROVIDER_MODEL", "").strip()
    if base and key and model:
        return {"baseUrl": base.rstrip("/"), "apiKey": key, "modelCode": model}
    return None


def status() -> dict:
    env = _env_provider()
    return {
        "available": True,
        "backend": "http" if env else "edge-tts",
        "defaultVoice": DEFAULT_VOICE,
        "note": "已配置 HTTP TTS 供应商" if env else "使用内置 edge-tts（微软在线语音，免费）",
    }


async def _synthesize_edge(text: str, voice: str, rate: str) -> tuple[bytes, int]:
    """流式合成：音频字节 + 末词边界推出的总时长（毫秒）。

    整体包 wait_for：edge-tts 的 aiohttp 流在连接半开时可永久挂起，worker 线程
    会跟着陪葬；超时归一化为 tts_timeout（可重试），绝不无限等。
    """
    return await asyncio.wait_for(_stream_edge(text, voice, rate), timeout=EDGE_TIMEOUT_SECONDS)


async def _stream_edge(text: str, voice: str, rate: str) -> tuple[bytes, int]:
    import edge_tts

    communicate = edge_tts.Communicate(text, voice, rate=rate)
    chunks: list[bytes] = []
    last_end_ms = 0
    async for chunk in communicate.stream():
        kind = chunk.get("type")
        if kind == "audio":
            chunks.append(chunk["data"])
        elif kind in ("WordBoundary", "SentenceBoundary"):
            offset = int(chunk.get("offset") or 0)
            duration = int(chunk.get("duration") or 0)
            # offset/duration 单位是 100ns（datetime.timedelta 的 ticks）
            last_end_ms = max(last_end_ms, round((offset + duration) / 10_000))
    audio = b"".join(chunks)
    if not audio:
        raise TTSUnavailable("empty_provider_response", "edge-tts 未返回任何音频")
    return audio, max(last_end_ms, 400)


def synthesize(text: str, *, voice: str | None = None, rate: str | None = None) -> tuple[bytes, str, int]:
    """文本 → (mp3 字节, mime, 时长毫秒)。阻塞调用，供 worker 线程使用。

    时长来自逐词时间戳的末点；拿不到边界时至少给一个非零下限，
    渲染端还会用 ffprobe 实测兜底，两个值取大。
    """
    content = (text or "").strip()
    if not content:
        raise TTSUnavailable("empty_input", "旁白文案为空，无法配音")
    if len(content) > MAX_NARRATION_CHARS:
        raise TTSUnavailable(
            "narration_too_long",
            f"单镜头旁白 {len(content)} 字超出上限 {MAX_NARRATION_CHARS}，请拆分镜头",
        )
    voice_name = normalize_voice(voice)
    rate_value = normalize_rate(rate)

    env = _env_provider()
    if env:
        raise TTSUnavailable(
            "tts_backend_not_implemented",
            "HTTP TTS 供应商通道尚未实现；请取消 TTS_PROVIDER_* 配置以使用内置 edge-tts",
        )

    try:
        import edge_tts  # noqa: F401
    except ImportError as exc:
        raise TTSUnavailable(
            "tts_dependency_missing",
            "缺少 edge-tts 依赖：pip install edge-tts 后重启后端",
        ) from exc

    try:
        audio, duration_ms = asyncio.run(_synthesize_edge(content, voice_name, rate_value))
    except TTSUnavailable:
        raise
    except (asyncio.TimeoutError, TimeoutError) as exc:
        raise TTSUnavailable(
            "tts_timeout",
            f"edge-tts 合成超时（>{EDGE_TIMEOUT_SECONDS}s）：网络可能被限速，请稍后重试",
        ) from exc
    except Exception as exc:  # edge-tts 抛的是通用异常（网络/403），归一化错误码
        name = type(exc).__name__
        text_repr = str(exc)[:200]
        if "403" in text_repr:
            raise TTSUnavailable(
                "tts_forbidden",
                f"edge-tts 服务拒绝访问（403）：常见原因是出口 IP 被限流，稍后重试或更换网络。原文：{text_repr}",
            ) from exc
        raise TTSUnavailable(
            "tts_unreachable",
            f"edge-tts 合成失败（{name}）：{text_repr}",
        ) from exc
    return audio, "audio/mpeg", duration_ms
