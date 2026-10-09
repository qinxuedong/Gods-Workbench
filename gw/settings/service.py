# -*- coding: utf-8 -*-
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

"""设置页服务层实现（Phase 10D）。

遵循 ``docs/contracts/SETTINGS-INTERFACE-CATALOG.yaml`` 的当前配置治理契约：

- 稳定 ID：结构体 ``structure_id = strc_NNNN``（确定性序号，禁止随机数/uuid）；
  provider 标识一律来自调用方提交，服务端**绝不自造**厂商条目；
- CAS：存储设置与 provider 集合用 ``revision``，结构体用结构级 ``version``；
  写操作冲突一律 409（存储/平台用 VERSION_CONFLICT，结构体用 STRUCTURE_VERSION_CONFLICT）；
- **零伪造**：无真实来源时 storage-settings 返回 ``configured: false`` + ``data_gaps``，
  providers 返回 ``providers: []``；结构成员不编造素材元数据；
- **凭据边界**：provider 输入由严格DTO验证，显式更新的秘密仅加密落盘，
  不回显、不写环境文件；未知凭据别名拒绝；
- **fail-closed**：探测由probes模块执行；缺失依赖或上游失败如实返回503，
  不伪造模型列表、连通性成功或延迟数字。

证据边界：ProviderService使用三態隔离的原子JSON与受保护密文，支持重启恢复，
仅保证单进程一致性。存储设置与素材结构服务仍为内存实现。
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone
import stat
import threading
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from pydantic import ValidationError
from gw.core import storage
from gw.core.errors import CleanroomException
from gw.settings import credentials
from gw.settings.models import (
    DATA_STATUS_NOT_CONFIGURED,
    DATA_STATUS_OK,
    GAP_MEMBER_ASSET_METADATA,
    GAP_PROVIDER_REGISTRY_SOURCE,
    GAP_STORAGE_SETTINGS_SOURCE,
    PROVIDER_PROBE_NOT_INTEGRATED,
    AssetStructureCreateRequest,
    AssetStructureCurrentRequest,
    AssetStructureItem,
    AssetStructureListResponse,
    AssetStructureMember,
    AssetStructureUpdateRequest,
    ProviderSnapshot,
    ProviderEntryRequest,
    StorageSettingsPatchRequest,
    StorageSettingsSnapshot,
)

#: 结构类型白名单；其余取值一律 400 失败关闭。
ALLOWED_STRUCTURE_KINDS = frozenset({"version", "group"})

#: 凭据类字段名（小写比对）；命中即剥离，绝不落库或回显。
_CREDENTIAL_KEY_MARKERS = (
    "api_key",
    "apikey",
    "access_key",
    "secret",
    "token",
    "password",
    "credential",
    "wallet",
    "private_key",
)

#: provider 条目中允许对外回显的字段白名单形状之外，其余原样保留但先剥离凭据字段。
_CREDENTIAL_EXACT_KEYS = frozenset(
    {
        "key",
        "keys",
        "auth",
        "authorization",
    }
)


def _now_iso() -> str:
    """生成标准 ISO 8601 UTC 时间戳字符串。"""
    return datetime.now(timezone.utc).isoformat()


def _is_credential_key(key: str) -> bool:
    """判断字段名是否为凭据类字段（小写、去除分隔符后比对）。"""
    normalized = str(key or "").strip().casefold().replace("-", "_").replace(" ", "_")
    if not normalized:
        return False
    if normalized in _CREDENTIAL_EXACT_KEYS:
        return True
    return any(marker in normalized for marker in _CREDENTIAL_KEY_MARKERS)


def strip_credential_fields(value: Any) -> Any:
    """递归剥离凭据类字段；返回新对象，绝不修改入参。

    零凭据口径：任何形如 ``api_key`` / ``access_key`` / ``token`` / ``secret`` /
    ``password`` 的字段都不落库、不进入响应。
    """
    if isinstance(value, dict):
        cleaned: Dict[str, Any] = {}
        for key, item in value.items():
            if _is_credential_key(key):
                continue
            cleaned[key] = strip_credential_fields(item)
        return cleaned
    if isinstance(value, list):
        return [strip_credential_fields(item) for item in value]
    return copy.deepcopy(value)


def _require_positive_version(expected_version: Optional[int], label: str) -> None:
    """expected_version 若提供必须为正整数；否则 400。"""
    if expected_version is not None and (not isinstance(expected_version, int) or isinstance(expected_version, bool) or expected_version < 1):
        raise CleanroomException(
            status_code=400,
            code="INVALID_REQUEST",
            message=f"{label} expected_version 必须为正整数",
        )


def _resolve_expected_version(payload: Any) -> Optional[int]:
    """解析 CAS 期望版本；``expected_revision`` 是前端既有别名。"""
    expected = getattr(payload, "expected_version", None)
    if expected is None:
        expected = getattr(payload, "expected_revision", None)
    return expected


def _conflict(
    code: str,
    expected_version: int,
    current_version: int,
    message: str,
) -> CleanroomException:
    """构造标准 409 冲突错误包（含 expected/current 版本便于调用方自愈）。"""
    return CleanroomException(
        status_code=409,
        code=code,
        message=message,
        extra={"expected_version": expected_version, "current_version": current_version},
    )


def _normalize_asset_ids(raw: Any, label: str) -> List[str]:
    """校验并规范化 asset_ids：至少 2 个、去重后不得为空、禁止重复项。"""
    if not isinstance(raw, (list, tuple)):
        raise CleanroomException(
            status_code=400,
            code="INVALID_REQUEST",
            message=f"{label} 必须为数组",
        )
    ids = [str(item or "").strip() for item in raw]
    if any(not item for item in ids):
        raise CleanroomException(
            status_code=400,
            code="INVALID_REQUEST",
            message=f"{label} 不能包含空标识",
        )
    if len(ids) != len(set(ids)):
        raise CleanroomException(
            status_code=400,
            code="INVALID_REQUEST",
            message=f"{label} 不能包含重复项",
        )
    if len(ids) < 2:
        raise CleanroomException(
            status_code=400,
            code="INVALID_REQUEST",
            message=f"{label} 至少需要 2 个成员",
        )
    return ids


class StorageSettingsService:
    """存储/目录设置的内存快照与 CAS 版本服务。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._revision = 1
        self._configured = False
        self._dirs: Dict[str, Any] = {}
        self._defaults: Dict[str, Any] = {}
        self._extra_local_entries: List[Dict[str, Any]] = []
        self._local_library_names: Dict[str, str] = {}
        self._local_library_order: List[str] = []

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------
    def get_snapshot(self) -> StorageSettingsSnapshot:
        """返回存储设置真值快照；无真实来源时如实标记未配置。"""
        with self._lock:
            return self._build_snapshot_locked()

    def _build_snapshot_locked(self) -> StorageSettingsSnapshot:
        extra_dirs = [entry.get("path", "") for entry in self._extra_local_entries]
        gaps = [] if self._configured else [GAP_STORAGE_SETTINGS_SOURCE]
        return StorageSettingsSnapshot(
            configured=self._configured,
            revision=self._revision,
            dirs=copy.deepcopy(self._dirs),
            defaults=copy.deepcopy(self._defaults),
            extra_local_dirs=extra_dirs,
            extra_local_entries=copy.deepcopy(self._extra_local_entries),
            local_library_names=dict(self._local_library_names),
            local_library_order=list(self._local_library_order),
            local_libraries=self._build_local_libraries_locked(),
            data_status=DATA_STATUS_OK if self._configured else DATA_STATUS_NOT_CONFIGURED,
            data_gaps=gaps,
        )

    def _build_local_libraries_locked(self) -> List[Dict[str, Any]]:
        """把本地目录与附加来源投影为前端直接可用的本地素材库列表。"""
        libraries: List[Dict[str, Any]] = []
        local_path = str(self._dirs.get("local") or "").strip()
        if local_path or self._configured:
            libraries.append(
                {
                    "id": "local",
                    "kind": "local",
                    "name": self._local_library_names.get("local") or "本地上传",
                    "path": local_path,
                }
            )
        for index, entry in enumerate(self._extra_local_entries):
            entry_id = str(entry.get("id") or entry.get("kind") or f"local_extra_{index}")
            libraries.append(
                {
                    "id": entry_id,
                    "kind": str(entry.get("kind") or entry_id),
                    "name": self._local_library_names.get(entry_id) or entry.get("name") or f"素材来源 {index + 1}",
                    "path": str(entry.get("path") or ""),
                }
            )
        return libraries

    # ------------------------------------------------------------------
    # 写入
    # ------------------------------------------------------------------
    def patch(self, payload: StorageSettingsPatchRequest) -> StorageSettingsSnapshot:
        """更新存储设置；CAS 冲突失败关闭，成功则 revision 递增。"""
        expected = _resolve_expected_version(payload)
        _require_positive_version(expected, "存储设置")
        with self._lock:
            if expected is not None and expected != self._revision:
                raise _conflict(
                    "VERSION_CONFLICT",
                    expected,
                    self._revision,
                    f"存储设置版本冲突（期望 {expected}，当前 {self._revision}），请重新读取后重试",
                )
            if payload.dirs is not None:
                self._dirs = copy.deepcopy(payload.dirs)
            if payload.extra_local_entries is not None:
                self._extra_local_entries = copy.deepcopy(payload.extra_local_entries)
                self._dirs = dict(self._dirs)
                self._dirs["extra_local_dirs"] = [
                    str(entry.get("path") or "") for entry in self._extra_local_entries
                ]
            if payload.local_library_names is not None:
                self._local_library_names = {str(k): str(v) for k, v in payload.local_library_names.items()}
            if payload.local_library_order is not None:
                self._local_library_order = [str(item) for item in payload.local_library_order]
            self._configured = True
            self._revision += 1
            return self._build_snapshot_locked()


