# -*- coding: utf-8 -*-
# Copyright 2026 Gods-Workbench Authors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""外部身份提供商（OIDC）运行期配置。

本模块只负责从环境变量读取配置并构造 ``core.oidc`` 所需的校验参数，
不保存任何密钥、令牌或凭据，也不在日志中输出敏感内容。

设计原则：
- 默认 ``GW_AUTH_MODE=local_account``：个人与小型团队使用本地账户会话；
- 仅当显式设置 ``GW_AUTH_MODE=oidc`` 时才启用真实外部 IdP 校验；
- 配置缺失一律**失败关闭**（拒绝请求），不静默降级为本地通行；
- JWKS 仅允许 HTTPS（本地回环地址例外，便于受控联调），并限制响应体积；
- 运行期配置按环境变量值缓存，使 JWKS 的 TTL 缓存**跨请求**真正生效。

环境变量：
- ``GW_AUTH_MODE``：``local_account``（默认）、``local``（仅显式开发/测试）或 ``oidc``；未识别取值按 ``oidc`` 处理并失败关闭；
- ``GW_OIDC_ISSUER``：issuer 完整 URL（同时用于 iss 校验与 discovery）；
- ``GW_OIDC_AUDIENCE``：预期 audience；
- ``GW_OIDC_CLIENT_ID``：当前 OIDC 公共客户端标识；当令牌含 ``azp`` 时，
  必须与 ``azp`` 完全一致（不得使用 ``GW_OIDC_AUDIENCE`` 代替）；
- ``GW_OIDC_JWKS_URL``：可选。缺省时自动走 ``issuer + /.well-known/openid-configuration``
  的 ``jwks_uri``（真实 IdP 接线的默认路径）；
- ``GW_OIDC_GROUPS_CLAIM``：组声明名，默认 ``groups``；
- ``GW_OIDC_LEEWAY_SECONDS``：时钟容差秒数，默认 60。
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional, Tuple
from urllib.parse import urlencode, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

AUTH_MODE_ENV = "GW_AUTH_MODE"
AUTH_MODE_LOCAL = "local"
AUTH_MODE_OIDC = "oidc"
AUTH_MODE_LOCAL_ACCOUNT = "local_account"
KNOWN_AUTH_MODES = frozenset({AUTH_MODE_LOCAL, AUTH_MODE_OIDC, AUTH_MODE_LOCAL_ACCOUNT})

JWKS_CACHE_SECONDS = 300
NEGATIVE_CACHE_SECONDS = 5
FORCE_REFRESH_MIN_SECONDS = 10
_MAX_REDIRECTS = 3
JWKS_TIMEOUT_SECONDS = 5
JWKS_MAX_BYTES = 512 * 1024
DISCOVERY_MAX_BYTES = 128 * 1024
ALLOWED_JWKS_SCHEMES = frozenset({"https"})
LOCAL_JWKS_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
DISCOVERY_PATH = "/.well-known/openid-configuration"
# OIDC 端点受信主机白名单（**可选、opt-in**）。
# 真实 IdP 常把 JWKS / token 端点点在另一个主机（例如 Google 的 issuer 是
# accounts.google.com，jwks_uri 却是 www.googleapis.com），严格的「逐字同源」会使其
# 完全无法接线。开启本白名单后，端点除必须满足 HTTPS（或本地回环）之外，
# **其主机名与 issuer 主机名都必须与白名单条目逐条精确相等**；未开启时仍强制逐字同源。
ENDPOINT_HOSTS_ENV = "GW_OIDC_ENDPOINT_HOSTS"
# **默认留空 = 严格同源**：不预置任何第三方主机，避免"默认放松"的隐性风险。
# 部署方如需接入跨主机 IdP（例：Google 的 issuer=accounts.google.com 但
# jwks_uri=www.googleapis.com），显式设置：
#   GW_OIDC_ENDPOINT_HOSTS=accounts.google.com,www.googleapis.com,oauth2.googleapis.com
DEFAULT_ENDPOINT_HOSTS = frozenset()


