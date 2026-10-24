import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from gw.api.routes_entities import router
from gw.core.errors import CleanroomException

AUTH = {"Authorization": "Bearer test-user", "X-User-Role": "editor"}


def _app() -> FastAPI:
    app = FastAPI()

    @app.exception_handler(CleanroomException)
    async def cleanroom_error(_request, exc: CleanroomException):
        return __import__("fastapi").responses.JSONResponse(
            status_code=exc.status_code,
            content=exc.to_envelope().model_dump(exclude_none=True),
        )

    app.include_router(router)
    return app


def test_repository_crud_happy_path(monkeypatch, tmp_path):
    monkeypatch.setenv("GW_RUNTIME_MODE", "test")
    monkeypatch.setenv("GW_AUTH_MODE", "local")
    monkeypatch.setenv("GW_DATA_DIR", str(tmp_path / "data"))

    with TestClient(_app()) as client:
        # Create
        created = client.post(
            "/api/entities",
            headers=AUTH,
            json={
                "project_id": "proj_1",
                "entity_type": "character",
                "name": "Alice",
            },
        )
        assert created.status_code == 201
        body = created.json()
        entity_id = body["entity"]["entity_id"]
        revision = body["revision"]

        # Get
        got = client.get(f"/api/entities/{entity_id}?project_id=proj_1", headers=AUTH)
        assert got.status_code == 200
        assert got.json()["entity"]["name"] == "Alice"

        # Update
        patched = client.patch(
            f"/api/entities/{entity_id}",
            headers=AUTH,
            json={
                "expected_version": revision,
                "description": "A character",
            },
        )
        assert patched.status_code == 200
        assert patched.json()["entity"]["description"] == "A character"
        new_revision = patched.json()["revision"]

        # Archive
        archived = client.post(
            f"/api/entities/{entity_id}/archive",
            headers=AUTH,
            json={
                "expected_version": new_revision,
            },
        )
        assert archived.status_code == 200

        # Verify Get 404s on Archived
        got_archived = client.get(f"/api/entities/{entity_id}?project_id=proj_1", headers=AUTH)
        assert got_archived.status_code == 404

        # Verify List filters Archived
        listed = client.get("/api/entities?project_id=proj_1", headers=AUTH)
        assert listed.status_code == 200
        assert len(listed.json()["entities"]) == 0


def test_repository_cas_conflict(monkeypatch, tmp_path):
    monkeypatch.setenv("GW_RUNTIME_MODE", "test")
    monkeypatch.setenv("GW_AUTH_MODE", "local")
    monkeypatch.setenv("GW_DATA_DIR", str(tmp_path / "data"))

    with TestClient(_app()) as client:
        # Create
        created = client.post(
            "/api/entities",
            headers=AUTH,
            json={
                "project_id": "proj_1",
                "entity_type": "prop",
                "name": "Sword",
            },
        )
        assert created.status_code == 201
        body = created.json()
        entity_id = body["entity"]["entity_id"]
        revision = body["revision"]

        # Update - succeeds
        patched = client.patch(
            f"/api/entities/{entity_id}",
            headers=AUTH,
            json={
                "expected_version": revision,
                "name": "Sword (Shiny)",
            },
        )
        assert patched.status_code == 200
        
        # Update - fails due to stale expected_version
        conflict = client.patch(
            f"/api/entities/{entity_id}",
            headers=AUTH,
            json={
                "expected_version": revision,
                "description": "Will fail",
            },
        )
        assert conflict.status_code == 409
        assert conflict.json()["detail"]["code"] == "VERSION_CONFLICT"


def test_entities_repository_fails_closed_when_entities_field_is_missing_or_invalid(
    monkeypatch, tmp_path, caplog
):
    import json

    from gw.core import storage
    from gw.entities import repository as entity_repo

    monkeypatch.setenv("GW_RUNTIME_MODE", "test")
    monkeypatch.setenv("GW_AUTH_MODE", "local")
    monkeypatch.setenv("GW_DATA_DIR", str(tmp_path / "data"))

    with TestClient(_app()) as client:
        # 先正常创建一个实体以落盘文件
        created = client.post(
            "/api/entities",
            headers=AUTH,
            json={
                "project_id": "proj_1",
                "entity_type": "character",
                "name": "Bob",
            },
        )
        assert created.status_code == 201

        # 模拟文件被外部写入，缺少 entities 字段
        entity_file = storage.file_for(entity_repo.NS_ENTITIES)
        entity_file.write_bytes(json.dumps({"revision": 2}, ensure_ascii=False).encode("utf-8"))

        caplog.clear()
        # 实体模块读取列表应该 fail-closed 返回 503
        list_res = client.get("/api/entities?project_id=proj_1", headers=AUTH)
        assert list_res.status_code == 503
        assert list_res.json()["detail"]["code"] == "ENTITY_INDEX_UNAVAILABLE"
        assert "entities" in caplog.text

        # 实体模块读取单条应该同样 fail-closed 返回 503
        get_res = client.get("/api/entities/ent_1?project_id=proj_1", headers=AUTH)
        assert get_res.status_code == 503
        assert get_res.json()["detail"]["code"] == "ENTITY_INDEX_UNAVAILABLE"


