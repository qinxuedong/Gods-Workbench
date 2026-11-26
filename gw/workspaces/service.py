# Copyright 2026 qinxuedong (Gods-Workbench Authors)
# SPDX-License-Identifier: FSL-1.1-Apache-2.0
#
# Licensed under the Functional Source License, Version 1.1, Apache 2.0
# Future License (the "License"); you may not use this file except in
# compliance with the License. You may obtain a copy of the License at
#     https://fsl.software/
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""工作区状态服务：单文件原子落盘、CAS 并发控制与单一写入工作区。"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from gw.core import storage
from gw.core.errors import CleanroomException
from gw.workspaces import context as workspace_context
from gw.workspaces import model, verification


def _conflict(code: str, expected: int, current: int, message: str) -> CleanroomException:
    """构造 409 标准冲突错误包。"""
    return CleanroomException(
        status_code=409,
        code=code,
        message=message,
        extra={"expected_version": expected, "current_version": current},
    )


def _normalize_state(raw: Any) -> dict[str, Any]:
    """把磁盘快照补齐为当前契约形状；不静默丢弃未知票的字段。"""
    default = model.default_snapshot()
    if not isinstance(raw, dict):
        return default
    state = dict(default)
    state.update({key: value for key, value in raw.items() if key in default})
    for key in ("schema_version", "revision"):
        if not isinstance(state[key], int) or isinstance(state[key], bool) or state[key] < 1:
            state[key] = default[key]
    if not isinstance(state["workspaces"], list) or not state["workspaces"]:
        state["workspaces"] = default["workspaces"]
    if not isinstance(state["mappings"], list):
        state["mappings"] = []
    valid_ids = {item.get("workspace_id") for item in state["workspaces"] if isinstance(item, dict)}
    if state["active_workspace_id"] not in valid_ids:
        state["active_workspace_id"] = model.WORKSPACE_LOCAL_ID
    return state


def _active_workspace(state: dict[str, Any]) -> dict[str, Any]:
    for workspace in state["workspaces"]:
        if workspace.get("workspace_id") == state["active_workspace_id"]:
            return workspace
    return state["workspaces"][0]


def _mapping_id(logical_source_id: str, execution_machine_id: str, workspace_id: str) -> str:
    payload = f"{logical_source_id}|{execution_machine_id}|{workspace_id}".encode()
    return "map_" + hashlib.sha256(payload).hexdigest()[:16]


