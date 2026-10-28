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

"""MVP2-T1a 参考策展读取 API（QIN-463 / GitHub #421）。

只暴露读端点：会话不存在一律 404 ``SESSION_NOT_FOUND``，并且不在服务端隐式
创建会话；响应只含稳定 ID、策展语义与时间戳，绝不回传本地绝对路径。
写路径（选中/拒绝/备注/排序）由 MVP2-T1b 在同一个路由模块内追加。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, Depends, Header, status

from gw.agent_episode.references import ReferenceSet, ReferenceStore
from gw.agent_runtime.storage.sqlite import SQLiteDatabase
from gw.core.auth import require_authenticated
from gw.core.errors import CleanroomException
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


__all__ = ["REFERENCE_DB_RELATIVE", "get_reference_store", "router"]
