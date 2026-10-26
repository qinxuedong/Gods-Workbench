"""MVP1-T0j: 挂载边界测试 (storage.canonical_relpath, directory_path_key, index_jobs._roots / root_facets 离线 facet)。"""

from __future__ import annotations

import unicodedata

import pytest

from gw.asset_registry import index_jobs, repository as repo
from gw.core import storage
from gw.core.errors import CleanroomException
from gw.settings.service import default_storage_settings_service


def test_canonical_relpath_nfc_nfd_normalization():
    """测试 canonical_relpath 对 Unicode NFC/NFD 的规范化转换。"""
    # NFD 形式: e + 组合重音符 (\u0301)
    nfd_string = "caf\u0065\u0301/folder/file.txt"
    # NFC 形式: é (\u00e9)
    nfc_string = "caf\u00e9/folder/file.txt"

    result_from_nfd = storage.canonical_relpath(nfd_string, windows=False)
    result_from_nfc = storage.canonical_relpath(nfc_string, windows=False)

    assert result_from_nfd == nfc_string
    assert result_from_nfc == nfc_string
    assert unicodedata.is_normalized("NFC", result_from_nfd)


def test_canonical_relpath_casefold_windows_and_non_windows():
    """测试 canonical_relpath 在 windows 与非 windows 模式下的大小写转换。"""
    mixed_case = "Media/Assets/Clip_01.MP4"

    # Windows 模式下应当统一转换为 casefold 小写
    win_result = storage.canonical_relpath(mixed_case, windows=True)
    assert win_result == mixed_case.casefold()
    assert win_result == "media/assets/clip_01.mp4"

    # 非 Windows 模式下保留原大小写
    non_win_result = storage.canonical_relpath(mixed_case, windows=False)
    assert non_win_result == mixed_case


def test_canonical_relpath_rejections():
    """测试 canonical_relpath 对非法相对路径参数的拒绝（抛出 CleanroomException）。"""
    invalid_paths = [
        "",
        None,
        123,
        "/absolute/path/file.txt",
        "\\absolute\\path\\file.txt",
        "C:/drive/letter/file.txt",
        "C:\\drive\\letter\\file.txt",
        "folder/./file.txt",
        "folder/../file.txt",
        "folder//file.txt",
        ".",
        "..",
    ]

    for invalid in invalid_paths:
        with pytest.raises(CleanroomException) as exc_info:
            storage.canonical_relpath(invalid)  # type: ignore[arg-type]
        assert exc_info.value.status_code == 400
        assert exc_info.value.code == "INVALID_PATH"


def test_directory_path_key_unc_prefixes_and_casefold():
    """测试 directory_path_key 对 UNC 前缀、驱动器盘符及大小写规范化比较。"""
    # 盘符与反斜杠/正斜杠替换
    drive1 = r"Z:\素材库\Media"
    drive2 = r"z:/素材库/MEDIA"
    assert storage.directory_path_key(drive1) == storage.directory_path_key(drive2)

    # UNC 路径前缀规范化 (\\server\share vs //SERVER/SHARE)
    unc1 = r"\\ServerName\ShareName\Folder\SubFolder"
    unc2 = r"//servername/sharename/folder/subfolder"
    unc3 = r"\\SERVERNAME\SHARENAME\FOLDER\SUBFOLDER"

    key1 = storage.directory_path_key(unc1)
    key2 = storage.directory_path_key(unc2)
    key3 = storage.directory_path_key(unc3)

    assert key1 == key2 == key3


