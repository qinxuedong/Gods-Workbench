"""MVP2-T2（GitHub Issue #366）：生产链产物回写素材注册表的来源链契约。

覆盖五条验收口径：
- AC1 缺任一必填来源链字段（缺失 / None / produced_by 非法 / 多余字段）→ 400
  PROVENANCE_REQUIRED，且不写入注册表；
- AC2 同 asset_id + 同来源链重复回写幂等，来源链不一致 → 409 PROVENANCE_CONFLICT；
- AC3 provenance_chain 列出 reference_asset_ids 并标注 exists，未知 asset_id → 404
  ASSET_NOT_FOUND；
- AC4 target_root_id 必须是已配置产出根，绝不写入源素材目录，返回值只回显
  root_id + 相对路径；
- AC5 所有成功返回值与异常 message/extra 都不含绝对路径。

follow-up（GitHub Issue #408）追加两条硬口径：
- sidecar 落盘 I/O 失败（产出根是文件、只读目录等）包装为 503
  PROVENANCE_SIDECAR_WRITE_FAILED，只回显 target_root_id 与相对 sidecar_path，
  且注册表不留条目；
- configure_produced_roots 拒绝空/空白/非绝对路径（含源素材根），
  绝不落到进程工作目录。
"""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from gw.asset_registry.models import ProducedAssetProvenance
from gw.asset_registry.service import AssetRegistryService
from gw.core.errors import CleanroomException

BASE_PROVENANCE: dict[str, Any] = {
    "project_id": "proj-ep01",
    "task_id": "task-0007",
    "artifact_version_id": "artifact-0001",
    "reference_asset_ids": ["asset-ref-known", "asset-ref-unknown"],
    "shot_ids": ["shot-0001", "shot-0002"],
    "produced_by": "agent_pipeline",
}

REQUIRED_FIELDS = tuple(BASE_PROVENANCE)


def _provenance(**overrides: Any) -> dict[str, Any]:
    payload = dict(BASE_PROVENANCE)
    payload.update(overrides)
    return payload


def _snapshot(root: Path) -> list[tuple[str, bytes]]:
    """列出目录下所有文件的相对路径与字节，用于证明源素材目录未被改动。"""
    return sorted(
        (path.relative_to(root).as_posix(), path.read_bytes())
        for path in root.rglob("*")
        if path.is_file()
    )


def _assert_no_absolute_paths(payload: Any, forbidden: str) -> None:
    """递归断言载荷中不出现本地绝对路径（含 tmp 根、POSIX 根、Windows 盘符）。"""
    if isinstance(payload, str):
        assert forbidden not in payload, f"回显了本地路径: {payload}"
        assert not payload.startswith("/"), f"回显了绝对路径: {payload}"
        assert not re.match(r"^[A-Za-z]:[\\/]", payload), f"回显了盘符路径: {payload}"
        assert "\\" not in payload, f"回显了反斜杠路径: {payload}"
        return
    if isinstance(payload, Mapping):
        for key, value in payload.items():
            _assert_no_absolute_paths(key, forbidden)
            _assert_no_absolute_paths(value, forbidden)
        return
    if isinstance(payload, Sequence) and not isinstance(payload, (str, bytes)):
        for value in payload:
            _assert_no_absolute_paths(value, forbidden)


def _assert_failure(
    error: CleanroomException,
    status_code: int,
    code: str,
    forbidden: str,
) -> None:
    assert error.status_code == status_code
    assert error.code == code
    _assert_no_absolute_paths({"message": error.message, "extra": error.extra}, forbidden)


@pytest.fixture
def local_root(tmp_path: Path) -> str:
    """返回本例临时根目录字符串，用于反向断言返回值不回显本地路径。"""
    return str(tmp_path)


