from __future__ import annotations

import hashlib
import json
import struct
import threading
import zlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gw.api.app import create_app
from gw.asset_review import repository as review_repo

AUTH = {"Authorization": "Bearer cleanroom-test", "X-User-Role": "editor"}
PUBLIC = "/api/public/shares"


def _asset(client: TestClient, name: str = "review.png") -> str:
    response = client.post(
        "/api/asset-registry/assets/import",
        headers=AUTH,
        json={"items": [{"name": name, "kind": "image"}]},
    )
    assert response.status_code == 200, response.text
    return response.json()["asset_ids"][0]


def _share(client: TestClient, asset_ids: list[str], **extra) -> tuple[str, str]:
    response = client.post(
        "/api/asset-reviews/shares",
        headers=AUTH,
        json={"asset_ids": asset_ids, "title": "访客审阅", **extra},
    )
    assert response.status_code == 201, response.text
    data = response.json()
    return data["token"], data["share"]["share_id"]


def _ticket(client: TestClient, token: str, password: str = "") -> str:
    response = client.post(f"{PUBLIC}/{token}/access", json={"password": password})
    assert response.status_code == 200, response.text
    return response.json()["ticket"]


def _png_1x1() -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"\x00\xff\xcc\x00\xff"))
        + chunk(b"IEND", b"")
    )


def _attach_file(client: TestClient, asset_id: str, name: str, data: bytes, root: Path, monkeypatch) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    source = root / name
    source.write_bytes(data)
    monkeypatch.setenv("GW_ALLOWED_ROOTS", str(root))
    response = client.post(
        f"/api/asset-registry/assets/{asset_id}/image-versions",
        headers=AUTH,
        json={"source_path": str(source)},
    )
    assert response.status_code == 200, response.text
    return source


def test_share_readback_isolated_and_private_session_link_survives_restart():
    with TestClient(create_app()) as client:
        asset_id = _asset(client)
        session_result = client.post(
            "/api/asset-reviews/sessions", headers=AUTH,
            json={"asset_id": asset_id, "title": "绑定会话"},
        )
        assert session_result.status_code == 201, session_result.text
        session_id = session_result.json()["session"]["session_id"]
        first_token, first_share_id = _share(client, [asset_id], session_id=session_id)
        isolated_token, _ = _share(client, [asset_id])

        old_ticket = _ticket(client, first_token)
        comment = client.post(
            f"{PUBLIC}/{first_token}/comments",
            json={"ticket": old_ticket, "asset_id": asset_id, "guest_name": "小林", "body": "需要调整高光"},
        )
        assert comment.status_code == 201, comment.text
        approval = client.put(
            f"{PUBLIC}/{first_token}/approvals",
            json={"ticket": old_ticket, "asset_id": asset_id, "guest_name": "小林", "status": "changes_requested", "note": "请调整"},
        )
        assert approval.status_code == 200, approval.text
        assert comment.json()["comment"]["share_id"] == first_share_id
        assert comment.json()["comment"]["session_id"] == session_id
        assert approval.json()["approval"]["session_id"] == session_id

        # 再次访问会替换当前票据，但已持久的 share/asset 记录可真实读回。
        _ticket(client, first_token)
        assert client.post(f"{PUBLIC}/{first_token}/comments", json={
            "ticket": old_ticket, "asset_id": asset_id, "body": "旧票据不得再写",
        }).status_code == 401
        first_readback = client.post(f"{PUBLIC}/{first_token}/access", json={}).json()["assets"][0]["review"]
        isolated_readback = client.post(f"{PUBLIC}/{isolated_token}/access", json={}).json()["assets"][0]["review"]
        assert [item["body"] for item in first_readback["comments"]] == ["需要调整高光"]
        assert [item["status"] for item in first_readback["approvals"]] == ["changes_requested"]
        assert isolated_readback == {"comments": [], "approvals": []}

        private_readback = client.get(f"/api/asset-reviews/sessions/{session_id}", headers=AUTH)
        assert private_readback.status_code == 200
        assert [item["body"] for item in private_readback.json()["comments"]] == ["需要调整高光"]
        assert private_readback.json()["approvals"][0]["status"] == "changes_requested"
        assert private_readback.json()["session"]["status"] == "open"  # 访客审批不推进私有状态机

        # 新 client 对同一 data root 执行重启后读回，令牌不依赖进程内缓存。
        with TestClient(create_app()) as restarted:
            meta = restarted.get(f"{PUBLIC}/{first_token}")
            assert meta.status_code == 200
            restored = restarted.post(f"{PUBLIC}/{first_token}/access", json={})
            assert restored.status_code == 200
            assert restored.json()["assets"][0]["review"]["comments"][0]["body"] == "需要调整高光"


