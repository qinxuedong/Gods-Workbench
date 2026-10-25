"""QIN-378 预留 ID 分配的崩溃恢复与旧 ID 兼容契约。"""

from __future__ import annotations

import importlib

import pytest

from gw.asset_library import service as asset_library
from gw.asset_registry import lifecycle
from gw.core import storage


def test_lifecycle_reservation_survives_crash_before_journal_write():
    first = lifecycle._next_id("fop_")

    # 模拟分配后、写入操作日志前进程中断；业务日志仍为空。
    assert lifecycle._read_journal() == []

    restarted_lifecycle = importlib.reload(lifecycle)
    second = restarted_lifecycle._next_id("fop_")

    assert first == "fop_0001"
    assert second == "fop_0002"
    assert second != first


def test_asset_library_reservation_survives_failed_business_state_write(monkeypatch):
    service = asset_library.AssetLibraryService()
    first = []

    def fail_business_write(_raw):
        raise RuntimeError("simulated crash before asset library write")

    def allocate(raw):
        value = service._next_seq(raw, "library")
        first.append(value)
        return value

    monkeypatch.setattr(service, "_write_raw", fail_business_write)
    with pytest.raises(RuntimeError, match="simulated crash"):
        service._mutate(allocate)

    # 新服务实例读取未提交的旧业务快照，但 reservation 已先行持久化。
    restarted_service = asset_library.AssetLibraryService()
    second = restarted_service._mutate(
        lambda raw: restarted_service._next_seq(raw, "library")
    )

    assert first == [1]
    assert second == 2


def test_legacy_ids_remain_readable():
    legacy_operation_id = "fop_20261024T120000_abcdef"
    lifecycle._append_journal(
        {"op_id": legacy_operation_id, "state": "done", "type": "rename"}
    )

    assert lifecycle.get_operation(legacy_operation_id)["op_id"] == legacy_operation_id

    legacy_library = asset_library._seeded_raw()
    storage.JsonState(
        asset_library.NS_LIBRARY, asset_library._default_raw
    ).write(legacy_library)
    snapshot = asset_library.AssetLibraryService().get_snapshot()

    assert snapshot.libraries[0].library_id == "library_default"
    assert snapshot.libraries[0].categories[0].category_id == "category_image"
