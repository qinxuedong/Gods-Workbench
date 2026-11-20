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

"""QIN-624 run.py 启动入口参数与 start.bat 参数透传单元测试。"""

from __future__ import annotations

import io
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest

import run
from gw.core import storage

ROOT_DIR = Path(run.__file__).resolve().parent


def test_resolve_host_cli_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC1: 命令行 --host 优先级高于环境变量 GW_HOST 与默认回环。"""
    monkeypatch.setenv("GW_HOST", "192.168.1.100")
    assert run._resolve_host("0.0.0.0") == "0.0.0.0"
    assert run._resolve_host("10.0.0.1") == "10.0.0.1"


def test_resolve_host_env_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC1: 未传 CLI --host 时回退使用环境变量 GW_HOST。"""
    monkeypatch.setenv("GW_HOST", "192.168.1.200")
    assert run._resolve_host(None) == "192.168.1.200"


def test_resolve_host_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC1: 既无命令行参数也无环境变量时使用 DEFAULT_HOST (127.0.0.1)。"""
    monkeypatch.delenv("GW_HOST", raising=False)
    assert run._resolve_host(None) == run.DEFAULT_HOST
    assert run._resolve_host(None) == "127.0.0.1"


def test_parser_accepts_host_argument() -> None:
    """AC1: 命令行解析器支持 --host 参数。"""
    parser = run._parser()
    args = parser.parse_args(["--host", "0.0.0.0", "--port", "8080"])
    assert args.host == "0.0.0.0"
    assert args.port == 8080


def test_get_lan_ip_socket_probe() -> None:
    """AC2: UDP socket 探测返回探测到的非回环 IPv4 地址。"""
    mock_socket = MagicMock()
    mock_socket.getsockname.return_value = ("192.168.2.10", 54321)
    mock_socket_cls = MagicMock(return_value=mock_socket)
    mock_socket.__enter__.return_value = mock_socket

    with patch("socket.socket", mock_socket_cls):
        lan_ip = run._get_lan_ip()
        assert lan_ip == "192.168.2.10"


def test_get_lan_ip_fallback_to_hostname() -> None:
    """AC2: UDP socket 探测异常时回退到 gethostbyname_ex。"""
    with (
        patch("socket.socket", side_effect=OSError("Network unreachable")),
        patch("socket.gethostname", return_value="my-workstation"),
        patch("socket.gethostbyname_ex", return_value=("my-workstation", [], ["127.0.0.1", "10.0.1.5"])),
    ):
        lan_ip = run._get_lan_ip()
        assert lan_ip == "10.0.1.5"


def test_get_lan_ip_graceful_degradation_on_all_failures() -> None:
    """AC2: 所有探测手段均抛出异常时优雅降级返回 None，不中断运行。"""
    with (
        patch("socket.socket", side_effect=OSError("Permission denied")),
        patch("socket.gethostname", side_effect=OSError("Name resolution failed")),
    ):
        assert run._get_lan_ip() is None


def test_browser_host_adapted_when_binding_all_interfaces(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC3: 当 host == '0.0.0.0' 且开启 --open-browser 时，唤起与健康检查地址自适应转换为 127.0.0.1。"""
    monkeypatch.setenv("GW_AUTH_MODE", "local_account")
    monkeypatch.setenv("GW_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("GW_LOCAL_AUTH_DB", str(tmp_path / "auth.sqlite3"))
    monkeypatch.setenv("GW_VIDEO_DATA_DIR", str(tmp_path / "video"))
    monkeypatch.setattr(storage, "acquire_data_dir_lock", lambda _root: object())

    opened_urls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        run,
        "_open_browser_when_ready",
        lambda url, health_url, **_kwargs: opened_urls.append((url, health_url)),
    )
    monkeypatch.setattr(run.uvicorn, "run", lambda *_args, **_kwargs: None)

    fake_app = ModuleType("gw.api.app")
    fake_app.app = object()
    monkeypatch.setitem(sys.modules, "gw.api.app", fake_app)

    exit_code = run.main(["--test", "--open-browser", "--host", "0.0.0.0", "--port", "3000"])
    assert exit_code == 0
    assert len(opened_urls) == 1
    browser_url, health_url = opened_urls[0]
    assert browser_url == "http://127.0.0.1:3000/static/pages/index.html"
    assert health_url == "http://127.0.0.1:3000/healthz"


