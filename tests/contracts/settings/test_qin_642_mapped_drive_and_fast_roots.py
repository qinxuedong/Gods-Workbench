# -*- coding: utf-8 -*-
"""QIN-642：Windows 映射网络盘解析对齐、跨会话提示与允许根快速快照契约测试。"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gw.core import storage


def test_reparse_target_within_root_aligns_same_drive_namespace_resolved_target(
    tmp_path: Path,
) -> None:
    """同盘符命名空间下，映射盘 resolve() 展开为 UNC 目标时，若 root.resolve() 同源则放行。"""
    unc_target = tmp_path / "unc-share" / "media"
    unc_target.mkdir(parents=True)
    outside_target = tmp_path / "outside"
    outside_target.mkdir(parents=True)

    class _FakePath:
        def __init__(self, text: str, resolved_target: Path) -> None:
            self._text = text
            self._resolved = resolved_target

        def __str__(self) -> str:
            return self._text

        def __fspath__(self) -> str:
            return self._text

        def resolve(self) -> Path:
            return self._resolved

        def is_relative_to(self, other: Path) -> bool:
            return False

    fake_root = _FakePath(r"Z:\Media", unc_target)
    fake_path = _FakePath(r"Z:\Media\sub\shot.png", unc_target)

    # 同盘符命名空间且 root.resolve() == target：放行
    assert storage._reparse_target_within_root(fake_path, unc_target, fake_root) is True  # type: ignore[arg-type]
    # 重解析点指向根外：必须拒绝
    assert storage._reparse_target_within_root(fake_path, outside_target, fake_root) is False  # type: ignore[arg-type]
    # 跨命名空间（UNC 访问路径 vs 盘符允许根）未确认别名：必须拒绝
    unc_path = _FakePath(r"\\nas\share\Media\sub\shot.png", unc_target)
    assert storage._reparse_target_within_root(unc_path, unc_target, fake_root) is False  # type: ignore[arg-type]


def test_probe_mount_reports_cross_session_guidance_for_unmapped_drive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """跨权限会话看不到映射盘符时返回可读的 UNC 指引；普通本地缺失目录仍返回 MOUNT_NOT_FOUND。"""
    monkeypatch.setattr(storage, "_is_network_path", lambda _raw: False)
    monkeypatch.setattr(storage, "_is_unmapped_windows_drive", lambda _raw: True)
    unmapped = storage._probe_mount_sync(r"Z:\MissingMappedFolder")
    assert unmapped["available"] is False
    assert unmapped["reason"] == "MOUNT_OFFLINE"
    assert "UNC" in str(unmapped["message"])

    monkeypatch.setattr(storage, "_is_unmapped_windows_drive", lambda _raw: False)
    local_missing = storage._probe_mount_sync(str(tmp_path / "missing-local-dir"))
    assert local_missing["available"] is False
    assert local_missing["reason"] == "MOUNT_NOT_FOUND"


def test_get_storage_allowed_roots_probe_false_skips_slow_mount_probe(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GET /api/storage-settings/roots?probe=0 跳过阻塞探活并标为 probing；默认 probe=1 保持探活。"""
    root_dir = tmp_path / "fast-root"
    root_dir.mkdir(parents=True)

    probe_calls: list[str] = []
    monkeypatch.setattr(
        storage,
        "probe_mount",
        lambda path, **_kw: (
            probe_calls.append(str(path))
            or {"available": True, "reason": "", "message": ""}
        ),
    )

    headers = {"X-User-Role": "admin", "Authorization": "Bearer cleanroom-test"}
    add_resp = client.post(
        "/api/storage-settings/roots",
        json={"path": str(root_dir), "expected_version": 1},
        headers=headers,
    )
    assert add_resp.status_code == 200
    probe_calls.clear()

    fast_resp = client.get("/api/storage-settings/roots?probe=0", headers=headers)
    assert fast_resp.status_code == 200
    assert probe_calls == []
    fast_roots = fast_resp.json()["roots"]
    assert len(fast_roots) >= 1
    target_fast = next(item for item in fast_roots if item["path"] == str(root_dir))
    assert target_fast["availability"] == "probing"
    assert "检查" in target_fast["availability_message"]

    full_resp = client.get("/api/storage-settings/roots", headers=headers)
    assert full_resp.status_code == 200
    assert str(root_dir) in probe_calls
    target_full = next(item for item in full_resp.json()["roots"] if item["path"] == str(root_dir))
    assert target_full["availability"] == "available"