@pytest.fixture
def env(tmp_path: Path):
    """构造源素材根 + 产出根，并注入已配置的产出根。"""
    source_root = tmp_path / "source-assets"
    output_root = tmp_path / "produced-output"
    source_root.mkdir()
    output_root.mkdir()
    (source_root / "ref-shot-0001.png").write_bytes(b"source-bytes")
    service = AssetRegistryService()
    service.configure_produced_roots(
        {"out_main": output_root}, source_roots={"src_main": source_root}
    )
    return service, source_root, output_root


# ---------------------------------------------------------------------------
# AC1：来源链必填，缺失/非法一律失败关闭且不写入注册表
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("missing", REQUIRED_FIELDS)
def test_missing_required_provenance_field_is_rejected(
    env, local_root: str, missing: str
):
    service, source_root, output_root = env
    payload = {
        key: value for key, value in BASE_PROVENANCE.items() if key != missing
    }
    before_source = _snapshot(source_root)

    with pytest.raises(CleanroomException) as raised:
        service.register_produced_asset(
            "asset-produced-1", provenance=payload, target_root_id="out_main"
        )

    _assert_failure(raised.value, 400, "PROVENANCE_REQUIRED", local_root)
    assert raised.value.extra.get("invalid_fields") == [missing]
    with pytest.raises(CleanroomException) as not_registered:
        service.get_asset("asset-produced-1")
    assert not_registered.value.code == "ASSET_NOT_FOUND"
    assert _snapshot(output_root) == []
    assert _snapshot(source_root) == before_source


@pytest.mark.parametrize("field", REQUIRED_FIELDS)
def test_none_provenance_field_is_rejected(env, local_root: str, field: str):
    service, source_root, output_root = env

    with pytest.raises(CleanroomException) as raised:
        service.register_produced_asset(
            "asset-produced-1",
            provenance=_provenance(**{field: None}),
            target_root_id="out_main",
        )

    _assert_failure(raised.value, 400, "PROVENANCE_REQUIRED", local_root)
    with pytest.raises(CleanroomException) as not_registered:
        service.get_asset("asset-produced-1")
    assert not_registered.value.code == "ASSET_NOT_FOUND"
    assert _snapshot(output_root) == []


@pytest.mark.parametrize(
    "payload",
    [
        _provenance(produced_by="human_upload"),
        _provenance(produced_by=""),
        _provenance(extra_field="unexpected"),
        _provenance(reference_asset_ids=["  "]),
        {},
        None,
        "not-an-object",
    ],
)
def test_invalid_or_unexpected_provenance_is_rejected(env, local_root: str, payload):
    service, _source_root, output_root = env

    with pytest.raises(CleanroomException) as raised:
        service.register_produced_asset(
            "asset-produced-1", provenance=payload, target_root_id="out_main"
        )

    _assert_failure(raised.value, 400, "PROVENANCE_REQUIRED", local_root)
    with pytest.raises(CleanroomException) as not_registered:
        service.get_asset("asset-produced-1")
    assert not_registered.value.code == "ASSET_NOT_FOUND"
    assert _snapshot(output_root) == []


def test_provenance_model_accepts_empty_references_and_forbids_extra_fields():
    """空 reference_asset_ids 合法（字段必填但可为空）；多余字段被拒绝。"""
    model = ProducedAssetProvenance.model_validate(_provenance(reference_asset_ids=[]))

    assert model.produced_by == "agent_pipeline"
    assert model.model_dump()["reference_asset_ids"] == []

    with pytest.raises(ValidationError):
        ProducedAssetProvenance.model_validate(_provenance(actor="someone"))


# ---------------------------------------------------------------------------
# AC2：幂等回写与来源链冲突
# ---------------------------------------------------------------------------


