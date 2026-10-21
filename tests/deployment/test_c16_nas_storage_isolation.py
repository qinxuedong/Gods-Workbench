"""C16-T1 NAS 部署文件契约测试。"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = ROOT / "deploy" / "docker" / "Dockerfile"
COMPOSE = ROOT / "deploy" / "docker" / "docker-compose.nas.yml"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_nas_dockerfile_uses_python311_and_hash_locked_runtime() -> None:
    """镜像必须使用 Python 3.11，并通过哈希锁安装运行期依赖。"""
    source = _read(DOCKERFILE)

    assert re.search(r"^FROM python:3\.11-slim(?:\s|$)", source, re.MULTILINE)
    assert "WORKDIR /app" in source
    assert "COPY requirements.lock.hashes /tmp/requirements.lock.hashes" in source
    assert (
        "pip install --no-cache-dir --require-hashes -r /tmp/requirements.lock.hashes"
        in source
    )
    assert "COPY gw ./gw" in source
    assert "COPY tools ./tools" in source
    assert "COPY web ./web" in source
    assert "COPY run.py ./run.py" in source
    assert re.search(r"GW_RUNTIME_MODE=prod(?:\s|\\\n)", source)
    assert re.search(r"GW_HOST=0.0.0.0(?:\s|\\\n)", source)
    assert re.search(r"GW_PORT=2077(?:\s|\\\n)", source)
    assert re.search(r"LOCALAPPDATA=/var/lib/gods-workbench(?:\s|\\\n)", source)
    assert re.search(
        r"^CMD\s+\[\s*\"python\"\s*,\s*\"run\.py\"\s*,\s*\"--prod\"",
        source,
        re.MULTILINE,
    )


def test_nas_compose_binds_only_the_three_production_storage_roots() -> None:
    """Compose 只把 data、auth、video 映射到生产态固定目录。"""
    source = _read(COMPOSE)

    assert "services:" in source
    assert re.search(r"^\s{2}gods-workbench:\s*$", source, re.MULTILINE)
    assert '"${GW_PORT:-2077}:2077"' in source
    assert "GW_RUNTIME_MODE: prod" in source
    assert "GW_HOST: 0.0.0.0" in source
    assert 'GW_PORT: "2077"' in source
    assert "LOCALAPPDATA: /var/lib/gods-workbench" in source
    assert ":/var/lib/gods-workbench/GodsWorkbench/data" in source
    assert ":/var/lib/gods-workbench/GodsWorkbench/auth" in source
    assert ":/var/lib/gods-workbench/GodsWorkbench/video" in source
    assert source.count("/var/lib/gods-workbench/GodsWorkbench/") == 3
    assert "read_only: true" in source
    assert "- /tmp" in source
    assert "restart: unless-stopped" in source
    assert "healthcheck:" in source


def test_nas_compose_keeps_single_process_and_rejects_host_escape_mounts() -> None:
    """NAS 模板不得放开多 worker、特权模式、Docker socket 或源码树挂载。"""
    source = _read(COMPOSE)

    assert '"python", "run.py", "--prod"' in source
    assert "--workers" not in source
    assert "privileged: true" not in source
    assert "/var/run/docker.sock" not in source
    assert "..:/app" not in source
    assert ":/app" not in source
