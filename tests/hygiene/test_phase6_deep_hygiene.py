"""Phase 6 全仓防污染与安全合规深度审计套件。

由【安全合规与洁净室审计专家】负责构建与维护：
1. 全仓零二进制受限资产深度遍历扫描
2. 端口固定规范（统一默认端口 2077）与旧仓硬编码隔离检查
3. 统一命名体系（god-canvas）与稳定实体 ID 命名规范体检
4. 敏感凭据防泄漏与明确排除项（插件协议）隔离性审计
"""

from pathlib import Path
import re
import pytest

# 从唯一事实来源导入禁用扩展名与白名单，避免多份清单再次漂移。
from cleanroom_extensions import ALLOWED_BINARY_ALLOWLIST, BANNED_EXTENSIONS

REPO_ROOT = Path(__file__).resolve().parent.parent.parent



def test_repo_wide_zero_binary_assets():
    """全仓递归扫描：坚决杜绝任何二进制图片、字体、压缩包或执行文件。

    本用例仅校验工作区文件（ignored_dirs 中保留 .git），git 对象库由独立的治理流程负责。
    """
    # 禁用扩展名清单来自**唯一事实来源**，禁止在此自带字面量（历史曾漂移 27 vs 39）。
    banned_extensions = BANNED_EXTENSIONS

    ignored_dirs = {".git", ".pytest_cache", "__pycache__", ".venv", "venv", ".idea", ".vscode"}

    violating_files = []
    for path in REPO_ROOT.rglob("*"):
        if path.is_file():
            # 过滤忽略目录
            if any(part in ignored_dirs for part in path.parts):
                continue
            if path.suffix.lower() in banned_extensions:
                relative_path = path.relative_to(REPO_ROOT).as_posix()
                # 命中白名单的逐条精确路径不再报错
                if relative_path in ALLOWED_BINARY_ALLOWLIST:
                    continue
                violating_files.append(relative_path)

    assert not violating_files, f"发现受限二进制资产，违反《洁净实现章程》: {violating_files}"


def test_port_standard_2077_enforcement():
    """验证系统运行端口严格固定为 2077，且启动入口 run.py 默认绑定 2077。"""
    run_py = REPO_ROOT / "run.py"
    assert run_py.exists(), "必须存在快速启动入口 run.py"
    content = run_py.read_text(encoding="utf-8")

    # 验证 run.py 中端口明确指定为 2077
    assert "port=2077" in content or "port = 2077" in content or "2077" in content
    # 验证 run.py 不存在硬编码旧端口 8000
    assert "port=8000" not in content and "port = 8000" not in content

    # 验证 AGENTS.md 规约了 2077 端口（若本地或仓库存在该规范文档）
    agents_md = REPO_ROOT / "AGENTS.md"
    if agents_md.exists():
        agents_content = agents_md.read_text(encoding="utf-8")
        assert "2077" in agents_content


def test_unified_naming_and_stable_id_spec():
    """验证画布组件全量统一更名为 god-canvas，且核心实体 ID 遵循稳定规范。"""
    src_dir = REPO_ROOT / "gw"

    # 1. 源码模块必须存在 god_canvas
    god_canvas_pkg = src_dir / "god_canvas"
    assert god_canvas_pkg.is_dir(), "源码目录中必须存在 god_canvas 统一模块"

    # 2. 核心路由与静态前端统一引用 god-canvas
    static_index = src_dir / "static" / "index.html"
    if static_index.exists():
        index_html = static_index.read_text(encoding="utf-8")
        assert "god-canvas" in index_html, "前端入口导航必须包含统一命名的 god-canvas"

    # 3. 稳定 ID 规范体检：project_id, canvas_id, entity_id, job_id, asset_id
    required_ids = ["project_id", "canvas_id", "entity_id", "job_id", "asset_id"]
    routes_god_canvas_file = src_dir / "api" / "routes_god_canvas.py"
    models_file = src_dir / "god_canvas" / "models.py"
    assert routes_god_canvas_file.exists(), "路由文件必须统一命名为 routes_god_canvas.py"
    assert models_file.exists(), "模型文件必须存在 models.py"

    combined_code = routes_god_canvas_file.read_text(encoding="utf-8") + models_file.read_text(encoding="utf-8")
    for field_id in required_ids:
        assert field_id in combined_code, f"核心模块中必须显式使用契约规约的标准实体字段: {field_id}"


def test_cleanroom_secrets_and_plugin_protocol_exclusion():
    """验证代码库无硬编码私钥凭据，且 PLUGIN-PROTOCOL-SPEC 依然严格排除。"""
    src_dir = REPO_ROOT / "gw"
    suspicious_patterns = [
        re.compile(r"-----BEGIN (RSA|EC|DSA|OPENSSH|PRIVATE) KEY-----"),
        re.compile(r"(api_key|secret_key|private_key)\s*=\s*['\"][a-zA-Z0-9_\-]{20,}['\"]", re.IGNORECASE),
    ]

    for py_file in src_dir.rglob("*.py"):
        text = py_file.read_text(encoding="utf-8")
        for pattern in suspicious_patterns:
            assert not pattern.search(text), f"在 {py_file} 中检测到疑似硬编码敏感密钥/凭据"

    # 验证插件协议排除红线：src/ 中不得存在插件协议运行时
    for path in src_dir.rglob("*"):
        name_lower = path.name.lower()
        assert "plugin_protocol" not in name_lower, f"发现受限排除项插件协议实现: {path}"
