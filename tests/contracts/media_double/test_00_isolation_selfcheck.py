"""C3 媒体替身隔离自检（C3-MD-ISOLATION-01）。

计划 §7 第 2 条要求“开工第一条用例是隔离自检”。本文件名以 test_00_ 开头，
pytest 按文件名排序收集，因此它是 tests/contracts/media_double/ 下收集顺序的第一条用例。
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from media_doubles import FIXED_PNG, HEADERS, count_assets


def _isolated_env(monkeypatch, root):
    """与 media_env 一致：数据目录、本地认证库、视频数据目录全部指向 tmp。"""
    monkeypatch.setenv("GW_RUNTIME_MODE", "test")
    monkeypatch.setenv("GW_AUTH_MODE", "local")
    monkeypatch.setenv("GW_DATA_DIR", str(root / "data"))
    monkeypatch.setenv("GW_LOCAL_AUTH_DB", str(root / "auth.sqlite3"))
    monkeypatch.setenv("GW_VIDEO_DATA_DIR", str(root / "video"))


def _register_output(client, tmp_name):
    from gw.god_workflow import asset_hub_bridge, registry, routes

    context = routes._auth(HEADERS["Authorization"], "editor", True)
    asset = registry.store_bytes(context, FIXED_PNG, tmp_name, "image/png")
    result = asset_hub_bridge.register_output_to_asset_hub(asset, context=context)
    assert not result.get("error"), result
    return asset["asset_id"]


def test_asset_stores_are_isolated_between_data_dirs(tmp_path, monkeypatch, no_network):
    """C3-MD-ISOLATION-01：两个数据目录的资产数互不串（计划 §7 第 2 条：开工第一条用例）。"""
    from gw.api.app import create_app

    _isolated_env(monkeypatch, tmp_path / "one")
    with TestClient(create_app()) as client:
        before = count_assets(client)
        asset_id = _register_output(client, "isolation.png")
        one = count_assets(client)
    assert one["registry"] == before["registry"] + 1
    assert one["library_image"] == before["library_image"] + 1
    assert asset_id in one["registry_ids"] and asset_id in one["library_image_ids"]

    _isolated_env(monkeypatch, tmp_path / "two")
    with TestClient(create_app()) as client:
        two = count_assets(client)
    assert asset_id not in two["registry_ids"]
    assert asset_id not in two["library_image_ids"]
    assert (
        two["registry"] == before["registry"]
        and two["library_image"] == before["library_image"]
    )