def test_identical_writeback_is_idempotent(env, local_root: str):
    service, source_root, output_root = env
    before_source = _snapshot(source_root)

    first = service.register_produced_asset(
        "asset-produced-2", provenance=_provenance(), target_root_id="out_main"
    )
    second = service.register_produced_asset(
        "asset-produced-2", provenance=_provenance(), target_root_id="out_main"
    )

    assert first == second
    assert first["provenance"] == BASE_PROVENANCE
    assert first["target_root_id"] == "out_main"
    assert first["sidecar_path"] == "provenance/asset-produced-2.json"
    _assert_no_absolute_paths(first, local_root)
    assert _snapshot(source_root) == before_source
    assert (output_root / "provenance" / "asset-produced-2.json").is_file()


def test_different_provenance_for_same_asset_conflicts(env, local_root: str):
    service, _source_root, _output_root = env
    service.register_produced_asset(
        "asset-produced-3", provenance=_provenance(), target_root_id="out_main"
    )

    with pytest.raises(CleanroomException) as raised:
        service.register_produced_asset(
            "asset-produced-3",
            provenance=_provenance(task_id="task-0008"),
            target_root_id="out_main",
        )

    _assert_failure(raised.value, 409, "PROVENANCE_CONFLICT", local_root)
    assert service.get_asset("asset-produced-3")["provenance"]["task_id"] == "task-0007"


def test_asset_registered_without_provenance_conflicts(env, local_root: str):
    """register_asset 登记的素材没有来源链，产物回写不得静默覆盖。"""
    service, _source_root, _output_root = env
    service.register_asset("asset-plain-1", metadata={"kind": "image"})

    with pytest.raises(CleanroomException) as raised:
        service.register_produced_asset(
            "asset-plain-1", provenance=_provenance(), target_root_id="out_main"
        )

    _assert_failure(raised.value, 409, "PROVENANCE_CONFLICT", local_root)
    assert "provenance" not in service.get_asset("asset-plain-1")


# ---------------------------------------------------------------------------
# AC3：按 asset_id 反查来源链
# ---------------------------------------------------------------------------


def test_provenance_chain_marks_existing_and_missing_references(env, local_root: str):
    service, _source_root, _output_root = env
    service.register_asset("asset-ref-known")
    service.register_produced_asset(
        "asset-produced-4", provenance=_provenance(), target_root_id="out_main"
    )

    chain = service.provenance_chain("asset-produced-4")

    assert chain["asset_id"] == "asset-produced-4"
    assert chain["provenance"] == BASE_PROVENANCE
    assert chain["references"] == [
        {"asset_id": "asset-ref-known", "exists": True},
        {"asset_id": "asset-ref-unknown", "exists": False},
    ]
    _assert_no_absolute_paths(chain, local_root)


def test_produced_reference_is_reported_as_existing(env):
    service, _source_root, _output_root = env
    service.register_produced_asset(
        "asset-produced-5",
        provenance=_provenance(reference_asset_ids=[], shot_ids=["shot-0001"]),
        target_root_id="out_main",
    )
    service.register_produced_asset(
        "asset-produced-6",
        provenance=_provenance(reference_asset_ids=["asset-produced-5"]),
        target_root_id="out_main",
    )

    chain = service.provenance_chain("asset-produced-6")

    assert chain["references"] == [{"asset_id": "asset-produced-5", "exists": True}]
    assert service.provenance_chain("asset-produced-5")["references"] == []


def test_unknown_asset_has_no_provenance_chain(env, local_root: str):
    service, _source_root, _output_root = env

    with pytest.raises(CleanroomException) as raised:
        service.provenance_chain("asset-does-not-exist")

    _assert_failure(raised.value, 404, "ASSET_NOT_FOUND", local_root)


def test_asset_without_provenance_cannot_be_traced(env, local_root: str):
    """已登记但没有来源链的素材不得返回空链冒充可反查。"""
    service, _source_root, _output_root = env
    service.register_asset("asset-plain-2")

    with pytest.raises(CleanroomException) as raised:
        service.provenance_chain("asset-plain-2")

    _assert_failure(raised.value, 404, "PROVENANCE_NOT_FOUND", local_root)


