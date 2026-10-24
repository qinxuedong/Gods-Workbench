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

"""C12-T1 实体母库的单实例 JSON 仓储。

实体记录以稳定 ``entity_id`` 落盘，仓储只使用 ``gw.core.storage.JsonState``
提供的原子替换和命名空间锁。跨进程多 worker 一致性不在本模块承诺范围内；
所有更新和归档都以母库顶层 ``revision`` 做 CAS。
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import logging
from typing import Any

from fastapi import APIRouter, Body, Header, Query, status
from pydantic import BaseModel, ConfigDict, Field

from gw.cinematography.schema import (
    EntityCreatePayload,
    EntityPatchPayload,
    EntityType,
    MasterCatalogEntity,
)
from gw.core import storage
from gw.core.auth import require_authenticated, require_edit_access
from gw.core.errors import CleanroomException, VersionConflictException

NS_ENTITIES = "cinematography_entities"
DATA_STATUS_OK = "ok"
FIXTURE_ID = "c12-t1-master-catalog-001"


_LOGGER = logging.getLogger(__name__)


def _blank() -> dict[str, Any]:
    return {"revision": 1, "entities": {}, "sequences": {}}


def state() -> storage.JsonState:
    """返回实体母库命名空间状态容器。"""

    return storage.JsonState(NS_ENTITIES, _blank)


def _read_state() -> dict[str, Any]:
    raw = state().read()
    if not isinstance(raw, dict) or not isinstance(raw.get("entities"), dict):
        _LOGGER.warning("实体母库缺少有效 entities 字典字段，服务暂不可用")
        raise CleanroomException(
            503,
            "ENTITY_INDEX_UNAVAILABLE",
            "实体母库数据无效，服务暂不可用",
        )
    return raw


def _bump(raw: dict[str, Any]) -> int:
    raw["revision"] = int(raw.get("revision", 1)) + 1
    return raw["revision"]


def _cas(
    raw: Mapping[str, Any], expected_version: int | None, *, required: bool
) -> None:
    current = int(raw.get("revision", 1))
    if expected_version is None:
        if required:
            raise CleanroomException(
                400,
                "EXPECTED_VERSION_REQUIRED",
                "写入实体母库必须携带 expected_version",
            )
        return
    if int(expected_version) != current:
        raise VersionConflictException(
            int(expected_version), current, "实体母库版本冲突，请重新读取后重试"
        )


def _next_entity_id(raw: dict[str, Any]) -> str:
    taken = list(raw.setdefault("entities", {}).keys())
    sequences = raw.setdefault("sequences", {})
    entity_id = storage.next_sequence("ent_", list(sequences.keys()) + taken)
    sequences[entity_id] = True
    return entity_id


def _validate_project_id(project_id: str) -> str:
    value = str(project_id or "").strip()
    if not value or not value.isascii() or any(char.isspace() for char in value):
        raise CleanroomException(
            400, "INVALID_PROJECT_ID", "project_id 必须是非空 ASCII 标识"
        )
    return value


def _record_view(record: Mapping[str, Any]) -> dict[str, Any]:
    """将 JSON 记录通过响应模型重新校验后输出，避免脏数据越过 API。"""

    return MasterCatalogEntity.model_validate(record).model_dump(mode="json")


def _entity_or_404(raw: Mapping[str, Any], entity_id: str) -> dict[str, Any]:
    record = raw.get("entities", {}).get(entity_id)
    if not isinstance(record, dict) or bool(record.get("archived")):
        raise CleanroomException(404, "ENTITY_NOT_FOUND", "实体不存在")
    return record


def _payload(value: EntityCreatePayload | Mapping[str, Any]) -> EntityCreatePayload:
    if isinstance(value, EntityCreatePayload):
        return value
    return EntityCreatePayload.model_validate(dict(value))


def _patch(value: EntityPatchPayload | Mapping[str, Any]) -> EntityPatchPayload:
    if isinstance(value, EntityPatchPayload):
        return value
    return EntityPatchPayload.model_validate(dict(value))


def create_entity(payload: EntityCreatePayload | Mapping[str, Any]) -> dict[str, Any]:
    """登记角色、道具或场景，并返回新的母库 revision。"""

    request = _payload(payload)

    def mutate(raw: dict[str, Any]) -> dict[str, Any]:
        _cas(raw, request.expected_version, required=False)
        entity_id = _next_entity_id(raw)
        now = storage.now_iso()
        record = MasterCatalogEntity(
            entity_id=entity_id,
            project_id=request.project_id,
            entity_type=request.entity_type,
            name=request.name,
            description=request.description,
            feature_base_prompt=request.feature_base_prompt,
            lora_trigger_words=request.lora_trigger_words,
            lora_id=request.lora_id,
            lora_strength=request.lora_strength,
            cinematography=request.cinematography,
            metadata=deepcopy(request.metadata),
            version=1,
            archived=False,
            created_at=now,
            updated_at=now,
        ).model_dump(mode="json")
        raw.setdefault("entities", {})[entity_id] = record
        revision = _bump(raw)
        return {
            "entity": _record_view(record),
            "revision": revision,
            "data_status": DATA_STATUS_OK,
            "data_gaps": [],
            "fixture_id": FIXTURE_ID,
        }

    return state().mutate(mutate)


def list_entities(
    project_id: str | None = None,
    entity_type: EntityType | str | None = None,
    *,
    include_archived: bool = False,
) -> dict[str, Any]:
    """按项目读取母库实体；默认只返回未归档记录。"""

    project = _validate_project_id(project_id) if project_id is not None else None
    kind = EntityType(entity_type) if entity_type is not None else None
    raw = _read_state()
    records: list[dict[str, Any]] = []
    for value in raw.get("entities", {}).values():
        if not isinstance(value, dict):
            continue
        if project is not None and value.get("project_id") != project:
            continue
        if kind is not None and value.get("entity_type") != kind.value:
            continue
        if not include_archived and value.get("archived"):
            continue
        records.append(_record_view(value))
    records.sort(key=lambda item: (item["created_at"], item["entity_id"]))
    return {
        "entities": records,
        "revision": int(raw.get("revision", 1)),
        "data_status": DATA_STATUS_OK,
        "data_gaps": [],
        "fixture_id": FIXTURE_ID,
    }


def get_entity(entity_id: str, *, project_id: str | None = None) -> dict[str, Any]:
    """读取一个未归档实体，并在提供项目时执行主体范围校验。"""

    raw = _read_state()
    record = _entity_or_404(raw, str(entity_id))
    if project_id is not None and record.get("project_id") != _validate_project_id(
        project_id
    ):
        raise CleanroomException(404, "ENTITY_NOT_FOUND", "实体不存在")
    return {
        "entity": _record_view(record),
        "revision": int(raw.get("revision", 1)),
        "data_status": DATA_STATUS_OK,
        "data_gaps": [],
        "fixture_id": FIXTURE_ID,
    }


def update_entity(
    entity_id: str, payload: EntityPatchPayload | Mapping[str, Any]
) -> dict[str, Any]:
    """更新实体；所有写入使用母库顶层 revision 做 CAS。"""

    request = _patch(payload)

    def mutate(raw: dict[str, Any]) -> dict[str, Any]:
        _cas(raw, request.expected_version, required=True)
        record = _entity_or_404(raw, str(entity_id))
        changes = request.model_dump(exclude_unset=True, mode="json")
        changes.pop("expected_version", None)
        for key, value in changes.items():
            record[key] = deepcopy(value)
        record["version"] = int(record.get("version", 1)) + 1
        record["updated_at"] = storage.now_iso()
        revision = _bump(raw)
        return {
            "entity": _record_view(record),
            "revision": revision,
            "data_status": DATA_STATUS_OK,
            "data_gaps": [],
            "fixture_id": FIXTURE_ID,
        }

    return state().mutate(mutate)


def archive_entity(entity_id: str, expected_version: int) -> dict[str, Any]:
    """归档实体，保留母库记录并要求 CAS。"""

    def mutate(raw: dict[str, Any]) -> dict[str, Any]:
        _cas(raw, expected_version, required=True)
        record = _entity_or_404(raw, str(entity_id))
        record["archived"] = True
        record["version"] = int(record.get("version", 1)) + 1
        record["updated_at"] = storage.now_iso()
        revision = _bump(raw)
        return {
            "entity": _record_view(record),
            "revision": revision,
            "data_status": DATA_STATUS_OK,
            "data_gaps": [],
            "fixture_id": FIXTURE_ID,
        }

    return state().mutate(mutate)


# 与既有仓储命名保持兼容，路由和未来 Agent 导出可按语义选择名称。
create_master_entity = create_entity
list_master_entities = list_entities
get_master_entity = get_entity
update_master_entity = update_entity


# C12-T1 的实体域是新增路由域。路由载体放在实体域包内，API 模块只做
# 稳定导出，避免在既有 ``gw/api`` 路由树中再造一个平行承载文件。
class ArchivePayload(BaseModel):
    """归档请求的最小 CAS 载荷。"""

    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(..., ge=1)


def _read_route(authorization: str | None, role: str):
    return require_authenticated(authorization, role)


def _write_route(authorization: str | None, role: str):
    return require_edit_access(authorization, role)


def list_entities_endpoint(
    project_id: str | None = Query(None),
    entity_type: EntityType | None = Query(None),
    include_archived: bool = Query(False),
    authorization: str | None = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    _read_route(authorization, x_user_role)
    return list_entities(project_id, entity_type, include_archived=include_archived)


def create_entity_endpoint(
    payload: EntityCreatePayload,
    authorization: str | None = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    _write_route(authorization, x_user_role)
    return create_entity(payload)


def get_entity_endpoint(
    entity_id: str,
    project_id: str | None = Query(None),
    authorization: str | None = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    _read_route(authorization, x_user_role)
    return get_entity(entity_id, project_id=project_id)


def update_entity_endpoint(
    entity_id: str,
    payload: EntityPatchPayload,
    authorization: str | None = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    _write_route(authorization, x_user_role)
    return update_entity(entity_id, payload)


def archive_entity_endpoint(
    entity_id: str,
    payload: ArchivePayload = Body(...),
    authorization: str | None = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    _write_route(authorization, x_user_role)
    return archive_entity(entity_id, payload.expected_version)


router = APIRouter(tags=["entities-c12"])
for _path in (
    "/api/entities",
    "/api/master-catalog/entities",
    "/api/cinematography/entities",
):
    router.add_api_route(
        _path,
        list_entities_endpoint,
        methods=["GET"],
        status_code=status.HTTP_200_OK,
        include_in_schema=_path == "/api/entities",
        summary="读取项目实体母库" if _path == "/api/entities" else None,
    )
    router.add_api_route(
        _path,
        create_entity_endpoint,
        methods=["POST"],
        status_code=status.HTTP_201_CREATED,
        include_in_schema=_path == "/api/entities",
        summary="登记实体母库记录" if _path == "/api/entities" else None,
    )
for _path in (
    "/api/entities/{entity_id}",
    "/api/master-catalog/entities/{entity_id}",
    "/api/cinematography/entities/{entity_id}",
):
    router.add_api_route(
        _path,
        get_entity_endpoint,
        methods=["GET"],
        status_code=status.HTTP_200_OK,
        include_in_schema=_path == "/api/entities/{entity_id}",
        summary="读取实体母库记录" if _path == "/api/entities/{entity_id}" else None,
    )
    router.add_api_route(
        _path,
        update_entity_endpoint,
        methods=["PATCH"],
        status_code=status.HTTP_200_OK,
        include_in_schema=_path == "/api/entities/{entity_id}",
        summary="更新实体母库记录" if _path == "/api/entities/{entity_id}" else None,
    )
for _path in (
    "/api/entities/{entity_id}/archive",
    "/api/master-catalog/entities/{entity_id}/archive",
    "/api/cinematography/entities/{entity_id}/archive",
):
    router.add_api_route(
        _path,
        archive_entity_endpoint,
        methods=["POST"],
        status_code=status.HTTP_200_OK,
        include_in_schema=_path == "/api/entities/{entity_id}/archive",
        summary="归档实体母库记录"
        if _path == "/api/entities/{entity_id}/archive"
        else None,
    )


__all__ = [
    "DATA_STATUS_OK",
    "FIXTURE_ID",
    "NS_ENTITIES",
    "archive_entity",
    "archive_entity_endpoint",
    "ArchivePayload",
    "create_entity",
    "create_entity_endpoint",
    "create_master_entity",
    "get_entity",
    "get_entity_endpoint",
    "get_master_entity",
    "list_entities",
    "list_entities_endpoint",
    "list_master_entities",
    "router",
    "state",
    "update_entity",
    "update_entity_endpoint",
    "update_master_entity",
]