CORRUPT_CASES = [
    ("zero_bytes", b""),
    ("whitespace_only", b"   \r\n\t  "),
    ("truncated_json", b'{"revision": 1, "entities": {'),
    ("utf8_bom", b'\xef\xbb\xbf{"revision": 1, "entities": {}}'),
    ("top_level_null", b"null"),
    ("top_level_list", b'[{"entity_id": "ent_1"}]'),
    ("missing_entities", b'{"revision": 1}'),
    ("entities_not_dict_list", b'{"revision": 1, "entities": ["not_a_dict"]}'),
    ("entities_not_dict_int", b'{"revision": 1, "entities": 123}'),
    ("revision_string_x", b'{"revision": "x", "entities": {}}'),
    ("revision_string_nan", b'{"revision": "NaN", "entities": {}}'),
    ("revision_zero", b'{"revision": 0, "entities": {}}'),
    ("revision_negative", b'{"revision": -1, "entities": {}}'),
    ("revision_bool", b'{"revision": true, "entities": {}}'),
    ("revision_float", b'{"revision": 1.5, "entities": {}}'),
    ("revision_null", b'{"revision": null, "entities": {}}'),
    ("latin1_bytes", b'{"revision": 1, "name": "\xe9"}'),
    ("invalid_utf8_binary", b"\xff\xfe\x00\x00\x12\x34"),
]


@pytest.mark.parametrize("case_name,corrupt_bytes", CORRUPT_CASES)
def test_entities_repository_corrupt_files_fail_closed_on_read(
    monkeypatch, tmp_path, caplog, case_name, corrupt_bytes
):

    from gw.core import storage
    from gw.entities import repository as entity_repo

    monkeypatch.setenv("GW_RUNTIME_MODE", "test")
    monkeypatch.setenv("GW_AUTH_MODE", "local")
    monkeypatch.setenv("GW_DATA_DIR", str(tmp_path / "data"))

    with TestClient(_app()) as client:
        entity_file = storage.file_for(entity_repo.NS_ENTITIES)
        entity_file.parent.mkdir(parents=True, exist_ok=True)
        entity_file.write_bytes(corrupt_bytes)

        caplog.clear()
        res = client.get("/api/entities?project_id=proj_1", headers=AUTH)
        assert res.status_code == 503, f"{case_name} expected 503, got {res.status_code}: {res.text}"
        assert res.json()["detail"]["code"] == "ENTITY_INDEX_UNAVAILABLE"
        assert str(tmp_path) not in res.text
        assert any(r.levelname == "WARNING" for r in caplog.records)

        # 单条读取也是 503
        get_res = client.get("/api/entities/ent_test?project_id=proj_1", headers=AUTH)
        assert get_res.status_code == 503
        assert get_res.json()["detail"]["code"] == "ENTITY_INDEX_UNAVAILABLE"
        assert str(tmp_path) not in get_res.text