class ProviderService:
    """平台与密文单快照原子落盘；各实例共享命名空间CAS锁。"""

    _secrets = {
        "api_key": ("clear_key", "has_key"),
        "wallet_api_key": ("clear_wallet_key", "has_wallet_key"),
        "volcengine_access_key_id": ("clear_volcengine_access_key_id", "has_volcengine_access_key"),
        "volcengine_secret_access_key": ("clear_volcengine_secret_access_key", "has_volcengine_secret_key"),
    }

    def __init__(self) -> None:
        self._state = storage.JsonState("providers", self._empty_state)

    @staticmethod
    def _empty_state():
        # 通用JSON读器对损坏文件返回默认值；配置集合必须拒绝该降级，避免下一次保存清空。
        if (storage.data_root() / "providers.json").exists():
            raise CleanroomException(503, "PROVIDER_STORAGE_UNAVAILABLE", "平台配置文件无法读取，未覆盖已有配置")
        return {"schema_version": 1, "revision": 1, "providers": [], "credentials": {}}

    def _checked_state(self, state):
        if (not isinstance(state, dict) or state.get("schema_version") != 1
                or set(state) != {"schema_version", "revision", "providers", "credentials"}
                or type(state.get("revision")) is not int or state["revision"] < 1
                or not isinstance(state.get("providers"), list) or not isinstance(state.get("credentials"), dict)):
            raise CleanroomException(503, "PROVIDER_STORAGE_UNAVAILABLE", "平台配置快照格式无效")
        try:
            seen = set()
            for item in state["providers"]:
                model = ProviderEntryRequest.model_validate(item)
                provider_id = model.provider_id or model.id
                if provider_id in seen or any(key in item for key in self._secrets):
                    raise ValueError()
                seen.add(provider_id)
            if set(state["credentials"]) - seen:
                raise ValueError()
            for fields in state["credentials"].values():
                if not isinstance(fields, dict) or set(fields) - set(self._secrets):
                    raise ValueError()
                if any(not isinstance(value, str) or not value for value in fields.values()):
                    raise ValueError()
        except (ValidationError, ValueError, TypeError):
            raise CleanroomException(503, "PROVIDER_STORAGE_UNAVAILABLE", "平台配置快照格式无效") from None
        return state

    @staticmethod
    def _check_files():
        """读取与写入前拒绝目标/临时文件链接，避免通用JSON读器跟随链接。"""
        for name in ("providers.json", "providers.json.tmp"):
            path = storage.data_root() / name
            try:
                info = path.lstat()
            except FileNotFoundError:
                continue
            if (not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)
                    or getattr(info, "st_file_attributes", 0) & 0x400):
                raise CleanroomException(503, "PROVIDER_STORAGE_UNAVAILABLE", "平台配置文件不允许链接或异常类型")

    def get_snapshot(self) -> ProviderSnapshot:
        self._check_files()
        return self._snapshot(self._checked_state(self._state.read()))

    def _snapshot(self, state) -> ProviderSnapshot:
        public = copy.deepcopy(state["providers"])
        for item in public:
            provider_id = item["provider_id"]
            item["source"] = "settings"
            for field, (_, flag) in self._secrets.items():
                encrypted = state["credentials"].get(provider_id, {}).get(field)
                item[flag] = bool(encrypted and credentials.reveal(provider_id, field, encrypted))
        configured = bool(public)
        return ProviderSnapshot(
            providers=public,
            revision=state["revision"],
            configured=configured,
            data_status=DATA_STATUS_OK if configured else DATA_STATUS_NOT_CONFIGURED,
            data_gaps=[] if configured else [GAP_PROVIDER_REGISTRY_SOURCE],
        )

    def replace(self, raw_providers: Any, expected_version: Optional[int]) -> ProviderSnapshot:
        """整体验证后持锁提交；空密钥保持、clear显式删除、非空密钥替换。"""
        _require_positive_version(expected_version, "provider 集合")
        if expected_version is None:
            raise CleanroomException(400, "EXPECTED_VERSION_REQUIRED", "保存平台配置必须携带expected_version")
        if not isinstance(raw_providers, list) or len(raw_providers) > 128:
            raise CleanroomException(
                status_code=400,
                code="INVALID_REQUEST",
                message="provider 请求体必须为数组",
            )
        normalized: List[ProviderEntryRequest] = []
        seen: set = set()
        for index, item in enumerate(raw_providers):
            if not isinstance(item, dict):
                raise CleanroomException(
                    status_code=400,
                    code="INVALID_REQUEST",
                    message=f"provider 第 {index + 1} 项必须为对象",
                )
            try:
                model = ProviderEntryRequest.model_validate(item)
            except ValidationError:
                # Pydantic异常包含input；不可把原文或字段值写入响应与日志。
                raise CleanroomException(400, "INVALID_REQUEST", f"平台第{index + 1}项字段或更新意图不合法") from None
            provider_id = model.provider_id or model.id
            if provider_id in seen:
                raise CleanroomException(
                    status_code=400,
                    code="INVALID_REQUEST",
                    message=f"provider_id 重复：{provider_id}",
                )
            seen.add(provider_id)
            normalized.append(model)

        def commit(state):
            self._checked_state(state)
            if expected_version != state["revision"]:
                raise _conflict(
                    "VERSION_CONFLICT",
                    expected_version,
                    state["revision"],
                    "平台集合已被修改，请保留编辑内容并重新读取后重试",
                )
            public, encrypted = [], {}
            excluded = set(self._secrets) | {value[0] for value in self._secrets.values()}
            for model in normalized:
                provider_id = model.provider_id or model.id
                fields = copy.deepcopy(state["credentials"].get(provider_id, {}))
                for field, (clear, _) in self._secrets.items():
                    if getattr(model, clear):
                        fields.pop(field, None)
                    elif (getattr(model, field) or "").strip():
                        fields[field] = credentials.protect(provider_id, field, getattr(model, field).strip())
                item = model.model_dump(exclude=excluded)
                item["id"] = item["provider_id"] = provider_id
                public.append(item)
                if fields:
                    encrypted[provider_id] = fields
            state.update(providers=public, credentials=encrypted, revision=state["revision"] + 1)
            return self._snapshot(state)

        try:
            self._check_files()
            return self._state.mutate(commit)
        except OSError:
            raise CleanroomException(503, "PROVIDER_STORAGE_UNAVAILABLE", "平台配置未能落盘，原配置保持不变") from None

    def credential(self, provider_id: str, field: str = "api_key", *, expected_version: Optional[int] = None) -> str:
        """仅供执行服务按稳定ID读取；HTTP响应不得调用后回显原文。"""
        if field not in self._secrets:
            raise ValueError("凭据字段不受支持")
        self._check_files()
        state = self._checked_state(self._state.read())
        if expected_version is not None and state["revision"] != expected_version:
            raise _conflict("VERSION_CONFLICT", expected_version, state["revision"],
                            "平台配置已变化，未使用新密钥执行旧请求，请重新开始操作")
        encrypted = state["credentials"].get(provider_id, {}).get(field)
        return credentials.reveal(provider_id, field, encrypted) if encrypted else ""