# ---------------------------------------------------------------------------
# AC4：产出根隔离，绝不写入源素材目录
# ---------------------------------------------------------------------------


def test_unknown_target_root_fails_closed(env, local_root: str):
    service, source_root, output_root = env
    before_source = _snapshot(source_root)

    with pytest.raises(CleanroomException) as raised:
        service.register_produced_asset(
            "asset-produced-7", provenance=_provenance(), target_root_id="out_unknown"
        )

    _assert_failure(raised.value, 400, "UNKNOWN_TARGET_ROOT", local_root)
    assert raised.value.extra.get("target_root_id") == "out_unknown"
    with pytest.raises(CleanroomException) as not_registered:
        service.get_asset("asset-produced-7")
    assert not_registered.value.code == "ASSET_NOT_FOUND"
    assert _snapshot(output_root) == []
    assert _snapshot(source_root) == before_source


def test_unconfigured_produced_roots_fail_closed(tmp_path: Path, local_root: str):
    """未调用 configure_produced_roots 时没有默认产出根，产物回写必须失败。"""
    service = AssetRegistryService()
    source_root = tmp_path / "source-assets"
    source_root.mkdir()
    (source_root / "clip.mp4").write_bytes(b"source-bytes")
    service.configure_produced_roots({}, source_roots={"src_main": source_root})
    before_source = _snapshot(source_root)

    with pytest.raises(CleanroomException) as raised:
        service.register_produced_asset(
            "asset-produced-8", provenance=_provenance(), target_root_id="out_main"
        )

    _assert_failure(raised.value, 400, "UNKNOWN_TARGET_ROOT", local_root)
    assert _snapshot(source_root) == before_source


def test_writeback_only_lands_in_the_configured_output_root(env, local_root: str):
    service, source_root, output_root = env
    before_source = _snapshot(source_root)

    result = service.register_produced_asset(
        "asset-produced-9",
        provenance=_provenance(),
        target_root_id="out_main",
        metadata={"kind": "video"},
    )

    assert result["target_root_id"] == "out_main"
    assert result["sidecar_path"] == "provenance/asset-produced-9.json"
    assert (output_root / "provenance" / "asset-produced-9.json").is_file()
    assert result["asset"]["metadata"] == {"kind": "video"}
    _assert_no_absolute_paths(result, local_root)
    # 源素材目录内容逐字节不变，且没有新增 sidecar。
    assert _snapshot(source_root) == before_source


def test_output_root_inside_source_root_is_rejected(tmp_path: Path, local_root: str):
    source_root = tmp_path / "source-assets"
    nested_output = source_root / "produced"
    nested_output.mkdir(parents=True)
    (source_root / "clip.mp4").write_bytes(b"source-bytes")
    service = AssetRegistryService()
    service.configure_produced_roots(
        {"nested": nested_output}, source_roots={"src_main": source_root}
    )
    before_source = _snapshot(source_root)

    with pytest.raises(CleanroomException) as raised:
        service.register_produced_asset(
            "asset-produced-10", provenance=_provenance(), target_root_id="nested"
        )

    _assert_failure(raised.value, 400, "UNKNOWN_TARGET_ROOT", local_root)
    assert _snapshot(source_root) == before_source


# ---------------------------------------------------------------------------
# AC5：成功与失败路径都不回显绝对路径
# ---------------------------------------------------------------------------


