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

"""统一资产中枢桥接服务（Phase 10A / 12 对齐）。

实现工作流文档与执行产物向统一资产中枢（AssetLibraryService 与 AssetRegistry）
的可恢复登记与主体稳定 ID 绑定；两份存储不能冒充原子事务。
"""

from __future__ import annotations

import logging
import uuid
import hashlib
import json
from functools import wraps
from typing import Any, Dict, Optional

from gw.asset_library.service import default_asset_library_service
from gw.asset_registry import repository as asset_repo
from gw.core.asset_visibility import asset_owner_key
from gw.god_workflow import registry

logger = logging.getLogger(__name__)


def _serialized(function):
    @wraps(function)
    def operation(*args, **kwargs):
        with registry._STORAGE_LOCK:
            return function(*args, **kwargs)
    return operation


def _receipt(context, key, payload):
    directory = registry._principal_root(context) / "asset-hub-pending"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (hashlib.sha256(key.encode()).hexdigest() + ".json")
    registry._atomic_write(path, payload)
    return path


def registration_health(result):
    pending = bool(result.get("error"))
    return {"data_status": "partial" if pending else "ok",
            "data_gaps": ["asset_hub_registration_pending"] if pending else []}


def _workflow_asset_id(context, workflow_id):
    # 外部来源也可能使用 UUID；所有文档资产都必须进入主体命名空间。
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"gw:workflow:{asset_owner_key(context)}:{workflow_id}"))


@_serialized
def remove_workflow_from_asset_hub(context, workflow_id):
    asset_id = _workflow_asset_id(context, workflow_id)
    owner = asset_owner_key(context)
    try:
        receipt = _receipt(context, asset_id, {"kind": "workflow_delete", "workflow_id": workflow_id})
        default_asset_library_service.remove_bridge_item(asset_id, owner)
        asset_repo.remove_bridge_asset(asset_id, owner)
        receipt.unlink(missing_ok=True)
        return {"asset_id": asset_id, "removed": True}
    except Exception as exc:
        logger.warning("移除工作流资产登记失败: %s", type(exc).__name__)
        return {"asset_id": asset_id, "error": "ASSET_HUB_REMOVAL_FAILED"}


@_serialized
def register_workflow_to_asset_hub(
    workflow_id: str,
    name: str,
    *,
    document: Optional[Dict[str, Any]] = None,
    context=None,
) -> Dict[str, Any]:
    """把工作流文档登记至统一资产中枢，存入资产库工作流分类，保证全局稳定 ID 绑定。"""
    if not workflow_id or context is None:
        return {}
    # 慢请求不能在新版本或删除后把旧投影写回；以主体文档为唯一真源。
    source = registry._principal_root(context) / (str(workflow_id) + '.json')
    try:
        if not source.resolve().is_relative_to(registry._principal_root(context).resolve()):
            raise OSError('来源路径越界')
        document = json.loads(source.read_text(encoding='utf-8'))
        name = document.get('name', name)
    except (OSError, ValueError, AttributeError):
        return {"asset_id": _workflow_asset_id(context, workflow_id), "error": "ASSET_HUB_SOURCE_UNAVAILABLE"}

    wf_name = (name or "").strip() or "工作流"
    asset_id = _workflow_asset_id(context, workflow_id)
    owner = asset_owner_key(context)
    url = f"/static/pages/workflow.html?id={workflow_id}"

    try:
        receipt = _receipt(context, asset_id, {"kind": "workflow", "workflow_id": workflow_id})
        # 1. 登记至素材库服务 (AssetLibraryService)，归入 workflow 分类
        lib_item = default_asset_library_service.register_item(
            asset_id=asset_id,
            name=wf_name,
            category_type="workflow",
            category_name="工作流",
            url=url,
            workflow_owner=owner,
        )

        # 2. 登记至底层资产注册表 (AssetRegistry)
        node_count = len(document.get("nodes", [])) if isinstance(document, dict) else 0
        asset_repo.register_asset_record(
            asset_id=asset_id,
            name=wf_name,
            kind="workflow",
            url=url,
            metadata={
                "workflow_id": workflow_id,
                "workflow_owner": owner,
                "node_count": node_count,
                "source_type": document.get("source_type") if isinstance(document, dict) else None,
                "workflow_version": document.get("version") if isinstance(document, dict) else None,
                "source_context": document.get("metadata", {}).get("source_context", {}) if isinstance(document, dict) else {},
            },
        )

        receipt.unlink(missing_ok=True)
        return lib_item.model_dump()
    except Exception as exc:
        logger.warning("登记工作流至统一资产中枢异常: %s", type(exc).__name__)
        return {"asset_id": asset_id, "error": "ASSET_HUB_REGISTRATION_FAILED"}


