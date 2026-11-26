# Copyright 2026 qinxuedong (Gods-Workbench Authors)
# SPDX-License-Identifier: FSL-1.1-Apache-2.0
#
# Licensed under the Functional Source License, Version 1.1, Apache 2.0
# Future License (the "License"); you may not use this file except in
# compliance with the License. You may obtain a copy of the License at
#     https://fsl.software/
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""QIN-621：资产总量仅图/视频、LUT 统一删除与防补回、user-lut 导入、默认预览静态资源契约测试。"""

import hashlib
from pathlib import Path
from typing import Optional

from fastapi.testclient import TestClient

from gw.api.app import create_app
from gw.asset_registry import builtin_luts

AUTH = {"Authorization": "Bearer cleanroom-test"}
EDITOR = {**AUTH, "X-User-Role": "editor"}
ADMIN = {**AUTH, "X-User-Role": "admin"}
BASE = "/api/asset-registry"

VALID_CUBE = (
    b'TITLE "Test QIN621"\n'
    b"LUT_3D_SIZE 2\n"
    b"0.0 0.0 0.0\n"
    b"1.0 0.0 0.0\n"
    b"0.0 1.0 0.0\n"
    b"1.0 1.0 0.0\n"
    b"0.0 0.0 1.0\n"
    b"1.0 0.0 1.0\n"
    b"0.0 1.0 1.0\n"
    b"1.0 1.0 1.0\n"
)


def _revision(client: TestClient) -> int:
    return int(client.get(f"{BASE}/status", headers=AUTH).json()["revision"])


def _upload_lut(
    client: TestClient,
    filename: str,
    content: bytes,
    *,
    conflict_resolution: str = "ask",
    client_request_id: Optional[str] = None,
):
    data = {
        "target": "user-lut",
        "kind": "lut",
        "conflict_resolution": conflict_resolution,
    }
    if client_request_id:
        data["client_request_id"] = client_request_id
    return client.post(
        f"{BASE}/assets/import",
        headers=EDITOR,
        data=data,
        files={"file": (filename, content, "application/octet-stream")},
    )


def test_ac1_overview_category_filter_excludes_lut_audio_document_across_pages_and_search(
    client: TestClient,
):
    """AC1：category=image,video 在数据源层过滤，翻页/搜索一致且不改 facets 口径。"""
    imported = client.post(
        f"{BASE}/assets/import",
        headers=EDITOR,
        json={
            "items": [
                {"name": "shot_a.png", "kind": "image"},
                {"name": "shot_b.jpg", "kind": "image"},
                {"name": "clip_a.mp4", "kind": "video"},
                {"name": "bgm_a.mp3", "kind": "audio"},
                {"name": "script_a.txt", "kind": "document"},
            ]
        },
    )
    assert imported.status_code == 200, imported.text
    assert client.post(f"{BASE}/builtin-luts/ensure", headers=EDITOR).status_code == 200
    assert _upload_lut(client, "user_grade.cube", VALID_CUBE).status_code == 200

    # 翻页：limit=2 逐页拉取 category=image,video
    page1 = client.get(
        f"{BASE}/assets",
        headers=AUTH,
        params={"category": "image,video", "limit": 2, "offset": 0},
    )
    assert page1.status_code == 200, page1.text
    p1_data = page1.json()
    assert p1_data["total"] == 3
    assert len(p1_data["assets"]) == 2
    assert p1_data["next_cursor"]

    page2 = client.get(
        f"{BASE}/assets",
        headers=AUTH,
        params={
            "category": "image,video",
            "limit": 2,
            "offset": 2,
            "cursor": p1_data["next_cursor"],
        },
    )
    assert page2.status_code == 200, page2.text
    combined = p1_data["assets"] + page2.json()["assets"]
    assert {item["name"] for item in combined} == {"shot_a.png", "shot_b.jpg", "clip_a.mp4"}

    # 搜索一致性
    searched = client.get(
        f"{BASE}/assets",
        headers=AUTH,
        params={"category": "image,video", "search": "shot"},
    )
    assert searched.status_code == 200
    assert {item["name"] for item in searched.json()["assets"]} == {"shot_a.png", "shot_b.jpg"}

    # facets 口径保留各分类
    facets = {item["name"]: item["count"] for item in client.get(f"{BASE}/facets", headers=AUTH).json()["categories"]}
    assert facets["image"] == 2
    assert facets["video"] == 1
    assert facets["audio"] == 1
    assert facets["document"] == 1
    assert facets["lut"] == len(builtin_luts.BUILTIN_LUT_SPECS) + 1

    # 独立 LUT 入口仍只返回 LUT
    lut_list = client.get(f"{BASE}/assets", headers=AUTH, params={"kind": "lut"})
    assert lut_list.status_code == 200
    assert len(lut_list.json()["assets"]) == len(builtin_luts.BUILTIN_LUT_SPECS) + 1

    # 非法分类过滤失败关闭
    bad = client.get(f"{BASE}/assets", headers=AUTH, params={"category": "image,invalid_cat"})
    assert bad.status_code == 400
    assert bad.json()["detail"]["code"] == "UNSUPPORTED_FILTER_VALUE"


def test_ac2_deleted_builtin_lut_survives_cold_restart_without_ensure_recreation(
    client: TestClient,
):
    """AC2：内置 LUT 删除后，即使重建新应用冷读，ensure 也不得自动补回。"""
    assert client.post(f"{BASE}/builtin-luts/ensure", headers=EDITOR).status_code == 200
    victim = "builtin_lut_cool"
    deleted = client.request(
        "DELETE",
        f"{BASE}/assets/{victim}",
        headers=EDITOR,
        json={"expected_version": _revision(client)},
    )
    assert deleted.status_code == 200, deleted.text

    # 模拟冷读新进程
    cold_client = TestClient(create_app())
    ensured = cold_client.post(f"{BASE}/builtin-luts/ensure", headers=EDITOR)
    assert ensured.status_code == 200, ensured.text
    assert victim in ensured.json().get("skipped_deleted", [])
    assert not (builtin_luts.materialized_builtin_lut_dir() / "builtin_lut_cool.cube").exists()

    listing = cold_client.get(f"{BASE}/assets", headers=AUTH, params={"kind": "lut"})
    assert victim not in [item["id"] for item in listing.json()["assets"]]


def test_ac3_import_user_lut_validation_conflict_dedup_and_recycle_restore(
    client: TestClient,
):
    """AC3：user-lut 仅接受合法 .cube，校验冲突/路径穿越/幂等去重，且支持回收与恢复。"""
    # 1. 拒绝非 .cube 或路径穿越或非法内容
    bad_ext = _upload_lut(client, "grade.3dl", VALID_CUBE)
    assert bad_ext.status_code == 400
    assert bad_ext.json()["detail"]["code"] == "INVALID_LUT_FORMAT"

    bad_path = _upload_lut(client, "../escape.cube", VALID_CUBE)
    assert bad_path.status_code == 400
    assert bad_path.json()["detail"]["code"] == "INVALID_LUT_PATH"

    bad_body = _upload_lut(client, "broken.cube", b"NOT A CUBE FILE\n")
    assert bad_body.status_code == 400
    assert bad_body.json()["detail"]["code"] == "LUT_CUBE_INVALID"

    # 2. 合法导入落入 data_root/registry_uploads/user-lut/
    ok = _upload_lut(client, "FilmLook.cube", VALID_CUBE, client_request_id="req_1")
    assert ok.status_code == 200, ok.text
    asset = ok.json()["asset"]
    assert asset["kind"] == "lut"
    assert asset["display_path"].startswith("registry_uploads/user-lut/")
    assert asset["can_recycle"] is True
    assert (builtin_luts.user_lut_dir() / "FilmLook.cube").is_file()

    # 3. 同 client_request_id 重试去重
    dedup = _upload_lut(client, "FilmLook.cube", VALID_CUBE, client_request_id="req_1")
    assert dedup.status_code == 200
    assert dedup.json()["asset"]["asset_id"] == asset["asset_id"]
    assert dedup.json().get("deduplicated") is True

    # 4. 同名冲突：默认 409、rename 生成新名、overwrite 原子覆盖
    conflict = _upload_lut(client, "FilmLook.cube", VALID_CUBE)
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "LUT_NAME_CONFLICT"

    renamed = _upload_lut(client, "FilmLook.cube", VALID_CUBE, conflict_resolution="rename")
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["asset"]["name"] == "FilmLook (1).cube"
    assert renamed.json()["asset"]["asset_id"] != asset["asset_id"]

    overwritten = _upload_lut(client, "FilmLook.cube", VALID_CUBE, conflict_resolution="overwrite")
    assert overwritten.status_code == 200, overwritten.text
    assert overwritten.json()["asset"]["asset_id"] == asset["asset_id"]
    assert overwritten.json()["asset"]["version"] == asset["version"] + 1

    # 5. user-lut 移入回收站并恢复
    deleted = client.request(
        "DELETE",
        f"{BASE}/assets/{asset['asset_id']}",
        headers=EDITOR,
        json={"expected_version": _revision(client)},
    )
    assert deleted.status_code == 200, deleted.text
    assert not (builtin_luts.user_lut_dir() / "FilmLook.cube").exists()

    entry_id = deleted.json()["recycle_entry_id"]
    restored = client.post(
        f"{BASE}/recycle-bin/{entry_id}/restore",
        headers=ADMIN,
        json={"expected_version": _revision(client)},
    )
    assert restored.status_code == 200, restored.text
    assert (builtin_luts.user_lut_dir() / "FilmLook.cube").is_file()


def test_ac4_default_preview_static_asset_available(client: TestClient):
    """AC4：默认预览图静态资源存在、SHA-256 匹配且可经 /static 访问。"""
    repo_root = Path(__file__).resolve().parents[3]
    preview_path = repo_root / "web/vendor/luts/default-preview.png"
    assert preview_path.is_file()
    digest = hashlib.sha256(preview_path.read_bytes()).hexdigest()
    assert digest == "2e4ad0c0b92507e31e2dc5c494e1a3ba7055fd3bab36dbd914696968c0a9bc9e"
    resp = client.get("/static/vendor/luts/default-preview.png")
    assert resp.status_code == 200
    assert resp.headers.get("content-type", "").startswith("image/png")


def test_import_request_id_rejects_different_content(client: TestClient):
    first = _upload_lut(client, "same.cube", VALID_CUBE, client_request_id="same-id")
    assert first.status_code == 200
    other = VALID_CUBE.replace(b'Test QIN621', b'Else QIN621')
    second = _upload_lut(client, "same.cube", other, client_request_id="same-id")
    assert second.status_code == 409
    assert second.json()["detail"]["code"] == "LUT_REQUEST_ID_CONFLICT"


def test_import_publication_failure_does_not_commit(client: TestClient, monkeypatch):
    from gw.asset_registry import repository

    original = repository.os.replace
    def fail_publish(src, dst):
        if str(dst).endswith("fail.cube"):
            raise OSError("injected publication failure")
        return original(src, dst)

    monkeypatch.setattr(repository.os, "replace", fail_publish)
    import pytest
    with pytest.raises(OSError):
        repository.import_user_lut_bytes("fail.cube", VALID_CUBE, client_request_id="retry-id")
    assert "retry-id" not in repository.state().read().get("lut_import_tokens", {})
    assert not (builtin_luts.user_lut_dir() / "fail.cube").exists()
    monkeypatch.setattr(repository.os, "replace", original)
    result = repository.import_user_lut_bytes("fail.cube", VALID_CUBE, client_request_id="retry-id")
    assert not result["deduplicated"]
    assert (builtin_luts.user_lut_dir() / "fail.cube").read_bytes() == VALID_CUBE


def test_concurrent_same_name_has_single_winner(client: TestClient):
    from concurrent.futures import ThreadPoolExecutor
    from gw.asset_registry import repository
    from gw.core.errors import CleanroomException

    def run(i):
        try:
            return repository.import_user_lut_bytes("race.cube", VALID_CUBE, client_request_id=f"race-{i}")
        except CleanroomException as exc:
            return exc
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, range(2)))
    assert sum(isinstance(item, dict) for item in results) == 1
    assert sum(isinstance(item, CleanroomException) for item in results) == 1
    assert (builtin_luts.user_lut_dir() / "race.cube").read_bytes() == VALID_CUBE


def test_overwrite_recovers_after_publication_before_commit(client: TestClient, monkeypatch):
    from gw.asset_registry import repository

    original = repository.import_user_lut_bytes("recover.cube", VALID_CUBE, client_request_id="original")
    replacement = VALID_CUBE.replace(b"Test QIN621", b"Else QIN621")
    target = builtin_luts.user_lut_dir() / "recover.cube"
    recover = repository._recover_user_lut_import

    def interrupted_commit(self, mutator):
        assert target.read_bytes() == replacement
        # 模拟发布后进程中断，直到下一次冷读才允许运行恢复。
        monkeypatch.setattr(repository, "_recover_user_lut_import", lambda: None)
        raise OSError("interrupted before registry commit")

    import pytest

    with monkeypatch.context() as patch:
        patch.setattr(repository.storage.JsonState, "mutate", interrupted_commit)
        with pytest.raises(OSError, match="interrupted before registry commit"):
            repository.import_user_lut_bytes(
                "recover.cube", replacement, conflict_resolution="overwrite", client_request_id="retry",
            )
    monkeypatch.setattr(repository, "_recover_user_lut_import", recover)
    assert target.read_bytes() == replacement
    assert repository.readonly_registry_snapshot()["assets"][original["asset_ids"][0]]["version"] == 1
    assert target.read_bytes() == VALID_CUBE
    result = repository.import_user_lut_bytes(
        "recover.cube", replacement, conflict_resolution="overwrite", client_request_id="retry",
    )
    assert result["asset"]["version"] == 2
    assert target.read_bytes() == replacement
