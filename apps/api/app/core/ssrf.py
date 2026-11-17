"""SSRF 防护（设计 §12.1）。

三层校验：
1. `validate_base_url` —— 纯字面量检查，写入数据库时使用，不依赖网络。
2. `_resolve_public_ips` —— 请求前把域名解析成 IP，逐条拒绝内网/保留地址。
3. `pin_url` / `safe_request` —— **把上一步校验过的 IP 直接固定连接层**，URL 的
   host 换成该 IP、Host 头保留原域名、`sni_hostname` 扩展保证 TLS 仍按原域名校验证书。
   这样「校验的 IP == 实际连接的 IP」，消除 DNS 二次解析造成的 rebinding/TOCTOU。

**可信主机例外**：公司内网自建网关（如内部 LLM 代理）的域名必然解析到私网地址，
会被第 2 步误杀。`SSRF_TRUSTED_HOSTS`（逗号分隔域名）显式登记的域名跳过内网 IP
拦截——但协议、端口白名单、BLOCKED_HOSTNAMES/SUFFIXES 字面量检查与 IP 固定
全部照旧，例外只针对「解析结果是内网地址」这一条。登记什么域名是运维的显式决定，
随机用户输入永远不会出现在这份名单里。

**本机明文例外**：开发机上的本地模型服务（Ollama、自建 OpenAI 兼容代理等）只监听
`http://127.0.0.1:端口`。`validate_base_url` 对 `localhost` / `127.0.0.1` / `::1`
放行 HTTP 与任意端口——环回地址天然不可达其他机器，也不存在 DNS 解析，
端口探测与 rebinding 都无从谈起。云元数据地址、私网网段、公网 HTTP 仍一律拒绝。

所有出站请求都必须走 `safe_request` 或 `pin_url`；禁止自己 new 一个 httpx client 直连外部域名。
重定向不自动跟随，每一跳重新解析并重新 pin。

**代理环境下的例外**：`httpcore` 在走 HTTP 代理隧道时不读 `sni_hostname` 扩展，
固定成 IP 会让 TLS 按 IP 校验证书，必然 `CERTIFICATE_VERIFY_FAILED`（表现为 ConnectError，
前端看起来像「供应商不可达」，实际是我们的请求被自己的防护弄坏了）。
因此检测到代理时改为把原域名交给代理：域名在第 2 步已经解析并校验为公网地址，
校验环节没有削弱；DNS rebinding 的二次解析风险转移到代理这个本地可信组件上。
"""

from __future__ import annotations

import ipaddress
import os
import re
import socket
from urllib.parse import urljoin, urlparse, urlunparse

import httpx

# 云元数据与常见内部域名（字面量拦截，避免 DNS 被劫持时漏网）
BLOCKED_HOSTNAMES = {
    "localhost",
    "localhost.localdomain",
    "metadata",
    "metadata.google.internal",
    "instance-data",
    "instance-data.ec2.internal",
    "169.254.169.254",
    "0.0.0.0",
    "::1",
    "[::1]",
}

BLOCKED_SUFFIXES = (".localhost", ".local", ".internal", ".localdomain", ".home.arpa")
# 非标准端口常被用于内网端口探测，只放开 HTTPS 常规端口。
# 环回地址例外：本地模型服务通常跑在任意端口的明文 HTTP 上，见 _LOOPBACK_HOSTS。
ALLOWED_PORTS = {None, 443, 8443}

# 本机环回地址：放行 HTTP 与任意端口，其余主机一律不允许。
# 只认字面量，不认 DNS 解析结果——`localhost` 之外的域名即使解析到 127.0.0.1 也不在此列。
_LOOPBACK_HOSTS = {"localhost", "localhost.localdomain", "127.0.0.1", "::1"}

# 显式登记的可信主机（逗号分隔域名）：解析到内网地址也放行，见模块 docstring
_TRUSTED_HOSTS_ENV = "SSRF_TRUSTED_HOSTS"

