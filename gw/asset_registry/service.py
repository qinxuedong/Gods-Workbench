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

"""Phase 11 B3 素材注册表的洁净室服务。

本块先建立可审计的注册表边界，而不是伪造文件/媒体/远程索引能力：
- 仅返回进程内可证明的空注册表、空回收站、空关系和空版本集合；
- 所有会读取或写入真实文件、媒体、远程资源、索引、压缩包的操作显式 503；
- 不创建演示素材，不扫描本机路径，不执行外部程序或网络请求；
- 所有响应携带 data_status/data_gaps，便于前端保留显式降级。
"""

from __future__ import annotations

import copy
import threading
from typing import Any, Dict, List, Optional

from gw.core.errors import CleanroomException

DATA_STATUS_OK = "ok"
DATA_STATUS_NOT_INTEGRATED = "not_integrated"
ASSET_REGISTRY_NOT_INTEGRATED = "ASSET_REGISTRY_NOT_INTEGRATED"
ASSET_NOT_FOUND = "ASSET_NOT_FOUND"
JOB_NOT_FOUND = "WORKSPACE_JOB_NOT_FOUND"

_GAP_REGISTRY_SOURCE = "asset_registry_source_not_connected"
_GAP_MEDIA_SOURCE = "media_storage_not_connected"
_GAP_REMOTE_SOURCE = "remote_asset_source_not_connected"
_GAP_INDEX_SOURCE = "asset_index_not_connected"


def _not_integrated(endpoint: str, *, code: str = ASSET_REGISTRY_NOT_INTEGRATED, gap: str = _GAP_REGISTRY_SOURCE) -> None:
    """对未获准入的副作用统一失败关闭。"""
    raise CleanroomException(
        status_code=503,
        code=code,
        message="素材注册表该能力尚未接入真实数据源，已拒绝执行；未读取或写入外部资源。",
        extra={
            "endpoint": endpoint,
            "unavailable": True,
            "data_status": DATA_STATUS_NOT_INTEGRATED,
            "data_gaps": [gap],
        },
        expose_extra_fields={"endpoint", "unavailable", "data_status"},
    )


def _not_found(code: str, message: str) -> None:
    """返回可区分的资源不存在错误。"""
    raise CleanroomException(status_code=404, code=code, message=message)


def _truthful_meta(*, gaps: Optional[List[str]] = None) -> Dict[str, Any]:
    """构造统一的可用性元数据，不把空集合误报为成功抓取。"""
    return {
        "data_status": DATA_STATUS_OK if not gaps else DATA_STATUS_NOT_INTEGRATED,
        "data_gaps": list(gaps or []),
    }


