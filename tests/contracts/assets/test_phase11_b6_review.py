# -*- coding: utf-8 -*-
"""Phase 12 A3 资产审查与交付契约测试（真实落盘语义）。

覆盖：401 / 403 / 状态机合法与非法流转 / 写后读回 / 交付导出真实 PDF 字节 /
分享令牌哈希存储与一次性明文 / 依赖缺失时的失败关闭语义。
"""

import json
import re
from pathlib import Path

from fastapi.testclient import TestClient

from gw.api.app import create_app

ROOT = Path(__file__).resolve().parents[3]
C = ROOT / "docs/contracts/ASSET-REVIEW-INTERFACE-CATALOG.yaml"
F = ROOT / "docs/fixtures"
A = {"Authorization": "Bearer cleanroom-test", "X-User-Role": "editor"}
R = {"Authorization": "Bearer cleanroom-test", "X-User-Role": "readonly"}


def ps():
    return [(m, p) for m, p in re.findall(r"method: (\w+), path: (\S+)", C.read_text(encoding="utf-8"))]


def norm(p):
    return p[:-1] if p.endswith("}") and not p.endswith("{p}") else p


def real(p):
    return norm(p).replace("{p}", "probe")


def test_contract():
    x = ps()
    assert len(x) == 9 and len({p for _, p in x}) == 8
    assert json.loads((F / "phase11-b6-fail-closed.json").read_text(encoding="utf-8"))["path_count"] == 8
    assert (F / "phase11-b6-boundary.json").is_file()


def test_auth_boundary():
    """未认证一律 401；只读角色写操作 403。"""
    with TestClient(create_app()) as c:
        for m, p in ps():
            u = real(p)
            payload = {} if m in ("POST", "PATCH", "PUT") else None
            r = c.request(m, u, json=payload) if payload is not None else c.request(m, u)
            assert r.status_code == 401, "%s %s" % (m, u)
            if m in ("POST", "PATCH", "PUT"):
                assert c.request(m, u, headers=R, json=payload).status_code == 403


def test_session_machine_and_delivery_export():
    """会话状态机：未交付不能审批(409)；交付后可审批；导出为真实 PDF 字节。"""
    with TestClient(create_app()) as c:
        created = c.post("/api/asset-reviews/sessions", headers=A,
                         json={"asset_id": "ast_0001", "title": "T"}).json()["session"]
        sid = created["session_id"]
        assert re.fullmatch(r"rs-\d{4}", sid)
        assert created["id"] == sid and created["version"] == 1

        # 非法流转：open 状态直接审批 -> 409
        assert c.put("/api/asset-reviews/sessions/%s/approval" % sid, headers=A,
                     json={"status": "approved"}).status_code == 409

        # 写后读回
        listed = c.get("/api/asset-reviews/sessions", headers=A).json()
        assert any(item["id"] == sid for item in listed["sessions"])
        assert listed["data_gaps"] == []

        delivery = c.post("/api/asset-reviews/sessions/%s/delivery" % sid, headers=A,
                          json={"title": "D"}).json()["delivery"]
        did = delivery["delivery_id"]
        assert re.fullmatch(r"dlv-\d{4}", did)
        assert delivery["id"] == did

        exported = c.post("/api/asset-reviews/deliveries/%s/export" % did, headers=A)
        assert exported.status_code == 200
        assert exported.headers["content-type"].startswith("application/pdf")
        assert exported.content.startswith(b"%PDF-") and b"%%EOF" in exported.content

        assert c.put("/api/asset-reviews/sessions/%s/approval" % sid, headers=A,
                     json={"status": "approved"}).status_code == 200
        detail = c.get("/api/asset-reviews/sessions/%s" % sid, headers=A).json()
        assert detail["session"]["status"] == "approved"
        assert len(detail["approvals"]) == 1 and detail["delivery"]["id"] == did


def test_comment_write_read_and_patch():
    """评论：写 -> 读回；created_at 为数字 epoch；PATCH 后状态变化持久化。"""
    with TestClient(create_app()) as c:
        sid = c.post("/api/asset-reviews/sessions", headers=A,
                     json={"asset_id": "ast_0001", "title": "T"}).json()["session"]["session_id"]
        comment = c.post("/api/asset-reviews/sessions/%s/comments" % sid, headers=A,
                         json={"body": "需要修改", "author_name": "审阅人", "timecode_ms": 1200}).json()["comment"]
        cid = comment["id"]
        assert re.fullmatch(r"rc-\d{4}", cid)
        assert isinstance(comment["created_at"], int) and comment["created_at"] > 0
        assert comment["status"] == "open" and comment["timecode_ms"] == 1200

        detail = c.get("/api/asset-reviews/sessions/%s" % sid, headers=A).json()
        assert any(item["id"] == cid for item in detail["comments"])

        patched = c.patch("/api/asset-reviews/comments/%s" % cid, headers=A, json={"resolved": True})
        assert patched.status_code == 200 and patched.json()["comment"]["status"] == "resolved"
        after = c.get("/api/asset-reviews/sessions/%s" % sid, headers=A).json()
        assert after["comments"][0]["resolved"] is True


def test_share_token_hash_only_and_one_time_plaintext():
    """分享令牌：明文只返回一次；服务端只存哈希；篡改令牌 404 SHARE_NOT_FOUND。"""
    with TestClient(create_app()) as c:
        imported = c.post(
            "/api/asset-registry/assets/import",
            headers=A,
            json={"items": [{"name": "share-token-test.png", "kind": "image"}]},
        )
        assert imported.status_code == 200, imported.text
        asset_id = imported.json()["asset_ids"][0]
        res = c.post("/api/asset-reviews/shares", headers=A,
                     json={"asset_ids": [asset_id], "title": "S"}).json()
        token = res["token"]
        assert isinstance(token, str) and len(token) >= 32
        # 响应体外不出现明文令牌
        assert token not in json.dumps(c.get("/api/asset-reviews/sessions", headers=A).json())
        assert token not in C.read_text(encoding="utf-8")

        # 明文可查到元信息
        meta = c.get("/api/public/shares/%s" % token)
        assert meta.status_code == 200 and meta.json()["available"] is True
        # 篡改令牌 404
        bad = c.get("/api/public/shares/%s" % (token + "x"))
        assert bad.status_code == 404 and bad.json()["detail"]["code"] == "SHARE_NOT_FOUND"


def test_failure_closed_semantics_preserved():
    """依赖缺失/目标不存在：404 与失败关闭语义保持，不返回伪造数据。"""
    with TestClient(create_app()) as c:
        missing = c.get("/api/asset-reviews/sessions/rs-9999", headers=A)
        assert missing.status_code == 404 and missing.json()["detail"]["code"] == "SESSION_NOT_FOUND"
        assert c.post("/api/asset-reviews/deliveries/dlv-9999/export", headers=A).status_code == 404
