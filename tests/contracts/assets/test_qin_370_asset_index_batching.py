"""QIN-370：素材导入与后台扫描批量更新检索索引。"""

import time
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gw.api.app import create_app
from gw.asset_registry import index_jobs, repository
from gw.core import storage
from gw.core.sqlite_vec_engine import SQLiteVecEngine

BASE = "/api/asset-registry"
AUTH = {"Authorization": "Bearer qin-370-owner", "X-User-Role": "editor"}


@pytest.fixture
def scan_root(tmp_path, monkeypatch):
    root = tmp_path / "scan-root"
    root.mkdir()
    monkeypatch.setenv("GW_ALLOWED_ROOTS", str(root))
    if index_jobs._default:
        index_jobs._default.shutdown()
    index_jobs._default = None
    yield root
    if index_jobs._default:
        index_jobs._default.shutdown()
    index_jobs._default = None


def _track_batch_calls(monkeypatch):
    calls = []
    original = SQLiteVecEngine.batch_upsert_documents

    def tracked(engine, documents, **kwargs):
        batch = list(documents)
        calls.append(batch)
        return original(engine, batch, **kwargs)

    monkeypatch.setattr(SQLiteVecEngine, "batch_upsert_documents", tracked)
    return calls


def _search_matches(query):
    path = storage.data_root() / repository.ASSET_SEARCH_INDEX_FILENAME
    with SQLiteVecEngine(path) as engine:
        return engine.search_fts(query)


def _wait_for_job(client, job_id):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        response = client.get(
            f"{BASE}/workspace-jobs/{job_id}", headers=AUTH
        )
        assert response.status_code == 200, response.text
        job = response.json()["job"]
        if job["status"] in index_jobs.TERMINAL:
            return job
        time.sleep(0.02)
    pytest.fail("后台索引未在期限内完成")


def test_import_assets_batch_upserts_search_index_without_changing_result(monkeypatch):
    calls = _track_batch_calls(monkeypatch)
    items = [
        {
            "name": "qin370-import-alpha.txt",
            "kind": "document",
            "tags": ["qin370importtoken"],
        },
        {
            "name": "qin370-import-beta.txt",
            "kind": "document",
            "tags": ["qin370importtoken"],
        },
    ]

    with TestClient(create_app()) as client:
        response = client.post(
            BASE + "/assets/import",
            headers=AUTH,
            json={"items": items, "source_kind": "manual"},
        )
    assert response.status_code == 200, response.text
    result = response.json()

    assert set(result) == {
        "assets",
        "asset_ids",
        "revision",
        "data_status",
        "data_gaps",
    }
    assert [asset["name"] for asset in result["assets"]] == [item["name"] for item in items]
    assert result["asset_ids"] == [asset["asset_id"] for asset in result["assets"]]
    assert len(calls) == 1
    assert [doc["asset_id"] for doc in calls[0]] == result["asset_ids"]
    assert {match.asset_id for match in _search_matches("qin370importtoken")} == set(
        result["asset_ids"]
    )


def test_repository_reindex_batch_upserts_scanned_assets(scan_root, monkeypatch):
    (scan_root / "qin370syncscan.token.txt").write_text("sample", encoding="utf-8")
    calls = _track_batch_calls(monkeypatch)

    result = repository.reindex(None)

    assert result["scanned"] == 1 and result["indexed"] == 1
    assets = repository.state().read()["assets"]
    scanned = next(
        asset
        for asset in assets.values()
        if asset["name"] == "qin370syncscan.token.txt"
    )
    assert len(calls) == 1
    assert [doc["asset_id"] for doc in calls[0]] == [scanned["asset_id"]]
    assert [match.asset_id for match in _search_matches("qin370syncscan")] == [
        scanned["asset_id"]
    ]

    repeated = repository.reindex(None)

    assert repeated["indexed"] == 0
    assert len(calls) == 2
    assert [doc["asset_id"] for doc in calls[1]] == [scanned["asset_id"]]


def test_background_reindex_batch_upserts_scanned_assets(scan_root, monkeypatch):
    (scan_root / "qin370scantoken.txt").write_text("sample", encoding="utf-8")
    calls = _track_batch_calls(monkeypatch)

    with TestClient(create_app()) as client:
        response = client.post(
            BASE + "/reindex",
            headers={**AUTH, "Idempotency-Key": "qin-370-scan"},
            json={"background": True},
        )
        assert response.status_code == 202, response.text
        job = _wait_for_job(client, response.json()["job_id"])

    assert job["status"] == "succeeded", job
    assert job["result"]["scanned"] == 1
    assert job["result"]["indexed"] == 1
    assets = repository.state().read()["assets"]
    scanned = next(
        asset for asset in assets.values() if asset["name"] == "qin370scantoken.txt"
    )
    assert len(calls) == 1
    assert [doc["asset_id"] for doc in calls[0]] == [scanned["asset_id"]]
    assert [match.asset_id for match in _search_matches("qin370scantoken")] == [
        scanned["asset_id"]
    ]