def test_create_share_requires_real_assets_and_matching_session():
    with TestClient(create_app()) as client:
        missing = client.post("/api/asset-reviews/shares", headers=AUTH,
                              json={"asset_ids": ["ast_9999"]})
        assert missing.status_code == 404
        assert missing.json()["detail"]["code"] == "SHARE_ASSET_NOT_FOUND"
        asset_id = _asset(client)
        other_asset = _asset(client, "other.png")
        session_id = client.post("/api/asset-reviews/sessions", headers=AUTH,
                                 json={"asset_id": asset_id}).json()["session"]["session_id"]
        mismatch = client.post("/api/asset-reviews/shares", headers=AUTH,
                               json={"asset_ids": [other_asset], "session_id": session_id})
        assert mismatch.status_code == 400
        assert mismatch.json()["detail"]["code"] == "SHARE_SESSION_ASSET_MISMATCH"

        token, _ = _share(client, [asset_id, other_asset], session_id=session_id)
        ticket = _ticket(client, token)
        comment = client.post(f"{PUBLIC}/{token}/comments", json={
            "ticket": ticket, "asset_id": other_asset, "body": "多资产内未绑定会话的素材仍可评论",
        })
        assert comment.status_code == 201
        assert comment.json()["comment"]["session_id"] is None


def test_share_expiry_formats_and_enforcement(monkeypatch):
    with TestClient(create_app()) as client:
        asset_id = _asset(client)
        no_expiry, _ = _share(client, [asset_id], expires_at=None)
        assert client.get(f"{PUBLIC}/{no_expiry}").status_code == 200
        assert _ticket(client, no_expiry)

        deadline_ms = int(review_repo._unix_time() * 1000) + 10_000
        expiry_token, _ = _share(client, [asset_id], expires_at=deadline_ms)
        stored = next(row for row in review_repo.state().read()["shares"].values()
                      if row["token_hash"] == review_repo._hash_token(expiry_token))
        assert stored["expires_at"].endswith("Z")
        iso_token, _ = _share(client, [asset_id], expires_at="2030-03-04T12:30:00+08:00")
        iso_stored = next(row for row in review_repo.state().read()["shares"].values()
                          if row["token_hash"] == review_repo._hash_token(iso_token))
        assert iso_stored["expires_at"] == "2030-03-04T04:30:00.000Z"

        for bad in (True, 0, -1, 1.5, "2030-03-04T12:30:00", "nonsense"):
            response = client.post("/api/asset-reviews/shares", headers=AUTH,
                                   json={"asset_ids": [asset_id], "expires_at": bad})
            assert response.status_code == 400, (bad, response.text)
            assert response.json()["detail"]["code"] == "INVALID_SHARE_EXPIRY"

        after_deadline = deadline_ms / 1000
        monkeypatch.setattr(review_repo, "_unix_time", lambda: after_deadline)
        for response in (
            client.get(f"{PUBLIC}/{expiry_token}"),
            client.post(f"{PUBLIC}/{expiry_token}/access", json={}),
            client.post(f"{PUBLIC}/{expiry_token}/comments", json={"body": "x", "asset_id": asset_id}),
            client.put(f"{PUBLIC}/{expiry_token}/approvals", json={"status": "approved", "asset_id": asset_id}),
            client.get(f"{PUBLIC}/{expiry_token}/assets/{asset_id}/media"),
        ):
            assert response.status_code == 410, response.text
            assert response.json()["detail"]["code"] == "SHARE_EXPIRED"
            assert response.headers["cache-control"] == "private, no-store"
            assert response.headers["x-content-type-options"] == "nosniff"


