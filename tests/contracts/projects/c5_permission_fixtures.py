"""C5-T1 permission matrix fixtures.

The fixture uses public HTTP routes for domain resources so every matrix cell
exercises the same contract as a client.  The only in-process state injection
is the offline index job, which is required to reach the provider-free 503
boundary without contacting a real filesystem provider.
"""

from __future__ import annotations

import base64
import hashlib
import os

from gw.core.auth import require_authenticated


USERS = {
    "owner": ("c5-owner-a", "editor"),
    "cross_project_editor": ("c5-owner-b", "editor"),
    "revoked_editor": ("c5-revoked-editor", "editor"),
    "read_grantee_editor": ("c5-read-grantee", "editor"),
    "admin": ("c5-admin", "admin"),
    "governor": ("c5-governor", "governor"),
    "readonly_grantee": ("c5-readonly-grantee", "readonly"),
    "readonly_no_grant": ("c5-readonly-none", "readonly"),
}

TOKENS = {role: values[0] for role, values in USERS.items()}


def subject(role: str) -> str:
    """Return the subject produced by the local bearer authentication path."""

    token, user_role = USERS[role]
    return require_authenticated(f"Bearer {token}", user_role).subject


def headers(role: str) -> dict[str, str]:
    """Build request headers for one matrix role."""

    if role == "anonymous":
        return {}
    token, user_role = USERS[role]
    return {"Authorization": f"Bearer {token}", "X-User-Role": user_role}


def _assert_status(response, expected: int) -> None:
    assert response.status_code == expected, response.text


def _create_project(client, role: str, name: str) -> str:
    response = client.post(
        "/api/asset-registry/projects",
        headers=headers(role),
        json={"name": name, "project_type": "film"},
    )
    _assert_status(response, 201)
    return response.json()["project"]["project_id"]


def _create_asset(client, role: str, project_id: str, name: str) -> tuple[str, int]:
    """Import a tiny real file so media-read cells have usable content."""

    # Multipart uploads are stored below the isolated data root.  The media
    # route resolves display_path through GW_ALLOWED_ROOTS, so admit only this
    # per-test root and point the imported record at the uploaded PNG.
    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGD4DwAB"
        "BAEAwS2OUAAAAABJRU5ErkJggg=="
    )
    response = client.post(
        "/api/asset-registry/assets/import",
        headers=headers(role),
        files={"file": ("c5-fixture.png", png, "image/png")},
    )
    _assert_status(response, 200)
    asset_id = response.json()["asset_ids"][0]
    from gw.asset_registry import repository as asset_repository
    from gw.core import storage

    os.environ["GW_ALLOWED_ROOTS"] = str(storage.data_root())
    stored_name = response.json()["asset"].get("stored_name")
    assert stored_name

    def expose_uploaded_media(raw):
        raw["assets"][asset_id]["display_path"] = f"registry_uploads/{stored_name}"
        return raw

    asset_repository.state().mutate(expose_uploaded_media)
    response = client.post(
        f"/api/asset-registry/projects/{project_id}/assets",
        headers=headers(role),
        json={"asset_ids": [asset_id]},
    )
    _assert_status(response, 200)
    # The asset mutation CAS is the registry-wide revision.  Read the same
    # repository snapshot the route uses after the link operation; the list
    # response can be served from its compatibility projection.
    revision = int(asset_repository.state().read().get("revision", 1))
    return asset_id, revision


def _create_canvas(client, role: str, project_id: str, title: str) -> str:
    response = client.post(
        "/api/canvases",
        headers=headers(role),
        json={
            "project_id": project_id,
            "title": title,
            "mode": "classic",
            "initial_payload": {
                "nodes": [{"entity_id": "c5-entry", "kind": "text_prompt"}],
                "connections": [],
            },
        },
    )
    _assert_status(response, 201)
    return response.json()["canvas"]["canvas_id"]