# 代理相关环境变量：命中任意一个就认为出站流量经代理转发
_PROXY_ENV_VARS = (
    "HTTPS_PROXY",
    "https_proxy",
    "ALL_PROXY",
    "all_proxy",
)

_NUMERIC_PART = re.compile(r"(?:0[xX][0-9a-fA-F]+|0[0-7]+|[0-9]+)\Z")
_PURE_NUMBER = re.compile(r"(?:0[xX][0-9a-fA-F]+|[0-9]+)\Z")


class UnsafeUrl(ValueError):
    """URL 不安全，不应发起请求。"""


def proxy_in_effect() -> bool:
    """当前进程是否配置了出站代理。

    只在环境变量层面判断，不做连通性探测：探测本身也要花钱花时间，
    而 pin 与否只影响 TLS 校验路径，判断错了顶多回退到直连重试。
    """
    return any((os.environ.get(name) or "").strip() for name in _PROXY_ENV_VARS)


def trusted_hosts() -> set[str]:
    """`SSRF_TRUSTED_HOSTS` 解析出的小写域名集合（未配置时为空）。"""
    raw = (os.environ.get(_TRUSTED_HOSTS_ENV) or "").strip()
    if not raw:
        return set()
    hosts: set[str] = set()
    for item in raw.split(","):
        name = item.strip().lower().strip("[]").rstrip(".")
        # 通配符和空项不属于可信主机；宁可漏放行，不可误放行
        if name and "*" not in name:
            hosts.add(name)
    return hosts


def _normalize_host(host: str) -> str:
    return host.strip().lower().strip("[]").rstrip(".")


def _is_loopback_host(host: str) -> bool:
    """主机名是否为字面量环回地址（localhost / 127.0.0.1 / ::1）。"""
    return _normalize_host(host) in _LOOPBACK_HOSTS


def _host_is_trusted(host: str) -> bool:
    name = _normalize_host(host)
    return bool(name) and name in trusted_hosts()


def _ip_is_blocked(ip: ipaddress._BaseAddress) -> bool:
    if ip.version == 6 and ip.ipv4_mapped and _ip_is_blocked(ip.ipv4_mapped):
        return True
    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _blocked_literal_host(host: str) -> str | None:
    name = host.strip().lower().strip("[]")
    if not name:
        return "地址缺少主机名"
    if name in BLOCKED_HOSTNAMES or any(name.endswith(s) for s in BLOCKED_SUFFIXES):
        return "禁止访问本机、内网或云元数据地址"
    # 十进制 / 十六进制 / 八进制 / 简写 IPv4（127.1、2130706433、0177.0.0.1）
    # 这些写法 ipaddress 不认识，但 getaddrinfo / 浏览器会解析成 127.0.0.1。
    if _PURE_NUMBER.fullmatch(name):
        return "禁止使用数字形式的内网地址"
    parts = name.split(".")
    if parts and all(_NUMERIC_PART.fullmatch(p) for p in parts):
        if len(parts) != 4:
            return "禁止使用简写或整数形式的内网地址"
        if any(p.startswith("0x") or p.startswith("0X") or (p.startswith("0") and len(p) > 1) for p in parts):
            return "禁止使用八进制或十六进制形式的 IP 地址"
    try:
        ip = ipaddress.ip_address(name)
    except ValueError:
        return None
    if _ip_is_blocked(ip):
        return "禁止访问保留网段地址"
    return None


