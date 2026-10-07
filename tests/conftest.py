# -*- coding: utf-8 -*-
"""Pytest 全局配置与夹具。

包含：
1. 沙箱环境下的 StrEnum 垫片与 agent 集成参数支持；
2. 应用导入期与用例隔离数据环境；
3. 全局 session 级夹具：repo_root, fixtures_dir, client。
"""
from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import str_enum_compat  # noqa: E402,F401
except ImportError:
    pass

os.environ.setdefault("GW_DISABLE_AGENT_INTEGRATION", "1")

# tests 不是包。浏览器测试需要按稳定模块名复用 Playwright 启动辅助。
_PLAYWRIGHT_SUPPORT = Path(__file__).resolve().parent / "e2e" / "playwright_support.py"
if _PLAYWRIGHT_SUPPORT.is_file() and "playwright_support" not in sys.modules:
    _support_spec = importlib.util.spec_from_file_location("playwright_support", _PLAYWRIGHT_SUPPORT)
    _support_module = importlib.util.module_from_spec(_support_spec)
    sys.modules["playwright_support"] = _support_module
    _support_spec.loader.exec_module(_support_module)
from fastapi.testclient import TestClient

# 应用模块被收集期直接导入时，先设置非生产临时运行态；用例内再切换到独立 tmp_path。
_BOOTSTRAP_RUNTIME = Path(tempfile.gettempdir()) / "gw-pytest-bootstrap"
os.environ.setdefault("GW_RUNTIME_MODE", "test")
os.environ.setdefault("GW_DATA_DIR", str(_BOOTSTRAP_RUNTIME / "data"))
os.environ.setdefault("GW_LOCAL_AUTH_DB", str(_BOOTSTRAP_RUNTIME / "auth" / "auth.sqlite3"))
os.environ.setdefault("GW_VIDEO_DATA_DIR", str(_BOOTSTRAP_RUNTIME / "video"))

# 确保仓库根目录在 Python 模块解析路径中
REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_PATH = REPO_ROOT
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))
from gw.projects_hub import service as projects_service_module
from gw.projects_hub.service import ProjectsService
from gw.api import routes_projects, routes_asset_registry


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path, monkeypatch):
    """Phase 12：把落盘数据根目录隔离到每个用例的临时目录。

    生产代码自 Phase 12 起把素材库/注册表等状态落到单实例 JSON；
    若用例共用真实数据目录，用例之间会互相污染且无法重放。
    本夹具确保每个用例拥有独立数据目录，且**默认不配置**本机文件允许根目录
    （未配置即禁止本机文件访问，符合失败关闭口径）。
    """
    monkeypatch.setenv("GW_RUNTIME_MODE", "test")
    # 大多数历史契约用例依赖显式开发认证；默认本地账户的行为由认证专用用例在清空环境后验证。
    monkeypatch.setenv("GW_AUTH_MODE", "local")
    monkeypatch.setenv("GW_DATA_DIR", str(tmp_path / "gw-data"))
    monkeypatch.setenv("GW_LOCAL_AUTH_DB", str(tmp_path / "gw-auth" / "auth.sqlite3"))
    monkeypatch.setenv("GW_VIDEO_DATA_DIR", str(tmp_path / "gw-video"))
    monkeypatch.delenv("GW_ALLOWED_ROOTS", raising=False)

    # HTTP契约用例显式注入内存黄金夹具；生产单例始终指向真实持久快照。
    memory_projects_service = ProjectsService(seed_golden_fixture=True)
    monkeypatch.setattr(projects_service_module, "default_projects_service", memory_projects_service)
    monkeypatch.setattr(routes_projects, "default_projects_service", memory_projects_service)
    monkeypatch.setattr(routes_asset_registry, "default_projects_service", memory_projects_service)
    from gw.video_tasks import service as video_service_module
    monkeypatch.setattr(video_service_module, "default_projects_service", memory_projects_service)

    # 视频任务有独立持久化根目录；用例间显式换库，不能通过改写/清空生产 ACL 来消除串扰。
    from gw.video_tasks import service as video_service_module
    previous_service = video_service_module._default_service
    video_service_module._default_service = None
    if previous_service is not None:
        previous_service.executor.shutdown(wait=False, cancel_futures=True)
    try:
        yield
    finally:
        active_service = video_service_module._default_service
        video_service_module._default_service = None
        if active_service is not None:
            active_service.executor.shutdown(wait=False, cancel_futures=True)


@pytest.fixture(scope="session")
def repo_root() -> Path:
    """返回仓库根目录路径。"""
    return REPO_ROOT


@pytest.fixture(scope="session")
def fixtures_dir(repo_root: Path) -> Path:
    """返回黄金夹具目录路径。"""
    return repo_root / "docs" / "fixtures"


@pytest.fixture(scope="session")
def client() -> TestClient:
    """返回 FastAPI 测试客户端。"""
    from gw.api.app import app
    return TestClient(app)