def test_scan_allowed_roots_resolves_only_the_allowed_root(tmp_path, monkeypatch):
    root = tmp_path / "scan-root"
    nested = root / "nested"
    nested.mkdir(parents=True)
    (root / "one.txt").write_text("one", encoding="utf-8")
    (nested / "two.txt").write_text("two", encoding="utf-8")
    monkeypatch.setenv("GW_ALLOWED_ROOTS", str(root))

    resolve_calls = []
    original_resolve = Path.resolve

    def tracked_resolve(path, *args, **kwargs):
        resolve_calls.append(path)
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", tracked_resolve)

    files, gaps = repository._scan_allowed_roots()

    assert gaps == []
    assert {item["display_path"] for item in files} == {"one.txt", "nested/two.txt"}
    assert {item["size_bytes"] for item in files} == {3}
    assert len(resolve_calls) == 1


def test_reindex_allocates_asset_ids_from_one_high_water_scan(scan_root, monkeypatch):
    (scan_root / "one.txt").write_text("one", encoding="utf-8")
    (scan_root / "two.txt").write_text("two", encoding="utf-8")
    raw = repository.state().read()
    raw["assets"]["ast_0008"] = {
        "asset_id": "ast_0008",
        "name": "existing.txt",
        "kind": "indexed",
        "display_path": "existing.txt",
    }
    raw["sequences"]["ast_0012"] = True
    repository.state().write(raw)

    calls = 0
    original_next_sequence = storage.next_sequence

    def tracked_next_sequence(prefix, existing):
        nonlocal calls
        calls += 1
        return original_next_sequence(prefix, existing)

    monkeypatch.setattr(storage, "next_sequence", tracked_next_sequence)

    result = repository.reindex(None)

    assert [asset["asset_id"] for asset in result["assets"]] == ["ast_0013", "ast_0014"]
    assert calls == 1


def test_background_scan_avoids_resolving_each_file_path(tmp_path, monkeypatch):
    root = tmp_path / "scan-root"
    nested = root / "nested"
    nested.mkdir(parents=True)
    (root / "one.txt").write_text("one", encoding="utf-8")
    (nested / "two.txt").write_text("two", encoding="utf-8")

    scanner = object.__new__(index_jobs.IndexJobs)
    scanner.condition = threading.Condition(threading.RLock())
    checkpoint_calls = []
    monkeypatch.setattr(
        scanner, "_checkpoint", lambda *_args: checkpoint_calls.append(True)
    )
    resolve_calls = []
    original_resolve = Path.resolve

    def tracked_resolve(path, *args, **kwargs):
        resolve_calls.append(path)
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", tracked_resolve)

    files = scanner._scan(
        "qin-370-test",
        1,
        {"scan-root": root},
        {
            "roots": ["scan-root"],
            "scopes": [
                {
                    "root_id": "scan-root",
                    "prefix": "",
                    "storage_root_id": "scan-root",
                }
            ],
            "hash_files": False,
            "max_files": 100,
        },
    )

    assert {item["display_path"] for item in files} == {"one.txt", "nested/two.txt"}
    assert len(resolve_calls) <= 5
    assert len(checkpoint_calls) <= 8


def test_index_job_reuses_resolved_roots_but_rechecks_authorization(tmp_path, monkeypatch):
    monkeypatch.setenv("GW_ALLOWED_ROOTS", str(tmp_path))
    root = tmp_path / "scan-root"
    alias = {
        "root_id": "scan-root",
        "root": root,
        "prefix": "",
        "storage_root_id": "scan-root",
        "name": "scan-root",
    }
    aliases = {"scan-root": alias}
    canonical = {"scan-root": root}
    fingerprint = index_jobs._digest(
        {key: str(value) for key, value in sorted(aliases.items())}
    )
    scanner = object.__new__(index_jobs.IndexJobs)
    scanner.root = storage.data_root().resolve()
    scanner.closed = False
    scanner._root_snapshot_cache = {}
    scanner._runtime_path_configuration = index_jobs._runtime_path_configuration()
    roots_calls = []
    admission_calls = []
    data_root_calls = []
    original_data_root = storage.data_root

    def tracked_data_root():
        data_root_calls.append(True)
        return original_data_root()

    def fake_roots():
        roots_calls.append(True)
        return canonical, aliases

    monkeypatch.setattr(index_jobs, "_roots", fake_roots)
    monkeypatch.setattr(storage, "data_root", tracked_data_root)
    monkeypatch.setattr(
        index_jobs, "_admission", lambda actor: admission_calls.append(actor)
    )
    job = {"_actor": {"subject": "test"}, "_fingerprint": fingerprint}

    assert scanner._check(job) == canonical
    assert scanner._check(job) == canonical

    assert len(roots_calls) == 1
    assert len(admission_calls) == 2
    assert data_root_calls == []
