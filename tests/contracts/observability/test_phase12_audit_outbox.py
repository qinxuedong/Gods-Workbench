"""真实HTTP治理操作产生outbox，再投递到持久收据，覆盖失败及竞争。"""
import copy
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from gw.api.app import create_app
from gw.asset_registry import repository as repo, audit_sink
from gw.core import storage
from gw.core.errors import CleanroomException

AUTH = {"Authorization": "Bearer cleanroom-test", "X-User-Role": "editor"}
GOV = {**AUTH, "X-User-Role": "governor"}
BASE = "/api/asset-registry"


def source_event(client):
    imported = client.post(BASE + "/assets/import", headers=AUTH, json={"items": [{"name": "审计夹具", "kind": "document"}]})
    assert imported.status_code == 200
    asset_id = imported.json()["assets"][0]["asset_id"]
    deleted = client.delete(BASE + "/assets/" + asset_id, headers=AUTH)
    assert deleted.status_code == 200
    return deleted.json()


def test_http_delete_restore_ack_readback_and_private_receipts():
    with TestClient(create_app()) as client:
        deleted = source_event(client)
        assert repo.state().read()["outbox"][deleted["event_id"]]["state"] == "pending"
        assert client.get(BASE + "/governance/overview", headers=AUTH).status_code == 403
        assert client.post(BASE + "/governance/audit-outbox/reconcile", headers=AUTH).status_code == 403
        response = client.post(BASE + "/governance/audit-outbox/reconcile", headers=GOV)
        assert response.status_code == 200 and response.json()["count"] == 1
        overview = client.get(BASE + "/governance/overview", headers=GOV).json()
        assert overview["outbox"]["replayed"] == 1
        assert overview["audit_receipts"]["count"] == 1
        receipt = overview["audit_receipts"]["items"][0]
        assert receipt["event_id"] == deleted["event_id"]
        assert "actor_key" not in receipt
        assert client.get("/api/download-output?path=asset_audit_receipts.json", headers=AUTH).status_code == 403
        restore = client.post(BASE + "/recycle-bin/" + deleted["recycle_entry_id"] + "/restore", headers=GOV)
        assert restore.status_code == 200
        assert client.post(BASE + "/governance/audit-outbox/reconcile", headers=GOV).json()["count"] == 1
        assert client.post(BASE + "/governance/audit-outbox/reconcile", headers=GOV).json()["count"] == 0
        # 创建新状态对象，验证不是内存ACK。
        receipts = audit_sink.state().read()["receipts"]
        assert len(receipts) == 2
        assert {item["event"]["event_type"] for item in receipts.values()} == {"asset.deleted", "asset.recycle_restored"}
        assert "cleanroom-test" not in json.dumps(receipts)
        before = copy.deepcopy(repo.state().read()["outbox"])
        assert client.delete(BASE + "/assets/missing", headers=AUTH).status_code == 404
        assert repo.state().read()["outbox"] == before


def test_sink_failure_keeps_failed_event_and_retry_same_content(monkeypatch):
    with TestClient(create_app()) as client:
        deleted = source_event(client)
        initial = copy.deepcopy(repo.state().read()["outbox"][deleted["event_id"]])
        original = audit_sink.deliver
        def unavailable(*args):
            raise OSError("敏感路径不应回显")
        monkeypatch.setattr(audit_sink, "deliver", unavailable)
        result = client.post(BASE + "/governance/audit-outbox/reconcile", headers=GOV).json()
        assert result["count"] == 0 and result["data_status"] == "partial" and result["remaining"] == 1
        assert "敏感" not in json.dumps(result)
        monkeypatch.setattr(audit_sink, "deliver", original)
        assert repo.reconcile_audit_outbox()["count"] == 1
        current = repo.state().read()["outbox"][deleted["event_id"]]
        assert current["event"] == initial["event"] and current["digest"] == initial["digest"]
        assert current["attempts"] == 2


def test_ack_then_registry_commit_failure_can_retry_without_duplicate(monkeypatch):
    with TestClient(create_app()) as client:
        deleted = source_event(client)
        original = storage.JsonState.write
        def fail_registry(self, payload):
            if self.namespace == repo.NS_REGISTRY:
                raise OSError("注入源事务提交失败")
            return original(self, payload)
        monkeypatch.setattr(storage.JsonState, "write", fail_registry)
        with pytest.raises(OSError):
            repo.reconcile_audit_outbox()
        assert repo.state().read()["outbox"][deleted["event_id"]]["state"] == "pending"
        first_receipt = copy.deepcopy(audit_sink.state().read()["receipts"])
        assert len(first_receipt) == 1
        monkeypatch.setattr(storage.JsonState, "write", original)
        assert repo.reconcile_audit_outbox()["count"] == 1
        assert audit_sink.state().read()["receipts"] == first_receipt