class _ValidatingRedirectHandler(HTTPRedirectHandler):
    """逐跳重校验「允许范围 + 起始 origin」的重定向处理器。

    只用单跳校验（is_allowed_jwks_url(url)）不足以拦住重定向绕过：
    urlopen 会自动跟随 302/303，攻击者可以从一个**合法**端点跳到任意
    https 或本地回环主机，让 verify_jwt 用攻击者自签的公钥验签（伪造成任意身份）。
    因此这里对**每一跳**同时校验两条：

    1. 目标 URL 仍在允许范围（HTTPS 或本地回环）内；
    2. 目标 URL 与**本次拉取的起始 origin**同源（scheme / hostname / 规范化端口一致）。

    起始 URL 由 _read_limited 在发请求前绑定到线程本地，避免并发请求互相串号。
    任一条件不满足即返回 None，交由 HTTPDefaultErrorHandler 抛 HTTPError（失败关闭）。
    合法的同源跳转（例如同一 IdP 从 /jwks 302 到 /keys）仍被允许，
    但跳数上限受 _MAX_REDIRECTS 约束。
    """

    max_redirections = _MAX_REDIRECTS

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102 - 覆写基类
        if not is_allowed_jwks_url(newurl):
            # 目标不在允许范围内：拒绝跟随（失败关闭，不跨跳重试）。
            return None
        allowed_origin = _current_redirect_origin()
        if not allowed_origin or not is_same_origin_as(newurl, allowed_origin):
            # 目标与起始 origin 不同源（异源主机或异端口）：拒绝跟随。
            # 没有这道锁，合法端点的 302 就能把 JWKS 拉到攻击者主机。
            return None
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class _NoRedirectHandler(HTTPRedirectHandler):
    """**绝不**跟随任何重定向（返回 None 即失败关闭）。用于令牌交换等凭据路径。"""

    max_redirections = 0

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102 - 覆写基类
        # 与 _ValidatingRedirectHandler 不同：这里连"同源合法跳转"也不放行。
        return None


# 只使用本模块自建的 opener（逐跳重校验），不使用全局默认 opener。
_VALIDATING_OPENER = build_opener(_ValidatingRedirectHandler)
# 令牌交换专用 opener：任何 3xx 一律拒绝（凭据不得经跳转流转）。
_NO_REDIRECT_OPENER = build_opener(_NoRedirectHandler)

# 每个线程各自保存「本次拉取的起始 URL」，重定向处理器据此锁定同源窗口。
# 用线程本地而不是全局变量：FastAPI 同步端点在线程池里并发执行，全局变量会串号。
_REDIRECT_GUARD = threading.local()


def _current_redirect_origin() -> str:
    """读取当前线程绑定的起始 URL；未绑定时返回空串（此时重定向一律拒绝）。"""
    return getattr(_REDIRECT_GUARD, "origin", "") or ""


ISSUER_ENV = "GW_OIDC_ISSUER"
AUDIENCE_ENV = "GW_OIDC_AUDIENCE"
JWKS_URL_ENV = "GW_OIDC_JWKS_URL"
GROUPS_CLAIM_ENV = "GW_OIDC_GROUPS_CLAIM"
LEEWAY_ENV = "GW_OIDC_LEEWAY_SECONDS"
# 授权码 + PKCE 流程所需配置：客户端标识、回调地址与 scope。
# 说明：本仓只支持**公共客户端 + PKCE S256**，不接受也不读取 client secret；
# 未配置 client_id / redirect_uri 时登录入口失败关闭（503 OIDC_NOT_CONFIGURED）。
CLIENT_ID_ENV = "GW_OIDC_CLIENT_ID"
REDIRECT_URI_ENV = "GW_OIDC_REDIRECT_URI"
SCOPES_ENV = "GW_OIDC_SCOPES"
DEFAULT_GROUPS_CLAIM = "groups"
DEFAULT_LEEWAY_SECONDS = 60
DEFAULT_SCOPES = "openid profile email"
# discovery 文档缓存秒数：登录入口需要 authorization_endpoint / token_endpoint，
# 不能让每个登录请求都外呼 IdP（避免自 Deni4l-of-svc）。
DISCOVERY_CACHE_SECONDS = 300


