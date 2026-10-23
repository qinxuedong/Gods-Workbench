"""C4-AC6 只读盘点脚本：识别两侧不一致，且对数据目录零写入。"""

import builtins
import hashlib
import importlib.util
import json
from pathlib import Path

SCRIPT = (
    Path(__file__).resolve().parents[3]
    / "tools"
    / "c4_acceptance"
    / "asset_inventory.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("c4_asset_inventory", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fixture(tmp_path):
    registry = {
        "assets": {
            "ok": {
                "asset_id": "ok",
                "kind": "workflow",
                "metadata": {"workflow_owner": "u1"},
            },
            "reg_only": {
                "asset_id": "reg_only",
                "kind": "workflow",
                "metadata": {"workflow_owner": "u1"},
            },
            "plain": {"asset_id": "plain", "kind": "image", "metadata": {}},
            "owner_x": {"asset_id": "owner_x", "metadata": {"workflow_owner": "u1"}},
            "live_recycled": {"asset_id": "live_recycled", "metadata": {}},
        },
        "recycle_bin": {
            "rcy_1": {
                "entry_id": "rcy_1",
                "kind": "asset",
                "asset_id": "gone",
                "payload": {},
            },
            "rcy_2": {
                "entry_id": "rcy_2",
                "kind": "asset",
                "asset_id": "live_recycled",
                "payload": {},
            },
            "rcy_3": {
                "entry_id": "rcy_3",
                "kind": "asset",
                "asset_id": "stashed",
                "payload": {"library_item": {"asset_id": "other"}},
            },
        },
    }
    items = [
        {"asset_id": "ok", "workflow_owner": "u1"},
        {"asset_id": "lib_only", "workflow_owner": "u1"},
        {"asset_id": "native_0001"},
        {"asset_id": "gone", "workflow_owner": "u1"},
        {"asset_id": "owner_x", "workflow_owner": "u2"},
        {"asset_id": "stashed", "workflow_owner": "u1"},
    ]
    library = {
        "libraries": {
            "lib_1": {"categories": [{"category_id": "cat_1", "items": items}]}
        }
    }
    (tmp_path / "asset_registry.json").write_text(
        json.dumps(registry), encoding="utf-8"
    )
    (tmp_path / "asset_library.json").write_text(json.dumps(library), encoding="utf-8")
    return registry, library


def _snapshot(root: Path):
    return {
        p.relative_to(root).as_posix(): (
            hashlib.sha256(p.read_bytes()).hexdigest(),
            p.stat().st_mtime_ns,
        )
        for p in sorted(root.rglob("*"))
    }


def test_inventory_detects_all_mismatch_classes(tmp_path):
    mod = _load()
    registry, library = _fixture(tmp_path)
    report = mod.inventory(registry, library)
    ids = {
        k: sorted(r.get("asset_id") for r in v) for k, v in report["findings"].items()
    }
    assert ids["registry_only_bridged"] == ["reg_only"]
    assert ids["library_only_bridged"] == ["lib_only"]
    assert ids["library_item_of_recycled_asset"] == ["gone", "stashed"]
    assert ids["owner_mismatch"] == ["owner_x"]
    assert ids["recycle_entry_for_live_asset"] == ["live_recycled"]
    assert ids["recycle_stash_id_mismatch"] == ["stashed"]
    assert ids["recycle_stash_still_in_library"] == ["stashed"]
    assert "native_0001" not in json.dumps(
        report["findings"]
    )  # 非桥接原生素材不算不一致
    assert report["consistent"] is False


def test_consistent_data_reports_clean(tmp_path):
    mod = _load()
    report = mod.inventory(
        {"assets": {"a": {"metadata": {"workflow_owner": "u"}}}},
        {
            "libraries": {
                "l": {
                    "categories": [
                        {"items": [{"asset_id": "a", "workflow_owner": "u"}]}
                    ]
                }
            }
        },
    )
    assert report["consistent"] is True


def test_cli_makes_no_writes_to_data_dir(tmp_path, monkeypatch, capsys):
    mod = _load()
    data = tmp_path / "data"
    data.mkdir()
    _fixture(data)
    before = _snapshot(data)
    real_open = builtins.open

    def guarded_open(file, mode="r", *a, **kw):
        path = Path(file).resolve() if isinstance(file, (str, Path)) else None
        if path is not None and (
            path == data.resolve() or data.resolve() in path.parents
        ):
            assert set(mode) <= {"r", "b", "t"}, (
                f"write attempted on data dir: {path} {mode}"
            )
        return real_open(file, mode, *a, **kw)

    monkeypatch.setattr(builtins, "open", guarded_open)
    out = tmp_path / "report.json"
    assert mod.main(["--data-dir", str(data), "--out", str(out)]) == 1
    assert mod.main(["--data-dir", str(data)]) == 1
    assert _snapshot(data) == before
    assert json.loads(out.read_text(encoding="utf-8"))["consistent"] is False
    assert "发现不一致" in capsys.readouterr().out


def test_cli_refuses_out_inside_data_dir(tmp_path):
    mod = _load()
    _fixture(tmp_path)
    before = _snapshot(tmp_path)
    assert (
        mod.main(["--data-dir", str(tmp_path), "--out", str(tmp_path / "r.json")]) == 2
    )
    assert _snapshot(tmp_path) == before


def test_script_does_not_import_runtime():
    source = SCRIPT.read_text(encoding="utf-8")
    assert "import gw" not in source and "from gw" not in source