def test_sink_readback_failure_never_ack(monkeypatch):
    with TestClient(create_app()) as client:
        deleted = source_event(client)
        original = storage.JsonState.read
        count = [0]
        def fail_after_write(self):
            if self.namespace == audit_sink.NAMESPACE:
                count[0] += 1
                if count[0] == 2:
                    raise OSError("注入收据读回失败")
            return original(self)
        monkeypatch.setattr(storage.JsonState, "read", fail_after_write)
        assert repo.reconcile_audit_outbox()["count"] == 0
        assert repo.state().read()["outbox"][deleted["event_id"]]["state"] == "failed"
        monkeypatch.setattr(storage.JsonState, "read", original)
        assert repo.reconcile_audit_outbox()["count"] == 1
        assert len(audit_sink.state().read()["receipts"]) == 1


def test_receipt_same_id_different_content_rejected():
    with TestClient(create_app()) as client:
        deleted = source_event(client)
        repo.reconcile_audit_outbox()
        event = copy.deepcopy(repo.state().read()["outbox"][deleted["event_id"]]["event"])
        event["asset_id"] = "different-asset"
        with pytest.raises(CleanroomException) as failure:
            audit_sink.deliver(event, audit_sink.digest_event(event))
        assert failure.value.code == "AUDIT_RECEIPT_CONFLICT"


def test_concurrent_reconcile_creates_one_receipt_per_real_event():
    with TestClient(create_app()) as client:
        source_event(client)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: repo.reconcile_audit_outbox(), range(2)))
        assert sum(result["count"] for result in results) == 1
        assert len(audit_sink.state().read()["receipts"]) == 1


def test_reconcile_ui_uses_actual_ack_counts_and_prevents_duplicate_clicks():
    """执行生产事件处理函数，部分失败不能显示成全部成功。"""
    import shutil
    import subprocess
    from pathlib import Path
    node = shutil.which("node")
    if not node:
        pytest.skip("Node不可用")
    script = r"""
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const text=fs.readFileSync(process.argv[1],'utf8');
const start=text.indexOf("  q('#btnReconcileAudit')?.addEventListener");
const end=text.indexOf("\n  q('#btnPurgeExpiredCanvases')",start);
let callback,resolveRequest,calls=0,reads=0; const messages=[];
const button={disabled:false,addEventListener(name,fn){callback=fn;}};
vm.runInNewContext(text.slice(start,end),{q:()=>button, api:()=>{calls++;return new Promise(r=>resolveRequest=r)},
 loadOverview:async()=>{reads++}, showToast:(...args)=>messages.push(args),trf:(key,v)=>JSON.stringify(v)});
(async()=>{const pending=callback();assert.equal(button.disabled,true);await callback();assert.equal(calls,1);
 resolveRequest({count:2,remaining:1,data_status:'partial'});await pending;
 assert.equal(reads,1);assert.equal(messages[0][1],true);assert.ok(messages[0][0].includes('2'));assert.ok(messages[0][0].includes('1'));
 assert.equal(button.disabled,false);
 const success=callback();resolveRequest({count:1,remaining:0,data_status:'ok'});await success;
 assert.equal(JSON.parse(messages[1][0]).replayed,1);assert.equal(messages[1][1],false);
})().catch(e=>{console.error(e);process.exitCode=1});
"""
    path = Path(__file__).resolve().parents[3] / "web/js/modules/governance.js"
    result = subprocess.run([node, "-e", script, str(path)], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr


def test_failed_first_batch_cannot_starve_later_healthy_http_event(monkeypatch):
    """101条真实源事件，前100项持续失败，持久游标仍使第101项获得投递。"""
    with TestClient(create_app()) as client:
        imported = client.post(BASE + "/assets/import", headers=AUTH,
                               json={"items": [{"name": f"审计批次{i}", "kind": "document"} for i in range(101)]})
        assert imported.status_code == 200
        events = []
        for asset in imported.json()["assets"]:
            response = client.delete(BASE + "/assets/" + asset["asset_id"], headers=AUTH)
            assert response.status_code == 200
            events.append(response.json()["event_id"])
        original = audit_sink.deliver
        blocked = set(events[:100])
        def selective_failure(event, digest):
            if event["event_id"] in blocked:
                raise OSError("固定坏事件")
            return original(event, digest)
        monkeypatch.setattr(audit_sink, "deliver", selective_failure)
        first = repo.reconcile_audit_outbox()
        assert first["count"] == 0 and len(first["failed"]) == 100
        assert repo.state().read()["outbox_cursor"] == events[99]
        # 换新应用实例，继续读取持久游标，不依赖上一个请求进程的内存队列。
        with TestClient(create_app()) as reopened:
            second = reopened.post(BASE + "/governance/audit-outbox/reconcile", headers=GOV)
        assert second.status_code == 200
        assert second.json()["replayed"] == [events[100]]
        assert second.json()["remaining"] == 100
        assert len(audit_sink.state().read()["receipts"]) == 1