def test_index_jobs_roots_and_root_facets_offline_facet(tmp_path, monkeypatch):
    """测试 index_jobs._roots() 及 root_facets() 对离线 (offline) 登记来源与确认别名的处理。"""
    monkeypatch.setattr(storage, "data_root", lambda: tmp_path)

    root_dir = tmp_path / "allowed_root"
    root_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("GW_ALLOWED_ROOTS", str(root_dir))

    online_lib = root_dir / "online_library"
    online_lib.mkdir(parents=True, exist_ok=True)

    offline_lib = root_dir / "offline_library"
    # 不创建 offline_lib 目录，模拟离线状态（path.resolve() 仍可得到路径，但 is_dir() 为 False）

    source_snapshot = [
        {
            "id": "lib_online",
            "name": "在线素材库",
            "path": str(online_lib),
        },
        {
            "id": "lib_offline",
            "name": "离线素材库",
            "path": str(offline_lib),
        },
    ]

    class DummySnapshot:
        def __init__(self, libs):
            self.local_libraries = libs

    monkeypatch.setattr(
        default_storage_settings_service,
        "get_snapshot",
        lambda: DummySnapshot(source_snapshot),
    )

    canonical, aliases = index_jobs._roots()

    assert "lib_online" in aliases
    assert aliases["lib_online"]["prefix"] == "online_library"

    # _roots() 中即使路径不可用 (offline)，登记来源只要属于 allowed_root，也会保留在 aliases 中
    assert "lib_offline" in aliases
    assert aliases["lib_offline"]["prefix"] == "offline_library"

    # 测试 root_facets 返回列表包含在线及离线来源的正确 availability 状态
    facets = index_jobs.root_facets()
    facet_map = {f["id"]: f for f in facets}

    assert "lib_online" in facet_map
    assert facet_map["lib_online"]["availability"] == "available"
    assert facet_map["lib_online"]["name"] == "在线素材库"

    assert "lib_offline" in facet_map
    assert facet_map["lib_offline"]["availability"] == "offline"
    assert facet_map["lib_offline"]["name"] == "离线素材库"


def test_index_jobs_offline_facet_with_bound_alias(tmp_path, monkeypatch):
    """测试离线库在包含持久确认别名 (root_aliases) 时的 _roots() 与 root_facets() 表现。"""
    monkeypatch.setattr(storage, "data_root", lambda: tmp_path)

    root_dir = tmp_path / "allowed_root"
    root_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("GW_ALLOWED_ROOTS", str(root_dir))

    # 根 ID
    root_id = storage.root_identity_id(root_dir)

    # 缺失的主登记路径
    offline_reg_path = root_dir / "offline_original"

    # 备用别名路径
    alias_path = root_dir / "offline_alias_dir"

    source_snapshot = [
        {
            "id": "lib_bound_offline",
            "name": "已绑定离线库",
            "path": str(offline_reg_path),
        }
    ]

    class DummySnapshot:
        def __init__(self, libs):
            self.local_libraries = libs

    monkeypatch.setattr(
        default_storage_settings_service,
        "get_snapshot",
        lambda: DummySnapshot(source_snapshot),
    )

    # 在 repo state 中写入 root_aliases 绑定信息
    bound_aliases = {
        "lib_bound_offline": {
            "root_id": root_id,
            "canonical_relpath": "offline_original",
            "aliases": [str(alias_path)],
        }
    }

    def _update_state(raw):
        raw["root_aliases"] = bound_aliases
        return raw

    repo.state().mutate(_update_state)

    # 当 alias_path 不存在时，_roots() 使用主登记路径，scan_path 未设置，joinpath 后 path 不存在 => availability="offline"
    canonical, aliases = index_jobs._roots()
    assert "lib_bound_offline" in aliases
    assert aliases["lib_bound_offline"].get("scan_path") is None

    facets = index_jobs.root_facets()
    facet_map = {f["id"]: f for f in facets}
    assert facet_map["lib_bound_offline"]["availability"] == "offline"

    # 现在创建 alias_path 目录，使其成为可用的离线恢复扫描通道
    alias_path.mkdir(parents=True, exist_ok=True)

    canonical, aliases = index_jobs._roots()
    assert "lib_bound_offline" in aliases
    assert aliases["lib_bound_offline"]["scan_path"] == alias_path

    # 在 root_facets 中，因为 alias_path 存在且是 dir，availability 为 available
    facets = index_jobs.root_facets()
    facet_map = {f["id"]: f for f in facets}
    assert facet_map["lib_bound_offline"]["availability"] == "available"