class AssetStructureService:
    """素材版本/组结构（stable structure_id）的内存存储与 CAS 版本服务。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._revision = 1
        self._sequence = 0
        self._structures: Dict[str, AssetStructureItem] = {}

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------
    def list_structures(self, asset_ids: Optional[Sequence[str]] = None) -> AssetStructureListResponse:
        """返回结构集合；给定 asset_ids 时按成员做真实过滤。"""
        wanted = {str(item or "").strip() for item in (asset_ids or []) if str(item or "").strip()}
        with self._lock:
            items = [copy.deepcopy(item) for item in self._sorted_structures_locked()]
        if wanted:
            items = [
                item for item in items
                if any(member.asset_id in wanted for member in item.members)
            ]
        return AssetStructureListResponse(
            items=items,
            total=len(items),
            revision=self._revision,
            data_status=DATA_STATUS_OK,
            data_gaps=[GAP_MEMBER_ASSET_METADATA],
        )

    def get_structure(self, structure_id: str) -> AssetStructureItem:
        """读取单个结构；不存在必须 404 FAIL-CLOSED。"""
        with self._lock:
            item = self._structures.get(str(structure_id or ""))
            if item is None:
                raise self._not_found(structure_id)
            return copy.deepcopy(item)

    def _sorted_structures_locked(self) -> List[AssetStructureItem]:
        return [self._structures[key] for key in sorted(self._structures)]

    @staticmethod
    def _not_found(structure_id: str) -> CleanroomException:
        return CleanroomException(
            status_code=404,
            code="STRUCTURE_NOT_FOUND",
            message=f"结构 {structure_id} 不存在",
        )

    @staticmethod
    def _next_id(sequence: int) -> str:
        return f"strc_{sequence:04d}"

    # ------------------------------------------------------------------
    # 写入
    # ------------------------------------------------------------------
    def create_structure(self, payload: AssetStructureCreateRequest) -> AssetStructureItem:
        """创建结构；集合 CAS 冲突失败关闭，成功则集合 revision 递增。"""
        kind = str(payload.kind or "").strip().lower()
        if kind not in ALLOWED_STRUCTURE_KINDS:
            raise CleanroomException(
                status_code=400,
                code="INVALID_REQUEST",
                message=f"结构类型不合法：{payload.kind}",
            )
        asset_ids = _normalize_asset_ids(payload.asset_ids, "asset_ids")
        current_asset_id = self._resolve_current(payload.current_asset_id, asset_ids)
        _require_positive_version(payload.expected_version, "结构集合")

        with self._lock:
            if payload.expected_version is not None and payload.expected_version != self._revision:
                raise _conflict(
                    "STRUCTURE_VERSION_CONFLICT",
                    payload.expected_version,
                    self._revision,
                    f"结构集合版本冲突（期望 {payload.expected_version}，当前 {self._revision}），请重新读取后重试",
                )
            self._sequence += 1
            now = _now_iso()
            structure = AssetStructureItem(
                structure_id=self._next_id(self._sequence),
                kind=kind,
                current_asset_id=current_asset_id,
                version=1,
                created_at=now,
                updated_at=now,
                members=self._build_members(asset_ids),
            )
            self._structures[structure.structure_id] = structure
            self._revision += 1
            return copy.deepcopy(structure)

    def update_structure(self, structure_id: str, payload: AssetStructureUpdateRequest) -> AssetStructureItem:
        """更新结构；结构级 CAS 冲突失败关闭，成功则结构 version 递增。"""
        if payload.asset_ids is None and payload.current_asset_id is None:
            raise CleanroomException(
                status_code=400,
                code="INVALID_REQUEST",
                message="更新结构至少需要 asset_ids 或 current_asset_id 之一",
            )
        expected = payload.expected_version
        _require_positive_version(expected, "结构")
        with self._lock:
            item = self._structures.get(str(structure_id or ""))
            if item is None:
                raise self._not_found(structure_id)
            if expected is not None and expected != item.version:
                raise _conflict(
                    "STRUCTURE_VERSION_CONFLICT",
                    expected,
                    item.version,
                    f"结构 {item.structure_id} 版本冲突（期望 {expected}，当前 {item.version}），请重新读取后重试",
                )
            asset_ids = (
                _normalize_asset_ids(payload.asset_ids, "asset_ids")
                if payload.asset_ids is not None
                else [member.asset_id for member in item.members]
            )
            requested_current = payload.current_asset_id if payload.current_asset_id is not None else item.current_asset_id
            current_asset_id = self._resolve_current(requested_current, asset_ids)
            updated = item.model_copy(
                update={
                    "current_asset_id": current_asset_id,
                    "version": item.version + 1,
                    "updated_at": _now_iso(),
                    "members": self._build_members(asset_ids),
                }
            )
            self._structures[item.structure_id] = updated
            return copy.deepcopy(updated)

    def set_current(self, structure_id: str, payload: AssetStructureCurrentRequest) -> AssetStructureItem:
        """切换当前代表素材；结构级 CAS 冲突失败关闭。"""
        expected = payload.expected_version
        _require_positive_version(expected, "结构")
        with self._lock:
            item = self._structures.get(str(structure_id or ""))
            if item is None:
                raise self._not_found(structure_id)
            if expected is not None and expected != item.version:
                raise _conflict(
                    "STRUCTURE_VERSION_CONFLICT",
                    expected,
                    item.version,
                    f"结构 {item.structure_id} 版本冲突（期望 {expected}，当前 {item.version}），请重新读取后重试",
                )
            member_ids = [member.asset_id for member in item.members]
            current_asset_id = self._resolve_current(payload.current_asset_id, member_ids)
            updated = item.model_copy(
                update={
                    "current_asset_id": current_asset_id,
                    "version": item.version + 1,
                    "updated_at": _now_iso(),
                }
            )
            self._structures[item.structure_id] = updated
            return copy.deepcopy(updated)

    def delete_structure(self, structure_id: str, expected_version: Optional[int]) -> Tuple[str, int]:
        """删除结构；结构级 CAS 冲突失败关闭，成功则集合 revision 递增。"""
        _require_positive_version(expected_version, "结构")
        with self._lock:
            item = self._structures.get(str(structure_id or ""))
            if item is None:
                raise self._not_found(structure_id)
            if expected_version is not None and expected_version != item.version:
                raise _conflict(
                    "STRUCTURE_VERSION_CONFLICT",
                    expected_version,
                    item.version,
                    f"结构 {item.structure_id} 版本冲突（期望 {expected_version}，当前 {item.version}），请重新读取后重试",
                )
            del self._structures[item.structure_id]
            self._revision += 1
            return item.structure_id, self._revision

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------
    @staticmethod
    def _build_members(asset_ids: Iterable[str]) -> List[AssetStructureMember]:
        """按调用方给定顺序建立成员；不编造任何素材元数据。"""
        return [
            AssetStructureMember(asset_id=asset_id, sort_order=index)
            for index, asset_id in enumerate(asset_ids)
        ]

    @staticmethod
    def _resolve_current(raw_current: Optional[str], asset_ids: Sequence[str]) -> str:
        """解析当前代表素材；缺省取首个成员，非成员一律 400。"""
        requested = str(raw_current or "").strip()
        if not requested:
            return asset_ids[0]
        if requested not in set(asset_ids):
            raise CleanroomException(
                status_code=400,
                code="INVALID_REQUEST",
                message=f"current_asset_id 必须属于成员：{requested}",
            )
        return requested


def raise_probe_not_integrated(endpoint: str) -> None:
    """探测端点统一 fail-closed：本阶段无真实出网能力，如实返回未接入错误包。"""
    raise CleanroomException(
        status_code=503,
        code=PROVIDER_PROBE_NOT_INTEGRATED,
        message=(
            f"{endpoint} 在本阶段无真实外网探测能力，已按洁净室口径拒绝执行；"
            "未返回任何模型列表 / 连通性结论 / 延迟数字。"
        ),
        extra={"endpoint": endpoint, "unavailable": True},
    )


#: 进程内单例（与既有各阶段同口径：重启即丢失、多 worker 不共享）。
default_storage_settings_service = StorageSettingsService()
default_provider_service = ProviderService()
default_asset_structure_service = AssetStructureService()