def _create_workflow(
    client, role: str, project_id: str, canvas_id: str, name: str, *, bind: bool = True
) -> tuple[str, int]:
    response = client.post(
        f"/api/god_workflow/documents?name={name}",
        headers=headers(role),
        json={"1": {"class_type": "LoadImage", "inputs": {}}},
    )
    _assert_status(response, 201)
    workflow_id = response.json()["workflow_id"]
    if not bind:
        return workflow_id, 1
    response = client.put(
        f"/api/god_workflow/documents/{workflow_id}",
        headers=headers(role),
        json={
            "expected_version": 1,
            "source_context": {"project_id": project_id, "canvas_id": canvas_id},
        },
    )
    _assert_status(response, 200)
    return workflow_id, int(response.json().get("version", 2))


def _seed_bundle(client, role: str, project_id: str, prefix: str) -> dict[str, object]:
    """Create the asset, canvas, and workflow bound to one project."""

    asset_id, asset_revision = _create_asset(client, role, project_id, f"{prefix}.txt")
    canvas_id = _create_canvas(client, role, project_id, prefix)
    workflow_id, workflow_version = _create_workflow(
        client, role, project_id, canvas_id, prefix
    )
    return {
        "asset_id": asset_id,
        "canvas_id": canvas_id,
        "workflow_id": workflow_id,
        "workflow_version": workflow_version,
        "asset_version": asset_revision,
    }


def _seed_offline_job(role: str, project_id: str | None = None) -> str | None:
    """Install a local queued job that reaches the provider-free 503 boundary."""

    if role not in {"owner", "admin", "governor"}:
        return None
    from gw.asset_registry import index_jobs

    service = index_jobs.service()
    token, user_role = USERS[role]
    context = require_authenticated(f"Bearer {token}", user_role)
    actor = index_jobs._actor(context)
    owner = index_jobs._owner(actor)
    job_id = "c5-offline-" + hashlib.sha256(role.encode()).hexdigest()[:12]
    aliases = index_jobs._roots()[1]
    fingerprint = index_jobs._digest(
        {key: str(value) for key, value in sorted(aliases.items())}
    )
    record = {
        "job_id": job_id,
        "job_type": "asset.index.sync",
        "name": "C5 offline fixture",
        "status": "queued",
        "version": 1,
        "attempt": 1,
        "max_attempts": 5,
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
        "scanned": 0,
        "error_count": 0,
        "result": None,
        "error_code": None,
        "project_id": project_id,
        "_actor": actor,
        "_owner": owner,
        "_fingerprint": fingerprint,
        "_request": {"kind": "sync", "roots": [], "max_files": 1, "hash_files": False},
        "_request_digest": "c5-offline",
    }

    def install(raw):
        raw["jobs"][job_id] = record
        return raw

    service.store.mutate(install)
    return job_id


WRITER_ROLES = frozenset({"owner", "admin", "governor"})