def resolve_auth_mode(value: Optional[str] = None) -> str:
    """解析认证模式；空值默认使用本地账户，未知值按 ``oidc`` 失败关闭。"""
    raw = (value if value is not None else os.environ.get(AUTH_MODE_ENV, "")).strip().lower()
    if raw in KNOWN_AUTH_MODES:
        return raw
    if not raw:
        return AUTH_MODE_LOCAL_ACCOUNT
    return AUTH_MODE_OIDC


@dataclass(frozen=True)
class JwksEndpointConfig:
    """JWKS 端点配置（不含任何密钥材料）。"""

    url: str
    cache_seconds: int = JWKS_CACHE_SECONDS
    timeout_seconds: int = JWKS_TIMEOUT_SECONDS


def normalize_endpoint_host(host: Any) -> str:
    """规范化主机名（去空白、转小写、去尾部点），非字符串返回空串。"""
    if not isinstance(host, str):
        return ""
    return host.strip().lower().rstrip(".")


def resolve_endpoint_hosts() -> frozenset:
    """解析受信端点主机白名单。

    - 未设置或设为空串：返回空集合，**严格同源**（默认，不放松）。
    - 其他取值：按逗号/空白分隔，作为**完整替换**的集合（部署方显式 opt-in）。
    """
    raw_value = os.environ.get(ENDPOINT_HOSTS_ENV)
    if raw_value is None:
        return DEFAULT_ENDPOINT_HOSTS
    if not raw_value.strip():
        return frozenset()
    parts = raw_value.replace(",", " ").split()
    return frozenset(host for host in (normalize_endpoint_host(part) for part in parts) if host)


def is_allowed_jwks_url(url: str) -> bool:
    """校验 JWKS URL：必须是 HTTPS，或指向本地回环的受控联调地址。"""
    if not isinstance(url, str) or not url.strip():
        return False
    parsed = urlparse(url.strip())
    if parsed.scheme in ALLOWED_JWKS_SCHEMES and parsed.netloc:
        return True
    if parsed.scheme == "http" and parsed.hostname in LOCAL_JWKS_HOSTS:
        return True
    return False


def _read_limited(
    request: Request,
    limit: int,
    description: str,
    timeout_seconds: int = JWKS_TIMEOUT_SECONDS,
    allow_redirects: bool = True,
) -> bytes:
    """发起一次 GET 并读取受限体积的响应体；非 2xx 或超限一律抛异常。

    发请求前把起始 URL 绑定到线程本地，使重定向守卫既能拦白名单外目标，
    也能拦住「合法主机 302 到异源主机」这一类绕过（R6-8）。

    ``allow_redirects=False`` 用于**授权码 / 令牌交换**：令牌响应体含凭据，
    即使 302 目标"同源合法"也不应被跟随，否则攻击者可借同源跳转偷看/改写响应。
    此时任何一个 3xx 都会以 HTTPError 失败关闭。
    """
    previous_origin = _current_redirect_origin()
    _REDIRECT_GUARD.origin = request.full_url
    try:
        opener = _VALIDATING_OPENER if allow_redirects else _NO_REDIRECT_OPENER
        with opener.open(request, timeout=max(1, int(timeout_seconds))) as response:  # noqa: S310 - 调用方已校验 scheme
            status_code = getattr(response, "status", 200)
            if status_code < 200 or status_code >= 300:
                raise ValueError(f"{description}返回非成功状态")
            payload = response.read(limit + 1)
    finally:
        # 恢复现场，避免同一线程后续请求误用上一次的 origin。
        _REDIRECT_GUARD.origin = previous_origin
    if len(payload) > limit:
        raise ValueError(f"{description}响应超过允许体积")
    return payload


def fetch_jwks(config: JwksEndpointConfig) -> Mapping[str, Any]:
    """拉取一次 JWKS 文档；非 2xx、超大响应或非 JSON 对象一律抛出异常。"""
    if not is_allowed_jwks_url(config.url):
        raise ValueError("JWKS URL 不在允许范围（仅 HTTPS 或本地回环）")
    request = Request(config.url, headers={"Accept": "application/json"}, method="GET")
    payload = _read_limited(request, JWKS_MAX_BYTES, "JWKS 端点", config.timeout_seconds)
    document = json.loads(payload.decode("utf-8"))
    if not isinstance(document, dict) or not isinstance(document.get("keys"), (list, tuple)):
        raise ValueError("JWKS 文档结构无效")
    return document


