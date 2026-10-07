"""T5-A 三态运行路径纯解析与越界/链接反例。"""

from pathlib import Path

import pytest

from gw.core.runtime_paths import (
    DATA_DIR_ENV,
    LOCAL_AUTH_DB_ENV,
    MODE_DEV,
    MODE_PROD,
    MODE_TEST,
    RuntimePathError,
    VIDEO_DATA_DIR_ENV,
    resolve_runtime_paths,
)


def test_dev_defaults_are_fixed_under_runtime_sandbox(tmp_path: Path):
    paths = resolve_runtime_paths(MODE_DEV, repo_root=tmp_path, env={})
    assert paths.data_root == tmp_path / ".local" / "runtime-dev" / "data"
    assert paths.auth_db == tmp_path / ".local" / "runtime-dev" / "auth" / "auth.sqlite3"
    assert paths.video_root == tmp_path / ".local" / "runtime-dev" / "video"
    assert not (tmp_path / ".local" / "runtime-dev").exists()


def test_dev_rejects_each_path_outside_sandbox(tmp_path: Path):
    sandbox = tmp_path / ".local" / "runtime-dev"
    cases = (
        (DATA_DIR_ENV, tmp_path / "outside-data"),
        (LOCAL_AUTH_DB_ENV, tmp_path / "outside-auth.sqlite3"),
        (VIDEO_DATA_DIR_ENV, tmp_path / "outside-video"),
    )
    for name, value in cases:
        with pytest.raises(RuntimePathError, match="开发态"):
            resolve_runtime_paths(MODE_DEV, repo_root=tmp_path, env={name: str(value)})
    assert not sandbox.exists()


@pytest.mark.parametrize("raw", ["relative/data", "C:foo", r"\foo"])
def test_test_mode_rejects_relative_and_drive_relative_paths(tmp_path: Path, raw: str):
    env = {
        DATA_DIR_ENV: raw,
        LOCAL_AUTH_DB_ENV: str(tmp_path / "auth" / "auth.sqlite3"),
        VIDEO_DATA_DIR_ENV: str(tmp_path / "video"),
    }
    with pytest.raises(RuntimePathError):
        resolve_runtime_paths(MODE_TEST, repo_root=tmp_path, env=env)


def test_test_mode_validates_all_three_before_returning(tmp_path: Path):
    env = {
        DATA_DIR_ENV: str(tmp_path / "data"),
        LOCAL_AUTH_DB_ENV: str(tmp_path / "auth" / "auth.sqlite3"),
        VIDEO_DATA_DIR_ENV: str(tmp_path / "video"),
    }
    paths = resolve_runtime_paths(MODE_TEST, repo_root=tmp_path, env=env)
    assert paths.mode == MODE_TEST
    assert paths.data_root == Path(env[DATA_DIR_ENV]).resolve()
    assert paths.auth_db == Path(env[LOCAL_AUTH_DB_ENV]).resolve()
    assert paths.video_root == Path(env[VIDEO_DATA_DIR_ENV]).resolve()
    assert not (tmp_path / "data").exists()
    assert not (tmp_path / "auth").exists()
    assert not (tmp_path / "video").exists()


def test_prod_requires_explicit_localappdata_when_missing(tmp_path: Path):
    with pytest.raises(RuntimePathError, match="LOCALAPPDATA"):
        resolve_runtime_paths(MODE_PROD, repo_root=tmp_path, env={})


def test_prod_defaults_are_outside_repo(tmp_path: Path):
    user_root = tmp_path / "user-localappdata"
    paths = resolve_runtime_paths(MODE_PROD, repo_root=tmp_path / "repo", localappdata=user_root, env={})
    assert paths.data_root == user_root / "GodsWorkbench" / "data"
    assert paths.auth_db == user_root / "GodsWorkbench" / "auth" / "auth.sqlite3"
    assert paths.video_root == user_root / "GodsWorkbench" / "video"
    assert paths.repo_root not in paths.data_root.parents


def test_prod_rejects_repo_localappdata(tmp_path: Path):
    repo = tmp_path / "repo"
    with pytest.raises(RuntimePathError, match="源码仓库"):
        resolve_runtime_paths(MODE_PROD, repo_root=repo, localappdata=repo / ".local", env={})


def test_existing_symlink_in_any_root_is_rejected(tmp_path: Path):
    target = tmp_path / "real"
    target.mkdir()
    link = tmp_path / "link"
    try:
        link.symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("当前 Windows 账户未授予创建目录符号链接权限")
    env = {
        DATA_DIR_ENV: str(link / "data"),
        LOCAL_AUTH_DB_ENV: str(tmp_path / "auth" / "auth.sqlite3"),
        VIDEO_DATA_DIR_ENV: str(tmp_path / "video"),
    }
    with pytest.raises(RuntimePathError, match="符号链接"):
        resolve_runtime_paths(MODE_TEST, repo_root=tmp_path, env=env)


def test_existing_file_cannot_be_used_as_data_or_video_root(tmp_path: Path):
    data = tmp_path / "data"
    data.write_text("not a directory", encoding="utf-8")
    env = {
        DATA_DIR_ENV: str(data),
        LOCAL_AUTH_DB_ENV: str(tmp_path / "auth" / "auth.sqlite3"),
        VIDEO_DATA_DIR_ENV: str(tmp_path / "video"),
    }
    with pytest.raises(RuntimePathError):
        resolve_runtime_paths(MODE_TEST, repo_root=tmp_path, env=env)
