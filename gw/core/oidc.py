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

"""外部 IdP（OIDC）JWT 校验模块。

本模块提供 OIDC / JWT 校验能力，默认关闭、失败关闭（fail closed）。
Phase 9 起它**已被 `core/auth.py` 在 `GW_AUTH_MODE=oidc` 模式下接线**；
默认 `local_account` 模式使用本地账户会话；`local` 仅显式开发/测试模式下启用。接线细节与真实链路证据见
`docs/governance/TASK-NOTES-2026-09-18.md` 第 21.11.3 / 21.12 节及
`src/gw/core/config.py`。

覆盖范围：
- JWT 签名校验（仅 RS256，拒绝 ``alg=none`` 与 ``HS*`` 算法混淆）；
- ``iss`` / ``aud`` / ``exp`` / ``nbf`` / ``iat`` 校验（显式时钟容差）；
- 未知 ``kid`` 时通过调用方注入的 ``jwks_fetcher`` 受控刷新一次，默认不可用即拒绝；
- ``nonce`` / ``state`` 流程辅助（含 PKCE S256）；
- IdP 组到本地角色的显式映射，未知组不升级，多组取最高已授权等级。

证据边界：本模块的接线为**配置驱动**（默认 `local_account`；仅显式 `GW_AUTH_MODE=oidc` 才启用 OIDC），
本仓**不含**任何真实 issuer / JWKS 地址 / 客户端密钥；本轮**未接入生产 IdP**，
**不构成**生产就绪或发布授权。
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Optional, Tuple
from urllib.parse import urlencode

from gw.core.errors import UnauthorizedException

try:  # pragma: no cover - 依赖缺失时由 verify_jwt 统一走失败关闭分支
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding, rsa

    CRYPTOGRAPHY_AVAILABLE = True
except ImportError:  # pragma: no cover
    CRYPTOGRAPHY_AVAILABLE = False


# 仅允许非对称 RS256；任何对称算法（HS*）或 alg=none 一律拒绝。
ALLOWED_ALGORITHMS = frozenset({"RS256"})

# IdP 组到本地角色的显式映射；键为小写规范化后的完整组名。
GROUP_ROLE_MAPPING = {
    "gw-admin": "admin",
    "gw-governor": "governor",
    "gw-editor": "editor",
    "gw-reviewer": "reviewer",
    "gw-readonly": "readonly",
}

# 角色权限从低到高；多组命中时取最高已授权等级。
ROLE_PRIORITY: Tuple[str, ...] = ("readonly", "reviewer", "editor", "governor", "admin")

DEFAULT_GROUPS_CLAIM = "groups"

JwksFetcher = Callable[[], Mapping[str, Any]]


@dataclass(frozen=True)
class OidcConfig:
    """OIDC 校验配置（已由 ``core/auth.py`` 在 ``oidc`` 模式下实际接线）。

    ``enabled`` 默认 ``False``：默认关闭，且校验器在关闭或配置缺失时一律拒绝，
    不会静默放行。``jwks_fetcher`` 由调用方注入；默认 ``None`` 表示不可用即拒绝。
    """

    issuer: str = ""
    audience: str = ""
    # 授权方（azp）必须绑定到当前 OIDC 客户端，而不是复用 audience 配置。
    client_id: str = ""
    enabled: bool = False
    allowed_algorithms: frozenset = ALLOWED_ALGORITHMS
    leeway_seconds: int = 60
    groups_claim: str = DEFAULT_GROUPS_CLAIM
    jwks: Mapping[str, Any] = field(default_factory=dict)
    jwks_fetcher: Optional[JwksFetcher] = None


@dataclass(frozen=True)
class OidcIdentity:
    """OIDC 校验通过后的最小身份上下文，不保存令牌原文。"""

    subject: str
    role: str
    groups: Tuple[str, ...]
    claims: Mapping[str, Any]


# ---------------------------------------------------------------------------
# Base64URL / JSON 解析
# ---------------------------------------------------------------------------

def _b64url_decode(segment: str) -> bytes:
    """严格解码 Base64URL 片段，非法输入直接拒绝。"""
    if not isinstance(segment, str) or not segment:
        raise UnauthorizedException(message="令牌片段为空，已拒绝")
    padded = segment + "=" * (-len(segment) % 4)
    try:
        # validate=True：让 Python 对非法字符直接抛错，而不是静默丢弃后返回空字节串。
        return base64.b64decode(padded.encode("ascii"), altchars=b"-_", validate=True)
    except (binascii.Error, ValueError, UnicodeEncodeError):
        raise UnauthorizedException(message="令牌片段不是合法 Base64URL，已拒绝")


def _decode_json_segment(segment: str, description: str) -> Mapping[str, Any]:
    """解码并解析 JWT 的 JSON 片段，非对象一律拒绝。"""
    raw = _b64url_decode(segment)
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise UnauthorizedException(message=f"{description}不是合法 JSON，已拒绝")
    if not isinstance(value, dict):
        raise UnauthorizedException(message=f"{description}不是 JSON 对象，已拒绝")
    return value


def split_token(token: str) -> Tuple[str, str, str]:
    """把 JWT 拆分为 header.payload.signature 三段，段数异常即拒绝。"""
    if not isinstance(token, str):
        raise UnauthorizedException(message="令牌类型无效，已拒绝")
    parts = token.strip().split(".")
    if len(parts) != 3:
        raise UnauthorizedException(message="令牌结构无效，已拒绝")
    header_segment, payload_segment, signature_segment = parts
    if not header_segment or not payload_segment:
        raise UnauthorizedException(message="令牌结构无效，已拒绝")
    return header_segment, payload_segment, signature_segment


def decode_header(token: str) -> Mapping[str, Any]:
    """解码 JWT 头部（仅解析，不校验签名）。"""
    header_segment, _, _ = split_token(token)
    return _decode_json_segment(header_segment, "令牌头部")


def decode_claims(token: str) -> Mapping[str, Any]:
    """解码 JWT 载荷（仅解析，不校验签名）。"""
    _, payload_segment, _ = split_token(token)
    return _decode_json_segment(payload_segment, "令牌载荷")


# ---------------------------------------------------------------------------
# nonce / state / PKCE 流程辅助
# ---------------------------------------------------------------------------

def generate_state() -> str:
    """生成一次性 state 随机值。"""
    return secrets.token_urlsafe(32)


def generate_nonce() -> str:
    """生成一次性 nonce 随机值。"""
    return secrets.token_urlsafe(32)


def constant_time_equals(left: Any, right: Any) -> bool:
    """常量时间比较字符串，避免时序侧信道。"""
    if not isinstance(left, str) or not isinstance(right, str):
        return False
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


def verify_state(expected_state: str, received_state: str) -> None:
    """校验回调 state；缺失或不匹配一律拒绝。"""
    if not expected_state or not received_state or not constant_time_equals(expected_state, received_state):
        raise UnauthorizedException(message="state 校验失败，已拒绝回调")


def verify_nonce(expected_nonce: str, claims: Mapping[str, Any]) -> None:
    """校验令牌 nonce 声明；缺失或不匹配一律拒绝。"""
    actual = claims.get("nonce") if isinstance(claims, Mapping) else None
    if not expected_nonce or not isinstance(actual, str) or not constant_time_equals(expected_nonce, actual):
        raise UnauthorizedException(message="nonce 校验失败，已拒绝会话")


def generate_pkce_pair() -> Tuple[str, str]:
    """生成 PKCE 的 (code_verifier, code_challenge)，挑战使用 S256。"""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def build_authorization_url(
    *,
    authorization_endpoint: str,
    client_id: str,
    redirect_uri: str,
    state: str,
    nonce: str,
    code_challenge: str,
    scope: str = "openid profile email",
) -> str:
    """构造授权码 + PKCE（S256）的授权跳转 URL，不包含任何密钥。"""
    if not authorization_endpoint or not client_id or not redirect_uri:
        raise UnauthorizedException(message="OIDC 授权配置缺失，已拒绝构造跳转")
    if not state or not nonce or not code_challenge:
        raise UnauthorizedException(message="state / nonce / PKCE 参数缺失，已拒绝构造跳转")
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": scope,
        "state": state,
        "nonce": nonce,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    return f"{authorization_endpoint}?{urlencode(params)}"


# ---------------------------------------------------------------------------
# 组到本地角色映射
# ---------------------------------------------------------------------------

def normalize_group_name(group: Any) -> str:
    """规范化组名：去首尾空白并转小写（完整匹配，不做前缀匹配）。"""
    if not isinstance(group, str):
        return ""
    return group.strip().lower()


def normalize_groups(value: Any) -> Tuple[str, ...]:
    """把 claim 中的组集合规范化为去空白的字符串元组。"""
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple, set, frozenset)):
        return ()
    result = []
    for item in value:
        if isinstance(item, str) and item.strip():
            result.append(item.strip())
    return tuple(result)


def resolve_role(groups: Any) -> Optional[str]:
    """返回最高已授权本地角色；未知组被忽略，无命中时返回 ``None``（不升级）。"""
    normalized = normalize_groups(groups)
    matched = []
    for group in normalized:
        role = GROUP_ROLE_MAPPING.get(normalize_group_name(group))
        if role is not None:
            matched.append(role)
    if not matched:
        return None
    return max(matched, key=ROLE_PRIORITY.index)


# ---------------------------------------------------------------------------
# JWKS 解析与受控刷新
# ---------------------------------------------------------------------------

def _jwk_base64url_to_int(value: Any) -> int:
    """把 JWK 的 Base64URL 大整数还原为 int。"""
    if not isinstance(value, str) or not value:
        raise UnauthorizedException(message="JWKS 参数缺失，已拒绝")
    padded = value + "=" * (-len(value) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
    except (binascii.Error, ValueError, UnicodeEncodeError):
        raise UnauthorizedException(message="JWKS 参数不是合法 Base64URL，已拒绝")
    return int.from_bytes(raw, "big")


def _jwk_to_public_key(jwk: Mapping[str, Any]) -> Any:
    """把 RSA JWK 转换为公钥对象；拒绝非 RSA 与携带私钥材料的 JWK。

    同时强制 JWK 的 ``use`` / ``alg`` 约束（RFC 7517 §4.2/§4.3）：
    真实 IdP 常在 JWKS 中混放加密密钥（``use=enc``）或非 RS256 密钥，
    若只按 ``kid`` 取钥，攻击者可用同一 ``kid`` 的加密密钥或其它算法密钥
    诱使本仓以错误用途验签。显式声明且与本仓口径冲突的密钥一律拒绝；
    **未声明**（如 Microsoft MSA 的 JWKS 不返回 ``alg``）仍按 RS256 使用。
    """
    if not isinstance(jwk, Mapping) or jwk.get("kty") != "RSA":
        raise UnauthorizedException(message="JWKS 密钥类型不受支持，已拒绝")
    if "d" in jwk:
        raise UnauthorizedException(message="JWKS 携带私钥材料，已拒绝")
    key_use = jwk.get("use")
    if isinstance(key_use, str) and key_use.strip() and key_use.strip() != "sig":
        raise UnauthorizedException(message="JWKS 密钥用途不是签名，已拒绝")
    key_alg = jwk.get("alg")
    if isinstance(key_alg, str) and key_alg.strip() and key_alg.strip() not in ALLOWED_ALGORITHMS:
        raise UnauthorizedException(message="JWKS 密钥算法不在允许列表，已拒绝")
    modulus = _jwk_base64url_to_int(jwk.get("n"))
    exponent = _jwk_base64url_to_int(jwk.get("e"))
    try:
        return rsa.RSAPublicNumbers(e=exponent, n=modulus).public_key()
    except ValueError:
        raise UnauthorizedException(message="JWKS 公钥参数无效，已拒绝")


def _find_jwk(jwks: Any, kid: str) -> Optional[Mapping[str, Any]]:
    """在 JWKS 中按 kid 精确查找密钥。"""
    if not isinstance(jwks, Mapping):
        return None
    keys = jwks.get("keys")
    if not isinstance(keys, (list, tuple)):
        return None
    for jwk in keys:
        if isinstance(jwk, Mapping) and jwk.get("kid") == kid:
            return jwk
    return None


def _resolve_public_key(kid: str, config: OidcConfig) -> Any:
    """解析 kid 对应公钥。

    先查已有 JWKS；未命中且调用方注入了 ``jwks_fetcher`` 时**受控刷新一次**。
    未知 kid 属密钥轮换信号，必须绕过 fetcher 的 TTL 缓存；因此优先使用
    ``jwks_fetcher.force_refresh()``（若提供），否则退回普通调用。
    fetcher 缺失、抛异常或刷新后仍未命中一律拒绝，并明确禁止盲重试。
    """
    jwk = _find_jwk(config.jwks, kid)
    if jwk is None and config.jwks_fetcher is not None:
        try:
            # 第一跳：走 fetcher 的常规路径（TTL 内命中缓存，不产生外呼）。
            fetched = config.jwks_fetcher()
        except Exception:
            raise UnauthorizedException(message="JWKS 刷新失败，已拒绝令牌")
        jwk = _find_jwk(fetched, kid)
        if jwk is None:
            # 仍未命中即疑似密钥轮换：受控强刷一次（绕过 TTL，带最小间隔限流）。
            force_refresh = getattr(config.jwks_fetcher, "force_refresh", None)
            if callable(force_refresh):
                try:
                    refreshed = force_refresh()
                except Exception:
                    raise UnauthorizedException(message="JWKS 刷新失败，已拒绝令牌")
                jwk = _find_jwk(refreshed, kid)
    if jwk is None:
        raise UnauthorizedException(message="令牌 kid 未受信任，已拒绝")
    return _jwk_to_public_key(jwk)


# ---------------------------------------------------------------------------
# 声明校验
# ---------------------------------------------------------------------------

def _numeric_date(claims: Mapping[str, Any], name: str, *, required: bool) -> Optional[float]:
    """读取 NumericDate 声明并校验类型。"""
    # 判定「键存在性」而非「取值是否为 None」：
    # `nbf: null` 之类「键存在但值非法」的形态必须落到类型检查分支显式拒绝，
    # 若用 `claims.get(name) is None` 会被误当作「声明缺失」而在可选声明上放行。
    if name not in claims:
        if required:
            raise UnauthorizedException(message=f"令牌缺少 {name} 声明，已拒绝")
        return None
    value = claims[name]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise UnauthorizedException(message=f"令牌 {name} 声明类型无效，已拒绝")
    return float(value)


def verify_time_claims(claims: Mapping[str, Any], *, now: float, leeway_seconds: int) -> None:
    """校验 exp / nbf / iat，允许显式时钟容差。"""
    leeway = max(0, int(leeway_seconds))
    expires_at = _numeric_date(claims, "exp", required=True)
    issued_at = _numeric_date(claims, "iat", required=True)
    not_before = _numeric_date(claims, "nbf", required=False)
    if now > expires_at + leeway:
        raise UnauthorizedException(message="令牌已过期，已拒绝")
    if not_before is not None and now < not_before - leeway:
        raise UnauthorizedException(message="令牌尚未生效，已拒绝")
    if now < issued_at - leeway:
        raise UnauthorizedException(message="令牌签发时间在未来，已拒绝")


def verify_issuer(claims: Mapping[str, Any], config: OidcConfig) -> None:
    """校验 iss 与配置的 issuer 完全一致。"""
    if not config.issuer or claims.get("iss") != config.issuer:
        raise UnauthorizedException(message="iss 校验失败，已拒绝令牌")


def verify_audience(claims: Mapping[str, Any], config: OidcConfig) -> None:
    """校验 aud 包含配置的 audience，支持字符串或字符串数组。"""
    if not config.audience:
        raise UnauthorizedException(message="aud 配置缺失，已拒绝令牌")
    audience = claims.get("aud")
    if isinstance(audience, str):
        matched = audience == config.audience
    elif isinstance(audience, (list, tuple)):
        matched = config.audience in [item for item in audience if isinstance(item, str)]
    else:
        matched = False
    if not matched:
        raise UnauthorizedException(message="aud 校验失败，已拒绝令牌")


def verify_authorized_party(claims: Mapping[str, Any], config: OidcConfig) -> None:
    """校验授权方 ``azp``（OIDC Core 1.0 §3.1.3.7 规则 4 与 5）。

    规则：
    - ``aud`` 为**多个**取值时，必须存在 ``azp``；缺失即拒绝
      （否则令牌可能被签发给另一个客户端却在本客户端被接受）。
    - ``azp`` 存在时必须与配置的 ``client_id``（``GW_OIDC_CLIENT_ID``）完全一致。
    - ``aud`` 为单值时 ``azp`` 可省略；若存在则同样必须与本客户端一致。

    ``aud`` 与 ``azp`` 是不同语义：前者是令牌受众，后者是授权客户端。
    即使部署上两者通常相同，也必须使用独立的 ``GW_OIDC_CLIENT_ID`` 配置，
    避免因两项配置漂移而错误接受跨客户端令牌。
    """
    audience = claims.get("aud")
    multi_audience = isinstance(audience, (list, tuple)) and len(audience) > 1
    # 存在性判定必须用 ``in``：`azp: null` 属于「存在但非法」，
    # 若用 ``claims.get("azp") is None`` 会被误当作「azp 缺失」而放行（fail-open）。
    if "azp" not in claims:
        if multi_audience:
            raise UnauthorizedException(message="多 audience 令牌缺少 azp，已拒绝")
        return
    azp = claims["azp"]
    if not isinstance(azp, str) or not azp.strip():
        raise UnauthorizedException(message="azp 声明类型无效，已拒绝")
    if not config.client_id or azp.strip() != config.client_id:
        raise UnauthorizedException(message="azp 校验失败，已拒绝令牌")


def _verify_signature(signing_input: bytes, signature: bytes, public_key: Any) -> None:
    """使用 RS256（RSASSA-PKCS1-v1_5 + SHA-256）校验签名。"""
    try:
        public_key.verify(signature, signing_input, padding.PKCS1v15(), hashes.SHA256())
    except InvalidSignature:
        raise UnauthorizedException(message="令牌签名校验失败，已拒绝")
    except ValueError:
        raise UnauthorizedException(message="令牌签名格式无效，已拒绝")


# ---------------------------------------------------------------------------
# 主校验入口
# ---------------------------------------------------------------------------

def verify_jwt(
    token: str,
    config: OidcConfig,
    *,
    now: Optional[float] = None,
    expected_nonce: Optional[str] = None,
) -> OidcIdentity:
    """OIDC 校验入口：任何配置缺失、算法异常或声明不合法一律拒绝。

    默认关闭：``config.enabled`` 为 ``False`` 时直接拒绝，不静默放行。
    """
    if not CRYPTOGRAPHY_AVAILABLE:
        raise UnauthorizedException(message="密码学依赖不可用，已拒绝令牌")
    if not isinstance(config, OidcConfig) or not config.enabled:
        raise UnauthorizedException(message="OIDC 校验未启用，已拒绝令牌")
    if not config.issuer or not config.audience:
        raise UnauthorizedException(message="OIDC 配置缺失，已拒绝令牌")

    header_segment, payload_segment, signature_segment = split_token(token)
    header = decode_header(token)
    claims = decode_claims(token)

    algorithm = header.get("alg")
    if not isinstance(algorithm, str) or not algorithm.strip():
        raise UnauthorizedException(message="令牌缺少 alg，已拒绝")
    normalized_algorithm = algorithm.strip()
    if normalized_algorithm.lower() == "none" or normalized_algorithm.upper().startswith("HS"):
        raise UnauthorizedException(message="检测到算法混淆，已拒绝令牌")
    allowed = frozenset(config.allowed_algorithms or ())
    if normalized_algorithm not in allowed or normalized_algorithm not in ALLOWED_ALGORITHMS:
        raise UnauthorizedException(message="令牌算法不在允许列表，已拒绝")

    kid = header.get("kid")
    if not isinstance(kid, str) or not kid.strip():
        raise UnauthorizedException(message="令牌缺少 kid，已拒绝")
    public_key = _resolve_public_key(kid.strip(), config)

    signature = _b64url_decode(signature_segment)
    signing_input = f"{header_segment}.{payload_segment}".encode("ascii")
    _verify_signature(signing_input, signature, public_key)

    verify_issuer(claims, config)
    verify_audience(claims, config)
    verify_authorized_party(claims, config)
    verify_time_claims(claims, now=now if now is not None else time.time(), leeway_seconds=config.leeway_seconds)
    if expected_nonce is not None:
        verify_nonce(expected_nonce, claims)

    subject = claims.get("sub")
    if not subject:
        raise UnauthorizedException(message="令牌缺少 sub，已拒绝")

    groups = normalize_groups(claims.get(config.groups_claim))
    # 未知组不升级、无组不授权：无任何已知组命中时失败关闭，不静默授予角色。
    role = resolve_role(groups)
    if role is None:
        raise UnauthorizedException(message="令牌组无已授权映射，已拒绝")
    return OidcIdentity(subject=str(subject), role=role, groups=groups, claims=dict(claims))
