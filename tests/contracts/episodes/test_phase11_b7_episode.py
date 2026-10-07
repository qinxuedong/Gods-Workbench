# -*- coding: utf-8 -*-
"""Phase 12 A3 剧集流水线契约测试（真实落盘 + 阶段状态机）。

覆盖：401 / 403 / 4 阶段建立 / 状态机合法与非法流转 / CAS 冲突 / 写后读回 /
删除语义（无删除接口，用取消终态代替） / 不触发真实渲染的证据边界。
"""

import json
import re
from pathlib import Path

from fastapi.testclient import TestClient

from gw.api.app import create_app

ROOT = Path(__file__).resolve().parents[3]
C = ROOT / "docs/contracts/EPISODE-PIPELINE-INTERFACE-CATALOG.yaml"
F = ROOT / "docs/fixtures"
A = {"Authorization": "Bearer cleanroom-test", "X-User-Role": "editor"}
R = {"Authorization": "Bearer cleanroom-test", "X-User-Role": "readonly"}
STAGES = ["script", "assets", "video", "audio_compose"]


def ps():
    return [(m, p) for m, p in re.findall(r"method: (\w+), path: (\S+)", C.read_text(encoding="utf-8"))]


def norm(p):
    return p[:-1] if p.endswith("}") and not p.endswith("{p}") else p


def real(p):
    return norm(p).replace("{p}", "probe")


def test_contract():
    x = ps()
    assert len(x) == 6 and len({p for _, p in x}) == 5
    assert json.loads((F / "phase11-b7-boundary.json").read_text(encoding="utf-8"))["path_count"] == 5
    text = C.read_text(encoding="utf-8")
    assert "不触发任何真实渲染" in text


def test_auth_boundary():
    with TestClient(create_app()) as c:
        for m, p in ps():
            u = real(p)
            payload = {} if m == "POST" else None
            r = c.request(m, u, json=payload) if payload is not None else c.request(m, u)
            assert r.status_code == 401, "%s %s" % (m, u)
            if m == "POST":
                assert c.request(m, u, headers=R, json=payload).status_code == 403


def _create(c, project_id="proj-01"):
    res = c.post("/api/episode-pipelines", headers=A, json={
        "project_id": project_id, "title": "T", "seed": "s",
        "prompt_library_id": "episode",
        "prompt_item_ids": {key: "p-%s" % key for key in STAGES},
    })
    assert res.status_code == 201
    return res.json()


def test_create_list_read_and_stage_order():
    """创建后立刻建立 4 个阶段（按 STAGES 顺序），写后可读回。"""
    with TestClient(create_app()) as c:
        created = _create(c)
        pid = created["pipeline_id"]
        assert re.fullmatch(r"ep-\d{4}", pid)
        assert [s["stage"] for s in created["stages"]] == STAGES
        for stage in created["stages"]:
            assert re.fullmatch(r"stg-\d{4}", stage["stage_id"])
            assert stage["status"] == "pending"
            assert stage["result"] == {}
        assert all(s["prompt_item_id"] == "p-%s" % s["stage"] for s in created["stages"])

        listed = c.get("/api/episode-pipelines?project_id=proj-01", headers=A).json()
        assert any(item["pipeline_id"] == pid for item in listed["pipelines"])
        assert listed["data_gaps"] == []

        detail = c.get("/api/episode-pipelines/%s" % pid, headers=A).json()
        assert detail["pipeline"]["pipeline_id"] == pid
        assert len(detail["pipeline"]["stages"]) == 4


def test_stage_state_machine_and_cas():
    """pending → running → completed；未启动完成/重复启动/终态再操作 409；CAS 冲突 409。"""
    with TestClient(create_app()) as c:
        pid = _create(c)["pipeline_id"]

        # 未启动直接完成 -> 409
        assert c.post("/api/episode-pipelines/%s/stages/script/complete" % pid,
                      headers=A, json={}).status_code == 409

        started = c.post("/api/episode-pipelines/%s/stages/script/start" % pid, headers=A, json={})
        assert started.status_code == 200 and started.json()["stage"]["status"] == "running"
        version = started.json()["stage"]["version"]

        # 重复启动 -> 409
        assert c.post("/api/episode-pipelines/%s/stages/script/start" % pid,
                      headers=A, json={}).status_code == 409

        # CAS 冲突 -> 409
        conflict = c.post("/api/episode-pipelines/%s/stages/script/cancel" % pid, headers=A,
                          json={"expected_version": version + 5})
        assert conflict.status_code == 409 and conflict.json()["detail"]["code"] == "VERSION_CONFLICT"

        done = c.post("/api/episode-pipelines/%s/stages/script/complete" % pid, headers=A, json={
            "status": "completed", "output_text": "剧本文本", "output_asset_ids": ["ast_0001"],
            "message": "完成",
        })
        assert done.status_code == 200
        stage = done.json()["stage"]
        assert stage["status"] == "completed"
        assert stage["result"]["output_text"] == "剧本文本"
        assert stage["result"]["output_asset_ids"] == ["ast_0001"]

        # 终态再操作 -> 409
        assert c.post("/api/episode-pipelines/%s/stages/script/start" % pid,
                      headers=A, json={}).status_code == 409
        # 写后读回
        after = c.get("/api/episode-pipelines/%s" % pid, headers=A).json()["pipeline"]
        assert after["stages"][0]["status"] == "completed"


def test_cancel_stage_is_terminal_and_visible():
    """取消后状态可读回，且不能再次启动（终态 409）。"""
    with TestClient(create_app()) as c:
        pid = _create(c)["pipeline_id"]
        cancelled = c.post("/api/episode-pipelines/%s/stages/assets/cancel" % pid, headers=A,
                           json={"message": "用户取消"})
        assert cancelled.status_code == 200 and cancelled.json()["stage"]["status"] == "cancelled"
        after = c.get("/api/episode-pipelines/%s" % pid, headers=A).json()["pipeline"]
        assert [s for s in after["stages"] if s["stage"] == "assets"][0]["status"] == "cancelled"
        assert c.post("/api/episode-pipelines/%s/stages/assets/start" % pid,
                      headers=A, json={}).status_code == 409


def test_not_found_semantics():
    with TestClient(create_app()) as c:
        assert c.get("/api/episode-pipelines/ep-9999", headers=A).status_code == 404
        pid = _create(c)["pipeline_id"]
        assert c.post("/api/episode-pipelines/%s/stages/stg-9999/start" % pid,
                      headers=A, json={}).status_code == 404
