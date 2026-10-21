"""C3 媒体替身自检：确定性、无网络、数据目录隔离（C3-MD-NONET-01 / C3-MD-ISOLATION-01）。"""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from media_doubles import (
    FIXED_PNG,
    FIXED_PNG_SHA256,
    HEADERS,
    MODES,
    TEST_API_KEY,
    FakeComfy,
    FakeRunningHub,
    NetworkBlocked,
    count_assets,
    output_transport,
)

# 裁定 9c 要求放 docs/fixtures/，但 docs/fixtures/ 受 H03 准入清单逐文件约束
# （tests/hygiene/fixtures/cleanroom_admission_metadata.json + 其哈希钉在 test_cleanroom_hygiene.py，二者均为 C1 冻结文件），
# 不改冻结文件就无法登记，故暂留本目录，待 cut 修订 9c。
SCENARIOS = Path(__file__).resolve().parent / "c3-media-double-scenarios.json"


def test_fixed_png_digest_is_stable():
    assert hashlib.sha256(FIXED_PNG).hexdigest() == FIXED_PNG_SHA256
    assert FIXED_PNG.startswith(b"\x89PNG\r\n\x1a\n")


def _rh_script(mode):
    fake = FakeRunningHub(mode)

    async def run():
        out = []
        for call in (
            lambda: fake.upload_rh_input(FIXED_PNG, "a.png", "image/png", TEST_API_KEY),
            lambda: fake.create_rh_cloud_task(
                None, [], TEST_API_KEY, workflow={"1": {}}
            ),
            lambda: fake.query_rh_task_outputs("fake-rh-task-0001", TEST_API_KEY),
            lambda: fake.query_rh_task_outputs("fake-rh-task-0001", TEST_API_KEY),
        ):
            try:
                out.append(await call())
            except Exception as exc:  # noqa: BLE001 - 记录异常码用于确定性比较
                out.append(getattr(exc, "code", type(exc).__name__))
        return out

    return asyncio.run(run()), fake.names()


@pytest.mark.parametrize("mode", MODES)
def test_rh_double_is_deterministic(mode):
    assert _rh_script(mode) == _rh_script(mode)


def test_rh_double_success_script():
    results, names = _rh_script("success")
    assert results[0] == "fake-rh/input-0001.png"
    assert results[1] == {"taskId": "fake-rh-task-0001"}
    assert results[2]["status"] == "RUNNING"
    assert results[3]["status"] == "SUCCESS"
    assert (
        results[3]["outputs"][0]["fileUrl"] == "https://approved.example/out-0001.png"
    )
    assert names == [
        "upload_rh_input",
        "create_rh_cloud_task",
        "query_rh_task_outputs",
        "query_rh_task_outputs",
    ]


def test_rh_double_timeout_uses_real_error_codes():
    results, _ = _rh_script("submit_timeout")
    assert results[1] == "WORKFLOW_PROVIDER_TIMEOUT"
    results, _ = _rh_script("upload_timeout")
    assert results[0] == "RH_INPUT_UPLOAD_TIMEOUT"


def test_comfy_double_success_script():
    fake = FakeComfy()

    async def run():
        prompt = await fake.submit({"1": {}})
        first = await fake.history(prompt["prompt_id"])
        second = await fake.history(prompt["prompt_id"])
        payload, media = await fake.fetch_output("out-0001.png")
        return prompt, first, second, payload, media

    prompt, first, second, payload, media = asyncio.run(run())
    assert prompt["prompt_id"] == "fake-prompt-0001"
    assert first["found"] is False and second["found"] is True
    assert payload == FIXED_PNG and media == "image/png"


def test_output_transport_only_serves_approved_host():
    async def run():
        async with httpx.AsyncClient(transport=output_transport()) as client:
            ok = await client.get("https://approved.example/out-0001.png")
            with pytest.raises(httpx.ConnectError):
                await client.get("https://evil.example/out.png")
            return ok

    response = asyncio.run(run())
    assert response.content == FIXED_PNG


def test_no_network_guard_blocks_real_http(no_network):
    """C3-MD-NONET-01：替身没覆盖到的真实 HTTP 调用直接失败。"""

    async def run():
        async with httpx.AsyncClient() as client:
            await client.get("https://www.runninghub.cn/")

    with pytest.raises(NetworkBlocked):
        asyncio.run(run())
    with pytest.raises(NetworkBlocked):
        httpx.Client().get("http://fake-comfy.invalid/history/x")
    assert len(no_network) == 2


def test_fake_media_installs_on_module_attributes(fake_media, media_env):
    from gw.god_workflow import comfy_executor, platforms

    assert platforms.create_rh_cloud_task == fake_media.rh.create_rh_cloud_task
    assert platforms.upload_rh_input == fake_media.rh.upload_rh_input
    assert platforms.query_rh_task_outputs == fake_media.rh.query_rh_task_outputs
    assert comfy_executor.submit == fake_media.comfy.submit
    assert comfy_executor.history == fake_media.comfy.history


def test_scenario_catalog_lists_every_fixture_id():
    catalog = json.loads(SCENARIOS.read_text(encoding="utf-8"))
    ids = [item["fixture_id"] for item in catalog["scenarios"]]
    assert len(ids) == len(set(ids))
    for item in catalog["scenarios"]:
        assert item["mode"] in MODES + ("n/a",)
        assert item["ac"] and item["test"]
    assert "TEST_API_KEY" not in SCENARIOS.read_text(encoding="utf-8")
    assert TEST_API_KEY not in SCENARIOS.read_text(encoding="utf-8")
