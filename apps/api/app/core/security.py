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


def to_utc(value: datetime | None) -> datetime | None:
    """SQLite 读回来的 DateTime 不带时区，统一按 UTC 解释，避免差 8 小时。

    库内所有时间都由 utcnow() 写入，因此「naive 即 UTC」这个假设是一致的。
    """
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def epoch_seconds(value: datetime | None) -> int:
    aware = to_utc(value)
    return int(aware.timestamp()) if aware else 0


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def _sign(payload: bytes) -> str:
    key = settings.secret_key.encode()
    return _b64(hmac.new(key, payload, hashlib.sha256).digest())


def create_token(
    user_id: str, ttl_seconds: int | None = None, *, pwd_epoch: int = 0
) -> str:
    """签发会话令牌。

    pwd_epoch 是用户密码最后修改时间（epoch 秒）。鉴权时会比对令牌里的
    iat 是否早于它——改密之后所有旧会话立即失效，不需要维护黑名单。
    """
    ttl = ttl_seconds or settings.token_ttl_seconds
    now = int(time.time())
    body = {
        "sub": user_id,
        "iat": now,
        "exp": now + ttl,
        "jti": uuid.uuid4().hex,
        "iss": TOKEN_ISSUER,
        "pwdv": int(pwd_epoch or 0),
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


# ── 密码哈希 ──────────────────────────────────────────────────────────
# 用标准库 hashlib.scrypt：无额外依赖（bcrypt/argon2 都要编译轮子），
# 内存硬（128*r*n 字节）能显著提高 GPU 爆破成本。参数变更后靠
# password_needs_rehash 在下次登录时静默升级。

_PWD_SCHEME = "scrypt"
_PWD_N = 2**14  # 约 16MB 内存、单次 ~50ms
_PWD_R = 8
_PWD_P = 1
_PWD_DKLEN = 32
_PWD_MAXMEM = 64 * 1024 * 1024  # 显式放宽，避免 OpenSSL 默认 32MB 上限误报
MAX_PASSWORD_LENGTH = 200
_DUMMY_SALT = b"\x00" * 16

_WEAK_PASSWORDS = {
    "12345678",
    "123456789",
    "password",
    "password1",
    "passw0rd",
    "qwertyui",
    "11111111",
    "abc12345",
    "admin123",
    "iloveyou",
}


def _scrypt(password: bytes, salt: bytes, n: int, r: int, p: int, dklen: int) -> bytes:
    return hashlib.scrypt(
        password, salt=salt, n=n, r=r, p=p, dklen=dklen, maxmem=_PWD_MAXMEM
    )


def hash_password(password: str) -> str:
    if not password or len(password) > MAX_PASSWORD_LENGTH:
        raise ValueError("密码长度不合法")
    salt = secrets.token_bytes(16)
    digest = _scrypt(password.encode(), salt, _PWD_N, _PWD_R, _PWD_P, _PWD_DKLEN)
    return f"{_PWD_SCHEME}${_PWD_N}${_PWD_R}${_PWD_P}${_b64(salt)}${_b64(digest)}"


def _burn(password: str) -> None:
    """对「账号不存在 / 无密码」也跑一次等价开销，避免用响应时间枚举账号。"""
    try:
        raw = (password or "x").encode()[:MAX_PASSWORD_LENGTH]
        _scrypt(raw, _DUMMY_SALT, _PWD_N, _PWD_R, _PWD_P, _PWD_DKLEN)
    except Exception:  # pragma: no cover - 纯兜底，不能因此改变登录结果
        pass


def verify_password(password: str, stored: str | None) -> bool:
    if not password or len(password) > MAX_PASSWORD_LENGTH:
        _burn(password or "")
        return False
    if not stored or not stored.startswith(f"{_PWD_SCHEME}$"):
        _burn(password)
        return False
    try:
        _scheme, n, r, p, salt_b64, digest_b64 = stored.split("$")
        salt, expected = _unb64(salt_b64), _unb64(digest_b64)
        actual = _scrypt(password.encode(), salt, int(n), int(r), int(p), len(expected))
    except (ValueError, TypeError):
        _burn(password)
        return False
    return hmac.compare_digest(actual, expected)


def password_needs_rehash(stored: str | None) -> bool:
    """参数升级后为真：登录成功时顺带用新参数重算，用户无感。"""
    if not stored:
        return True
    parts = stored.split("$")
    if len(parts) != 6 or parts[0] != _PWD_SCHEME:
        return True
    try:
        n, r, p = int(parts[1]), int(parts[2]), int(parts[3])
    except ValueError:
        return True
    return (n, r, p) != (_PWD_N, _PWD_R, _PWD_P)


def validate_password(password: str, *, min_length: int = 8) -> str | None:
    """返回错误信息；通过则返回 None。规则只挡明显的弱口令，不做复杂度表演。"""
    if len(password) < min_length:
        return f"密码至少 {min_length} 位"
    if len(password) > MAX_PASSWORD_LENGTH:
        return f"密码不能超过 {MAX_PASSWORD_LENGTH} 位"
    if password != password.strip():
        return "密码首尾不能有空格"
    if password.lower() in _WEAK_PASSWORDS:
        return "密码过于常见，请换一个"
    if len(set(password)) < 4:
        return "密码字符过于单一，请增加变化"
    return None


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
