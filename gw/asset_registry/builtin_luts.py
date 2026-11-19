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

"""QIN-553：预置 LUT 包的内置登记（幂等 ensure）。

仓库内 ``web/vendor/luts/`` 只读分发合法 ``.cube``；首次进入 LUT 库时前端
调用 ``ensure_builtin_luts``，服务端把文件复制到数据根受管目录
``registry_uploads/builtin_luts/`` 并以稳定 ``asset_id`` 登记为
``metadata.builtin=true`` 的 LUT 资产。重复启动/重复调用不产生重复登记、
不推高注册表版本；数据根副本缺失时按源文件自动补物化。用户可隐藏内置
条目，但不能移入回收站或删除源文件（守卫见 repository 与路由层）。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List

from gw.asset_registry.lut_cube import LutCubeError, parse_lut_cube_bytes
from gw.core import storage
from gw.core.errors import CleanroomException

BUILTIN_LUT_ASSET_ID_PREFIX = "builtin_lut_"
BUILTIN_LUT_RELATIVE_DIR = Path("web") / "vendor" / "luts"
BUILTIN_LUT_UPLOAD_SUBDIR = "builtin_luts"
USER_LUT_UPLOAD_SUBDIR = "user-lut"
BUILTIN_LUT_NOTES = (
    "程序生成的演示级基准 LUT（Apache-2.0, 2026 Gods-Workbench Authors）；"
    "来源与规格见 web/vendor/luts/README.md"
)

# (slug, 展示名, 文件名, 通道公式)——与 web/vendor/luts/ 同名一一对应。
BUILTIN_LUT_SPECS: List[Dict[str, str]] = [
    {"slug": "neutral", "name": "中性基准", "file": "builtin_lut_neutral.cube",
     "formula": "identity"},
    {"slug": "warm", "name": "暖调", "file": "builtin_lut_warm.cube",
     "formula": "R*1.06 G*1.01 B*0.93"},
    {"slug": "cool", "name": "冷调", "file": "builtin_lut_cool.cube",
     "formula": "R*0.93 G*1.00 B*1.07"},
    {"slug": "high_contrast", "name": "高对比", "file": "builtin_lut_high_contrast.cube",
     "formula": "smoothstep(0.08,0.92,x)"},
    {"slug": "cinematic", "name": "电影感", "file": "builtin_lut_cinematic.cube",
     "formula": "lifted-blacks + rolled-highlights"},
]


def _package_root() -> Path:
    return Path(__file__).resolve().parents[2]


def source_builtin_cube_path(filename: str) -> Path:
    """仓库内只读分发源路径；文件名必须是登记的单段 .cube 名。"""
    name = str(filename or "").strip()
    if not name or "/" in name or "\\" in name or name in {".", ".."} \
            or not name.lower().endswith(".cube"):
        raise CleanroomException(400, "INVALID_REQUEST", "内置 LUT 文件名无效")
    return _package_root() / BUILTIN_LUT_RELATIVE_DIR / name


def materialized_builtin_lut_dir() -> Path:
    return storage.data_root() / "registry_uploads" / BUILTIN_LUT_UPLOAD_SUBDIR


def user_lut_dir() -> Path:
    return storage.data_root() / "registry_uploads" / USER_LUT_UPLOAD_SUBDIR


def is_builtin_lut_display_path(display: Any) -> bool:
    normalized = str(display or "").replace("\\", "/")
    return normalized.startswith(f"registry_uploads/{BUILTIN_LUT_UPLOAD_SUBDIR}/")


def is_user_lut_display_path(display: Any) -> bool:
    normalized = str(display or "").replace("\\", "/")
    return normalized.startswith(f"registry_uploads/{USER_LUT_UPLOAD_SUBDIR}/")


def is_managed_lut_display_path(display: Any) -> bool:
    return is_builtin_lut_display_path(display) or is_user_lut_display_path(display)


def _materialize(spec: Dict[str, str]) -> Dict[str, Any]:
    """把仓库源文件复制到数据根受管目录并解析 Cube 头部；返回可入库字段。

    失败关闭：源缺失或非法 Cube 一律 503，不写半成品；已存在且字节一致的
    副本直接复用。复制在注册表事务前完成，复制本身幂等。
    """
    source = source_builtin_cube_path(spec["file"])
    if not source.is_file():
        raise CleanroomException(503, "BUILTIN_LUT_SOURCE_MISSING",
                                 f"内置 LUT 源文件缺失：{spec['file']}")
    data = source.read_bytes()
    try:
        lut_meta = parse_lut_cube_bytes(data)
    except LutCubeError as exc:
        raise CleanroomException(503, "BUILTIN_LUT_SOURCE_INVALID",
                                 f"内置 LUT 源文件非法：{spec['file']}") from exc
    directory = materialized_builtin_lut_dir()
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / spec["file"]
    if not target.is_file() or target.read_bytes() != data:
        tmp = directory / (spec["file"] + ".tmp")
        try:
            tmp.write_bytes(data)
            os.replace(tmp, target)
        except OSError:
            tmp.unlink(missing_ok=True)
            raise CleanroomException(503, "BUILTIN_LUT_MATERIALIZE_FAILED",
                                     "内置 LUT 物化到数据根失败")
    return {
        "asset_id": BUILTIN_LUT_ASSET_ID_PREFIX + spec["slug"],
        "name": spec["file"],
        "display_path": f"registry_uploads/{BUILTIN_LUT_UPLOAD_SUBDIR}/{spec['file']}",
        "size_bytes": len(data),
        "metadata": {
            "builtin": True,
            "builtin_slug": spec["slug"],
            "builtin_formula": spec["formula"],
            "notes": BUILTIN_LUT_NOTES,
            "lut": {
                "title": lut_meta["title"],
                "lut_3d_size": lut_meta.get("lut_3d_size"),
                "lut_1d_size": lut_meta.get("lut_1d_size"),
                "sample_count": lut_meta["sample_count"],
            },
            "extension": "cube",
        },
    }


def _validate_existing(existing: Dict[str, Any], item: Dict[str, Any]) -> None:
    """已登记条目的身份一致性守卫：被篡改或 id 冲突时失败关闭，不静默覆盖。"""
    asset_id = item["asset_id"]
    metadata = existing.get("metadata")
    if not isinstance(metadata, dict) or metadata.get("builtin") is not True:
        raise CleanroomException(503, "BUILTIN_LUT_IDENTITY_CONFLICT",
                                 f"内置 LUT 资产 id 已被占用：{asset_id}")
    if existing.get("kind") != "lut" or \
            str(existing.get("display_path") or "") != item["display_path"]:
        raise CleanroomException(503, "BUILTIN_LUT_IDENTITY_CONFLICT",
                                 f"内置 LUT 资产路径或类型漂移：{asset_id}")


def ensure_builtin_luts(reveal: bool = False) -> Dict[str, Any]:
    """幂等登记内置 LUT：补缺失条目，迁移旧 hidden 态，不自动补回已删除条目。

    - 已通过回收站删除（记录于 ``deleted_builtin_luts`` 或仍在 ``recycle_bin`` 中）
      的内置 LUT 不会被重新登记或重新物化；
    - 历史版本留下的 ``hidden=True`` 条目在迁移时统一置为 ``hidden=False``（可见），
      绝不把旧隐藏当作删除；
    - 重复调用全部命中已登记或已删除分支：不新增、不推高注册表 revision。
    """
    from gw.asset_registry import repository as repo

    del reveal  # QIN-621：已取消隐藏/恢复显示功能，保留参数签名仅作调用兼容。
    snapshot = repo.state().read()
    deleted_ids = set(str(x) for x in (snapshot.get("deleted_builtin_luts") or []) if str(x))
    recycled_ids = {
        str(entry.get("asset_id") or "")
        for entry in (snapshot.get("recycle_bin") or {}).values()
        if isinstance(entry, dict) and entry.get("kind") == "asset"
    }
    suppressed_ids = deleted_ids | recycled_ids
    existing_assets = snapshot.get("assets") or {}

    prepared: List[Dict[str, Any]] = []
    for spec in BUILTIN_LUT_SPECS:
        asset_id = BUILTIN_LUT_ASSET_ID_PREFIX + spec["slug"]
        if asset_id not in existing_assets and asset_id in suppressed_ids:
            continue
        prepared.append(_materialize(spec))

    created: List[str] = []
    migrated_visible: List[str] = []
    skipped_deleted: List[str] = []

    def mutate(raw: Dict[str, Any]) -> Any:
        assets = raw.setdefault("assets", {})
        cur_deleted = set(str(x) for x in (raw.get("deleted_builtin_luts") or []) if str(x))
        cur_recycled = {
            str(entry.get("asset_id") or "")
            for entry in (raw.get("recycle_bin") or {}).values()
            if isinstance(entry, dict) and entry.get("kind") == "asset"
        }
        cur_suppressed = cur_deleted | cur_recycled
        prepared_map = {item["asset_id"]: item for item in prepared}
        for spec in BUILTIN_LUT_SPECS:
            asset_id = BUILTIN_LUT_ASSET_ID_PREFIX + spec["slug"]
            existing = assets.get(asset_id)
            if existing is not None:
                item = prepared_map.get(asset_id) or _materialize(spec)
                _validate_existing(existing, item)
                if existing.get("hidden"):
                    existing["hidden"] = False
                    existing["version"] = int(existing.get("version", 1)) + 1
                    existing["updated_at"] = storage.now_iso()
                    migrated_visible.append(asset_id)
                continue
            if asset_id in cur_suppressed:
                skipped_deleted.append(asset_id)
                continue
            item = prepared_map.get(asset_id) or _materialize(spec)
            assets[asset_id] = {
                "id": asset_id,
                "asset_id": asset_id,
                "name": item["name"],
                "kind": "lut",
                "archived": False,
                "hidden": False,
                "builtin": True,
                "version": 1,
                "created_at": storage.now_iso(),
                "updated_at": storage.now_iso(),
                "tags": [],
                "relations": [],
                "display_path": item["display_path"],
                "size_bytes": item["size_bytes"],
                "metadata": item["metadata"],
            }
            raw.setdefault("sequences", {})[asset_id] = True
            created.append(asset_id)
        changed = bool(created) or bool(migrated_visible)
        revision = repo._bump(raw) if changed else int(raw.get("revision", 1))
        return {"revision": revision}

    result = repo.state().mutate(mutate)
    all_ids = [BUILTIN_LUT_ASSET_ID_PREFIX + spec["slug"] for spec in BUILTIN_LUT_SPECS]
    return {
        "created": created,
        "existing": [
            asset_id for asset_id in all_ids
            if asset_id not in created and asset_id not in skipped_deleted
        ],
        "skipped_deleted": skipped_deleted,
        "migrated_visible": migrated_visible,
        "revealed": [],
        "hidden": [],
        "revision": result["revision"],
        "data_status": "ok",
        "data_gaps": [],
    }