class WorkspaceService:
    """工作区真值服务；所有写操作走同一命名空间锁与 CAS 版本。"""

    def __init__(self) -> None:
        self._state = storage.JsonState(model.STATE_NAMESPACE, model.default_snapshot)

    def compatibility(self) -> dict[str, str]:
        return verification.compatibility_status()

    def get_snapshot(self) -> dict[str, Any]:
        """返回当前真值快照；兼容状态每次实时读取，避免缓存过期结论。"""
        state = _normalize_state(self._state.read())
        active = _active_workspace(state)
        return {
            **state,
            "compatibility": self.compatibility(),
            "execution_machine_id": model.LOCAL_EXECUTION_MACHINE_ID,
            "result_root_label": str(active.get("display_name", "独立本机工作区")),
        }

    def verify_path(self, payload: Any) -> dict[str, str]:
        """只探测不落盘；探测宿主由服务端上下文决定，不采信客户端标签。"""
        return verification.verify_executor_path(payload.access_path, payload.capability)

    def build_execution_context(
        self,
        *,
        client_instance_id: str | None = None,
        execution_machine_id: str | None = None,
        logical_source_id: str | None = None,
        capability: str = model.CAPABILITY_READ_WRITE,
    ) -> dict[str, Any]:
        """为真实项目/索引写入构建执行上下文。

        历史 ``verified`` 标记只作为映射存在性提示，不能代替执行前实时探测。
        NAS 工作区每次真实写入前都重新核验执行端，并用该映射派生结果落点；
        本机工作区保持既有数据根，避免迁移既有项目快照。
        """
        state = _normalize_state(self._state.read())
        client_id = (
            client_instance_id
            or workspace_context.current_client_instance_id()
            or "client_local"
        )
        self._check_writer_guard(state, client_id)
        active = _active_workspace(state)
        machine_id = execution_machine_id or model.LOCAL_EXECUTION_MACHINE_ID
        base = {
            "workspace_id": str(active["workspace_id"]),
            "workspace_mode": str(active.get("mode", model.MODE_LOCAL)),
            "data_namespace": str(active.get("data_namespace", "")),
            "execution_machine_id": machine_id,
            "client_instance_id": client_id,
            "capability": capability,
            "logical_source_id": logical_source_id,
            "mapping_id": None,
            "access_path": None,
            "verification": None,
            "compatibility": self.compatibility(),
        }
        if active.get("mode") != model.MODE_NAS:
            return {
                **base,
                "result_root": str(storage.data_root().expanduser().resolve(strict=False)),
                "result_root_label": str(active.get("display_name", "独立本机工作区")),
            }

        compatible = base["compatibility"]
        if compatible["status"] != model.COMPATIBILITY_COMPATIBLE:
            raise CleanroomException(
                status_code=409,
                code="WORKSPACE_INCOMPATIBLE",
                message=compatible["detail"],
            )
        candidates = [
            item
            for item in state["mappings"]
            if item.get("workspace_id") == active["workspace_id"]
            and item.get("execution_machine_id") == machine_id
            and (logical_source_id is None or item.get("logical_source_id") == logical_source_id)
        ]
        if not candidates:
            raise CleanroomException(
                status_code=409,
                code="WORKSPACE_MAPPING_REQUIRED",
                message="当前执行机器没有可用共享映射，项目/索引写入已拒绝，未产生半写入",
            )
        mapping = candidates[0]
        live_verification = verification.verify_executor_path(
            str(mapping["access_path"]), capability
        )
        access_path = str(mapping["access_path"])
        return {
            **base,
            "logical_source_id": str(mapping["logical_source_id"]),
            "mapping_id": str(mapping["mapping_id"]),
            "access_path": access_path,
            "verification": live_verification,
            "result_root": str(
                Path(access_path).expanduser().resolve(strict=False)
                / str(active.get("data_namespace", "workspace_nas_01"))
            ),
            "result_root_label": (
                f"{active.get('display_name', '连接 NAS 工作区')} · {machine_id}"
            ),
        }

    def _check_writer_guard(self, state: dict[str, Any], client_instance_id: str | None) -> None:
        guard = state.get("writer_guard")
        if not isinstance(guard, dict):
            return
        try:
            expires = datetime.strptime(
                str(guard.get("lease_expires_at", "")), "%Y-%m-%dT%H:%M:%SZ"
            ).replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            state["writer_guard"] = None
            return
        if expires <= datetime.now(timezone.utc):
            state["writer_guard"] = None
            return
        if guard.get("client_instance_id") != client_instance_id:
            raise CleanroomException(
                status_code=409,
                code="WORKSPACE_WRITE_CONFLICT",
                message="另一个客户端正在写入工作区，请稍后重试；不会静默接管",
            )

    def _mutate(self, expected_version: int, mutator: Any) -> dict[str, Any]:
        with storage.namespace_lock(model.STATE_NAMESPACE):
            state = _normalize_state(self._state.read())
            current = state["revision"]
            if current != expected_version:
                raise _conflict(
                    "VERSION_CONFLICT",
                    expected_version,
                    current,
                    "工作区配置已变更，请重新读取后再提交",
                )
            mutator(state)
            state["revision"] = current + 1
            self._state.write(state)
        return self.get_snapshot()

    def add_mapping(self, payload: Any) -> dict[str, Any]:
        """登记或刷新映射；同一逻辑来源+执行机器+工作区只保留一条记录。"""
        verification_result = verification.verify_executor_path(
            payload.access_path, payload.capability
        )
        try:
            root, prefix = storage.resolve_asset_registration_root(payload.access_path)
        except CleanroomException:
            raise CleanroomException(
                status_code=403,
                code="WORKSPACE_PATH_NOT_PERMITTED",
                message="该目录未获服务端授权，需共享/授权给实际执行端后再登记映射",
            ) from None
        root_identity = payload.root_identity_id or storage.root_identity_id(root)
        relative = storage.canonical_relpath(prefix) if prefix else ""
        logical_source_id = model.derive_logical_source_id(root_identity, relative)
        workspace_id = payload.workspace_id or _active_workspace(
            _normalize_state(self._state.read())
        )["workspace_id"]

        def mutate(state: dict[str, Any]) -> None:
            if not any(
                item.get("workspace_id") == workspace_id for item in state["workspaces"]
            ):
                raise CleanroomException(
                    status_code=404,
                    code="WORKSPACE_NOT_FOUND",
                    message="目标工作区不存在",
                )
            for existing in state["mappings"]:
                if (
                    existing.get("logical_source_id") == logical_source_id
                    and existing.get("execution_machine_id") == payload.execution_machine_id
                    and existing.get("workspace_id") == workspace_id
                ):
                    # 幂等刷新核验结果，绝不产生第二条来源登记。
                    existing["verification"] = verification_result
                    existing["expected_version"] = state["revision"] + 1
                    return
            state["mappings"].append(
                {
                    "mapping_id": _mapping_id(
                        logical_source_id, payload.execution_machine_id, workspace_id
                    ),
                    "workspace_id": workspace_id,
                    "logical_source_id": logical_source_id,
                    "root_identity_id": root_identity,
                    "execution_machine_id": payload.execution_machine_id,
                    "access_path": storage.normalize_directory_path(payload.access_path),
                    "capability": payload.capability,
                    "verification": verification_result,
                    "expected_version": state["revision"] + 1,
                }
            )

        return self._mutate(payload.expected_version, mutate)

    def switch_workspace(self, payload: Any) -> dict[str, Any]:
        """切换写入工作区：兼容检查 -> 执行端映射核验 -> CAS 提交。"""
        target_id = payload.workspace_id

        def mutate(state: dict[str, Any]) -> None:
            self._check_writer_guard(state, payload.client_instance_id)
            target = next(
                (item for item in state["workspaces"] if item.get("workspace_id") == target_id),
                None,
            )
            if target is None:
                raise CleanroomException(
                    status_code=404,
                    code="WORKSPACE_NOT_FOUND",
                    message="目标工作区不存在",
                )
            if (
                target["workspace_id"] != state["active_workspace_id"]
                and target.get("mode") == model.MODE_NAS
            ):
                compatible = self.compatibility()
                if compatible["status"] != model.COMPATIBILITY_COMPATIBLE:
                    raise CleanroomException(
                        status_code=409,
                        code="WORKSPACE_INCOMPATIBLE",
                        message=compatible["detail"],
                    )
                verified = [
                    item
                    for item in state["mappings"]
                    if item.get("workspace_id") == target_id
                    and item.get("verification", {}).get("status") == model.VERIFICATION_VERIFIED
                ]
                if not verified:
                    raise CleanroomException(
                        status_code=409,
                        code="WORKSPACE_MAPPING_REQUIRED",
                        message="连接 NAS 工作区前，请至少登记并核验一条共享路径映射",
                    )
            state["active_workspace_id"] = target_id
            if payload.client_instance_id:
                expires = datetime.now(timezone.utc) + timedelta(seconds=model.LEASE_SECONDS)
                state["writer_guard"] = {
                    "client_instance_id": payload.client_instance_id,
                    "lease_expires_at": expires.strftime("%Y-%m-%dT%H:%M:%SZ"),
                }

        return self._mutate(payload.expected_version, mutate)


default_workspace_service = WorkspaceService()
