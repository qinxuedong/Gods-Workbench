"""QIN-553：预置 LUT 包内置登记契约（首次注入 / 幂等 / 隐藏 / 删除保护）。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from gw.asset_registry import builtin_luts

BASE = "/api/asset-registry"
AUTH = {"Authorization": "Bearer cleanroom-test", "X-User-Role": "editor"}
EDITOR = {"Authorization": "Bearer cleanroom-test", "X-User-Role": "editor"}

BUILTIN_LUT_COUNT = 5


def _revision(client: TestClient) -> int:
    response = client.get(f"{BASE}/status", headers=AUTH)
    assert response.status_code == 200, response.text
    return int(response.json()["revision"])


def test_ensure_registers_builtin_luts_once(client: TestClient):
    """AC1/AC4：干净环境首次 ensure 全部登记为 kind=lut，重复调用零新增（AC3）。"""
    first = client.post(f"{BASE}/builtin-luts/ensure", headers=EDITOR)
    assert first.status_code == 200, first.text
    body = first.json()
    assert len(body["created"]) == BUILTIN_LUT_COUNT
    assert body["existing"] == []

    second = client.post(f"{BASE}/builtin-luts/ensure", headers=EDITOR)
    assert second.status_code == 200, second.text
    again = second.json()
    assert again["created"] == []
    assert len(again["existing"]) == BUILTIN_LUT_COUNT
    # 幂等：重复 ensure 不推高注册表 revision
    assert again["revision"] == body["revision"]

    listing = client.get(f"{BASE}/assets", headers=AUTH, params={"kind": "lut"})
    assert listing.status_code == 200, listing.text
    assets = listing.json()["assets"]
    assert len(assets) == BUILTIN_LUT_COUNT
    assert {item["kind"] for item in assets} == {"lut"}
    assert all(item["metadata"]["builtin"] is True for item in assets)
    names = sorted(item["name"] for item in assets)
    assert names == sorted(
        spec["file"] for spec in builtin_luts.BUILTIN_LUT_SPECS
    )


def test_builtin_luts_have_license_notes_and_readable_media(client: TestClient):
    """AC2：许可/来源说明落元数据；数据根副本可经媒体端点读取。"""
    assert client.post(f"{BASE}/builtin-luts/ensure", headers=EDITOR).status_code == 200
    listing = client.get(f"{BASE}/assets", headers=AUTH, params={"kind": "lut"})
    for item in listing.json()["assets"]:
        notes = str(item["metadata"].get("notes") or "")
        assert "Apache" in notes and "web/vendor/luts" in notes, item["name"]
        media = client.get(f"{BASE}/assets/{item['id']}/media", headers=AUTH)
        assert media.status_code == 200, (item["name"], media.text)
        assert b"LUT_3D_SIZE" in media.content
    # 分发源与说明文档随仓库存在（不依赖运行数据根）
    for spec in builtin_luts.BUILTIN_LUT_SPECS:
        assert builtin_luts.source_builtin_cube_path(spec["file"]).is_file()
    readme = builtin_luts._package_root() / "web" / "vendor" / "luts" / "README.md"
    assert readme.is_file()
    assert "Apache" in readme.read_text(encoding="utf-8")


def test_builtin_lut_facets_count_and_rootless_visibility(client: TestClient):
    """facets 计入内置 LUT；无来源根不影响列表可见。"""
    assert client.post(f"{BASE}/builtin-luts/ensure", headers=EDITOR).status_code == 200
    facets = client.get(f"{BASE}/facets", headers=AUTH).json()
    lut_count = {item["name"]: item["count"] for item in facets["categories"]}["lut"]
    assert lut_count == BUILTIN_LUT_COUNT
    total = client.get(f"{BASE}/assets", headers=AUTH).json()["total"]
    assert total == BUILTIN_LUT_COUNT


def test_builtin_lut_can_be_recycled_and_restored_but_cannot_be_archived_or_renamed(client: TestClient):
    """QIN-621：内置 LUT 可移入回收站并恢复，ensure 不自动补回；仍禁止归档与物理重命名。"""
    assert client.post(f"{BASE}/builtin-luts/ensure", headers=EDITOR).status_code == 200
    asset_id = "builtin_lut_neutral"
    version = _revision(client)

    archived = client.patch(f"{BASE}/assets/{asset_id}", headers=EDITOR,
                            json={"archived": True, "expected_version": version})
    assert archived.status_code == 403, archived.text
    assert archived.json()["detail"]["code"] == "BUILTIN_ASSET_PROTECTED"

    renamed = client.post(f"{BASE}/assets/{asset_id}/file-operations/rename",
                          headers=EDITOR,
                          json={"new_name": "hacked.cube", "expected_version": version})
    assert renamed.status_code == 403, renamed.text
    assert renamed.json()["detail"]["code"] == "BUILTIN_ASSET_PROTECTED"
    assert client.get(f"{BASE}/status", headers=AUTH).json()["revision"] == version

    deleted = client.request(
        "DELETE", f"{BASE}/assets/{asset_id}", headers=EDITOR,
        json={"expected_version": version},
    )
    assert deleted.status_code == 200, deleted.text
    entry_id = deleted.json()["recycle_entry_id"]

    # 删除后 ensure 不自动补回
    ensure_after = client.post(f"{BASE}/builtin-luts/ensure", headers=EDITOR)
    assert ensure_after.status_code == 200
    assert asset_id in ensure_after.json().get("skipped_deleted", [])
    listing = client.get(f"{BASE}/assets", headers=AUTH, params={"kind": "lut"})
    assert asset_id not in [item["id"] for item in listing.json()["assets"]]

    # 从回收站恢复后重新可见（回收站恢复需要治理权限）
    restored = client.post(
        f"{BASE}/recycle-bin/{entry_id}/restore",
        headers={**EDITOR, "X-User-Role": "admin"},
        json={"expected_version": _revision(client)},
    )
    assert restored.status_code == 200, restored.text
    listing_after = client.get(f"{BASE}/assets", headers=AUTH, params={"kind": "lut"})
    assert asset_id in [item["id"] for item in listing_after.json()["assets"]]


def test_builtin_lut_legacy_hidden_migrated_to_visible(client: TestClient):
    """QIN-621：已取消隐藏开关；历史 hidden=true 在 ensure 迁移时恢复可见，不误当删除。"""
    from gw.asset_registry import repository as repo

    assert client.post(f"{BASE}/builtin-luts/ensure", headers=EDITOR).status_code == 200
    asset_id = "builtin_lut_warm"

    def mark_legacy_hidden(raw):
        raw["assets"][asset_id]["hidden"] = True
        repo._bump(raw)
        return True

    repo.state().mutate(mark_legacy_hidden)

    migrated = client.post(f"{BASE}/builtin-luts/ensure", headers=EDITOR)
    assert migrated.status_code == 200, migrated.text
    assert asset_id in migrated.json().get("migrated_visible", [])

    listing = client.get(f"{BASE}/assets", headers=AUTH, params={"kind": "lut"})
    assert asset_id in [item["id"] for item in listing.json()["assets"]]


def test_hide_rejected_for_all_assets(client: TestClient):
    """QIN-621：隐藏开关已取消，任何资产传 hidden 一律返回 400 INVALID_REQUEST。"""
    created = client.post(f"{BASE}/assets/import", headers=EDITOR,
                          json={"items": [{"name": "user.png", "kind": "image"}]})
    assert created.status_code == 200, created.text
    asset_id = created.json()["asset_ids"][0]
    rejected = client.patch(f"{BASE}/assets/{asset_id}", headers=EDITOR,
                            json={"hidden": True, "expected_version": _revision(client)})
    assert rejected.status_code == 400, rejected.text
    assert rejected.json()["detail"]["code"] == "INVALID_REQUEST"


def test_materialize_fills_missing_copy_without_duplicate(client: TestClient, tmp_path):
    """数据根副本被清理后，再次 ensure 只补文件、不新增登记。"""
    assert client.post(f"{BASE}/builtin-luts/ensure", headers=EDITOR).status_code == 200
    directory = builtin_luts.materialized_builtin_lut_dir()
    victim = directory / "builtin_lut_cool.cube"
    assert victim.is_file()
    victim.unlink()
    again = client.post(f"{BASE}/builtin-luts/ensure", headers=EDITOR).json()
    assert again["created"] == []
    assert victim.is_file()
    listing = client.get(f"{BASE}/assets", headers=AUTH, params={"kind": "lut"})
    assert len(listing.json()["assets"]) == BUILTIN_LUT_COUNT


@pytest.mark.parametrize("slug", ["neutral", "warm", "cool", "high_contrast", "cinematic"])
def test_builtin_ids_parse_as_valid_cubes(slug: str):
    """五个内置文件都必须能过 QIN-552 解析器（防漂移）。"""
    path = builtin_luts.source_builtin_cube_path(f"builtin_lut_{slug}.cube")
    meta = builtin_luts.parse_lut_cube_bytes(path.read_bytes())
    assert meta["sample_count"] == 512
