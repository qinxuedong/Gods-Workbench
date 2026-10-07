"""外部 IdP 影子校验模块契约测试（Phase 1，不接线）。

测试内的 RSA 密钥全部在运行时生成，绝不落盘、绝不入库。覆盖：
- 正向：RS256 有效令牌、组到角色映射（含多组取最高、未知组不升级）；
- 负向（>= 6 条）：过期、错误 iss、错误 aud、未知 kid、alg 混淆、未知组，
  另含默认关闭、签名篡改、nbf/iat 异常、nonce/state 失败、JWKS 刷新失败等。

证据边界：本文件只证明本地影子校验模块的行为，不代表 OIDC 已启用、
生产就绪或发布授权。
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


# ---------------------------------------------------------------------------
# 测试内运行时生成密钥与令牌工厂（不落盘）
# ---------------------------------------------------------------------------

ISSUER = "https://idp.example.test"
AUDIENCE = "gods-workbench"
KID = "test-key-1"
NOW = 1_800_000_000.0


def _b64url(raw: bytes) -> str:
    """Base64URL 编码（无填充）。"""
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _int_to_b64url(value: int) -> str:
    """大整数转 Base64URL（JOSE 约定，最短长度）。"""
    length = (value.bit_length() + 7) // 8
    return _b64url(value.to_bytes(length, "big"))


@pytest.fixture(scope="module")
def rsa_keypair():
    """运行时生成 RSA 密钥对（仅内存，不落盘）。"""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()


@pytest.fixture(scope="module")
def jwks(rsa_keypair) -> dict:
    """由运行时公钥生成 JWKS（仅内存）。"""
    _, public_key = rsa_keypair
    numbers = public_key.public_numbers()
    return {
        "keys": [
            {
                "kty": "RSA",
                "use": "sig",
                "alg": "RS256",
                "kid": KID,
                "n": _int_to_b64url(numbers.n),
                "e": _int_to_b64url(numbers.e),
            }
        ]
    }


def make_config(jwks: dict, **overrides) -> oidc.OidcConfig:
    """构造默认启用、配置完整的影子校验配置。"""
    params = dict(
        issuer=ISSUER,
        audience=AUDIENCE,
        client_id=AUDIENCE,
        enabled=True,
        jwks=jwks,
        leeway_seconds=0,
    )
    params.update(overrides)
    return oidc.OidcConfig(**params)


def default_claims(**overrides) -> dict:
    """构造默认合法的声明集合。"""
    claims = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": "user-001",
        "exp": NOW + 300,
        "nbf": NOW - 60,
        "iat": NOW - 60,
        "nonce": "nonce-abc",
        "groups": ["gw-editor"],
    }
    claims.update(overrides)
    return claims


def make_token(private_key, claims: dict, *, alg: str = "RS256", kid: str = KID) -> str:
    """在测试内签发 RS256 JWT（仅内存）。"""
    header = {"alg": alg, "typ": "JWT", "kid": kid}
    header_segment = _b64url(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    payload_segment = _b64url(json.dumps(claims, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{header_segment}.{payload_segment}".encode("ascii")
    signature = private_key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    return f"{header_segment}.{payload_segment}.{_b64url(signature)}"


def make_unsigned_token(claims: dict) -> str:
    """构造 alg=none 的无签名令牌（用于算法混淆负向用例）。"""
    header_segment = _b64url(json.dumps({"alg": "none", "typ": "JWT", "kid": KID}).encode("utf-8"))
    payload_segment = _b64url(json.dumps(claims, separators=(",", ":")).encode("utf-8"))
    return f"{header_segment}.{payload_segment}."


# ---------------------------------------------------------------------------
# 正向用例
# ---------------------------------------------------------------------------

def test_valid_token_returns_identity(rsa_keypair, jwks):
    """正向：合法 RS256 令牌通过校验并返回身份。"""
    private_key, _ = rsa_keypair
    token = make_token(private_key, default_claims())
    identity = oidc.verify_jwt(token, make_config(jwks), now=NOW)
    assert identity.subject == "user-001"
    assert identity.role == "editor"
    assert identity.groups == ("gw-editor",)


def test_valid_token_with_expected_nonce(rsa_keypair, jwks):
    """正向：期望 nonce 与令牌声明一致时通过。"""
    private_key, _ = rsa_keypair
    token = make_token(private_key, default_claims())
    identity = oidc.verify_jwt(token, make_config(jwks), now=NOW, expected_nonce="nonce-abc")
    assert identity.role == "editor"


def test_group_mapping_takes_highest_authorized_role(rsa_keypair, jwks):
    """正向：多组命中时取最高已授权等级，不做隐式升级。"""
    private_key, _ = rsa_keypair
    token = make_token(private_key, default_claims(groups=["gw-readonly", "gw-admin", "gw-editor"]))
    identity = oidc.verify_jwt(token, make_config(jwks), now=NOW)
    assert identity.role == "admin"


def test_group_mapping_is_case_insensitive(rsa_keypair, jwks):
    """正向：组名大小写规范化后匹配。"""
    private_key, _ = rsa_keypair
    token = make_token(private_key, default_claims(groups=["GW-Governor"]))
    identity = oidc.verify_jwt(token, make_config(jwks), now=NOW)
    assert identity.role == "governor"


def test_jwks_refresh_on_unknown_kid(rsa_keypair, jwks):
    """正向：未知 kid 时通过注入的 fetcher 受控刷新一次并命中。"""
    private_key, _ = rsa_keypair
    token = make_token(private_key, default_claims(), kid="rotated-key")
    rotated = {
        "keys": [
            {"kty": "RSA", "use": "sig", "alg": "RS256", "kid": "rotated-key",
             "n": jwks["keys"][0]["n"], "e": jwks["keys"][0]["e"]}
        ]
    }
    calls = {"count": 0}

    def fetcher():
        calls["count"] += 1
        return rotated

    config = make_config(jwks, jwks_fetcher=fetcher)
    identity = oidc.verify_jwt(token, config, now=NOW)
    assert identity.role == "editor"
    assert calls["count"] == 1


def test_time_claims_within_leeway_pass(rsa_keypair, jwks):
    """正向：时钟容差内的 exp/nbf/iat 通过。"""
    private_key, _ = rsa_keypair
    claims = default_claims(exp=NOW - 10, nbf=NOW + 10, iat=NOW + 10)
    token = make_token(private_key, claims)
    identity = oidc.verify_jwt(token, make_config(jwks, leeway_seconds=120), now=NOW)
    assert identity.role == "editor"


# ---------------------------------------------------------------------------
# 负向用例（> 6 条）
# ---------------------------------------------------------------------------

def test_expired_token_rejected(rsa_keypair, jwks):
    """负向：过期令牌必须拒绝。"""
    private_key, _ = rsa_keypair
    token = make_token(private_key, default_claims(exp=NOW - 3600))
    with pytest.raises(UnauthorizedException):
        oidc.verify_jwt(token, make_config(jwks), now=NOW)


def test_wrong_issuer_rejected(rsa_keypair, jwks):
    """负向：错误 iss 必须拒绝。"""
    private_key, _ = rsa_keypair
    token = make_token(private_key, default_claims(iss="https://evil.example.test"))
    with pytest.raises(UnauthorizedException):
        oidc.verify_jwt(token, make_config(jwks), now=NOW)


def test_wrong_audience_rejected(rsa_keypair, jwks):
    """负向：错误 aud 必须拒绝。"""
    private_key, _ = rsa_keypair
    token = make_token(private_key, default_claims(aud="another-client"))
    with pytest.raises(UnauthorizedException):
        oidc.verify_jwt(token, make_config(jwks), now=NOW)


def test_unknown_kid_without_fetcher_rejected(rsa_keypair, jwks):
    """负向：未知 kid 且无 fetcher 时必须拒绝。"""
    private_key, _ = rsa_keypair
    token = make_token(private_key, default_claims(), kid="missing-key")
    with pytest.raises(UnauthorizedException):
        oidc.verify_jwt(token, make_config(jwks), now=NOW)


def test_alg_none_rejected(rsa_keypair, jwks):
    """负向：alg=none 必须拒绝（算法混淆红线）。"""
    token = make_unsigned_token(default_claims())
    with pytest.raises(UnauthorizedException):
        oidc.verify_jwt(token, make_config(jwks), now=NOW)


def test_unknown_group_rejected(rsa_keypair, jwks):
    """负向：未知组不升级且失败关闭，必须拒绝。"""
    private_key, _ = rsa_keypair
    token = make_token(private_key, default_claims(groups=["gw-superuser"]))
    with pytest.raises(UnauthorizedException):
        oidc.verify_jwt(token, make_config(jwks), now=NOW)


def test_missing_groups_rejected(rsa_keypair, jwks):
    """负向：无任何已授权组时必须拒绝。"""
    private_key, _ = rsa_keypair
    claims = default_claims()
    claims.pop("groups")
    token = make_token(private_key, claims)
    with pytest.raises(UnauthorizedException):
        oidc.verify_jwt(token, make_config(jwks), now=NOW)


def test_disabled_config_rejects(rsa_keypair, jwks):
    """负向：默认关闭时一律拒绝，不静默放行。"""
    private_key, _ = rsa_keypair
    token = make_token(private_key, default_claims())
    with pytest.raises(UnauthorizedException):
        oidc.verify_jwt(token, make_config(jwks, enabled=False), now=NOW)


def test_tampered_signature_rejected(rsa_keypair, jwks):
    """负向：篡改签名必须拒绝。"""
    private_key, _ = rsa_keypair
    token = make_token(private_key, default_claims())
    header, payload, signature = token.split(".")
    # 篡改必须**确定性**改变签名字节：直接翻转其首字节的一个比特位。
    # 不能把末尾两字符替换成固定字面量：Base64URL 末位存在同值别名
    # （例如 "xw" 与 "xx" 仅差填充位、解码后字节完全相同），
    # 那种写法约 1/256 概率篡改失效，会让本用例变成伪失败。
    tampered_raw = bytearray(base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4)))
    tampered_raw[0] ^= 0x01
    tampered_signature = base64.urlsafe_b64encode(bytes(tampered_raw)).rstrip(b"=").decode("ascii")
    assert tampered_signature != signature, "篡改失败：签名字节未发生变化"
    tampered = f"{header}.{payload}.{tampered_signature}"
    with pytest.raises(UnauthorizedException):
        oidc.verify_jwt(tampered, make_config(jwks), now=NOW)


def test_not_yet_valid_token_rejected(rsa_keypair, jwks):
    """负向：nbf 在未来且超出容差必须拒绝。"""
    private_key, _ = rsa_keypair
    token = make_token(private_key, default_claims(nbf=NOW + 3600))
    with pytest.raises(UnauthorizedException):
        oidc.verify_jwt(token, make_config(jwks), now=NOW)


def test_nonce_mismatch_rejected(rsa_keypair, jwks):
    """负向：nonce 不匹配必须拒绝。"""
    private_key, _ = rsa_keypair
    token = make_token(private_key, default_claims())
    with pytest.raises(UnauthorizedException):
        oidc.verify_jwt(token, make_config(jwks), now=NOW, expected_nonce="nonce-other")


def test_state_mismatch_rejected():
    """负向：state 不匹配必须拒绝回调。"""
    with pytest.raises(UnauthorizedException):
        oidc.verify_state("state-expected", "state-attacker")


def test_jwks_fetcher_failure_rejected(rsa_keypair, jwks):
    """负向：JWKS 刷新异常必须失败关闭，不静默放行。"""
    private_key, _ = rsa_keypair
    token = make_token(private_key, default_claims(), kid="rotated-key")

    def broken_fetcher():
        raise RuntimeError("模拟 IdP 不可用")

    with pytest.raises(UnauthorizedException):
        oidc.verify_jwt(token, make_config(jwks, jwks_fetcher=broken_fetcher), now=NOW)


def test_jwks_with_private_material_rejected(rsa_keypair, jwks):
    """负向：JWKS 携带私钥材料（d）必须拒绝。"""
    private_key, _ = rsa_keypair
    token = make_token(private_key, default_claims())
    poisoned = {"keys": [dict(jwks["keys"][0], d="AAAA")]}
    with pytest.raises(UnauthorizedException):
        oidc.verify_jwt(token, make_config(poisoned), now=NOW)


def test_hs256_confusion_rejected(rsa_keypair, jwks):
    """负向：HS256 算法混淆必须拒绝（即使密钥存在）。"""
    private_key, _ = rsa_keypair
    token = make_token(private_key, default_claims(), alg="HS256")
    with pytest.raises(UnauthorizedException):
        oidc.verify_jwt(token, make_config(jwks), now=NOW)


def test_pkce_pair_uses_s256():
    """正向：PKCE 生成 S256 挑战，verifier 与 challenge 匹配。"""
    verifier, challenge = oidc.generate_pkce_pair()
    import hashlib

    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert challenge == expected


def test_authorization_url_contains_pkce_and_no_secret():
    """正向：授权 URL 携带 S256 与 state/nonce，且不含客户端密钥。"""
    verifier, challenge = oidc.generate_pkce_pair()
    url = oidc.build_authorization_url(
        authorization_endpoint="https://idp.example.test/authorize",
        client_id="gods-workbench",
        redirect_uri="https://app.example.test/callback",
        state=oidc.generate_state(),
        nonce=oidc.generate_nonce(),
        code_challenge=challenge,
    )
    assert "code_challenge_method=S256" in url
    assert "response_type=code" in url
    assert "client_secret" not in url