class AssetRegistryService:
    """素材注册表的最小进程内真值。

    当前不从旧仓、数据库、本地目录或远程服务读取资产，因此初始集合严格为空。
    这样可以支持列表/状态/治理页面的确定性空态，同时让真实资源操作保持 503。
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._revision = 1
        self._team_preferences: Dict[str, Any] = {
            "team_id": None,
            "default_library_id": None,
            "default_folder_id": None,
            "view": "grid",
            "sort": "newest",
            "version": 1,
        }

    def _meta(self) -> Dict[str, Any]:
        return {"revision": self._revision, **_truthful_meta()}

    def root_snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "registry": "asset-registry",
                "revision": self._revision,
                "assets": [],
                "features": self._features(),
                "backend": "cleanroom-memory",
                **_truthful_meta(),
            }

    def status(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "ready": True,
                "backend": "cleanroom-memory",
                "revision": self._revision,
                "assets_count": 0,
                "jobs": [],
                "overview": {"assets": 0, "projects": 0, "canvases": 0},
                "features": self._features(),
                **_truthful_meta(),
            }

    @staticmethod
    def _features() -> List[Dict[str, Any]]:
        return [
            {"id": "asset_registry", "name": "素材登记", "enabled": False, "data_status": DATA_STATUS_NOT_INTEGRATED},
            {"id": "media_preview", "name": "媒体预览", "enabled": False, "data_status": DATA_STATUS_NOT_INTEGRATED},
            {"id": "remote_assets", "name": "远程素材", "enabled": False, "data_status": DATA_STATUS_NOT_INTEGRATED},
            {"id": "index_automation", "name": "索引自动化", "enabled": False, "data_status": DATA_STATUS_NOT_INTEGRATED},
        ]

    def list_assets(self, *, limit: int = 100, offset: int = 0, cursor: Optional[str] = None, **_: Any) -> Dict[str, Any]:
        # 当前真实集合为空；返回稳定的分页外壳，不生成演示数据。
        if limit < 1 or limit > 500:
            raise CleanroomException(status_code=400, code="INVALID_LIMIT", message="limit 必须在 1 到 500 之间")
        if offset < 0:
            raise CleanroomException(status_code=400, code="INVALID_OFFSET", message="offset 不能为负数")
        with self._lock:
            return {
                "assets": [],
                "total": 0,
                "limit": limit,
                "offset": offset,
                "next_cursor": None,
                "revision": self._revision,
                **_truthful_meta(),
            }

    def get_asset(self, asset_id: str) -> Dict[str, Any]:
        # 初始集合没有任何 asset_id；不伪造一个空 asset 详情。
        _not_found(ASSET_NOT_FOUND, f"素材 {asset_id} 不存在")

    def image_versions(self, asset_id: str, *, limit: int = 500) -> Dict[str, Any]:
        if limit < 1 or limit > 500:
            raise CleanroomException(status_code=400, code="INVALID_LIMIT", message="limit 必须在 1 到 500 之间")
        self.get_asset(asset_id)
        return {"versions": [], "asset_id": asset_id, **_truthful_meta()}

    def facets(self) -> Dict[str, Any]:
        return {"kinds": [], "categories": [], "tags": [], "collections": [], "roots": [], **_truthful_meta()}

    def folders(self, root_id: Optional[str] = None) -> Dict[str, Any]:
        return {"folders": [], "root_id": root_id, "revision": self._revision, **_truthful_meta()}

    def preferences(self) -> Dict[str, Any]:
        with self._lock:
            return {"preferences": copy.deepcopy(self._team_preferences), **self._meta()}

    def recycle_bin(self) -> Dict[str, Any]:
        return {"items": [], "assets": [], "projects": [], "canvases": [], "revision": self._revision, **_truthful_meta()}

    def governance_overview(self) -> Dict[str, Any]:
        return {
            "assets": [],
            "projects": [],
            "canvases": [],
            "outbox": {"pending": 0, "failed": 0, "remaining": 0, "replayed": 0},
            "revision": self._revision,
            **_truthful_meta(),
        }

    def templates(self, project_type: Optional[str] = None) -> Dict[str, Any]:
        return {"templates": [], "project_type": project_type, "revision": self._revision, **_truthful_meta()}

    def workspace_job(self, job_id: str) -> Dict[str, Any]:
        _not_found(JOB_NOT_FOUND, f"工作区任务 {job_id} 不存在")

    def assert_feature_id(self, feature_id: str) -> None:
        known = {item["id"] for item in self._features()}
        if feature_id not in known:
            _not_found("FEATURE_NOT_FOUND", f"素材注册表能力 {feature_id} 不存在")


# 单例只保存非敏感、进程内可重建状态。
default_asset_registry_service = AssetRegistryService()


__all__ = [
    "ASSET_REGISTRY_NOT_INTEGRATED",
    "ASSET_NOT_FOUND",
    "DATA_STATUS_NOT_INTEGRATED",
    "DATA_STATUS_OK",
    "JOB_NOT_FOUND",
    "AssetRegistryService",
    "default_asset_registry_service",
    "_not_integrated",
]
