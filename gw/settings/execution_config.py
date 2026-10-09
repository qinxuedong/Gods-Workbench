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

"""服务端 Provider 运行配置解析；只保存配置引用，不保存或返回密钥。"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, replace
from typing import Dict, Optional, Tuple
from urllib.parse import urlsplit, urlunsplit

from gw.core.errors import CleanroomException

PROVIDER_RUNTIME_ENV = "GW_PROVIDER_RUNTIME_JSON"
LEGACY_PROVIDER_ID = "legacy_environment"
LEGACY_BASE_URL_ENV = "GW_CHAT_BASE_URL"
LEGACY_API_KEY_ENV = "GW_CHAT_API_KEY"
LEGACY_MODEL_ENV = "GW_CHAT_MODEL"
DEFAULT_LEGACY_MODEL = "gpt-4o-mini"
MAX_RUNTIME_JSON_CHARS = 64_000
_SUPPORTED_VIDEO_PROTOCOLS = {"newapi_video"}
_PROVIDER_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_MODEL_ID = re.compile(r"^[^\s\x00-\x1f]{1,256}$")
_ALLOWED_FIELDS = {"base_url", "api_key_env", "model", "models", "default_model", "video_protocol"}


def normalize_provider_base_url(base_url: str, protocol: str = "openai") -> str:
    """OpenAI兼容地址补单个/v1；原生协议与URL非法成分交给原有校验。"""
    value = str(base_url or "").strip().rstrip("/")
    if not value or protocol not in {"openai", "apimart", "grok"}:
        return value
    try:
        parts = urlsplit(value)
    except ValueError:
        return value
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        return value
    path = parts.path.rstrip("/")
    if "v1" not in path.split("/"):
        path += "/v1"
    return urlunsplit((parts.scheme, parts.netloc, path, parts.query, parts.fragment))


class RuntimeConfigError(ValueError):
    """Provider 运行配置格式不合法；异常不包含任何原始配置内容。"""


class ProviderSelectionRequired(RuntimeConfigError):
    """存在多个服务端 Provider 且调用未明确选择。"""


@dataclass(frozen=True)
class RuntimeProvider:
    """可供服务端解析的 Provider 引用，不含凭据值。"""

    provider_id: str
    base_url: str
    api_key_env: str
    models: Tuple[str, ...]
    default_model: str
    video_protocol: Optional[str] = None
    source: str = "runtime_mapping"
    chat_models: Optional[Tuple[str, ...]] = None
    video_models: Optional[Tuple[str, ...]] = None
    credential_revision: Optional[int] = None


def _unique_object(pairs):
    """拒绝重复 JSON 键，避免依赖解析器的覆盖顺序。"""
    result = {}
    for key, value in pairs:
        if key in result:
            raise RuntimeConfigError("运行配置包含重复 JSON 键")
        result[key] = value
    return result


def _valid_url_shape(value: object) -> bool:
    if not isinstance(value, str) or not value or len(value) > 2048:
        return False
    try:
        parsed = urlsplit(value)
        return bool(
            parsed.scheme in {"http", "https"}
            and parsed.hostname
            and not parsed.username
            and not parsed.password
            and not parsed.query
            and not parsed.fragment
        )
    except Exception:
        return False


def _read_models(spec: dict) -> Tuple[Tuple[str, ...], str]:
    has_model = "model" in spec
    has_models = "models" in spec
    if has_model and has_models:
        raise RuntimeConfigError("每个 Provider 只能配置 model 或 models 之一")
    if has_model:
        value = spec.get("model")
        if not isinstance(value, str) or not _MODEL_ID.fullmatch(value):
            raise RuntimeConfigError("Provider model 配置无效")
        models = (value,)
    elif has_models:
        value = spec.get("models")
        if not isinstance(value, list) or not value or len(value) > 64:
            raise RuntimeConfigError("Provider models 必须是 1 至 64 项列表")
        if any(not isinstance(item, str) or not _MODEL_ID.fullmatch(item) for item in value):
            raise RuntimeConfigError("Provider models 配置无效")
        if len(set(value)) != len(value):
            raise RuntimeConfigError("Provider models 不得重复")
        models = tuple(value)
    else:
        raise RuntimeConfigError("Provider 必须配置 model 或 models")

    default_model = spec.get("default_model", models[0])
    if not isinstance(default_model, str) or default_model not in models:
        raise RuntimeConfigError("default_model 必须属于 Provider 已配置的 models")
    return models, default_model


def _environment_runtime() -> Dict[str, RuntimeProvider]:
    """读取并严格解析 GW_PROVIDER_RUNTIME_JSON；不回显或持久化密钥。"""
    raw = os.environ.get(PROVIDER_RUNTIME_ENV, "")
    if not raw.strip():
        return {}
    if len(raw) > MAX_RUNTIME_JSON_CHARS:
        raise RuntimeConfigError("Provider 运行配置超过长度上限")
    try:
        parsed = json.loads(raw, object_pairs_hook=_unique_object)
    except RuntimeConfigError:
        raise
    except Exception:
        raise RuntimeConfigError("Provider 运行配置不是有效 JSON") from None
    if not isinstance(parsed, dict) or len(parsed) > 128:
        raise RuntimeConfigError("Provider 运行配置必须是有限 Provider 映射")

    providers: Dict[str, RuntimeProvider] = {}
    for provider_id, spec in parsed.items():
        if (
            not isinstance(provider_id, str)
            or not _PROVIDER_ID.fullmatch(provider_id)
            or provider_id == LEGACY_PROVIDER_ID
        ):
            raise RuntimeConfigError("Provider ID 配置无效")
        if not isinstance(spec, dict) or set(spec) - _ALLOWED_FIELDS:
            raise RuntimeConfigError("Provider 配置字段不受支持")
        base_url = spec.get("base_url")
        api_key_env = spec.get("api_key_env")
        if not _valid_url_shape(base_url):
            raise RuntimeConfigError("Provider base_url 配置无效")
        if not isinstance(api_key_env, str) or not _ENV_NAME.fullmatch(api_key_env):
            raise RuntimeConfigError("Provider api_key_env 必须是环境变量名称")
        models, default_model = _read_models(spec)
        video_protocol = spec.get("video_protocol")
        if video_protocol is not None and video_protocol not in _SUPPORTED_VIDEO_PROTOCOLS:
            raise RuntimeConfigError("Provider video_protocol 不受支持")
        providers[provider_id] = RuntimeProvider(
            provider_id=provider_id,
            base_url=base_url.strip(),
            api_key_env=api_key_env,
            models=models,
            default_model=default_model,
            video_protocol=video_protocol,
        )
    return providers


def load_provider_runtime() -> Dict[str, RuntimeProvider]:
    """页面保存为当前真源；同ID覆盖环境映射，未登记的环境项继续兼容。"""
    from gw.settings.service import ProviderService

    providers = _environment_runtime()
    try:
        snapshot = ProviderService().get_snapshot()
        entries = snapshot.providers
    except CleanroomException:
        raise RuntimeConfigError("平台持久配置或凭据无法读取") from None
    for item in entries:
        provider_id = item["provider_id"]
        # 页面显式禁用或尚未配置的同ID项也遮蔽旧环境，不能静默调用旧账户。
        providers.pop(provider_id, None)
        if not item["enabled"] or not item["base_url"] or item["protocol"] not in {"openai", "apimart", "grok"}:
            continue
        chat_models = tuple(model for model in item["chat_models"]
                            if item["model_protocols"].get(model, "openai") == "openai")
        video_models = tuple(item["video_models"]) if item.get("video_protocol") == "newapi_video" else ()
        models = tuple(dict.fromkeys((*chat_models, *video_models)))
        if not models:
            continue
        providers[provider_id] = RuntimeProvider(provider_id=provider_id, base_url=normalize_provider_base_url(item["base_url"], item["protocol"]),
            api_key_env="", models=models, default_model=models[0], video_protocol=item.get("video_protocol"),
            source="settings", chat_models=chat_models, video_models=video_models,
            credential_revision=snapshot.revision)
    return providers


def _for_capability(provider: RuntimeProvider, capability: str) -> Optional[RuntimeProvider]:
    if capability == "video" and provider.video_protocol != "newapi_video":
        return None
    models = provider.chat_models if capability == "chat" else provider.video_models
    if models is None:
        return provider
    if not models:
        return None
    return replace(provider, models=models, default_model=models[0])


def legacy_provider() -> Optional[RuntimeProvider]:
    """读取既有 GW_CHAT_* 环境配置；客户端字段永远不能覆盖它。"""
    base_url = os.environ.get(LEGACY_BASE_URL_ENV, "").strip()
    api_key = os.environ.get(LEGACY_API_KEY_ENV, "").strip()
    if not base_url or not api_key:
        return None
    if not _valid_url_shape(base_url):
        raise RuntimeConfigError("旧版 Chat base_url 配置无效")
    model = os.environ.get(LEGACY_MODEL_ENV, DEFAULT_LEGACY_MODEL).strip() or DEFAULT_LEGACY_MODEL
    if not _MODEL_ID.fullmatch(model):
        raise RuntimeConfigError("旧版 Chat model 配置无效")
    return RuntimeProvider(
        provider_id=LEGACY_PROVIDER_ID,
        base_url=base_url,
        api_key_env=LEGACY_API_KEY_ENV,
        models=(model,),
        default_model=model,
        source="legacy_environment",
    )


def api_endpoint_url(base_url: str, endpoint_path: str) -> str:
    """拼接 API 路径；base_url 可在末尾带单个 /v1，避免重复版本段。"""
    base = str(base_url or "").rstrip("/")
    path = str(endpoint_path or "")
    if not base or not path.startswith("/"):
        raise RuntimeConfigError("Provider API 路径无效")
    base_path = urlsplit(base).path.rstrip("/").casefold()
    endpoint = path
    if base_path.endswith("/v1") and path.casefold().startswith("/v1/"):
        endpoint = path[3:]
    return base + endpoint


def resolve_provider(provider_id: Optional[str], *, capability: str = "chat") -> Optional[RuntimeProvider]:
    """按服务端映射解析 Provider；缺省仅在唯一配置或旧环境兼容时选取。"""
    if capability not in {"chat", "video"}:
        raise RuntimeConfigError("Provider capability 不受支持")
    providers = load_provider_runtime()
    legacy = legacy_provider() if capability == "chat" else None

    if provider_id:
        if provider_id == LEGACY_PROVIDER_ID:
            candidate = legacy
        else:
            candidate = providers.get(provider_id)
        if candidate is None:
            return None
        return _for_capability(candidate, capability)

    if capability == "chat" and legacy is not None:
        return legacy
    candidates = [
        candidate for item in providers.values()
        if (candidate := _for_capability(item, capability)) is not None
    ]
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        raise ProviderSelectionRequired("请求必须选择已配置的 Provider")
    return None


def provider_api_key(provider: RuntimeProvider) -> str:
    """只在请求执行时按引用读取秘密值；调用方不得记录或返回。"""
    if provider.source == "settings":
        from gw.settings.service import ProviderService
        if provider.credential_revision is None:
            return ""
        try:
            return ProviderService().credential(provider.provider_id, expected_version=provider.credential_revision)
        except CleanroomException:
            # 状态与执行按凭据不可用失败关闭；不输出加密异常或秘密值。
            return ""
    return os.environ.get(provider.api_key_env, "").strip()


def public_configuration() -> dict:
    """生成不含 base_url、环境变量名或秘密值的只读配置摘要。"""
    try:
        providers = load_provider_runtime()
        legacy = legacy_provider()
    except RuntimeConfigError:
        return {
            "configured": False,
            "configuration_status": "misconfigured",
            "default_provider_id": None,
            "providers": [],
            "data_status": "not_integrated",
            "data_gaps": ["provider_runtime_configuration_invalid"],
        }

    entries = []
    if legacy is not None:
        entries.append(legacy)
    entries.extend(providers.values())
    public_entries = [
        {
            "provider_id": item.provider_id,
            "models": list(item.models),
            "default_model": item.default_model,
            "configured": bool(provider_api_key(item)),
            "source": item.source,
            "video_protocol": item.video_protocol,
        }
        for item in entries
    ]
    usable = [item for item in entries if provider_api_key(item)]
    default_provider_id = None
    if legacy is not None and provider_api_key(legacy):
        default_provider_id = legacy.provider_id
    elif len(usable) == 1:
        default_provider_id = usable[0].provider_id
    configured = bool(usable)
    return {
        "configured": configured,
        "configuration_status": "configured" if configured else "not_configured",
        "default_provider_id": default_provider_id,
        "providers": public_entries,
        "data_status": "ok" if configured else "not_integrated",
        "data_gaps": [] if configured else ["provider_runtime_configuration_missing"],
    }
