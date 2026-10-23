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