@_serialized
def register_output_to_asset_hub(
    asset_dict: Dict[str, Any],
    *, context=None,
) -> Dict[str, Any]:
    """把工作流执行产物登记至统一资产中枢，存入资产库图片/输出分类，保证全局稳定 ID 绑定。"""
    asset_id = str(asset_dict.get("asset_id") or "")
    if not asset_id or context is None:
        return {}

    filename = str(asset_dict.get("filename") or "output.png")
    media_type = str(asset_dict.get("media_type") or "image")
    url = str(asset_dict.get("url") or f"/api/god_workflow/assets/{asset_id}/content")
    task_id = asset_dict.get("task_id")

    try:
        receipt = _receipt(context, asset_id, {"kind": "output", "asset_id": asset_id})
        # 1. 登记至素材库服务 (AssetLibraryService)，归入 image 分类
        lib_item = default_asset_library_service.register_item(
            asset_id=asset_id,
            name=filename,
            category_type=media_type,
            category_name={"image": "图片", "video": "视频", "document": "产物原件"}.get(media_type, "产物原件"),
            url=url,
            workflow_owner=asset_owner_key(context),
        )

        # 2. 登记至底层资产注册表 (AssetRegistry)
        asset_repo.register_asset_record(
            asset_id=asset_id,
            name=filename,
            kind=media_type,
            url=url,
            task_id=str(task_id) if task_id else None,
            metadata={
                "task_id": task_id,
                "workflow_owner": asset_owner_key(context),
                "size_bytes": asset_dict.get("size_bytes"),
                "width": asset_dict.get("width"),
                "height": asset_dict.get("height"),
                "extension": asset_dict.get("extension"),
                "job_id": asset_dict.get("job_id"),
                "workflow_id": asset_dict.get("workflow_id"),
                "workflow_version": asset_dict.get("workflow_version"),
                "source_context": asset_dict.get("source_context", {}),
            },
        )

        receipt.unlink(missing_ok=True)
        return lib_item.model_dump()
    except Exception as exc:
        logger.warning("登记执行产物至统一资产中枢异常: %s", type(exc).__name__)
        return {"asset_id": asset_id, "error": "ASSET_HUB_REGISTRATION_FAILED"}


def reconcile_pending(context):
    """从主体源记录恢复跨存储登记；重试不会再次提交生成。"""
    directory = registry._principal_root(context) / "asset-hub-pending"
    results = []
    for path in sorted(directory.glob("*.json")):
        try:
            receipt = json.loads(path.read_text(encoding="utf-8"))
            if receipt["kind"] == "workflow_delete":
                result = remove_workflow_from_asset_hub(context, receipt["workflow_id"])
            elif receipt["kind"] == "workflow":
                document_path = registry._principal_root(context) / (str(receipt["workflow_id"]) + ".json")
                document = json.loads(document_path.read_text(encoding="utf-8"))
                result = register_workflow_to_asset_hub(receipt["workflow_id"], document.get("name"), document=document, context=context)
            else:
                asset = next(value for value in registry.load_assets(context) if value["asset_id"] == receipt["asset_id"])
                result = register_output_to_asset_hub(asset, context=context)
            results.append(result)
        except (OSError, ValueError, KeyError, StopIteration):
            results.append({"error": "ASSET_HUB_SOURCE_UNAVAILABLE"})
    return {"results": results, "pending": sum(bool(value.get("error")) for value in results)}