def validate_base_url(url: str) -> str:
    """字面量校验。返回去掉尾部斜杠的 URL；空串表示未配置，原样返回。

    环回地址（localhost / 127.0.0.1 / ::1）允许 HTTP 与任意端口，供本机模型服务使用；
    其他主机仍必须是 HTTPS 且端口限于 443/8443。云元数据与内网地址不在例外范围内。
    """
    raw = (url or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw)
    loopback = _is_loopback_host(parsed.hostname or "")
    if parsed.scheme not in ("https", "http"):
        raise UnsafeUrl("Base URL 必须使用 HTTP 或 HTTPS")
    if parsed.scheme != "https" and not loopback:
        raise UnsafeUrl("Base URL 必须使用 HTTPS（仅本机环回地址允许 HTTP）")
    if parsed.username or parsed.password:
        raise UnsafeUrl("Base URL 不允许携带用户名或密码")
    if parsed.fragment:
        raise UnsafeUrl("Base URL 不允许携带片段")
    # 环回地址本身就在字面量黑名单里（防 SSRF），这里是显式例外，跳过该条检查
    if not loopback:
        problem = _blocked_literal_host(parsed.hostname or "")
        if problem:
            raise UnsafeUrl(problem)
    try:
        port = parsed.port
    except ValueError as exc:
        raise UnsafeUrl("Base URL 端口无效") from exc
    if not loopback and port not in ALLOWED_PORTS:
        raise UnsafeUrl("仅允许 HTTPS 默认端口（443/8443），禁止自定义端口探测")
    return raw.rstrip("/")


def _resolve_public_ips(url: str) -> tuple[str, object, list[ipaddress._BaseAddress]]:
    normalized = validate_base_url(url)
    if not normalized:
        raise UnsafeUrl("未配置 Base URL")
    normalized = validate_base_url(url)
    if not normalized:
        raise UnsafeUrl("未配置 Base URL")
    parsed = urlparse(normalized)
    host = parsed.hostname or ""
    # 白名单匹配的是用户配置的域名本身。不能信任 DNS 返回值：DNS 被劫持时，
    # 可信域名也可能被解析成 169.254.169.254 之类的元数据地址，那仍然要拦。
    trusted = _host_is_trusted(host)
    loopback = _is_loopback_host(host)
    try:
        infos = socket.getaddrinfo(host, parsed.port or 443, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError, OSError) as exc:
        raise UnsafeUrl(f"域名无法解析：{host}") from exc
    ips: list[ipaddress._BaseAddress] = []
    seen: set[str] = set()
    for info in infos:
        address = info[4][0].split("%")[0]
        if address in seen:
            continue
        seen.add(address)
        try:
            ip = ipaddress.ip_address(address)
        except ValueError as exc:
            raise UnsafeUrl(f"域名解析结果异常：{address}") from exc
        # 可信主机例外只豁免「内网/保留网段」这一条；云元数据与 unspecified 地址
        # 任何情况下都拦截（DNS 被劫持时可信域名也可能被指到这些地址）。
        # 环回主机例外只豁免「解析结果仍是环回地址」：localhost 被劫持到内网或
        # 云元数据地址时照样拦截。
        always_blocked = ip.is_unspecified or str(ip) in BLOCKED_HOSTNAMES or (
            ip.version == 6 and ip.ipv4_mapped is not None and str(ip.ipv4_mapped) in BLOCKED_HOSTNAMES
        )
        loopback_ok = loopback and ip.is_loopback
        if always_blocked or (_ip_is_blocked(ip) and not trusted and not loopback_ok):
            raise UnsafeUrl(f"域名解析到禁止访问的地址：{address}")
        ips.append(ip)
    if not ips:
        raise UnsafeUrl(f"域名没有可用解析结果：{host}")
    return normalized, parsed, ips


def assert_safe_url(url: str) -> str:
    """仅做校验（不发起请求）；实际请求请用 pin_url / safe_request。"""
    normalized, _, _ = _resolve_public_ips(url)
    return normalized


