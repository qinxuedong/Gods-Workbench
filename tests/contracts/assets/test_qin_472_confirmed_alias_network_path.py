# Copyright 2026 Gods-Workbench Authors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""QIN-472：已确认 alias 媒体回读不再把 resolve/absolute 形态差误判为失败。"""

from __future__ import annotations

from pathlib import Path

from gw.asset_registry import index_jobs, repository
from gw.core import storage

PAYLOAD = b"qin-472 mapped drive alias media payload"
ROOT_ID = "root-qin-472"
SOURCE_ID = "src-qin-472"
RELPATH = "library/clip.mp4"


def _alias_boundary(tmp_path: Path) -> Path:
    """构造形态差边界：absolute() 保留 `..`，resolve() 归一为真实目录。

    真实映射盘上的形态差来自 resolve() 返回 UNC、absolute() 保留盘符；
    两者语义相同（同一目录、路径文本不同），本地用 `..` 等价复现该形态差，
    这样无需挂载真实网络盘即可覆盖判定分支。
    """
    real = tmp_path / "alias-root"
    (real / "library").mkdir(parents=True)
    (real / "library" / "clip.mp4").write_bytes(PAYLOAD)
    boundary = tmp_path / "outer" / ".." / "alias-root"
    assert boundary.resolve() != boundary.absolute()
    return boundary


def _register_alias(
    monkeypatch,
    boundary: Path,
    *,
    root_id: str = ROOT_ID,
    storage_root_id: str = SOURCE_ID,
    prefix: str = "",
) -> None:
    alias = {
        "root_id": root_id,
        "prefix": prefix,
        "storage_root_id": storage_root_id,
        "scan_path": str(boundary),
    }
    monkeypatch.setattr(index_jobs, "_roots", lambda: ({}, {storage_root_id: alias}))


def _treat_as_network(monkeypatch) -> None:
    """把路径标定为网络/映射盘，等价于真机上 GetDriveTypeW 返回远程盘。"""
    monkeypatch.setattr(storage, "_is_network_path", lambda _raw_path: True)


def test_network_alias_form_difference_returns_media(tmp_path, monkeypatch):
    """AC1：网络/映射 alias 上仅形态差时媒体路径不再误返 None。"""
    boundary = _alias_boundary(tmp_path)
    _register_alias(monkeypatch, boundary)
    _treat_as_network(monkeypatch)

    result = repository._confirmed_alias_media_path(SOURCE_ID, ROOT_ID, RELPATH)

    assert result is not None
    assert result.is_file()
    assert result.read_bytes() == PAYLOAD


def test_local_alias_form_difference_still_rejected(tmp_path, monkeypatch):
    """AC2：本地盘同一形态差仍失败关闭，不因形态差放宽判定。"""
    boundary = _alias_boundary(tmp_path)
    _register_alias(monkeypatch, boundary)

    assert storage._is_network_path(str(boundary)) is False
    assert repository._confirmed_alias_media_path(SOURCE_ID, ROOT_ID, RELPATH) is None


def test_network_alias_escape_still_rejected(tmp_path, monkeypatch):
    """AC1 安全护栏：网络豁免只放行形态差，解析结果越出边界仍失败关闭。"""
    boundary = _alias_boundary(tmp_path)
    _register_alias(monkeypatch, boundary)
    _treat_as_network(monkeypatch)
    outside = tmp_path / "outside"
    outside.mkdir()
    escaped = outside / "escape.mp4"
    escaped.write_bytes(b"outside alias boundary")
    target = boundary.joinpath(*RELPATH.split("/"))
    original_resolve = Path.resolve

    def resolve_as_escape(path, *args, **kwargs):
        if path == target:
            return escaped
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve_as_escape)

    assert repository._confirmed_alias_media_path(SOURCE_ID, ROOT_ID, RELPATH) is None


def test_network_alias_directory_target_still_rejected(tmp_path, monkeypatch):
    """AC2：网络路径上目标不是文件仍失败关闭。"""
    boundary = _alias_boundary(tmp_path)
    (boundary / "library" / "subdir").mkdir()
    _register_alias(monkeypatch, boundary)
    _treat_as_network(monkeypatch)

    assert repository._confirmed_alias_media_path(
        SOURCE_ID, ROOT_ID, "library/subdir"
    ) is None


def test_network_alias_identity_mismatch_still_rejected(tmp_path, monkeypatch):
    """AC2：别名绑定的根身份与资产不一致时仍失败关闭。"""
    boundary = _alias_boundary(tmp_path)
    _register_alias(monkeypatch, boundary, root_id="root-qin-472-other")
    _treat_as_network(monkeypatch)

    assert repository._confirmed_alias_media_path(SOURCE_ID, ROOT_ID, RELPATH) is None


def test_media_file_reads_alias_on_network_form_difference(tmp_path, monkeypatch):
    """AC1 回归护栏：映射盘形态差下 media_file 仍能回读 alias 媒体。"""
    boundary = _alias_boundary(tmp_path)
    _register_alias(monkeypatch, boundary)
    _treat_as_network(monkeypatch)
    asset = repository._plain_asset(
        "ast_qin472_alias_media",
        "映射盘 alias 素材",
        "video",
        root_id=ROOT_ID,
        storage_root_id=SOURCE_ID,
        canonical_relpath=RELPATH,
        display_path=RELPATH,
        is_remote=False,
    )
    repository.state().mutate(
        lambda state: state["assets"].update({asset["asset_id"]: asset})
    )

    path = repository.media_file(asset["asset_id"])

    assert path is not None
    assert path.read_bytes() == PAYLOAD