def fetch_discovery_document(issuer: str) -> Mapping[str, Any]:
    """拉取 issuer 的 OIDC discovery 文档（``/.well-known/openid-configuration``）。"""
    base = (issuer or "").strip().rstrip("/")
    discovery_url = base + DISCOVERY_PATH
    if not is_allowed_jwks_url(discovery_url):
        raise ValueError("discovery 端点不在允许范围（仅 HTTPS 或本地回环）")
    request = Request(discovery_url, headers={"Accept": "application/json"}, method="GET")
    payload = _read_limited(request, DISCOVERY_MAX_BYTES, "discovery 端点")
    document = json.loads(payload.decode("utf-8"))
    if not isinstance(document, dict):
        raise ValueError("discovery 文档结构无效")
    return document


def resolve_jwks_url(issuer: str, explicit_url: str = "") -> str:
    """确定 JWKS URL：优先显式配置，缺省时按 issuer 走 discovery 的 ``jwks_uri``。

    安全关键：discovery 文档中的 ``jwks_uri`` 必须与 issuer **同源**。
    否则被篡改的 discovery 可把 JWKS 指向攻击者主机，攻击者自签密钥即可伪造令牌。
    """
    candidate = (explicit_url or "").strip()
    if not candidate:
        document = fetch_discovery_document(issuer)
        candidate = str(document.get("jwks_uri") or "").strip()
    if not is_allowed_jwks_url(candidate):
        raise ValueError("JWKS 端点配置无效（仅 HTTPS 或本地回环）")
    if not is_trusted_endpoint(candidate, issuer):
        raise ValueError("JWKS 端点不在 issuer 的受信范围，已拒绝")
    return candidate


def build_jwks_fetcher(config: JwksEndpointConfig) -> Callable[[], Mapping[str, Any]]:
    """构造带 TTL 缓存的 JWKS 拉取器，供 ``oidc.OidcConfig.jwks_fetcher`` 注入。

    返回值是零参可调用对象，因此与既有 ``jwks_fetcher`` 协议完全兼容；
    另外挂载 ``force_refresh()`` 属性：供校验器在 **未知 kid**（密钥轮换）时
    绕过 TTL 受控刷新一次，避免 TTL 窗口内新令牌被旧文档误拒。
    """
    cache: dict = {"at": 0.0, "document": None, "force_at": 0.0}

    def _load() -> Mapping[str, Any]:
        document = fetch_jwks(config)
        cache["document"] = document
        cache["at"] = time.time()
        return document

    def fetcher() -> Mapping[str, Any]:
        now = time.time()
        cached = cache.get("document")
        if cached is not None and now - float(cache.get("at") or 0.0) < max(0, int(config.cache_seconds)):
            return cached
        return _load()

    def force_refresh() -> Mapping[str, Any]:
        """未知 kid（密钥轮换）时的受控刷新：绕过 TTL，但有最小间隔限流。

        限流防止攻击者用随机 kid 反复触发外呼造成对 IdP 的自 DoS；限流窗口内
        返回当前缓存文档（可能命中不了新 kid，此时校验失败关闭）。
        """
        now = time.time()
        cached = cache.get("document")
        if cached is not None and now - float(cache.get("force_at") or 0.0) < FORCE_REFRESH_MIN_SECONDS:
            return cached
        document = _load()
        cache["force_at"] = now
        return document

    # 未知 kid 的受控刷新入口：显式绕过 TTL，但仍受体积/结构/白名单校验约束。
    fetcher.force_refresh = force_refresh
    return fetcher


@dataclass(frozen=True)
class RuntimeAuthConfig:
    """认证运行期配置汇总；``ready`` 为假时调用方必须失败关闭。"""

    mode: str
    ready: bool
    reason: str
    oidc: Any = None
    jwks_url: str = ""
    # 授权码 + PKCE 流程信息（仅公共客户端，无 client secret）。
    client_id: str = ""
    redirect_uri: str = ""
    scopes: str = DEFAULT_SCOPES
    # 登录入口可用性：需要同时具备校验配置与客户端配置。
    login_ready: bool = False


