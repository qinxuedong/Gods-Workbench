"""QIN-650：生成素材默认落盘策略与存量兼容读取契约测试。"""

from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from gw.core import storage
from gw.core.errors import CleanroomException
from gw.god_workflow import registry
from gw.workspaces import context as workspace_context

_TINY_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\rIDATx\x9cc```\xf8\x0f\x00"
    b"\x01\x04\x01\x00\x18\xdd\x8d\xb1\x00\x00\x00\x00IEND\xaeB`\x82"
)


@pytest.fixture()
def isolated_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    data_dir = tmp_path / "data"
    video_dir = tmp_path / "video"
    auth_db = tmp_path / "auth" / "auth.sqlite3"
    data_dir.mkdir(parents=True, exist_ok=True)
    video_dir.mkdir(parents=True, exist_ok=True)
    auth_db.parent.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("GW_ENV", "test")
    monkeypatch.setenv("GW_DATA_DIR", str(data_dir))
    monkeypatch.setenv("GW_VIDEO_DIR", str(video_dir))
    monkeypatch.setenv("GW_AUTH_DB", str(auth_db))
    return data_dir


def _ctx(subject: str = "user_qin650") -> SimpleNamespace:
    return SimpleNamespace(identity_domain="local", subject=subject, role="owner")


def test_project_scoped_generation_writes_under_project_workspace(
    isolated_runtime: Path, tmp_path: Path
):
    """AC1：归属于项目的生成任务，默认产物目录落盘在该项目工作区目录下。"""
    ctx = _ctx()
    project_ws = tmp_path / "custom_project_workspace"
    project_ws.mkdir(parents=True, exist_ok=True)

    with workspace_context.bind_execution_context({"result_root": str(project_ws)}):
        task = registry.create_task(
            ctx,
            workflow_id="wf_demo",
            prompt_id="prompt_demo",
            target="local_comfy",
        )
        registry.update_task(
            ctx,
            task["job_id"],
            source_context={"project_id": "proj_alpha", "canvas_id": "cv_01"},
        )
        asset = registry.store_bytes(
            ctx,
            _TINY_PNG,
            "shot_001.png",
            "image/png",
            task_id=task["job_id"],
        )

        expected = (
            project_ws
            / "projects"
            / "proj_alpha"
            / "generated"
            / f"{asset['asset_id']}.png"
        )
        assert expected.is_file()
        assert expected.read_bytes() == _TINY_PNG
        assert asset["storage_scope"] == "project"
        assert asset["storage_relpath"] == f"projects/proj_alpha/generated/{asset['asset_id']}.png"

        resolved = registry.resolve_asset_file(ctx, asset["asset_id"])
        assert resolved is not None
        assert resolved[0].resolve() == expected.resolve()
        listed_ids = {item["asset_id"] for item in registry.list_assets(ctx)}
        assert asset["asset_id"] in listed_ids


@pytest.mark.parametrize("project_id", [None, "", "default"])
def test_unassigned_or_default_generation_writes_under_data_root_generated(
    isolated_runtime: Path, project_id: str | None
):
    """AC2：无归属项目或 default 画布生成产物落盘在 <data_root>/generated/ 下。"""
    ctx = _ctx()
    source_ctx = {"project_id": project_id, "canvas_id": "cv_free"} if project_id is not None else None
    asset = registry.store_bytes(
        ctx,
        _TINY_PNG,
        "standalone.png",
        "image/png",
        source_context=source_ctx,
    )

    expected = isolated_runtime / "generated" / f"{asset['asset_id']}.png"
    assert expected.is_file()
    assert expected.read_bytes() == _TINY_PNG
    assert asset["storage_scope"] == "generated"
    assert asset["storage_relpath"] == f"generated/{asset['asset_id']}.png"

    # 校验 OUTPUT_NAMESPACES 准入该相对产物路径
    assert storage.resolve_output_path(f"generated/{asset['asset_id']}.png").resolve() == expected.resolve()
    resolved = registry.resolve_asset_file(ctx, asset["asset_id"])
    assert resolved is not None
    assert resolved[0].resolve() == expected.resolve()


@pytest.mark.parametrize("bad_project_id", ["../escape", "a/b", "CON", "proj."])
def test_invalid_project_id_is_rejected(isolated_runtime: Path, bad_project_id: str):
    """非法 project_id 必须被拒绝，不允许路径穿越或保留字落盘。"""
    with pytest.raises(CleanroomException) as exc_info:
        storage.resolve_generated_output_root(bad_project_id)
    assert exc_info.value.status_code == 400
    assert exc_info.value.code == "INVALID_REQUEST"


def test_legacy_assets_remain_readable_and_recoverable(isolated_runtime: Path):
    """AC3：存量位于旧 workflow/<principal>/assets/ 目录的资产读取与补偿恢复不受影响。"""
    ctx = _ctx()
    legacy_id = "11111111-1111-4111-8111-111111111111"
    legacy_dir = registry._assets_root(ctx)
    legacy_file = legacy_dir / f"{legacy_id}.png"
    legacy_file.write_bytes(_TINY_PNG)

    legacy_entry = {
        "asset_id": legacy_id,
        "filename": "legacy.png",
        "media_type": "image",
        "content_type": "image/png",
        "size_bytes": len(_TINY_PNG),
        "width": 1,
        "height": 1,
        "extension": ".png",
        "content_sha256": hashlib.sha256(_TINY_PNG).hexdigest(),
        "url": f"/api/god_workflow/assets/{legacy_id}/content",
        "origin": "task",
        "preview_available": True,
    }
    registry._atomic_write(registry._assets_manifest_path(ctx), [legacy_entry])

    listed = registry.list_assets(ctx)
    assert [item["asset_id"] for item in listed] == [legacy_id]
    resolved = registry.resolve_asset_file(ctx, legacy_id)
    assert resolved is not None
    assert resolved[0].resolve() == legacy_file.resolve()

    # 模拟清单损坏，验证新旧路径均可通过 output_recovery 补偿恢复
    new_asset = registry.store_bytes(ctx, _TINY_PNG, "new_unassigned.png", "image/png")
    registry._atomic_write(
        registry._principal_root(ctx) / "output_recovery" / f"{legacy_id}.json",
        legacy_entry,
    )
    registry._assets_manifest_path(ctx).write_text("CORRUPTED_JSON", encoding="utf-8")

    recovered_ids = {item["asset_id"] for item in registry.list_assets(ctx)}
    assert {legacy_id, new_asset["asset_id"]}.issubset(recovered_ids)
