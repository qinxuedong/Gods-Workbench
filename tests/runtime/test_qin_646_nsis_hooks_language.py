"""QIN-646：验证 NSIS hooks.nsh 内嵌 PowerShell 语法与中英文向导配置。"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

from clients.desktop.packaging import build_release

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOKS_NSH = REPO_ROOT / "clients" / "desktop" / "packaging" / "nsis" / "hooks.nsh"


def _extract_embedded_powershell_commands(nsh_text: str) -> list[str]:
    """从 hooks.nsh 的 nsExec::ExecToStack 调用中提取内嵌 PowerShell -Command 内容。"""
    pattern = re.compile(
        r'nsExec::ExecToStack\s+\'"\$SYSDIR\\WindowsPowerShell\\v1\.0\\powershell\.exe"[^"]*-Command\s+"([^"]+)"\''
    )
    commands = pattern.findall(nsh_text)
    assert len(commands) == 5, f"预期提取 5 条内嵌 PowerShell 命令，实际 {len(commands)} 条"
    return commands


def _render_nsis_to_powershell(raw_cmd: str) -> str:
    """模拟 NSIS 编译期与运行期变量展开，还原实际传给 PowerShell 的命令文本。"""
    rendered = raw_cmd.replace("$$", "$")
    rendered = rendered.replace("${GW_BOOTSTRAP_DIR_NAME}", "GodsWorkbench")
    rendered = rendered.replace("${GW_DATA_DIR_DEFAULT_NAME}", "Gods Workbench data")
    for nsis_var, sample in (
        ("$INSTDIR", r"C:\Program Files\Gods Workbench"),
        ("$GwDataDir", r"C:\Users\tester\AppData\Local\Gods Workbench data"),
        ("$R6", r"C:\Users\tester\AppData\Local"),
        ("$R7", r"C:\Users\tester\AppData\Local\GodsWorkbench"),
        ("$1", r"OK|C:\Users\tester\AppData\Local|C:\Users\tester\AppData\Roaming"),
    ):
        rendered = rendered.replace(nsis_var, sample)
    return rendered


def test_hooks_nsh_has_no_double_single_quotes_and_valid_powershell_syntax() -> None:
    """hooks.nsh 不得再出现 '' 双单引号，且展开后的 PowerShell 语句必须零语法错误。"""
    nsh_text = HOOKS_NSH.read_text(encoding="utf-8")
    commands = _extract_embedded_powershell_commands(nsh_text)

    for idx, raw_cmd in enumerate(commands, start=1):
        assert "''" not in raw_cmd, f"第 {idx} 条内嵌 PowerShell 命令仍包含非法双单引号 ''"
        ps_code = _render_nsis_to_powershell(raw_cmd)

        if sys.platform == "win32":
            ps_exe = shutil.which("powershell.exe") or r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
            if Path(ps_exe).is_file():
                # 使用 PowerShell 官方 AST Parser 校验语法，不执行实际命令
                probe = (
                    "$err = $null; "
                    "$tokens = $null; "
                    "$code = [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($env:GW_PS_B64)); "
                    "[void][System.Management.Automation.Language.Parser]::ParseInput($code, [ref]$tokens, [ref]$err); "
                    "if ($err -and $err.Count -gt 0) { $err | ForEach-Object { Write-Error $_.Message }; exit 1 }"
                )
                import base64
                import os

                env = dict(os.environ)
                env["GW_PS_B64"] = base64.b64encode(ps_code.encode("utf-8")).decode("ascii")
                res = subprocess.run(
                    [ps_exe, "-NoProfile", "-NonInteractive", "-Command", probe],
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=15,
                    check=False,
                )
                assert res.returncode == 0, f"第 {idx} 条 PowerShell 语法解析失败: {res.stderr}"


def test_build_release_tauri_config_enables_simpchinese_and_english(tmp_path: Path) -> None:
    """build_release.py 生成的 NSIS 配置必须包含中英文自动匹配语言列表。"""
    staging = tmp_path / "staging"
    staging.mkdir()
    output = tmp_path / "output"
    output.mkdir()

    config_path = build_release.write_release_config(output, staging, "0.1.0")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    nsis_cfg = config["bundle"]["windows"]["nsis"]
    assert nsis_cfg["languages"] == ["SimpChinese", "English"]
    assert nsis_cfg["installMode"] == "perMachine"