def test_success_and_failure_payloads_never_echo_local_paths(env, local_root: str):
    service, _source_root, output_root = env
    produced = service.register_produced_asset(
        "asset-produced-11", provenance=_provenance(), target_root_id="out_main"
    )
    chain = service.provenance_chain("asset-produced-11")

    for payload in (produced, chain):
        _assert_no_absolute_paths(payload, local_root)

    with pytest.raises(CleanroomException) as conflict:
        service.register_produced_asset(
            "asset-produced-11",
            provenance=_provenance(task_id="task-9999"),
            target_root_id="out_main",
        )
    with pytest.raises(CleanroomException) as unknown_root:
        service.register_produced_asset(
            "asset-produced-12", provenance=_provenance(), target_root_id="out_other"
        )
    with pytest.raises(CleanroomException) as missing:
        service.provenance_chain("asset-produced-12")

    for error in (conflict.value, unknown_root.value, missing.value):
        _assert_no_absolute_paths(
            {"message": error.message, "extra": error.extra}, local_root
        )
    assert (output_root / "provenance" / "asset-produced-12.json").exists() is False


# ---------------------------------------------------------------------------
# QIN-440-followup（GitHub Issue #408）：sidecar 落盘 I/O 失败包装
# ---------------------------------------------------------------------------


def test_output_root_that_is_a_file_fails_closed(tmp_path: Path, local_root: str):
    """产出根是已存在文件：mkdir 抛 OSError，必须包成受控异常且注册表不留条目。"""
    output_file = tmp_path / "produced-output"
    output_file.write_bytes(b"not-a-directory")
    service = AssetRegistryService()
    service.configure_produced_roots({"out_file": output_file}, source_roots={})

    with pytest.raises(CleanroomException) as raised:
        service.register_produced_asset(
            "asset-produced-file-root",
            provenance=_provenance(),
            target_root_id="out_file",
        )

    _assert_failure(raised.value, 503, "PROVENANCE_SIDECAR_WRITE_FAILED", local_root)
    assert raised.value.extra.get("target_root_id") == "out_file"
    assert raised.value.extra.get("sidecar_path") == (
        "provenance/asset-produced-file-root.json"
    )
    # 原始 OSError 不得随异常链外泄（message 会带本机绝对路径）。
    assert raised.value.__cause__ is None
    with pytest.raises(CleanroomException) as not_registered:
        service.get_asset("asset-produced-file-root")
    assert not_registered.value.code == "ASSET_NOT_FOUND"
    assert output_file.read_bytes() == b"not-a-directory"


@pytest.mark.skipif(
    sys.platform == "win32" or (hasattr(os, "geteuid") and os.geteuid() == 0),
    reason="Windows 目录只读属性不阻止新建文件；root 不受目录写权限限制",
)
def test_read_only_output_root_fails_closed(tmp_path: Path, local_root: str):
    """产出根只读：sidecar 无法落盘，同样受控失败且不写入注册表。"""
    output_root = tmp_path / "produced-readonly"
    output_root.mkdir()
    output_root.chmod(0o500)
    service = AssetRegistryService()
    try:
        service.configure_produced_roots({"out_ro": output_root}, source_roots={})

        with pytest.raises(CleanroomException) as raised:
            service.register_produced_asset(
                "asset-produced-readonly",
                provenance=_provenance(),
                target_root_id="out_ro",
            )

        _assert_failure(raised.value, 503, "PROVENANCE_SIDECAR_WRITE_FAILED", local_root)
        assert raised.value.extra.get("target_root_id") == "out_ro"
        assert raised.value.extra.get("sidecar_path") == (
            "provenance/asset-produced-readonly.json"
        )
        with pytest.raises(CleanroomException) as not_registered:
            service.get_asset("asset-produced-readonly")
        assert not_registered.value.code == "ASSET_NOT_FOUND"
        assert list(output_root.iterdir()) == []
    finally:
        output_root.chmod(0o700)