def auth_config_generation() -> str:
    """返回当前认证环境指纹的稳定代际标识。

    发行者、受众、JWKS、端点主机等信任根相关变量任一变化都会改变该值。
    会话在建立时绑定它；读取时不一致即视为信任根已变更。
    只暴露摘要，不把环境变量原文写入会话或响应。
    """
    import hashlib

    material = "\n".join(_environment_fingerprint())
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _environment_fingerprint() -> Tuple[str, ...]:
    """采集与认证配置相关的环境变量快照，用作运行期配置缓存键。"""
    return (
        os.environ.get(AUTH_MODE_ENV, ""),
        os.environ.get(ISSUER_ENV, ""),
        os.environ.get(AUDIENCE_ENV, ""),
        os.environ.get(JWKS_URL_ENV, ""),
        os.environ.get(GROUPS_CLAIM_ENV, ""),
        os.environ.get(LEEWAY_ENV, ""),
        os.environ.get(CLIENT_ID_ENV, ""),
        os.environ.get(REDIRECT_URI_ENV, ""),
        os.environ.get(SCOPES_ENV, ""),
        os.environ.get(ENDPOINT_HOSTS_ENV, ""),
    )


# 运行期配置缓存：同一进程内环境变量不变时复用同一个 ``OidcConfig``，
# 使 ``build_jwks_fetcher`` 的 TTL 缓存跨请求生效（避免每请求都打 IdP）。
_RUNTIME_CACHE: dict = {}
# 失败结果短负缓存：key = 环境变量指纹，value = (记录时间, RuntimeAuthConfig)
_NEGATIVE_CACHE: dict = {}


def reset_runtime_auth_config_cache() -> None:
    """清空运行期配置缓存（供测试与热更新使用）。"""
    _RUNTIME_CACHE.clear()
    _NEGATIVE_CACHE.clear()


def load_runtime_auth_config() -> RuntimeAuthConfig:
    """从环境变量装载认证配置；任何缺失都返回不可用状态，由调用方拒绝请求。

    - 可用配置按环境变量指纹长期复用（使 JWKS TTL 缓存跨请求生效）。
    - 失败配置只缓存 ``NEGATIVE_CACHE_SECONDS`` 秒；到期自动重试，故障自愈无需重启。
    """
    fingerprint = _environment_fingerprint()
    cached = _RUNTIME_CACHE.get(fingerprint)
    if cached is not None:
        return cached
    negative = _NEGATIVE_CACHE.get(fingerprint)
    if negative is not None and time.time() - float(negative[0]) < NEGATIVE_CACHE_SECONDS:
        return negative[1]
    runtime = _build_runtime_auth_config(fingerprint)
    if runtime.ready:
        _RUNTIME_CACHE[fingerprint] = runtime
        _NEGATIVE_CACHE.pop(fingerprint, None)
        return runtime
    # 失败结果只做**短**负缓存：既避免 IdP 不可达时每请求重复外呼（自 DoS），
    # 又保证故障自愈不需要重启进程。
    _RUNTIME_CACHE.pop(fingerprint, None)
    _NEGATIVE_CACHE[fingerprint] = (time.time(), runtime)
    return runtime


