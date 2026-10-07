"""Explicit, bounded HTTP JSON gateway boundary (not native provider adapters)."""
from __future__ import annotations

import json
import math
import os
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx

from gw.core.errors import CleanroomException

MAX_PROVIDER_BYTES = 4 * 1024 * 1024
PROVIDERS = ("runninghub", "liblib", "comfyui")


def unavailable(code: str, message: str) -> CleanroomException:
    return CleanroomException(503, code, message, {"unavailable": True}, {"unavailable"})


@dataclass(frozen=True)
class ProviderClient:
    """Opt-in gateway: GET {base_url}/workflows/{id}, Bearer API key, graph JSON.

    GW_<PROVIDER>_FETCH_ENABLED=1 explicitly admits this gateway contract.
    Configuration is resolved per call; arbitrary caller URLs and redirects are forbidden.
    """
    provider: str
    base_url: str
    api_key_env: str
    timeout: float = 15.0
    enabled: bool = False

    @property
    def configured(self) -> bool:
        parts = urlsplit(self.base_url)
        return bool(self.enabled and parts.scheme in ("http", "https") and parts.hostname
                    and not parts.username and not parts.password and not parts.query
                    and not parts.fragment and os.getenv(self.api_key_env, "").strip()
                    and math.isfinite(self.timeout) and 0 < self.timeout <= 120)

    async def fetch(self, workflow_id: str) -> dict[str, Any]:
        if not isinstance(workflow_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", workflow_id):
            raise CleanroomException(400, "INVALID_PROVIDER_REFERENCE", "Expected an opaque workflow identifier, not a URL or path")
        if not self.configured:
            raise unavailable("WORKFLOW_PROVIDER_UNAVAILABLE", "Workflow JSON gateway is not configured")
        url = f"{self.base_url.rstrip('/')}/workflows/{workflow_id}"
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(self.timeout), follow_redirects=False, trust_env=False) as client:
                async with client.stream("GET", url, headers={"Authorization": f"Bearer {os.environ[self.api_key_env]}", "Accept": "application/json"}) as response:
                    if response.status_code == 404:
                        raise CleanroomException(404, "PROVIDER_WORKFLOW_NOT_FOUND", "Provider workflow not found")
                    if response.status_code != 200:
                        raise CleanroomException(502, "WORKFLOW_PROVIDER_ERROR", "Provider rejected the fetch request")
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        content.extend(chunk)
                        if len(content) > MAX_PROVIDER_BYTES:
                            raise CleanroomException(502, "WORKFLOW_PROVIDER_RESPONSE_TOO_LARGE", "Provider response exceeds the size limit")
            result = json.loads(content)
            if not isinstance(result, dict):
                raise ValueError()
            return result
        except httpx.TimeoutException:
            raise CleanroomException(504, "WORKFLOW_PROVIDER_TIMEOUT", "Provider request timed out") from None
        except httpx.HTTPError:
            raise CleanroomException(502, "WORKFLOW_PROVIDER_ERROR", "Provider request failed") from None
        except (ValueError, UnicodeDecodeError):
            raise CleanroomException(502, "WORKFLOW_PROVIDER_INVALID_RESPONSE", "Provider returned invalid JSON") from None

    async def execute(self, workflow: dict[str, Any]) -> dict[str, Any]:
        raise unavailable("WORKFLOW_EXECUTOR_UNAVAILABLE", "No workflow execution adapter is admitted")


def get_provider(provider: str) -> ProviderClient:
    if provider not in PROVIDERS:
        raise CleanroomException(400, "INVALID_PROVIDER", "Unsupported workflow provider")
    prefix = f"GW_{provider.upper()}"
    try:
        timeout = float(os.getenv(f"{prefix}_TIMEOUT_SECONDS", "15"))
    except ValueError:
        timeout = 0
    return ProviderClient(provider, os.getenv(f"{prefix}_URL", ""), f"{prefix}_API_KEY", timeout,
                          os.getenv(f"{prefix}_FETCH_ENABLED", "") == "1")
