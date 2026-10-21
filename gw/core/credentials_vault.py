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

"""服务端凭据保管库与单向出口鉴权边界。

``ProviderService`` 是现有设置契约的持久化真源：它把 Provider 元数据与密文
作为一个原子快照保存，密文由当前用户 DPAPI 或 auth 根内的受限 Fernet 密钥保护。
本模块不复制密钥，也不创建第二份明文存储，而是把“读取密钥”和“向上游注入鉴权”
收敛为服务端内部接口。HTTP 路由只能读取状态，不能读取原文。

安全边界：

* 客户端只提交稳定 ``provider_id``、模型与消息，不能提交 URL 或凭据；
* ``public_snapshot`` 只返回已配置字段的布尔状态；
* ``authorization_headers`` 只供服务端出向 transport 使用，绝不作为响应对象返回；
* Provider 集合版本绑定到密钥读取，配置在请求期间变化时旧请求失败关闭。
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, Optional

from gw.core.errors import CleanroomException

_PROVIDER_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

# 字段名与 ProviderService 的对外状态标记保持同一契约。
SUPPORTED_FIELDS = {
    "api_key": "has_key",
    "wallet_api_key": "has_wallet_key",
    "volcengine_access_key_id": "has_volcengine_access_key",
    "volcengine_secret_access_key": "has_volcengine_secret_key",
}

# 请求体中一律禁止出现的凭据别名。值从不进入错误消息。
_CREDENTIAL_MARKERS = (
    "api_key",
    "apikey",
    "access_key",
    "secret",
    "token",
    "password",
    "credential",
    "authorization",
    "auth",
    "private_key",
    "wallet",
)


class CredentialVault:
    """ProviderService 的服务端凭据边界。

    ``provider_service`` 参数仅用于测试隔离和路由级依赖注入；生产默认创建现有
    ProviderService，因此沿用其 CAS、原子 JSON、DPAPI/Fernet 和损坏快照 fail-closed
    语义。
    """

    def __init__(self, provider_service: Any | None = None) -> None:
        if provider_service is None:
            from gw.settings.service import ProviderService

            provider_service = ProviderService()
        self._provider_service = provider_service

    @staticmethod
    def _validate_provider_id(provider_id: str) -> str:
        if not isinstance(provider_id, str) or not _PROVIDER_ID.fullmatch(provider_id):
            raise CleanroomException(
                400, "INVALID_REQUEST", "provider_id 不符合服务端凭据索引格式"
            )
        return provider_id

    @staticmethod
    def _validate_field(field: str) -> str:
        if field not in SUPPORTED_FIELDS:
            raise ValueError("凭据字段不受支持")
        return field

    @staticmethod
    def _unavailable() -> CleanroomException:
        # 不带 provider、字段或任何上游错误文本，避免把配置索引泄露给客户端。
        return CleanroomException(
            503,
            "PROVIDER_CREDENTIAL_UNAVAILABLE",
            "服务端凭据不可用，未向外部发送请求",
        )

    def replace(self, raw_providers: Any, expected_version: int | None):
        """通过保管库边界提交 Provider 集合，沿用 ProviderService 原子 CAS。"""
        return self._provider_service.replace(raw_providers, expected_version)

    def public_snapshot(self) -> dict[str, Any]:
        """返回凭据状态摘要；永远不返回原文、密文、URL 或环境变量名。"""
        snapshot = self._provider_service.get_snapshot()
        providers = []
        for item in snapshot.providers:
            provider_id = item.get("provider_id")
            if not isinstance(provider_id, str):
                # ProviderService 已做严格校验；此处仍保持 fail-closed。
                raise self._unavailable()
            fields = {
                field: True
                for field, marker in SUPPORTED_FIELDS.items()
                if item.get(marker) is True
            }
            providers.append({"provider_id": provider_id, "fields": fields})
        configured = any(item["fields"] for item in providers)
        return {
            "revision": snapshot.revision,
            "configured": configured,
            "providers": providers,
            "data_status": "ok" if configured else "not_configured",
            "data_gaps": [] if configured else ["provider_credentials_not_configured"],
        }

    # ``status`` / ``snapshot`` 是内部调用方更易读的别名；两者仍只返回摘要。
    status = public_snapshot
    snapshot = public_snapshot

    def resolve(
        self,
        provider_id: str,
        field: str = "api_key",
        *,
        expected_revision: int | None = None,
    ) -> str:
        """仅供服务端 transport 读取密钥原文；缺失或损坏时失败关闭。"""
        provider_id = self._validate_provider_id(provider_id)
        field = self._validate_field(field)
        try:
            secret = self._provider_service.credential(
                provider_id,
                field,
                expected_version=expected_revision,
            )
        except CleanroomException:
            raise
        except Exception:
            raise self._unavailable() from None
        if not isinstance(secret, str) or not secret:
            raise self._unavailable()
        return secret

    # 内部执行代码常用的语义化别名；不在 API 路由中直接暴露。
    secret = resolve
    resolve_secret = resolve

    def require(
        self, provider_id: str, *, expected_revision: int | None = None
    ) -> None:
        """确认默认 API Key 可用，不把密钥交给调用方。"""
        self.resolve(provider_id, expected_revision=expected_revision)

    def authorization_headers(
        self,
        provider_id: str,
        *,
        expected_revision: int | None = None,
        extra_headers: Mapping[str, str] | None = None,
    ) -> dict[str, str]:
        """构造一次性上游鉴权头；调用方不得把结果写入 HTTP 响应或日志。"""
        if extra_headers is not None:
            for key in extra_headers:
                if str(key).casefold() in {"authorization", "proxy-authorization"}:
                    raise ValueError("出口鉴权头由服务端保管库生成")
        secret = self.resolve(provider_id, expected_revision=expected_revision)
        headers = {str(key): str(value) for key, value in (extra_headers or {}).items()}
        headers["Authorization"] = f"Bearer {secret}"
        return headers

    @staticmethod
    def reject_client_credentials(payload: Any) -> None:
        """拒绝客户端把凭据混入网关请求；错误不含输入值。"""

        def walk(value: Any) -> bool:
            if isinstance(value, Mapping):
                for key, nested in value.items():
                    normalized = (
                        str(key).strip().casefold().replace("-", "_").replace(" ", "_")
                    )
                    if normalized in {"key", "keys"} or any(
                        marker in normalized for marker in _CREDENTIAL_MARKERS
                    ):
                        return True
                    if walk(nested):
                        return True
            elif isinstance(value, (list, tuple)):
                return any(walk(item) for item in value)
            return False

        if walk(payload):
            raise CleanroomException(
                400,
                "INVALID_REQUEST",
                "客户端不得提交 Provider 地址或凭据；请使用服务端已配置的 provider_id",
            )


__all__ = ["CredentialVault", "SUPPORTED_FIELDS"]