def _build_runtime_auth_config(fingerprint: Tuple[str, ...]) -> RuntimeAuthConfig:
    """按环境变量快照构造运行期认证配置（失败一律 ``ready=False``）。"""
    mode = resolve_auth_mode(fingerprint[0])
    if mode != AUTH_MODE_OIDC:
        return RuntimeAuthConfig(mode=mode, ready=False, reason="未启用外部 IdP 校验")

    issuer = fingerprint[1].strip()
    audience = fingerprint[2].strip()
    groups_claim = fingerprint[4].strip() or DEFAULT_GROUPS_CLAIM
    client_id = (fingerprint[6] if len(fingerprint) > 6 else "").strip()
    redirect_uri = (fingerprint[7] if len(fingerprint) > 7 else "").strip()
    scopes = ((fingerprint[8] if len(fingerprint) > 8 else "").strip() or DEFAULT_SCOPES)
    if not issuer or not audience:
        return RuntimeAuthConfig(
            mode=mode,
            ready=False,
            reason="OIDC 配置缺失，已拒绝请求",
            client_id=client_id,
            redirect_uri=redirect_uri,
            scopes=scopes,
        )

    try:
        jwks_url = resolve_jwks_url(issuer, fingerprint[3])
    except Exception:
        # discovery 失败、jwks_uri 缺失或 scheme 非法：失败关闭。
        return RuntimeAuthConfig(mode=mode, ready=False, reason="JWKS 端点配置无效，已拒绝请求")

    try:
        leeway = int(fingerprint[5])
    except ValueError:
        leeway = DEFAULT_LEEWAY_SECONDS

    from gw.core.oidc import OidcConfig  # 延迟导入，避免模块循环

    oidc_config = OidcConfig(
        issuer=issuer,
        audience=audience,
        client_id=client_id,
        enabled=True,
        leeway_seconds=max(0, leeway),
        groups_claim=groups_claim,
        jwks_fetcher=build_jwks_fetcher(JwksEndpointConfig(url=jwks_url)),
    )
    # 登录入口可用性：校验配置就绪 + 客户端标识与回调地址齐备。
    login_ready = bool(client_id and redirect_uri)
    login_reason = "已启用外部 IdP 校验" if login_ready else "授权码流程配置缺失（client_id / redirect_uri）"
    return RuntimeAuthConfig(
        mode=mode,
        ready=True,
        reason=login_reason,
        oidc=oidc_config,
        jwks_url=jwks_url,
        client_id=client_id,
        redirect_uri=redirect_uri,
        scopes=scopes,
        login_ready=login_ready,
    )

# ---------------------------------------------------------------------------
# 授权码 + PKCE 流程：discovery 端点缓存与令牌交换
# ---------------------------------------------------------------------------

_DISCOVERY_CACHE: dict = {"at": 0.0, "issuer": "", "document": None}


def reset_discovery_cache() -> None:
    """清空 discovery 缓存（供测试与热更新使用）。"""
    _DISCOVERY_CACHE.update({"at": 0.0, "issuer": "", "document": None})


def _require_document_issuer_matches(document: Mapping[str, Any], base: str) -> None:
    """校验 discovery 文档自述的 ``issuer`` 与请求 issuer 逐字相同。

    OIDC Discovery 1.0 §4.3 要求 ``issuer`` 必须与用于检索该文档的 issuer 完全一致。
    不校验会让「文档由 A 主机提供、却自述是 B」这一混合攻击（mix-up）路径成立：
    authorization / token / jwks 端点随后都会被按 B 的信任口径去比对。
    这里采用**逐字**比较（两侧都去掉尾部斜杠），比 URL 规范化更严格，失败关闭。
    """
    declared = document.get("issuer")
    if not isinstance(declared, str) or declared.strip().rstrip("/") != base:
        raise ValueError("discovery 文档 issuer 与配置 issuer 不一致，已拒绝")
    if not declared.strip():
        raise ValueError("discovery 文档缺少 issuer，已拒绝")


def load_discovery_document(issuer: str, *, cache_seconds: Optional[int] = None) -> Mapping[str, Any]:
    """按 issuer 读取 discovery 文档，带 TTL 缓存（避免每个登录请求都外呼 IdP）。

    返回前强制校验文档自述 ``issuer`` 与请求 issuer 逐字一致（见
    :func:`_require_document_issuer_matches`）；不一致一律抛 ``ValueError`` 失败关闭。
    """
    base = (issuer or "").strip().rstrip("/")
    if not base:
        raise ValueError("issuer 缺失")
    ttl = DISCOVERY_CACHE_SECONDS if cache_seconds is None else max(0, int(cache_seconds))
    cached = _DISCOVERY_CACHE.get("document")
    if (
        cached is not None
        and _DISCOVERY_CACHE.get("issuer") == base
        and time.time() - float(_DISCOVERY_CACHE.get("at") or 0.0) < ttl
    ):
        _require_document_issuer_matches(cached, base)
        return cached
    document = fetch_discovery_document(base)
    _require_document_issuer_matches(document, base)
    _DISCOVERY_CACHE.update({"at": time.time(), "issuer": base, "document": document})
    return document


