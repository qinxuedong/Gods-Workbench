import pytest
from gw.agent_runtime.config import (
    PathIsolationError,
    resolve_workspace_path,
    resolve_trusted_path,
)
@pytest.fixture
def mock_workspace(tmp_path, monkeypatch):
    monkeypatch.setattr("gw.agent_runtime.config.workspace_root", lambda: tmp_path)
    return tmp_path
def test_resolve_workspace_path_valid(mock_workspace):
    res = resolve_workspace_path("data/foo")
    assert res == mock_workspace / "data" / "foo"

    res = resolve_workspace_path(mock_workspace / "data" / "bar")
    assert res == mock_workspace / "data" / "bar"
def test_resolve_workspace_path_escape(mock_workspace):
    with pytest.raises(PathIsolationError):
        resolve_workspace_path("../escaped")

    outside = mock_workspace.parent / "outside"
    with pytest.raises(PathIsolationError):
        resolve_workspace_path(outside)
def test_resolve_workspace_path_symlink(mock_workspace):
    outside = mock_workspace.parent / "outside_target"
    outside.mkdir(exist_ok=True)

    symlink_path = mock_workspace / "symlink_dir"
    try:
        symlink_path.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Symlinks not supported on this system")

    with pytest.raises(PathIsolationError):
        resolve_workspace_path("symlink_dir/file.txt")
def test_resolve_trusted_path_valid(tmp_path):
    root = tmp_path / "trusted"
    root.mkdir()

    res = resolve_trusted_path("data/foo", trusted_root=root)
    assert res == root / "data" / "foo"

    res = resolve_trusted_path(root / "data" / "bar", trusted_root=root)
    assert res == root / "data" / "bar"
def test_resolve_trusted_path_escape(tmp_path):
    root = tmp_path / "trusted"
    root.mkdir()

    with pytest.raises(PathIsolationError):
        resolve_trusted_path("../escaped", trusted_root=root)

    with pytest.raises(PathIsolationError):
        resolve_trusted_path(tmp_path / "outside", trusted_root=root)
def test_resolve_trusted_path_not_absolute(tmp_path):
    with pytest.raises(PathIsolationError):
        resolve_trusted_path("data", trusted_root="relative/path")
def test_resolve_trusted_path_symlink(tmp_path):
    root = tmp_path / "trusted"
    root.mkdir()

    outside = tmp_path / "outside_target"
    outside.mkdir(exist_ok=True)

    symlink_path = root / "symlink_dir"
    try:
        symlink_path.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Symlinks not supported on this system")

    with pytest.raises(PathIsolationError):
        resolve_trusted_path("symlink_dir/file.txt", trusted_root=root)