def test_console_output_lan_ip_when_binding_all_interfaces(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """AC2: 当 host == '0.0.0.0' 时，控制台输出服务地址与局域网访问地址。"""
    monkeypatch.setenv("GW_AUTH_MODE", "local_account")
    monkeypatch.setenv("GW_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("GW_LOCAL_AUTH_DB", str(tmp_path / "auth.sqlite3"))
    monkeypatch.setenv("GW_VIDEO_DATA_DIR", str(tmp_path / "video"))
    monkeypatch.setattr(storage, "acquire_data_dir_lock", lambda _root: object())
    monkeypatch.setattr(run, "_get_lan_ip", lambda: "192.168.1.66")
    monkeypatch.setattr(run.uvicorn, "run", lambda *_args, **_kwargs: None)

    fake_app = ModuleType("gw.api.app")
    fake_app.app = object()
    monkeypatch.setitem(sys.modules, "gw.api.app", fake_app)

    exit_code = run.main(["--test", "--host", "0.0.0.0", "--port", "2077"])
    assert exit_code == 0
    stdout = capsys.readouterr().out
    assert "服务地址: http://0.0.0.0:2077" in stdout
    assert "局域网访问地址: http://192.168.1.66:2077" in stdout


def test_console_output_omits_lan_ip_when_binding_loopback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """AC2: 当 host 为 127.0.0.1 时，控制台不输出局域网访问地址。"""
    monkeypatch.setenv("GW_AUTH_MODE", "local")
    monkeypatch.setenv("GW_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("GW_LOCAL_AUTH_DB", str(tmp_path / "auth.sqlite3"))
    monkeypatch.setenv("GW_VIDEO_DATA_DIR", str(tmp_path / "video"))
    monkeypatch.setattr(storage, "acquire_data_dir_lock", lambda _root: object())
    monkeypatch.setattr(run.uvicorn, "run", lambda *_args, **_kwargs: None)

    fake_app = ModuleType("gw.api.app")
    fake_app.app = object()
    monkeypatch.setitem(sys.modules, "gw.api.app", fake_app)

    exit_code = run.main(["--test", "--host", "127.0.0.1", "--port", "2077"])
    assert exit_code == 0
    stdout = capsys.readouterr().out
    assert "服务地址: http://127.0.0.1:2077" in stdout
    assert "局域网访问地址" not in stdout


def test_start_bat_preserves_whitelist_and_forwards_arguments() -> None:
    """AC4: start.bat 保持唯一白名单结构并支持参数透传 %*。"""
    bat_path = ROOT_DIR / "start.bat"
    content = bat_path.read_text(encoding="utf-8")
    lines = [line.strip() for line in io.StringIO(content) if line.strip()]
    assert '@echo off' in lines
    assert 'setlocal' in lines
    assert 'cd /d "%~dp0"' in lines
    assert any(line == "python run.py --prod --open-browser %*" for line in lines)
    # 确保没有其它未批准的 .bat 文件存在于仓库中
    extra_bats = [p.name for p in ROOT_DIR.glob("*.bat") if p.name != "start.bat"]
    assert extra_bats == [], f"禁止新增未批准的批处理文件: {extra_bats}"


def test_auth_mode_local_blocks_non_loopback_host(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """AC5: GW_AUTH_MODE=local 且指定 --host 0.0.0.0 时拒绝启动并报错。"""
    monkeypatch.setenv("GW_AUTH_MODE", "local")
    with pytest.raises(SystemExit) as raised:
        run.main(["--test", "--host", "0.0.0.0"])

    assert raised.value.code == 2
    err = capsys.readouterr().err
    assert "GW_AUTH_MODE=local 仅允许绑定" in err