def seed(
    client, state: str, action: str | None = None, role: str | None = None
) -> dict[str, object]:
    """Seed the project state and only resources needed by one matrix cell."""

    active_id = _create_project(client, "owner", "P_ACTIVE_A")
    cross_id = _create_project(client, "cross_project_editor", "P_ACTIVE_B")

    response = client.post(
        f"/api/asset-registry/projects/{active_id}/read-grants",
        headers=headers("owner"),
        json={"subject": subject("revoked_editor")},
    )
    _assert_status(response, 200)
    for grantee in ("read_grantee_editor", "readonly_grantee"):
        response = client.post(
            f"/api/asset-registry/projects/{active_id}/read-grants",
            headers=headers("owner"),
            json={"subject": subject(grantee)},
        )
        _assert_status(response, 200)
    response = client.post(
        f"/api/asset-registry/projects/{active_id}/read-grants/revoke",
        headers=headers("owner"),
        json={"subject": subject("revoked_editor")},
    )
    _assert_status(response, 200)
    response = client.post(
        f"/api/asset-registry/projects/{active_id}/read-grants/explicit",
        headers=headers("owner"),
    )
    _assert_status(response, 200)

    legacy_id = _create_project(client, "owner", "P_LEGACY_A")
    from gw.projects_hub import service as projects_service_module
    with projects_service_module.default_projects_service._operation() as (root, p_state, _):
        if legacy_id in p_state.get("read_access", {}):
            p_state["read_access"][legacy_id]["mode"] = "legacy_open"
            projects_service_module.default_projects_service._commit_state(root, p_state)
    response = client.post(
        f"/api/asset-registry/projects/{legacy_id}/read-grants/revoke",
        headers=headers("owner"),
        json={"subject": subject("revoked_editor")},
    )
    _assert_status(response, 200)

    target_id = {
        "active": active_id,
        "active_legacy_open": legacy_id,
        "archived": active_id,
        "deleted": active_id,
        "unknown": "P_UNKNOWN",
    }[state]

    needs_bundle = action is None or action.startswith(("asset_", "canvas_", "workflow_"))
    needs_cross_bundle = action in {"asset_list_all", "canvas_list_all"}
    empty_bundle = {
        "asset_id": None,
        "canvas_id": None,
        "workflow_id": None,
        "workflow_version": 1,
    }
    target_bundle = dict(empty_bundle)
    cross_bundle = dict(empty_bundle)
    legacy_bundle = dict(empty_bundle)
    if needs_bundle and state != "unknown":
        target_bundle = _seed_bundle(client, "owner", target_id, "ASSET_ACTIVE_A")
    if needs_cross_bundle:
        cross_bundle = _seed_bundle(client, "cross_project_editor", cross_id, "ASSET_ACTIVE_B")
    if (
        action == "workflow_task"
        and role in WRITER_ROLES
        and role != "owner"
        and state != "unknown"
    ):
        # Workflows are stored per subject directory, so a writer can only
        # execute a workflow it owns.  Seed one for the writer and bind it to
        # the target project via the public source_context PUT.
        workflow_id, workflow_version = _create_workflow(
            client, role, target_id, target_bundle["canvas_id"], f"WF_{role}",
            bind=role == "owner",
        )
        target_bundle["workflow_id"] = workflow_id
        target_bundle["workflow_version"] = workflow_version
    if needs_cross_bundle and state == "active_legacy_open":
        legacy_bundle = _seed_bundle(client, "owner", legacy_id, "ASSET_LEGACY_A")

    project_version = 1
    if state in {"archived", "deleted"}:
        response = client.get(f"/api/projects/{active_id}", headers=headers("owner"))
        _assert_status(response, 200)
        version = response.json()["project"]["version"]
        response = client.request(
            "DELETE",
            f"/api/asset-registry/projects/{active_id}",
            headers=headers("governor"),
            json={"expected_version": version},
        )
        _assert_status(response, 200)
        project_version = int(response.json()["project"]["version"])
        if state == "deleted":
            response = client.get(f"/api/projects/{active_id}", headers=headers("owner"))
            _assert_status(response, 200)
            version = response.json()["project"]["version"]
            response = client.post(
                f"/api/asset-registry/projects/{active_id}/trash",
                headers=headers("governor"),
                json={"expected_version": version},
            )
            _assert_status(response, 200)
            project_version = int(response.json()["project"]["version"])

    if state == "unknown":
        target_bundle = {
            "asset_id": "ASSET_UNKNOWN",
            "canvas_id": "CANVAS_UNKNOWN",
            "workflow_id": "WORKFLOW_UNKNOWN",
            "workflow_version": 1,
        }

    return {
        "project_id": target_id,
        "active_project_id": active_id,
        "legacy_project_id": legacy_id,
        "cross_project_id": cross_id,
        "unknown_project_id": "P_UNKNOWN",
        "asset_id": target_bundle["asset_id"],
        "canvas_id": target_bundle["canvas_id"],
        "workflow_id": target_bundle["workflow_id"],
        "workflow_version": target_bundle["workflow_version"],
        "cross_asset_id": cross_bundle["asset_id"],
        "cross_canvas_id": cross_bundle["canvas_id"],
        "cross_workflow_id": cross_bundle["workflow_id"],
        "legacy_asset_id": legacy_bundle["asset_id"],
        "legacy_canvas_id": legacy_bundle["canvas_id"],
        "legacy_workflow_id": legacy_bundle["workflow_id"],
        "asset_version": target_bundle.get("asset_version", 1),
        "project_version": project_version,
        "state": state,
        "canvas_version": 1,
        "job_id": (
            _seed_offline_job(role or "", target_id)
            if action == "asset_task" and state != "unknown"
            else "C5-OFFLINE-JOB"
        ),
    }