def is_trusted_endpoint(endpoint: str, issuer: str, hosts: Optional[frozenset] = None) -> bool:
    """端点是否可信：**逐字同源**，或端点主机**精确命中** opt-in 受信主机白名单。

    设计要点（安全关键）：
    - 默认（未配置 ``GW_OIDC_ENDPOINT_HOSTS``）＝ **严格同源**，行为与旧的
      ``is_same_origin_as`` 完全一致，不存在任何隐式放松。
    - 放宽仅在部署方**显式**给出主机清单时生效；采用的是**精确主机名相等**
      （大小写不敏感、忽略尾部点），不做后缀/前缀/子域匹配，因此
      ``www.googleapis.com.evil.com`` 这类后缀把戏不会命中白名单。
    - 放宽分支要求 **issuer 主机与端点主机同时**命中白名单；只命中一侧一律拒绝。
    - 白名单**不是通用安全边界**：匹配是「与 allowlist 逐条精确相等」的幼稚比较，
      既不做公共后缀表（PSL）判定，也不做同一可注册域（eTLD+1）推导——
      因此 ``a.example.co.uk`` 与 ``b.example.co.uk`` 会被视为**不同**主机（严格），
      而 ``idp.example.com`` 与 ``evil.com`` 若被同时填入白名单则**双双命中**。
      填错白名单＝自毁信任根；该清单必须由部署方按 IdP 官方 discovery 文档逐条照抄，
      本仓**不预置**任何第三方主机。
    - ``is_same_origin_as`` 本身语义**不变**；本函数只作为「同源 OR 显式 opt-in 白名单」
      的联合判据，供 JWKS 与 token 端点使用（authorization 端点仍强制逐字同源）。
    """
    if is_same_origin_as(endpoint, issuer):
        return True
    allowed = resolve_endpoint_hosts() if hosts is None else hosts
    if not allowed:
        return False
    try:
        target = urlparse((endpoint or "").strip())
        base = urlparse((issuer or "").strip().rstrip("/"))
    except ValueError:
        return False
    if not target.scheme or not target.hostname or not base.scheme or not base.hostname:
        return False
    # 带 userinfo 的 URL 一律拒绝（host 与书写不符，易被利用）。
    if target.username is not None or target.password is not None:
        return False
    if base.username is not None or base.password is not None:
        return False
    if target.scheme.lower() != base.scheme.lower():
        return False
    if (target.port or (443 if target.scheme.lower() == "https" else 80)) != (
        base.port or (443 if base.scheme.lower() == "https" else 80)
    ):
        return False
    # 放宽分支要求两侧主机都在白名单内：白名单即本部署对该 IdP 的信任根，
    # 避免为 A 家 IdP 配置的清单被 B 家 issuer 无声复用。
    # 主机比较只做小写：**不去尾随点**。因此 ``idp.example.com.`` 这类 FQDN 尾点写法
    # 在两条分支都被拒（严格分支因 hostname 不等而拒，放宽分支因与白名单条目不等而拒）。
    # 配置侧 entry 由 normalize_endpoint_host 去掉尾点，二者口径刻意保持"更严"而非"更松"。
    target_host = (target.hostname or "").lower()
    issuer_host = (base.hostname or "").lower()
    return target_host in allowed and issuer_host in allowed


