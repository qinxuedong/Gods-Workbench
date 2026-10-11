"""由工作台提供本地浏览器适配，不安装节点或改写 ComfyUI 核心文件。"""
from pathlib import Path
import os
import tempfile

from gw.core.errors import CleanroomException


EXTENSION_URL = '/extensions/gods_workbench/workbench.js'
SCRIPT = Path(__file__).resolve().parents[2] / 'tools/comfy_workbench_bridge/js/workbench.js'


def prepare(root: str) -> dict:
    """仅在已配置安装目录内提供项目脚本；未知环境或目标改动均不覆盖。"""
    base = Path(root)
    if not root or not base.is_absolute() or not base.is_dir():
        raise CleanroomException(422, 'COMFY_ADAPTER_ROOT_REQUIRED', '自动导入需要在设置中配置本地 ComfyUI 安装根目录')
    base = base.resolve()
    candidates = [base / 'python_embeded/Lib/site-packages',
                  base / '.venv/Lib/site-packages', base / 'venv/Lib/site-packages']
    for environment in (base / '.venv', base / 'venv'):
        candidates.extend(sorted((environment / 'lib').glob('python*/site-packages')))
    static = next((site / 'comfyui_frontend_package/static' for site in candidates
                   if (site / 'comfyui_frontend_package/static/scripts/app.js').is_file()), None)
    if static is None:
        raise CleanroomException(422, 'COMFY_ADAPTER_FRONTEND_UNSUPPORTED', '未找到本地 ComfyUI 前端目录，请核对安装根目录；工作流原文已保留')
    static = static.resolve()
    target = (static / 'extensions/gods_workbench/workbench.js').resolve()
    if not static.is_relative_to(base) or not target.is_relative_to(static):
        raise CleanroomException(422, 'COMFY_ADAPTER_PATH_INVALID', '本地页面适配路径越界，未写入文件')
    try:
        content = SCRIPT.read_bytes()
        target.parent.mkdir(parents=True, exist_ok=True)
        # 完整写好后原子建立目标，不让宿主枚举到半写脚本，也不覆盖已有文件。
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=target.parent, suffix='.tmp', delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(content)
            try:
                os.link(temporary, target)
            except FileExistsError:
                if target.read_bytes() != content:
                    raise CleanroomException(409, 'COMFY_ADAPTER_CONFLICT', '页面适配文件已有不同内容，已保留原文件，请核对后更新') from None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    except OSError:
        raise CleanroomException(503, 'COMFY_ADAPTER_WRITE_FAILED', '无法提供本地页面适配，请检查 ComfyUI 安装目录的写入权限') from None
    return {'mode': 'managed', 'extension_url': EXTENSION_URL}
