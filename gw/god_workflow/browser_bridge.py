"""由工作台提供本地浏览器适配，不安装节点或改写 ComfyUI 核心文件。"""

import hashlib
import os
import re
import tempfile
from pathlib import Path
from typing import Optional, Tuple

from gw.core.errors import CleanroomException

EXTENSION_URL = "/extensions/gods_workbench/workbench.js"
TARGET_RELPATH = "extensions/gods_workbench/workbench.js"
SCRIPT = (
    Path(__file__).resolve().parents[2] / "tools/comfy_workbench_bridge/js/workbench.js"
)
_MARKER = re.compile(rb"//\s*gods-workbench-adapter-version:\s*(\d+)")

# 既往已发布、尚无版本标记的历史官方适配脚本内容指纹；命中即视为可自动升级的旧版适配。
# 摘要来源：git show <提交>:tools/comfy_workbench_bridge/js/workbench.js | sha256sum
KNOWN_LEGACY_SHA256 = {
    # 提交 9b0ff8a
    "c380c5ad22ef2ced8678efcf4cc3a5ae4acdaaf44719058900bd31690163c515": "9b0ff8a",
    # 提交 1c915d7
    "80827a84a49945c2639e1c21c0894fb43cc5100db02731e2525a95a4bd521254": "1c915d7",
    # 提交 efd55f4
    "77d405e00f4061a5654758dd830935af5a4b5f4d5fa88cf4b67106de754a59ac": "efd55f4",
}


def _marker_version(content: bytes) -> int | None:
    """版本标记只在脚本首行生效；其余位置的同样文本一律不识别。"""
    first_line = content.split(b"\n", 1)[0].strip()
    matched = _MARKER.fullmatch(first_line)
    return int(matched.group(1)) if matched else None


def _script_version() -> int:
    version = _marker_version(SCRIPT.read_bytes())
    if version is None:
        raise RuntimeError(
            "workbench.js 首行缺少 gods-workbench-adapter-version 版本标记"
        )
    return version


# 版本唯一来源是随包脚本的首行标记；模块常量只做转述，禁止另行硬编码。
ADAPTER_VERSION = _script_version()


def _classify(content: bytes) -> tuple[str, bool]:
    """识别已安装文件：返回（installed_version, 是否为可自动升级的官方旧版适配）。"""
    version = _marker_version(content)
    if version is not None:
        return str(version), version < ADAPTER_VERSION
    commit = KNOWN_LEGACY_SHA256.get(hashlib.sha256(content).hexdigest())
    if commit is not None:
        return f"legacy-{commit}", True
    return "unknown", False


def _conflict(installed_version: str, upgradable: bool) -> CleanroomException:
    if upgradable:
        hint = "已安装的 ComfyUI 页面适配是旧版本，点「升级适配」即可更新"
    else:
        hint = "ComfyUI 里有一个不是神工坊安装的同名文件，为了安全不会覆盖，请手动移走后重试"
    return CleanroomException(
        409,
        "COMFY_ADAPTER_CONFLICT",
        "页面适配文件已有不同内容，已保留原文件，请核对后更新",
        extra={
            "installed_version": installed_version,
            "required_version": str(ADAPTER_VERSION),
            "fix_hint": hint,
            "upgradable": upgradable,
        },
        expose_extra_fields={
            "installed_version",
            "required_version",
            "fix_hint",
            "upgradable",
        },
    )


def _atomic_write(target: Path, content: bytes, *, replace: bool) -> None:
    """完整写好临时文件后原子落位；replace=False 只新建不覆盖，replace=True 仅替换适配文件本身。"""
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=target.parent, suffix=".tmp", delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(content)
        if replace:
            os.replace(temporary, target)
            temporary = None
            return
        try:
            os.link(temporary, target)
        except FileExistsError:
            installed = target.read_bytes()
            if installed != content:
                version, upgradable = _classify(installed)
                raise _conflict(version, upgradable) from None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def prepare(root: str, upgrade: bool = False) -> dict:
    """仅在已配置安装目录内提供项目脚本；未知环境或目标改动均不覆盖。

    upgrade=True 时允许把可识别的旧官方适配原子替换为当前版本；其余冲突一律 409 保留原文件。
    """
    base = Path(root)
    if not root or not base.is_absolute() or not base.is_dir():
        raise CleanroomException(
            422,
            "COMFY_ADAPTER_ROOT_REQUIRED",
            "自动导入需要在设置中配置本地 ComfyUI 安装根目录",
        )
    base = base.resolve()
    candidates = [
        base / "python_embeded/Lib/site-packages",
        base / ".venv/Lib/site-packages",
        base / "venv/Lib/site-packages",
    ]
    for environment in (base / ".venv", base / "venv"):
        candidates.extend(sorted((environment / "lib").glob("python*/site-packages")))
    static = next(
        (
            site / "comfyui_frontend_package/static"
            for site in candidates
            if (site / "comfyui_frontend_package/static/scripts/app.js").is_file()
        ),
        None,
    )
    if static is None:
        raise CleanroomException(
            422,
            "COMFY_ADAPTER_FRONTEND_UNSUPPORTED",
            "未找到本地 ComfyUI 前端目录，请核对安装根目录；工作流原文已保留",
        )
    static = static.resolve()
    target = (static / TARGET_RELPATH).resolve()
    if not static.is_relative_to(base) or not target.is_relative_to(static):
        raise CleanroomException(
            422, "COMFY_ADAPTER_PATH_INVALID", "本地页面适配路径越界，未写入文件"
        )
    try:
        content = SCRIPT.read_bytes()
        target.parent.mkdir(parents=True, exist_ok=True)
        installed = target.read_bytes() if target.is_file() else None
        upgraded = False
        if installed is not None and installed != content:
            version, upgradable = _classify(installed)
            if not (upgrade and upgradable):
                raise _conflict(version, upgradable) from None
            _atomic_write(target, content, replace=True)
            upgraded = True
        elif installed is None:
            _atomic_write(target, content, replace=False)
    except OSError:
        raise CleanroomException(
            503,
            "COMFY_ADAPTER_WRITE_FAILED",
            "无法提供本地页面适配，请检查 ComfyUI 安装目录的写入权限",
        ) from None
    return {
        "mode": "managed",
        "extension_url": EXTENSION_URL,
        "adapter_version": ADAPTER_VERSION,
        "upgraded": upgraded,
    }