@pytest.mark.parametrize("case_name,corrupt_bytes", CORRUPT_CASES)
def test_entities_repository_corrupt_files_fail_closed_on_write_and_preserve_file(
    monkeypatch, tmp_path, caplog, case_name, corrupt_bytes
):
    import hashlib

    from gw.core import storage
    from gw.entities import repository as entity_repo

    monkeypatch.setenv("GW_RUNTIME_MODE", "test")
    monkeypatch.setenv("GW_AUTH_MODE", "local")
    monkeypatch.setenv("GW_DATA_DIR", str(tmp_path / "data"))

    with TestClient(_app()) as client:
        entity_file = storage.file_for(entity_repo.NS_ENTITIES)
        entity_file.parent.mkdir(parents=True, exist_ok=True)
        entity_file.write_bytes(corrupt_bytes)
        before_sha = hashlib.sha256(entity_file.read_bytes()).hexdigest()

        caplog.clear()
        # 写路径：create_entity
        create_res = client.post(
            "/api/entities",
            headers=AUTH,
            json={
                "project_id": "proj_1",
                "entity_type": "character",
                "name": "Eve",
            },
        )
        assert create_res.status_code == 503, f"{case_name} create expected 503, got {create_res.status_code}: {create_res.text}"
        assert create_res.json()["detail"]["code"] == "ENTITY_INDEX_UNAVAILABLE"
        assert str(tmp_path) not in create_res.text
        assert any(r.levelname == "WARNING" for r in caplog.records)

        # 核心断言：原文件字节和 sha256 绝对不变，绝不能覆盖！
        after_sha = hashlib.sha256(entity_file.read_bytes()).hexdigest()
        assert after_sha == before_sha, f"{case_name} file was modified during write!"

        # 写路径：update_entity
        update_res = client.patch(
            "/api/entities/ent_dummy",
            headers=AUTH,
            json={
                "expected_version": 1,
                "name": "Updated",
            },
        )
        assert update_res.status_code == 503
        assert update_res.json()["detail"]["code"] == "ENTITY_INDEX_UNAVAILABLE"
        assert str(tmp_path) not in update_res.text
        assert hashlib.sha256(entity_file.read_bytes()).hexdigest() == before_sha

        # 写路径：archive_entity
        archive_res = client.post(
            "/api/entities/ent_dummy/archive",
            headers=AUTH,
            json={
                "expected_version": 1,
            },
        )
        assert archive_res.status_code == 503
        assert archive_res.json()["detail"]["code"] == "ENTITY_INDEX_UNAVAILABLE"
        assert str(tmp_path) not in archive_res.text
        assert hashlib.sha256(entity_file.read_bytes()).hexdigest() == before_sha


def test_entities_repository_oserror_directory_placeholder_fails_closed(
    monkeypatch, tmp_path, caplog
):
    from gw.core import storage
    from gw.entities import repository as entity_repo

    monkeypatch.setenv("GW_RUNTIME_MODE", "test")
    monkeypatch.setenv("GW_AUTH_MODE", "local")
    monkeypatch.setenv("GW_DATA_DIR", str(tmp_path / "data"))

    with TestClient(_app()) as client:
        # 把实体母库路径创建为一个目录（目录占位导致 read_bytes / open 抛 IsADirectoryError / PermissionError 等 OSError）
        entity_path = storage.file_for(entity_repo.NS_ENTITIES)
        entity_path.parent.mkdir(parents=True, exist_ok=True)
        entity_path.mkdir(exist_ok=True)

        caplog.clear()
        res = client.get("/api/entities?project_id=proj_1", headers=AUTH)
        assert res.status_code == 503
        assert res.json()["detail"]["code"] == "ENTITY_INDEX_UNAVAILABLE"
        assert str(tmp_path) not in res.text
        assert any(r.levelname == "WARNING" for r in caplog.records)

        create_res = client.post(
            "/api/entities",
            headers=AUTH,
            json={
                "project_id": "proj_1",
                "entity_type": "character",
                "name": "Eve",
            },
        )
        assert create_res.status_code == 503
        assert create_res.json()["detail"]["code"] == "ENTITY_INDEX_UNAVAILABLE"
        assert str(tmp_path) not in create_res.text
        assert entity_path.is_dir()


def test_entity_repository_uses_public_namespace_lock(monkeypatch, tmp_path):
    from gw.core import storage
    from gw.entities import repository as entity_repo

    monkeypatch.setenv("GW_RUNTIME_MODE", "test")
    monkeypatch.setenv("GW_AUTH_MODE", "local")
    monkeypatch.setenv("GW_DATA_DIR", str(tmp_path / "data"))

    public_lock = getattr(storage, "namespace_lock", None)
    assert callable(public_lock)
    lock_namespaces = []

    def track_namespace_lock(namespace):
        lock_namespaces.append(namespace)
        return public_lock(namespace)

    monkeypatch.setattr(storage, "namespace_lock", track_namespace_lock)

    with TestClient(_app()) as client:
        created = client.post(
            "/api/entities",
            headers=AUTH,
            json={
                "project_id": "proj_1",
                "entity_type": "character",
                "name": "Lock Contract",
            },
        )

    assert created.status_code == 201, created.text
    assert entity_repo.NS_ENTITIES in lock_namespaces


def test_json_state_does_not_expose_unused_file_path_property():
    from gw.core import storage

    assert not hasattr(storage.JsonState("unused", dict), "file_path")
