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

"""MVP2-T1a/T1b 参考策展读写 API（QIN-463 / #421，QIN-464 / #422）。

读端点：会话不存在一律 404 ``SESSION_NOT_FOUND``，并且不在服务端隐式创建
会话；写端点：``expected_version`` 不符返回 409 ``VERSION_CONFLICT``，项目上下文
缺失或与会话绑定不一致一律 fail closed。响应只含稳定 ID、策展语义与时间戳，
绝不回传本地绝对路径。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, Depends, Header, status

from gw.agent_episode.references import (
    ReferenceProjectContextError,
    ReferenceSet,
    ReferenceStore,
    ReferenceVersionConflict,
    ReferenceWriteRequest,
)
from gw.agent_runtime.errors import DomainError
from gw.agent_runtime.models import ErrorCode
from gw.agent_runtime.storage.sqlite import SQLiteDatabase
from gw.core.auth import require_authenticated
from gw.core.errors import CleanroomException, VersionConflictException
from gw.core.runtime_paths import resolve_runtime_paths

router = APIRouter(tags=["agent-references"])

#: 参考集台账固定在运行态数据根的 ``agent/`` 子目录，随三态隔离落盘。
REFERENCE_DB_RELATIVE = Path("agent") / "references.sqlite3"


@lru_cache(maxsize=4)
def _store_for_data_root(data_root: str) -> ReferenceStore:
    """按数据根缓存台账实例；同一根下重复请求复用已建表结构。"""
    return ReferenceStore(
        SQLiteDatabase(REFERENCE_DB_RELATIVE, trusted_root=Path(data_root))
    )


def get_reference_store() -> ReferenceStore:
    """取当前运行态的参考集台账（测试可覆盖此依赖注入临时目录）。"""
    return _store_for_data_root(str(resolve_runtime_paths().data_root))


@router.get(
    "/api/agent/sessions/{session_id}/references",
    response_model=ReferenceSet,
    status_code=status.HTTP_200_OK,
)
def get_session_references(
    session_id: str,
    store: ReferenceStore = Depends(get_reference_store),
    authorization: str | None = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
) -> ReferenceSet:
    """读取会话的参考策展集合；会话不存在返回 404 ``SESSION_NOT_FOUND``。"""
    require_authenticated(authorization, x_user_role)
    reference_set = store.get(session_id)
    if reference_set is None:
        raise CleanroomException(404, "SESSION_NOT_FOUND", "参考策展会话不存在")
    return reference_set


@router.put(
    "/api/agent/sessions/{session_id}/references/{asset_id}",
    response_model=ReferenceSet,
    status_code=status.HTTP_200_OK,
)
def put_session_reference(
    session_id: str,
    asset_id: str,
    payload: ReferenceWriteRequest,
    store: ReferenceStore = Depends(get_reference_store),
    authorization: str | None = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
) -> ReferenceSet:
    """写入/更新一条参考条目；CAS 冲突 409，项目上下文不符 fail closed。

    同值重复 PUT 幂等：``decision``/``note``/``order`` 与库内一致时不推进
    ``version``，响应与上一次写入完全一致，可安全重放。
    """
    require_authenticated(authorization, x_user_role)
    try:
        return store.upsert_item(
            session_id=session_id,
            asset_id=asset_id,
            project_id=payload.project_id,
            decision=payload.decision,
            note=payload.note,
            order=payload.order,
            expected_version=payload.expected_version,
        )
    except DomainError as exc:
        raise _as_cleanroom_exception(exc) from exc


def _as_cleanroom_exception(exc: DomainError) -> CleanroomException:
    """把参考集领域错误映射为契约规定的 HTTP 错误包。

    领域层保持框架中立（只给 ``ErrorCode``），形状与状态码由传输层决定：
    ``VALIDATION_FAILED`` 默认映射 422，与票面「缺 project_id → 400」不一致，
    因此在此显式收敛，避免沿用默认映射造成契约漂移。
    """
    if isinstance(exc, ReferenceVersionConflict):
        return VersionConflictException(
            expected_version=exc.expected_version,
            current_version=exc.current_version,
            message=exc.message,
        )
    if isinstance(exc, ReferenceProjectContextError):
        if exc.reason == ReferenceProjectContextError.REASON_MISSING:
            return CleanroomException(400, "PROJECT_CONTEXT_REQUIRED", exc.message)
        return CleanroomException(409, "PROJECT_CONTEXT_MISMATCH", exc.message)
    if exc.code is ErrorCode.PROJECT_UNAVAILABLE:
        return CleanroomException(404, "SESSION_NOT_FOUND", exc.message)
    return CleanroomException(exc.http_status, str(exc.code), exc.message)


__all__ = ["REFERENCE_DB_RELATIVE", "get_reference_store", "router"]