def is_same_origin_as(endpoint: str, issuer: str) -> bool:
    """判断端点是否与 issuer **同源**（scheme + 主机名 + 端口 精确相等）。

    实现说明（安全关键）：不得用字符串前缀比较。
    ``"https://idp.example.com".startswith`` 既会被 ``https://idp.example.com.evil.com``
    命中，也会被携带 userinfo 的 ``https://idp.example.com@evil.com`` 命中（后者的真实
    host 是 ``evil.com``）。因此这里解析后比较 scheme / hostname / 规范化端口，
    并把带 userinfo 的 URL 直接判为不合规。
    """
    try:
        target = urlparse((endpoint or "").strip())
        base = urlparse((issuer or "").strip().rstrip("/"))
    except ValueError:
        return False
    if not target.scheme or not target.hostname or not base.scheme or not base.hostname:
        return False
    # 带 userinfo 的 URL（``https://user@host/...``）一律拒绝：host 与书写不符，易被利用。
    if target.username is not None or target.password is not None:
        return False
    if base.username is not None or base.password is not None:
        return False
    if target.scheme.lower() != base.scheme.lower():
        return False
    if (target.hostname or "").lower() != (base.hostname or "").lower():
        return False
    target_port = target.port or (443 if target.scheme.lower() == "https" else 80)
    base_port = base.port or (443 if base.scheme.lower() == "https" else 80)
    return target_port == base_port


def resolve_endpoint(issuer: str, name: str) -> str:
    """从 discovery 文档解析指定端点（``authorization_endpoint`` / ``token_endpoint``）。

    端点必须通过 :func:`is_allowed_jwks_url` 的同口径白名单（HTTPS 或本地回环），
    且必须通过受信判据，避免 discovery 文档被篡改后把授权/令牌请求
    指向第三方主机（那样授权码会被 POST 给攻击者）。

    **两档受信口径（刻意不对称，安全关键）**：

    - ``authorization_endpoint``：强制与 issuer **逐字同源**（``is_same_origin_as``），
      **不受** ``GW_OIDC_ENDPOINT_HOSTS`` 放宽影响。授权端点是浏览器跳转目标，
      同源即可满足所有主流 IdP（Google / Microsoft 均在 issuer 主机上）。
    - ``token_endpoint``：允许 ``is_trusted_endpoint``（同源 **或** 显式 opt-in 白名单）。
      真实 IdP 会把令牌端点放在另一主机（Google: issuer=accounts.google.com，
      token=oauth2.googleapis.com），若强制同源则**无法接线**；放宽后令牌端点仍必须
      逐条命中部署方显式填写的白名单，且 :func:`exchange_authorization_code`
      对令牌响应**不跟随任何重定向**。
    """
    document = load_discovery_document(issuer)
    endpoint = str(document.get(name) or "").strip()
    if not endpoint:
        raise ValueError(f"discovery 文档缺少 {name}")
    if not is_allowed_jwks_url(endpoint):
        raise ValueError(f"{name} 不在允许范围（仅 HTTPS 或本地回环）")
    trusted = (
        is_same_origin_as(endpoint, issuer)
        if name == "authorization_endpoint"
        else is_trusted_endpoint(endpoint, issuer)
    )
    if not trusted:
        raise ValueError(f"{name} 不在 issuer 的受信范围内，已拒绝")
    return endpoint


def exchange_authorization_code(
    *,
    token_endpoint: str,
    code: str,
    code_verifier: str,
    client_id: str,
    redirect_uri: str,
) -> Mapping[str, Any]:
    """用授权码 + PKCE 换取代币；使用公共客户端语义（不发 client secret）。

    返回令牌响应文档；调用方**不得**把它写入 Cookie、响应体或日志。
    """
    if not is_allowed_jwks_url(token_endpoint):
        raise ValueError("token 端点不在允许范围（仅 HTTPS 或本地回环）")
    if not code or not code_verifier or not client_id or not redirect_uri:
        raise ValueError("令牌交换参数缺失")
    body = urlencode(
        {
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": code_verifier,
            "client_id": client_id,
            "redirect_uri": redirect_uri,
        }
    ).encode("utf-8")
    request = Request(
        token_endpoint,
        data=body,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    # 凭据路径：任何 3xx 一律失败关闭，不跟随重定向（allow_redirects=False）。
    payload = _read_limited(
        request,
        DISCOVERY_MAX_BYTES,
        "token 端点",
        JWKS_TIMEOUT_SECONDS,
        allow_redirects=False,
    )
    document = json.loads(payload.decode("utf-8"))
    if not isinstance(document, dict):
        raise ValueError("令牌响应结构无效")
    return document
