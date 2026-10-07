"""T5-B 启动参数与入口隔离的最小反例。"""

from pathlib import Path
import os

import pytest

import run
from gw.core.runtime_paths import RuntimePathError


def test_reset_target_requires_explicit_dev_sandbox_path(tmp_path: Path):
    repo = tmp_path / "repo"
    target = repo / ".local" / "runtime-dev" / "data"
    target.mkdir(parents=True)
    assert run._reset_target(str(target), repo) == target.resolve()
    with pytest.raises(RuntimePathError):
        run._reset_target(str(repo), repo)
    with pytest.raises(RuntimePathError):
        run._reset_target(str(tmp_path / "outside"), repo)


def test_reset_does_not_accept_relative_or_production_target(tmp_path: Path):
    repo = tmp_path / "repo"
    with pytest.raises(RuntimePathError):
        run._reset_target("relative", repo)
    with pytest.raises(RuntimePathError):
        run._reset_target(str(tmp_path / "user" / "GodsWorkbench"), repo)


def test_run_entry_defines_single_worker_and_no_reload():
    source = Path(run.__file__).read_text(encoding="utf-8")
    assert "workers=1" in source
    assert "reload=False" in source
    assert "--prod" in source and "--open-browser" in source and "--reset" in source


def test_start_bat_is_self_locating_and_only_calls_prod_entry():
    source = (Path(run.__file__).with_name("start.bat")).read_text(encoding="ascii")
    assert 'cd /d "%~dp0"' in source
    assert "python run.py --prod --open-browser" in source
    assert "启动GodsWorkbench" not in source
