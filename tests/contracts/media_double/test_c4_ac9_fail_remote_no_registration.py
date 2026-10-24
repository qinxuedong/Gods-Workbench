"""C4-AC9（GitHub issue #11）：ComfyUI 远端失败时，真实路径不得登记任何产物资产。

C3 替身（media_doubles.FakeComfy.fail_remote）在模块函数层替换 comfy_executor，
其 error 条目 outputs 为空，掩盖了真实缺陷：真实 ComfyUI 失败 history 的 outputs
里可能残留已完成节点的输出/错误条目，routes.task_status 会照常下载并登记为资产。
本模块在 HTTP 客户端层（monkeypatch httpx.AsyncClient.send）回放
fixtures/comfyui/history_fail_remote_c4_ac9.json 的失败样例，comfy_executor 与
routes/registry/asset_hub_bridge 全部走真实代码，断言持久化后资产数为 0、
任务如实 failed 并带错误信息，与替身 fail_remote 的可观测行为一致
（C3-MD-FAIL-01）；另用同一替身验证成功路径不受影响。
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from media_doubles import (
    FIXED_PNG,
    HEADERS,
    TASKS,
    NetworkBlocked,
    execute,
    prepare_workflow,
)

from gw.god_workflow import comfy_executor

FIXTURE_PATH = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "comfyui"
    / "history_fail_remote_c4_ac9.json"
)
FIXTURE_ID = "C4-AC9-FAIL-REMOTE-01"
FAIL_PROMPT_ID = "c4ac9-fail-prompt-0001"
OK_PROMPT_ID = "c4ac9-ok-prompt-0001"
COMFY_HOST = "fake-comfy.invalid"


def _load_fixture() -> dict:
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    assert payload["fixture_id"] == FIXTURE_ID, "fixture 必须显式携带 fixture id"
    history = payload["history"]
    entry = history[FAIL_PROMPT_ID]
    assert entry["status"]["status_str"] == "error"
    # 关键前置：真实 summarize 会从该失败条目里摘出输出条目——这正是此前被误登记的缺陷入口。
    assert comfy_executor.summarize(entry)["outputs"], (
        "fixture 的 error 条目必须残留输出"
    )
    return history


class FakeComfyHTTP:
    """httpx.AsyncClient.send 层替身：只回放 fake-comfy.invalid 的官方接口。

    upload/prompt/history/view 全部记录；/view 会返回真实 PNG 字节，
    让"失败仍被下载登记"的旧缺陷在修复前真实成立。其余主机一律拒绝，兜底不联网。
    """

    def __init__(self, history: dict, prompt_id: str):
        self.history = history
        self.prompt_id = prompt_id
        self.calls: list[tuple[str, str]] = []
        self.view_files: list[str] = []

    async def __call__(self, request, *args, **kwargs):
        if request.url.host != COMFY_HOST:
            raise NetworkBlocked(f"C4-AC9 替身禁止真实网络: {request.url.host}")
        path = request.url.path
        self.calls.append((request.method, path))
        if request.method == "POST" and path == "/upload/image":
            return httpx.Response(
                200,
                json={"name": "c4ac9-input-0001.png", "subfolder": ""},
                request=request,
            )
        if request.method == "POST" and path == "/prompt":
            return httpx.Response(
                200,
                json={"prompt_id": self.prompt_id, "number": 1, "node_errors": {}},
                request=request,
            )
        if request.method == "GET" and path == f"/history/{self.prompt_id}":
            if self.calls.count(("GET", path)) == 1:
                return httpx.Response(
                    200, json={}, request=request
                )  # 首轮轮询：尚未入历史
            return httpx.Response(200, json=self.history, request=request)
        if request.method == "GET" and path == "/view":
            self.view_files.append(str(request.url.params.get("filename") or ""))
            return httpx.Response(
                200,
                content=FIXED_PNG,
                headers={"content-type": "image/png"},
                request=request,
            )
        return httpx.Response(
            404, json={"error": f"unexpected fake-comfy path {path}"}, request=request
        )


@pytest.fixture
def comfy_http(monkeypatch):
    fake = FakeComfyHTTP(_load_fixture(), FAIL_PROMPT_ID)
    monkeypatch.setattr(httpx.AsyncClient, "send", fake)
    return fake


@pytest.fixture
def comfy_http_success(monkeypatch):
    history = {
        OK_PROMPT_ID: {
            "prompt": [1, OK_PROMPT_ID, {}, {}, []],
            "outputs": {
                "9": {
                    "images": [
                        {
                            "filename": "c4ac9_ok_00001_.png",
                            "subfolder": "",
                            "type": "output",
                        }
                    ]
                }
            },
            "status": {
                "status_str": "success",
                "completed": True,
                "messages": [
                    ["execution_success", {}],
                    ["progress", {"value": 1, "max": 1}],
                ],
            },
        }
    }
    fake = FakeComfyHTTP(history, OK_PROMPT_ID)
    monkeypatch.setattr(httpx.AsyncClient, "send", fake)
    return fake


def test_fail_remote_error_history_registers_zero_assets(
    media_client, comfy_http, asset_counts
):
    """C4-AC9-FAIL-REMOTE-01：失败 history（残留输出条目）→ 资产 +0、如实 failed 带错误信息。"""
    _, workflow = prepare_workflow(media_client)
    before = asset_counts()
    started = execute(media_client, workflow, "local_comfy", key="c4-ac9-fail")
    assert started.status_code == 202, started.text
    job_id = started.json()["job_id"]
    first = media_client.get(f"{TASKS}/{job_id}", headers=HEADERS)
    assert first.status_code == 200 and first.json()["status"] == "running", first.text
    done = media_client.get(f"{TASKS}/{job_id}", headers=HEADERS).json()
    assert done["status"] == "failed", done
    assert done["execution_status"] == "failed"
    assert done["outputs"] == []
    assert "COMFY_REMOTE_EXECUTION_FAILED" in done["messages"]
    assert any(
        isinstance(line, str) and line.startswith("execution_error node_type=")
        for line in done["messages"]
    ), done["messages"]
    # 一个产物都没下载：history 里残留的输出条目没有触发任何 /view。
    assert comfy_http.view_files == []
    # 持久化后的两个存储都不新增资产，与替身 fail_remote 行为一致（C3-MD-FAIL-01）。
    assert asset_counts() == before
    # 终态稳定：重复轮询与 collect 都不能再把错误条目登记为资产。
    again = media_client.get(f"{TASKS}/{job_id}", headers=HEADERS).json()
    assert again["status"] == "failed" and again["outputs"] == []
    collected = media_client.post(f"{TASKS}/{job_id}/collect", headers=HEADERS)
    assert collected.status_code == 409, collected.text
    assert collected.json()["detail"]["code"] == "EXECUTION_NOT_COMPLETED"
    assert asset_counts() == before


def test_success_history_still_registers_assets(
    media_client, comfy_http_success, asset_counts
):
    """成功路径不受影响：同一 HTTP 层替身下 status_str=success 仍登记 1 份产物。"""
    _, workflow = prepare_workflow(media_client)
    before = asset_counts()
    started = execute(media_client, workflow, "local_comfy", key="c4-ac9-ok")
    assert started.status_code == 202, started.text
    job_id = started.json()["job_id"]
    first = media_client.get(f"{TASKS}/{job_id}", headers=HEADERS)
    assert first.status_code == 200 and first.json()["status"] == "running", first.text
    done = media_client.get(f"{TASKS}/{job_id}", headers=HEADERS).json()
    assert done["status"] == "completed", done
    assert done["collection_status"] == "completed"
    assert len(done["outputs"]) == 1 and done["outputs"][0]["registered"] is True
    after = asset_counts()
    assert after["registry"] == before["registry"] + 1
    assert after["library_image"] == before["library_image"] + 1
    assert done["outputs"][0]["asset_id"] in after["registry_ids"]