def test_public_rate_failures_are_durable_bounded_and_counted_before_body(monkeypatch, tmp_path):
    with TestClient(create_app()) as client:
        asset_id = _asset(client)
        token, _ = _share(client, [asset_id], password="secret")
        monkeypatch.setattr(review_repo, "_unix_time", lambda: 1_800_000_000)
        for _ in range(120):
            response = client.post(f"{PUBLIC}/{token}/access", json={"password": "wrong"})
            assert response.status_code == 401
        assert client.post(f"{PUBLIC}/{token}/access", json={"password": "wrong"}).status_code == 429

        # 同一 data root 上的重建 client 仍看到计数；且超限请求不再解析超大正文。
        with TestClient(create_app()) as restarted:
            blocked = restarted.post(f"{PUBLIC}/{token}/access", content=b"x" * 20000,
                                     headers={"Content-Type": "application/json"})
            assert blocked.status_code == 429
            assert blocked.json()["detail"]["code"] == "RATE_LIMITED"

    monkeypatch.setenv("GW_DATA_DIR", str(tmp_path / "unknown-data"))
    with TestClient(create_app()) as unknown_client:
        monkeypatch.setattr(review_repo, "_unix_time", lambda: 1_800_000_000)
        for i in range(120):
            assert unknown_client.get(f"{PUBLIC}/unknown-{i}").status_code == 404
        limited = unknown_client.get(f"{PUBLIC}/unknown-overflow")
        assert limited.status_code == 429
        rates = review_repo.state().read()["rate"]
        assert len(rates) == 1  # 未知令牌聚合到固定哈希桶，不按令牌无限建桶
        assert all(len(bucket) <= 120 for bucket in rates.values())


def test_public_body_limit_counts_the_rejected_request():
    with TestClient(create_app()) as client:
        asset_id = _asset(client)
        token, _ = _share(client, [asset_id])
        response = client.post(f"{PUBLIC}/{token}/access", content=json.dumps({"x": "y" * 20000}),
                               headers={"Content-Type": "application/json"})
        assert response.status_code == 413
        assert response.headers["cache-control"] == "private, no-store"
        assert response.headers["x-content-type-options"] == "nosniff"
        key = "%s:access" % review_repo._hash_token(token)
        assert len(review_repo.state().read()["rate"][key]) == 1


def test_public_media_auth_mime_range_download_and_hash(tmp_path, monkeypatch):
    with TestClient(create_app()) as client:
        data = _png_1x1()
        asset_id = _asset(client)
        source = _attach_file(client, asset_id, "tiny.png", data, tmp_path / "media", monkeypatch)
        token, _ = _share(client, [asset_id], can_download=True)
        ticket = _ticket(client, token)
        media_url = f"{PUBLIC}/{token}/assets/{asset_id}/media"

        denied = client.get(media_url)
        assert denied.status_code == 401
        assert denied.headers["cache-control"] == "private, no-store"
        assert denied.headers["x-content-type-options"] == "nosniff"

        preview = client.get(media_url, headers={"X-Share-Ticket": ticket})
        assert preview.status_code == 200
        assert preview.headers["content-type"] == "image/png"
        assert preview.headers["cache-control"] == "private, no-store"
        assert preview.headers["x-content-type-options"] == "nosniff"
        assert preview.content == source.read_bytes() == data
        assert hashlib.sha256(preview.content).hexdigest() == hashlib.sha256(data).hexdigest()
        request_url = str(preview.request.url).lower()
        assert "ticket" not in request_url
        assert "authorization" not in request_url

        ranged = client.get(media_url, headers={"X-Share-Ticket": ticket, "Range": "bytes=0-7"})
        assert ranged.status_code == 206
        assert ranged.content == data[:8]
        assert ranged.headers["cache-control"] == "private, no-store"
        assert ranged.headers["x-content-type-options"] == "nosniff"

        download = client.get(media_url + "?download=true", headers={"X-Share-Ticket": ticket})
        assert download.status_code == 200
        assert "attachment" in download.headers.get("content-disposition", "")
        assert download.content == data

        # 活跃 SVG 和未知类型不能作为同源 inline 内容执行。
        svg_id = _asset(client, "active.svg")
        svg_source = _attach_file(client, svg_id, "active.svg", b"<svg onload='alert(1)'></svg>", tmp_path / "media", monkeypatch)
        svg_token, _ = _share(client, [svg_id])
        svg_ticket = _ticket(client, svg_token)
        svg_response = client.get(f"{PUBLIC}/{svg_token}/assets/{svg_id}/media",
                                  headers={"X-Share-Ticket": svg_ticket})
        assert svg_response.status_code == 415
        assert svg_response.headers["cache-control"] == "private, no-store"
        assert svg_source.exists()

        no_download_token, _ = _share(client, [asset_id], can_download=False)
        no_download_ticket = _ticket(client, no_download_token)
        no_download_url = f"{PUBLIC}/{no_download_token}/assets/{asset_id}/media?download=true"
        blocked_download = client.get(no_download_url, headers={"X-Share-Ticket": no_download_ticket})
        assert blocked_download.status_code == 403
        assert blocked_download.json()["detail"]["code"] == "SHARE_DOWNLOAD_FORBIDDEN"


