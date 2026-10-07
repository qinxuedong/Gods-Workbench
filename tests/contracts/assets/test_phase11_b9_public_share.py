# -*- coding: utf-8 -*-
"""Phase 12 A3 公开分享契约测试（公开无认证 + 限流 + 哈希令牌）。

覆盖：公开端点不需要 401 / 哈希存储与一次性明文 / 口令与次数限制 /
票据校验 / 篡改令牌 404 / 评论审批写后读回。
"""

import json
import re
from pathlib import Path

from fastapi.testclient import TestClient

from gw.api.app import create_app

ROOT = Path(__file__).resolve().parents[3]
C = ROOT / "docs/contracts/PUBLIC-SHARE-INTERFACE-CATALOG.yaml"
F = ROOT / "docs/fixtures"
A = {"Authorization": "Bearer cleanroom-test", "X-User-Role": "editor"}


def ps():
    return [(m, p) for m, p in re.findall(r"method: (\w+), path: (\S+)", C.read_text(encoding="utf-8"))]


def norm(p):
    # 正则会捕获 YAML flow-map 的终止大括号。
    return p[:-1] if p.endswith("}") else p


def real(p, token="bogus-token"):
    return norm(p).replace("{p}", token).replace("{share_token}", token).replace("{asset_id}", "ast_0001")


def _create_asset(c, name="review.png"):
    response = c.post("/api/asset-registry/assets/import", headers=A,
                      json={"items": [{"name": name, "kind": "image"}]})
    assert response.status_code == 200, response.text
    return response.json()["asset_ids"][0]


def _upload_asset(c, name, content, content_type):
    response = c.post("/api/asset-registry/assets/import", headers=A,
                      data={"name": name}, files={"file": (name, content, content_type)})
    assert response.status_code == 200, response.text
    return response.json()["asset_ids"][0]


def _create_share(c, **extra):
    asset_ids = extra.pop("asset_ids", None) or [_create_asset(c)]
    payload = {"asset_ids": asset_ids, "title": "S"}
    payload.update(extra)
    res = c.post("/api/asset-reviews/shares", headers=A, json=payload)
    assert res.status_code == 201
    return res.json()["token"]


def test_contract():
    x = ps()
    assert len(x) == 5 and len({p for _, p in x}) == 5
    fixture = json.loads((F / "phase11-b9-boundary.json").read_text(encoding="utf-8"))
    assert fixture["path_count"] == fixture["method_path_count"] == 5
    text = C.read_text(encoding="utf-8")
    assert "本地分享" in text and "未接公网托管" in text
    assert "X-Share-Ticket" in text and "private, no-store" in text


def test_public_endpoints_need_no_auth_but_unknown_token_is_404():
    """公开端点不返回 401/403；未知令牌返回 404 SHARE_NOT_FOUND。"""
    with TestClient(create_app()) as c:
        for m, p in ps():
            r = c.request(m, real(p), json={"body": "x"})
            assert r.status_code == 404, "%s %s -> %s" % (m, p, r.status_code)
            assert r.json()["detail"]["code"] == "SHARE_NOT_FOUND"


def test_token_hash_only_and_plaintext_once():
    """明文令牌仅在创建响应出现；服务端只存哈希；篡改令牌 404。"""
    with TestClient(create_app()) as c:
        token = _create_share(c)
        assert len(token) >= 32
        assert c.get("/api/public/shares/%s" % token).status_code == 200
        assert c.get("/api/public/shares/%s" % (token + "x")).status_code == 404
        # 再读一次创建响应不可能拿到明文：列表接口不包含 token
        sessions = json.dumps(c.get("/api/asset-reviews/sessions", headers=A).json())
        assert token not in sessions


def test_password_and_max_access_count():
    """口令错误 401；超过最大访问次数 410。"""
    with TestClient(create_app()) as c:
        token = _create_share(c, password="s3cret", max_access_count=1)
        meta = c.get("/api/public/shares/%s" % token).json()
        assert meta["requires_password"] is True
        wrong = c.post("/api/public/shares/%s/access" % token, json={"password": "bad"})
        assert wrong.status_code == 401 and wrong.json()["detail"]["code"] == "SHARE_PASSWORD_REQUIRED"
        ok = c.post("/api/public/shares/%s/access" % token, json={"password": "s3cret"})
        assert ok.status_code == 200
        exhausted = c.post("/api/public/shares/%s/access" % token, json={"password": "s3cret"})
        assert exhausted.status_code == 410 and exhausted.json()["detail"]["code"] == "SHARE_EXPIRED"


def test_ticket_required_for_comment_and_approval():
    """评论/审批必须携带有效票据；缺失 401。"""
    with TestClient(create_app()) as c:
        asset_id = _create_asset(c)
        token = _create_share(c, asset_ids=[asset_id])
        no_ticket = c.post("/api/public/shares/%s/comments" % token, json={"body": "c", "asset_id": asset_id})
        assert no_ticket.status_code == 401 and no_ticket.json()["detail"]["code"] == "SHARE_TICKET_REQUIRED"
        assert c.put("/api/public/shares/%s/approvals" % token,
                     json={"status": "approved", "asset_id": asset_id}).status_code == 401

        ticket = c.post("/api/public/shares/%s/access" % token, json={}).json()["ticket"]
        comment = c.post("/api/public/shares/%s/comments" % token,
                         json={"ticket": ticket, "asset_id": asset_id, "body": "修改建议", "guest_name": "访客"})
        assert comment.status_code == 201
        payload = comment.json()["comment"]
        assert payload["body"] == "修改建议" and isinstance(payload["created_at"], int)

        approval = c.put("/api/public/shares/%s/approvals" % token,
                         json={"ticket": ticket, "asset_id": asset_id, "status": "changes_requested", "note": "再改"})
        assert approval.status_code == 200
        assert approval.json()["approval"]["status"] == "changes_requested"


def test_permissions_enforced():
    """关闭评论能力时，评论/审批返回 403。"""
    with TestClient(create_app()) as c:
        asset_id = _create_asset(c)
        token = _create_share(c, asset_ids=[asset_id], can_comment=False)
        ticket = c.post("/api/public/shares/%s/access" % token, json={}).json()["ticket"]
        assert c.post("/api/public/shares/%s/comments" % token,
                      json={"ticket": ticket, "asset_id": asset_id, "body": "c"}).status_code == 403
        assert c.put("/api/public/shares/%s/approvals" % token,
                     json={"ticket": ticket, "asset_id": asset_id, "status": "approved"}).status_code == 403