def pin_url(url: str) -> tuple[str, dict, dict]:
    """返回 (要请求的 URL, 需要合并的请求头, 需要写入 Request.extensions 的字典)。

    调用方必须使用返回的 URL 发起连接，并把 headers / extensions 传给 httpx，
    否则 DNS rebinding 防护不成立。

    配置了出站代理时返回原 URL 且不带 Host / sni 覆盖：代理隧道下 `sni_hostname`
    不生效，固定 IP 会让 TLS 按 IP 校验证书而失败。域名已在 `_resolve_public_ips`
    里解析并逐条校验为公网地址，这一步的拦截强度不变。
    """
    _, parsed, ips = _resolve_public_ips(url)
    if proxy_in_effect():
        return url.rstrip("/"), {}, {}
    host = parsed.hostname or ""
    port = parsed.port
    chosen = ips[0]
    literal = f"[{chosen}]" if chosen.version == 6 else str(chosen)
    netloc = literal + (f":{port}" if port else "")
    pinned = urlunparse(parsed._replace(netloc=netloc))
    host_value = f"[{host}]" if ":" in host else host
    if port and port != 443:
        host_value = f"{host_value}:{port}"
    return pinned, {"Host": host_value}, {"sni_hostname": host}


def safe_request(
    method: str,
    url: str,
    *,
    headers: dict | None = None,
    json_body: dict | None = None,
    form_body: dict | None = None,
    files: list[tuple] | None = None,
    timeout: float = 8.0,
    max_redirects: int = 3,
) -> httpx.Response:
    """不自动跟随重定向；每一跳都重新解析并 pin IP。

    `files` 用于 multipart 上传（如微信永久素材），同样经 pin 后的连接发送，
    不允许调用方为了上传素材绕过这里直接 new 一个 httpx client。

    代理不可用时的回退：环境里配了代理但代理没起来（或连不上），
    直接抛 ConnectError 会让用户以为「供应商挂了」。这里捕获后改用
    `trust_env=False` + IP 固定重试一次，两条路都失败才把原始错误抛出去。
    """
    via_proxy = proxy_in_effect()
    try:
        return _send(
            method, url, headers=headers, json_body=json_body, form_body=form_body,
            files=files, timeout=timeout, max_redirects=max_redirects,
            trust_env=True, use_pin=not via_proxy,
        )
    except (httpx.ConnectError, httpx.ProxyError, httpx.ConnectTimeout):
        if not via_proxy:
            raise
        # 代理在生效但连不上，回退直连（不再经代理，必须恢复 IP 固定）
        return _send(
            method, url, headers=headers, json_body=json_body, form_body=form_body,
            files=files, timeout=timeout, max_redirects=max_redirects,
            trust_env=False, use_pin=True,
        )


def _send(
    method: str,
    url: str,
    *,
    headers: dict | None,
    json_body: dict | None,
    form_body: dict | None,
    files: list[tuple] | None,
    timeout: float,
    max_redirects: int,
    trust_env: bool,
    use_pin: bool,
) -> httpx.Response:
    current = url
    with httpx.Client(
        timeout=timeout, follow_redirects=False, trust_env=trust_env
    ) as client:
        for hop in range(max_redirects + 1):
            if use_pin:
                target, pinned_headers, extensions = _pin_force(current)
            else:
                target, pinned_headers, extensions = current, {}, {}
            merged = {**(headers or {}), **pinned_headers}
            request = client.build_request(
                method, target, headers=merged, json=json_body, data=form_body, files=files
            )
            request.extensions.update(extensions)
            response = client.send(request)
            if response.status_code not in (301, 302, 303, 307, 308):
                return response
            location = response.headers.get("location")
            if not location:
                return response
            if hop >= max_redirects:
                raise UnsafeUrl("重定向次数超过上限")
            current = urljoin(current, location)
        raise UnsafeUrl("重定向次数超过上限")


def _pin_force(url: str) -> tuple[str, dict, dict]:
    """强制固定 IP，忽略代理设置（回退直连时用）。"""
    _, parsed, ips = _resolve_public_ips(url)
    host = parsed.hostname or ""
    port = parsed.port
    chosen = ips[0]
    literal = f"[{chosen}]" if chosen.version == 6 else str(chosen)
    netloc = literal + (f":{port}" if port else "")
    pinned = urlunparse(parsed._replace(netloc=netloc))
    host_value = f"[{host}]" if ":" in host else host
    if port and port != 443:
        host_value = f"{host_value}:{port}"
    return pinned, {"Host": host_value}, {"sni_hostname": host}
