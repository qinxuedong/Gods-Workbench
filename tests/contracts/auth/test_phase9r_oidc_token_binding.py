# -*- coding: utf-8 -*-
"""Phase 9R：OIDC 令牌绑定加固（azp / JWK use·alg）契约测试。

真实外部 IdP 接线（用户裁决第 6 项）在 §9I 只读实测中暴露两条**规范级**缺口：

1. **azp 未校验**（OIDC Core 1.0 §3.1.3.7 规则 4 与 5）：
   ``aud`` 为多值时必须存在 ``azp``；``azp`` 存在时必须与本客户端 ``client_id`` 一致。
   缺失该校验时，签发给**另一个客户端**的 id_token 可在本客户端被接受。
2. **JWK ``use`` / ``alg`` 未约束**（RFC 7517 §4.2 / §4.3）：
   真实 IdP 的 JWKS 常混放加密密钥与非 RS256 密钥；只按 ``kid`` 取钥
   会让错误用途的密钥参与验签判定。

本文件为**纯本地契约测试**（RSA 密钥运行时生成，不落盘、不入库）。
证据边界：本文件**不**执行真实用户登录，**不**证明生产登录可用，
**不**构成生产就绪或发布授权。
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SRC_PATH = REPO_ROOT
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

from gw.core.errors import UnauthorizedException  # noqa: E402
from gw.core import oidc  # noqa: E402

ISSUER = "https://idp.example.test"
AUDIENCE = "gw-client-a"
OTHER_CLIENT = "gw-client-b"
KID = "key-1"
NOW = 1_800_000_000.0


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _int_to_b64url(value: int) -> str:
    length = (value.bit_length() + 7) // 8
    return _b64url(value.to_bytes(length, "big"))


@pytest.fixture(scope="module")
def rsa_keypair():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()


def _jwk(public_key, **overrides) -> dict:
    numbers = public_key.public_numbers()
    data = {
        "kty": "RSA",
        "use": "sig",
        "alg": "RS256",
        "kid": KID,
        "n": _int_to_b64url(numbers.n),
        "e": _int_to_b64url(numbers.e),
    }
    data.update(overrides)
    return data


def make_config(jwk: dict, **overrides) -> oidc.OidcConfig:
    params = dict(
        issuer=ISSUER,
        audience=AUDIENCE,
        client_id=AUDIENCE,
        enabled=True,
        jwks={"keys": [jwk]},
        leeway_seconds=0,
    )
    params.update(overrides)
    return oidc.OidcConfig(**params)


def claims(**overrides) -> dict:
    data = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": "user-001",
        "exp": NOW + 300,
        "nbf": NOW - 60,
        "iat": NOW - 60,
        "groups": ["gw-editor"],
    }
    data.update(overrides)
    return data


def make_token(private_key, claim_set: dict) -> str:
    header = {"alg": "RS256", "typ": "JWT", "kid": KID}
    h = _b64url(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    p = _b64url(json.dumps(claim_set, separators=(",", ":")).encode("utf-8"))
    signing_input = (h + "." + p).encode("ascii")
    signature = private_key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    return h + "." + p + "." + _b64url(signature)


# ---------------------------------------------------------------------------
# azp（OIDC Core 1.0 §3.1.3.7）
# ---------------------------------------------------------------------------

def test_single_audience_without_azp_is_accepted(rsa_keypair):
    """单 audience 且无 azp 属正常形态，必须通过。"""
    private_key, public_key = rsa_keypair
    token = make_token(private_key, claims())
    identity = oidc.verify_jwt(token, make_config(_jwk(public_key)), now=NOW)
    assert identity.subject == "user-001"


def test_matching_azp_is_accepted(rsa_keypair):
    """azp 存在且等于本客户端时必须通过。"""
    private_key, public_key = rsa_keypair
    token = make_token(private_key, claims(azp=AUDIENCE))
    identity = oidc.verify_jwt(token, make_config(_jwk(public_key)), now=NOW)
    assert identity.role == "editor"


def test_mismatched_azp_is_rejected(rsa_keypair):
    """azp 指向另一个客户端时必须拒绝（防跨客户端令牌复用）。"""
    private_key, public_key = rsa_keypair
    token = make_token(private_key, claims(azp=OTHER_CLIENT))
    with pytest.raises(UnauthorizedException):
        oidc.verify_jwt(token, make_config(_jwk(public_key)), now=NOW)


def test_azp_uses_client_id_not_audience(rsa_keypair):
    """aud 与 client_id 不同时，azp 必须按 client_id 校验而非按 audience 放行。"""
    private_key, public_key = rsa_keypair
    client_id = "actual-client-id"
    token = make_token(private_key, claims(azp=client_id))
    identity = oidc.verify_jwt(
        token,
        make_config(_jwk(public_key), client_id=client_id),
        now=NOW,
    )
    assert identity.subject == "user-001"

    audience_matching_token = make_token(private_key, claims(azp=AUDIENCE))
    with pytest.raises(UnauthorizedException, match="azp"):
        oidc.verify_jwt(
            audience_matching_token,
            make_config(_jwk(public_key), client_id=client_id),
            now=NOW,
        )


def test_multi_audience_without_azp_is_rejected(rsa_keypair):
    """多 audience 缺 azp 必须拒绝（OIDC Core 1.0 §3.1.3.7 规则 4）。"""
    private_key, public_key = rsa_keypair
    token = make_token(private_key, claims(aud=[AUDIENCE, OTHER_CLIENT]))
    with pytest.raises(UnauthorizedException):
        oidc.verify_jwt(token, make_config(_jwk(public_key)), now=NOW)


def test_multi_audience_with_matching_azp_is_accepted(rsa_keypair):
    """多 audience 且 azp 命中本客户端时必须通过。"""
    private_key, public_key = rsa_keypair
    token = make_token(private_key, claims(aud=[AUDIENCE, OTHER_CLIENT], azp=AUDIENCE))
    identity = oidc.verify_jwt(token, make_config(_jwk(public_key)), now=NOW)
    assert identity.subject == "user-001"


def test_multi_audience_with_mismatched_azp_is_rejected(rsa_keypair):
    """多 audience 且 azp 指向别的客户端必须拒绝。"""
    private_key, public_key = rsa_keypair
    token = make_token(private_key, claims(aud=[AUDIENCE, OTHER_CLIENT], azp=OTHER_CLIENT))
    with pytest.raises(UnauthorizedException):
        oidc.verify_jwt(token, make_config(_jwk(public_key)), now=NOW)


def test_empty_azp_is_rejected(rsa_keypair):
    """azp 为空串属非法声明，必须拒绝。"""
    private_key, public_key = rsa_keypair
    token = make_token(private_key, claims(azp="   "))
    with pytest.raises(UnauthorizedException):
        oidc.verify_jwt(token, make_config(_jwk(public_key)), now=NOW)


def test_null_azp_is_rejected(rsa_keypair):
    """``azp`` 键存在但值为 ``null`` 时必须拒绝（不得被误当作「缺失」放行）。

    回归缺陷：原实现用 ``azp = claims.get("azp"); if azp is None: ... return``，
    于是 ``"azp": null`` 走「azp 缺失」分支，在单 ``aud`` 场景被**放行**（fail-open）。
    正确判定必须用 ``"azp" in claims`` 区分「缺失」与「存在但非法」。
    """
    private_key, public_key = rsa_keypair
    payload = claims(azp=None)
    assert "azp" in payload, "用例前提：azp 键必须存在于载荷中（值为 null）"
    token = make_token(private_key, payload)
    with pytest.raises(UnauthorizedException, match="azp"):
        oidc.verify_jwt(token, make_config(_jwk(public_key)), now=NOW)


def test_multi_audience_with_null_azp_is_rejected(rsa_keypair):
    """多 ``aud`` 且 ``azp: null`` 同样必须拒绝（不得因类型异常绕过）。"""
    private_key, public_key = rsa_keypair
    token = make_token(private_key, claims(aud=[AUDIENCE, OTHER_CLIENT], azp=None))
    with pytest.raises(UnauthorizedException, match="azp"):
        oidc.verify_jwt(token, make_config(_jwk(public_key)), now=NOW)


# ---------------------------------------------------------------------------
# JWK use / alg（RFC 7517 §4.2 / §4.3）
# ---------------------------------------------------------------------------

def test_encryption_key_is_rejected(rsa_keypair):
    """use=enc 的密钥不得参与签名验签。"""
    private_key, public_key = rsa_keypair
    token = make_token(private_key, claims())
    with pytest.raises(UnauthorizedException):
        oidc.verify_jwt(token, make_config(_jwk(public_key, use="enc")), now=NOW)


def test_non_rs256_alg_key_is_rejected(rsa_keypair):
    """JWK 显式声明非 RS256 算法时必须拒绝。"""
    private_key, public_key = rsa_keypair
    token = make_token(private_key, claims())
    with pytest.raises(UnauthorizedException):
        oidc.verify_jwt(token, make_config(_jwk(public_key, alg="RSA-OAEP")), now=NOW)


def test_key_without_use_or_alg_still_works(rsa_keypair):
    """未声明 use / alg 的密钥仍按 RS256 使用（Microsoft MSA JWKS 的实际形态）。"""
    private_key, public_key = rsa_keypair
    jwk = _jwk(public_key)
    jwk.pop("use")
    jwk.pop("alg")
    token = make_token(private_key, claims())
    identity = oidc.verify_jwt(token, make_config(jwk), now=NOW)
    assert identity.subject == "user-001"
# ---------------------------------------------------------------------------
# NumericDate 可选声明（nbf）：键存在性 vs 取值 None
# ---------------------------------------------------------------------------

def test_null_nbf_is_rejected(rsa_keypair):
    """nbf 键存在但值为 null 属非法形态，必须显式拒绝。

    回归 _numeric_date 的 ``claims.get(name) is None`` fail-open 反模式：
    nbf 为可选声明，旧实现把 nbf: null 误当作「未声明」而静默放行。
    """
    private_key, public_key = rsa_keypair
    token = make_token(private_key, claims(nbf=None))
    with pytest.raises(UnauthorizedException, match="nbf"):
        oidc.verify_jwt(token, make_config(_jwk(public_key)), now=NOW)


def test_missing_optional_nbf_is_accepted(rsa_keypair):
    """真正缺失（键不存在）的 nbf 仍属合法可选形态，必须放行。"""
    private_key, public_key = rsa_keypair
    payload = claims()
    payload.pop("nbf")
    token = make_token(private_key, payload)
    identity = oidc.verify_jwt(token, make_config(_jwk(public_key)), now=NOW)
    assert identity.subject == "user-001"
