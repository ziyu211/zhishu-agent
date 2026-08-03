"""SSRF 防护：出网请求前校验目标主机，拒绝内网/私有/回环/链路本地地址。

复用了 api/models.py 中 fetch_models 的判定口径，作为 web/safe_web_fetch 与
plugin http 类型工具的统一步骤，防止提示注入诱使智能体访问内网服务或云元数据。
"""
from __future__ import annotations

import socket
import ipaddress
from urllib.parse import urlparse


def is_internal_host(host: str) -> bool:
    """主机解析为内网/私有/回环/链路本地地址，或解析失败时（按不可信）返回 True。"""
    host = (host or "").strip()
    if not host:
        return True
    try:
        for info in socket.getaddrinfo(host, None):
            ip = info[4][0]
            addr = ipaddress.ip_address(ip)
            if addr.is_private or addr.is_loopback or addr.is_link_local:
                return True
    except Exception:
        return True
    return False


def guard_url(url: str, *, allow_private: bool = False) -> bool:
    """URL 是否允许出网访问。

    - 仅允许 http/https；
    - 内网/私有/回环地址默认拒绝（allow_private=True 时放行）。
    返回 True 表示允许，False 表示应被拦截。
    """
    parsed = urlparse(url or "")
    if parsed.scheme not in ("http", "https"):
        return False
    if allow_private:
        return True
    return not is_internal_host(parsed.hostname)