def test_sidecar_failure_does_not_overwrite_existing_registration(tmp_path: Path):
    """落盘失败时，已登记的产物条目必须原样保留，不得被半成品覆盖。"""
    output_root = tmp_path / "produced-output"
    output_root.mkdir()
    service = AssetRegistryService()
    service.configure_produced_roots({"out_main": output_root}, source_roots={})
    service.register_produced_asset(
        "asset-produced-locked", provenance=_provenance(), target_root_id="out_main"
    )
    sidecar = output_root / "provenance" / "asset-produced-locked.json"
    assert sidecar.is_file()
    # 已有 sidecar 只读：重复幂等回写也无法落盘，注册表条目必须原样保留。
    sidecar.chmod(0o400)
    sidecar.chmod(0o400)

    with pytest.raises(CleanroomException) as raised:
        service.register_produced_asset(
            "asset-produced-locked", provenance=_provenance(), target_root_id="out_main"
        )

    assert raised.value.code == "PROVENANCE_SIDECAR_WRITE_FAILED"
    assert service.get_asset("asset-produced-locked")["provenance"] == BASE_PROVENANCE
    sidecar.chmod(0o600)


# ---------------------------------------------------------------------------
# QIN-440-followup：产出根/源素材根配置的严格度（拒绝空串与相对路径）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_root", ["", "   ", "relative-output", "./output"])
def test_non_absolute_or_empty_root_is_rejected(
    tmp_path: Path, local_root: str, monkeypatch: pytest.MonkeyPatch, bad_root: str
):
    """空/空白/相对路径不得被 resolve() 落到进程工作目录。"""
    monkeypatch.chdir(tmp_path)
    service = AssetRegistryService()

    with pytest.raises(CleanroomException) as raised:
        service.configure_produced_roots({"out_main": bad_root})

    _assert_failure(raised.value, 400, "UNKNOWN_TARGET_ROOT", local_root)
    assert raised.value.extra.get("root_id") == "out_main"
    # 被拒绝的配置不生效，产物回写继续失败关闭，且 CWD 未被写入。
    with pytest.raises(CleanroomException) as not_configured:
        service.register_produced_asset(
            "asset-produced-bad-root",
            provenance=_provenance(),
            target_root_id="out_main",
        )
    assert not_configured.value.code == "UNKNOWN_TARGET_ROOT"
    assert list(tmp_path.iterdir()) == []


def test_source_roots_are_equally_strict(tmp_path: Path, local_root: str):
    """源素材根同样拒绝相对路径，且合法产出根不得被部分应用。"""
    output_root = tmp_path / "produced-output"
    output_root.mkdir()
    service = AssetRegistryService()

    with pytest.raises(CleanroomException) as raised:
        service.configure_produced_roots(
            {"out_main": output_root}, source_roots={"src_main": "relative-source"}
        )

    _assert_failure(raised.value, 400, "UNKNOWN_TARGET_ROOT", local_root)
    assert raised.value.extra.get("root_id") == "src_main"
    with pytest.raises(CleanroomException) as not_configured:
        service.register_produced_asset(
            "asset-produced-src-bad", provenance=_provenance(), target_root_id="out_main"
        )
    assert not_configured.value.code == "UNKNOWN_TARGET_ROOT"
    assert list(output_root.iterdir()) == []


@pytest.mark.parametrize("root_id", ["", "   "])
def test_blank_root_id_is_rejected(tmp_path: Path, root_id: str):
    service = AssetRegistryService()

    with pytest.raises(CleanroomException) as raised:
        service.configure_produced_roots({root_id: tmp_path})

    assert raised.value.status_code == 400
    assert raised.value.code == "INVALID_REQUEST"


def test_absolute_roots_still_configure(tmp_path: Path, local_root: str):
    """严格化不得误伤绝对路径（含字符串形式）。"""
    output_root = tmp_path / "produced-output"
    output_root.mkdir()
    service = AssetRegistryService()
    service.configure_produced_roots(
        {"out_main": str(output_root)}, source_roots={"src_main": tmp_path / "source"}
    )

    result = service.register_produced_asset(
        "asset-produced-abs", provenance=_provenance(), target_root_id="out_main"
    )

    assert result["target_root_id"] == "out_main"
    assert (output_root / "provenance" / "asset-produced-abs.json").is_file()
    _assert_no_absolute_paths(result, local_root)
