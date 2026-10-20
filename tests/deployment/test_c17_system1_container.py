"""C17-T2 Kev 纯 CPU 容器配置契约。"""

from __future__ import annotations

from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = REPO_ROOT / "deploy" / "docker" / "Dockerfile.system1"
COMPOSE_FILE = REPO_ROOT / "deploy" / "docker" / "docker-compose.system1.yml"


def _compose() -> dict:
    return yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8"))


def test_system1_dockerfile_is_pinned_cpu_only_and_non_root():
    text = DOCKERFILE.read_text(encoding="utf-8")

    assert "FROM python:3.11-slim-bookworm" in text
    assert "PIP_NO_CACHE_DIR=1" in text
    assert "GW_CPU_ONLY=1" in text
    assert "CUDA_VISIBLE_DEVICES=\"\"" in text
    assert "NVIDIA_VISIBLE_DEVICES=void" in text
    assert "HF_HUB_OFFLINE=1" in text
    assert "TRANSFORMERS_OFFLINE=1" in text
    assert "USER 10001:10001" in text
    assert "--workers\", \"1\"" in text


def test_system1_compose_declares_one_cpu_service_and_pinned_build():
    document = _compose()
    service = document["services"]["system1"]

    assert set(document) == {"services", "volumes"}
    assert service["build"] == {
        "context": "../..",
        "dockerfile": "deploy/docker/Dockerfile.system1",
    }
    assert service["image"] == "gods-workbench-system1:local"
    assert service["user"] == "10001:10001"
    assert service["environment"]["GW_CPU_ONLY"] == "1"
    assert service["environment"]["KEV_DEVICE"] == "cpu"
    assert service["environment"]["CUDA_VISIBLE_DEVICES"] == ""
    assert service["environment"]["NVIDIA_VISIBLE_DEVICES"] == "void"
    assert service["environment"]["HF_HUB_OFFLINE"] == "1"
    assert service["environment"]["TRANSFORMERS_OFFLINE"] == "1"

    assert service["deploy"]["resources"]["limits"] == {
        "cpus": "2.0",
        "memory": "500M",
    }
    assert service["mem_limit"] == "500m"
    assert service["restart"] == "unless-stopped"


def test_system1_compose_uses_read_only_model_input_and_explicit_runtime_storage():
    document = _compose()
    service = document["services"]["system1"]
    volumes = service["volumes"]

    assert "system1-models:/models/kev:ro" in volumes
    assert "system1-data:/var/lib/GodsWorkbench" in volumes
    assert service["read_only"] is True
    assert service["tmpfs"] == ["/tmp:rw,noexec,nosuid,size=64m"]
    assert service["cap_drop"] == ["ALL"]
    assert service["security_opt"] == ["no-new-privileges:true"]
    assert "gpus" not in service
    assert "runtime" not in service
    assert "devices" not in service


def test_system1_healthcheck_is_local_and_does_not_require_credentials():
    document = _compose()
    service = document["services"]["system1"]
    healthcheck = service["healthcheck"]

    assert healthcheck["test"] == [
        "CMD",
        "python",
        "-c",
        "import urllib.request; urllib.request.urlopen('http://127.0.0.1:2077/healthz', timeout=2)",
    ]
    assert healthcheck["interval"] == "10s"
    assert healthcheck["timeout"] == "3s"
    assert healthcheck["retries"] == 3
    assert healthcheck["start_period"] == "20s"

    text = DOCKERFILE.read_text(encoding="utf-8") + COMPOSE_FILE.read_text(encoding="utf-8")
    forbidden = ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "Authorization:")
    assert not any(marker in text for marker in forbidden)

