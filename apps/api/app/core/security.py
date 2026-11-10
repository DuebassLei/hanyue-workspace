"""会话令牌与凭据加密。

开发环境用 HMAC 签名的无状态令牌 + 基于 secret_key 派生的对称加密。
令牌带 jti，可由 `POST /auth/logout` 吊销；生产应把 secret_key 托管到 KMS，
并把这里的自研对称加密替换为标准 AEAD（保持 encrypt/decrypt 接口不变）。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
import uuid
from datetime import datetime, timezone

from .config import get_settings

settings = get_settings()

TOKEN_ISSUER = "hanyue-workspace"


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def _sign(payload: bytes) -> str:
    key = settings.secret_key.encode()
    return _b64(hmac.new(key, payload, hashlib.sha256).digest())


def create_token(user_id: str, ttl_seconds: int | None = None) -> str:
    ttl = ttl_seconds or settings.token_ttl_seconds
    now = int(time.time())
    body = {
        "sub": user_id,
        "iat": now,
        "exp": now + ttl,
        "jti": uuid.uuid4().hex,
        "iss": TOKEN_ISSUER,
    }
    payload = _b64(json.dumps(body, separators=(",", ":")).encode())
    return f"{payload}.{_sign(payload.encode())}"


def decode_token(token: str) -> dict | None:
    """校验签名 / 过期 / 签发方，返回完整载荷；任何异常都视为无效令牌。"""
    if not token:
        return None
    try:
        payload, signature = token.split(".", 1)
    except ValueError:
        return None
    if not hmac.compare_digest(signature, _sign(payload.encode())):
        return None
    try:
        body = json.loads(_unb64(payload))
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(body, dict):
        return None
    if body.get("iss") != TOKEN_ISSUER:
        return None
    try:
        if int(body.get("exp", 0)) < int(time.time()):
            return None
    except (TypeError, ValueError):
        return None
    if not isinstance(body.get("sub"), str):
        return None
    return body


def verify_token(token: str) -> str | None:
    body = decode_token(token)
    return body["sub"] if body else None


def create_upload_session(user_id: str, *, max_bytes: int, ttl_seconds: int = 900) -> dict:
    """一次性上传会话：HMAC 签名，含用户、过期时间与大小上限。"""
    expires = int(time.time()) + ttl_seconds
    body = {"sub": user_id, "exp": expires, "maxBytes": max_bytes, "kind": "upload"}
    payload = _b64(json.dumps(body, separators=(",", ":")).encode())
    token = f"{payload}.{_sign(payload.encode())}"
    return {
        "token": token,
        "expiresAt": datetime.fromtimestamp(expires, tz=timezone.utc).isoformat(),
    }


def verify_upload_session(token: str) -> dict | None:
    try:
        payload, signature = token.split(".", 1)
    except ValueError:
        return None
    if not hmac.compare_digest(signature, _sign(payload.encode())):
        return None
    try:
        body = json.loads(_unb64(payload))
    except (ValueError, json.JSONDecodeError):
        return None
    if body.get("kind") != "upload" or int(body.get("exp", 0)) < int(time.time()):
        return None
    return body


def sign_asset_url(asset_id: str, ttl_seconds: int = 7 * 24 * 3600) -> str:
    """素材直链签名：让 <img src> 这类不带 Authorization 头的请求也能取图。

    `<img>` 无法携带 Bearer 头，正文里的图片链接若走鉴权接口会全部 401，
    预览一片空白、复制出去的链接在别处也打不开。签名串带上过期时间，
    泄露后的影响窗口有限；ttl 默认 7 天，覆盖编辑到发布的周期。
    """
    expires = int(time.time()) + ttl_seconds
    payload = _b64(json.dumps({"aid": asset_id, "exp": expires, "kind": "asset"}, separators=(",", ":")).encode())
    sig = _sign(payload.encode())
    return f"exp={expires}&sig={sig}"


def verify_asset_signature(asset_id: str, exp: int, sig: str) -> bool:
    if exp < int(time.time()):
        return False
    payload = _b64(json.dumps({"aid": asset_id, "exp": exp, "kind": "asset"}, separators=(",", ":")).encode())
    return hmac.compare_digest(sig, _sign(payload.encode()))


def _keystream(nonce: bytes, length: int) -> bytes:
    key = settings.secret_key.encode()
    out = bytearray()
    counter = 0
    while len(out) < length:
        out.extend(hmac.new(key, nonce + counter.to_bytes(4, "big"), hashlib.sha256).digest())
        counter += 1
    return bytes(out[:length])


def encrypt_secret(plaintext: str) -> str:
    """保存可逆密文，仅服务端可用；API 永不返回明文。

    v1 格式保持兼容（旧密文仍可解），但 nonce 改为密码学随机数，
    相同明文每次得到不同密文，避免确定性加密泄露凭据是否相同。
    """
    if not plaintext:
        return ""
    data = plaintext.encode()
    nonce = secrets.token_bytes(16)
    cipher = bytes(a ^ b for a, b in zip(data, _keystream(nonce, len(data))))
    mac = hmac.new(settings.secret_key.encode(), nonce + cipher, hashlib.sha256).digest()[:16]
    return f"v1.{_b64(nonce)}.{_b64(cipher)}.{_b64(mac)}"


def decrypt_secret(ciphertext: str) -> str:
    if not ciphertext or not ciphertext.startswith("v1."):
        return ""
    try:
        _, nonce_b64, cipher_b64, mac_b64 = ciphertext.split(".")
        nonce, cipher, mac = _unb64(nonce_b64), _unb64(cipher_b64), _unb64(mac_b64)
    except ValueError:
        return ""
    expected = hmac.new(settings.secret_key.encode(), nonce + cipher, hashlib.sha256).digest()[:16]
    if not hmac.compare_digest(mac, expected):
        return ""
    try:
        return bytes(a ^ b for a, b in zip(cipher, _keystream(nonce, len(cipher)))).decode()
    except UnicodeDecodeError:
        return ""


def mask_secret(ciphertext: str, tail: int = 4) -> str:
    plaintext = decrypt_secret(ciphertext)
    if not plaintext:
        return ""
    return "•" * 8 + plaintext[-tail:]
