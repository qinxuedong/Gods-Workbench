"""T5-B 最小启动入口参数门禁。"""

from pathlib import Path

import pytest

import run


def test_help_is_available():
    with pytest.raises(SystemExit) as exc:
        run.main(["--help"])
    assert exc.value.code == 0


def test_prod_without_localappdata_is_rejected(monkeypatch):
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    with pytest.raises(SystemExit) as exc:
        run.main(["--prod"])
    assert exc.value.code == 2


def test_reset_without_value_is_rejected():
    with pytest.raises(SystemExit) as exc:
        run.main(["--reset"])
    assert exc.value.code == 2


def test_reset_cannot_be_combined_with_prod(tmp_path):
    target = tmp_path / ".local" / "runtime-dev"
    with pytest.raises(SystemExit) as exc:
        run.main(["--prod", "--reset", str(target)])
    assert exc.value.code == 2


def test_start_bat_calls_only_production_entry():
    source = (Path(run.__file__).with_name("start.bat")).read_text(encoding="ascii")
    assert 'cd /d "%~dp0"' in source
    assert "python run.py --prod --open-browser" in source