def test_expiry_and_ticket_rotation_are_linearized_against_public_write(monkeypatch):
    with TestClient(create_app()) as client:
        asset_id = _asset(client)
        deadline_ms = int(review_repo._unix_time() * 1000) + 5_000
        token, _ = _share(client, [asset_id], expires_at=deadline_ms)
        ticket = _ticket(client, token)
        clock_values = iter([(deadline_ms - 1) / 1000, deadline_ms / 1000])
        with monkeypatch.context() as clock:
            clock.setattr(review_repo, "_unix_time", lambda: next(clock_values))
            expired_write = client.post(f"{PUBLIC}/{token}/comments", json={
                "ticket": ticket, "asset_id": asset_id, "body": "不得写入",
            })
            assert expired_write.status_code == 410
            assert review_repo.state().read()["comments"] == {}

    with TestClient(create_app()) as client:
        asset_id = _asset(client)
        token, _ = _share(client, [asset_id])
        old_ticket = _ticket(client, token)
        original_json = __import__("gw.api.routes_public_b9", fromlist=["_json"])._json
        barrier = threading.Barrier(2)

        async def synchronized_json(request):
            payload = await original_json(request)
            barrier.wait(timeout=5)
            return payload

        import gw.api.routes_public_b9 as public_routes
        monkeypatch.setattr(public_routes, "_json", synchronized_json)

        def write_comment():
            with TestClient(create_app()) as local:
                return local.post(f"{PUBLIC}/{token}/comments", json={
                    "ticket": old_ticket, "asset_id": asset_id, "body": "竞态评论",
                })

        def rotate_ticket():
            with TestClient(create_app()) as local:
                return local.post(f"{PUBLIC}/{token}/access", json={})

        with ThreadPoolExecutor(max_workers=2) as executor:
            comment_future = executor.submit(write_comment)
            rotate_future = executor.submit(rotate_ticket)
            comment_result = comment_future.result(timeout=10)
            rotate_result = rotate_future.result(timeout=10)

        monkeypatch.setattr(public_routes, "_json", original_json)
        assert rotate_result.status_code == 200, rotate_result.text
        assert comment_result.status_code in (201, 401), comment_result.text
        latest = client.post(f"{PUBLIC}/{token}/access", json={}).json()
        readback = latest["assets"][0]["review"]["comments"]
        assert len(readback) == (1 if comment_result.status_code == 201 else 0)
        if readback:
            assert readback[0]["body"] == "竞态评论"


def test_create_share_frontend_uses_real_one_time_token():
    """执行生产创建分享函数，禁止生成/share/undefined或回退到不存在的secret字段。"""
    import shutil
    import subprocess
    node = shutil.which("node")
    if not node:
        pytest.skip("Node不可用")
    script = r"""
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const source=fs.readFileSync(process.argv[1],'utf8');
const start=source.indexOf('    async function submitShare(form)');
const end=source.indexOf('    async function handleClick',start);
const result={innerHTML:''},errors=[];let payload;
const box={FormData:class{get(k){return ({asset_ids:'ast_0001',title:'真实分享',days:'7'})[k]||''} has(){return false}},
 location:{origin:'http://localhost'},q:()=>result,esc:x=>x,attr:x=>x,icon:()=>'',window:{lucide:{createIcons(){}}},
 toast:(text,error)=>errors.push([text,error]),assetReviewApi:()=>({createReviewShare:p=>{payload=p;return {token:'safe-token_123'}}}),
 reviewJsonResponse:async value=>value};vm.createContext(box);vm.runInContext(source.slice(start,end)+'\nthis.submit=submitShare;',box);
(async()=>{await box.submit({});assert.ok(result.innerHTML.includes('http://localhost/share/safe-token_123'));
 assert.equal(payload.asset_ids[0],'ast_0001');assert.equal(errors.length,0);
 result.innerHTML='';box.reviewJsonResponse=async()=>({});await box.submit({});assert.equal(result.innerHTML,'');assert.equal(errors.length,1);
})().catch(e=>{console.error(e);process.exitCode=1});
"""
    path = Path(__file__).resolve().parents[3] / "web/js/modules/asset-review.js"
    result = subprocess.run([node, "-e", script, str(path)], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
