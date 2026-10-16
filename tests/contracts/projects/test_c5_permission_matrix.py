"""C5-T1 permission matrix contract tests (C5-AC1 through C5-AC9)."""

from __future__ import annotations

import json

import pytest

from tests.contracts.projects.c5_permission_fixtures import headers, seed, subject
from tests.contracts.projects.c5_permission_matrix_data import ACTIONS, CELLS


# The data module intentionally contains only the generated matrix.  These
# three route specs are kept here because their cells are limited to 401 and
# the QA matrix requires the exact method/path for each action (R11).
ACTION_SPECS = {
    **ACTIONS,
    "project_read_grants": (
        "GET",
        "/api/asset-registry/projects/{project_id}/read-grants",
        "C5-AC7",
    ),
    "project_grant": (
        "POST",
        "/api/asset-registry/projects/{project_id}/read-grants",
        "C5-AC7",
    ),
    "project_revoke": (
        "POST",
        "/api/asset-registry/projects/{project_id}/read-grants/revoke",
        "C5-AC7",
    ),
}


KNOWN_GAPS: dict[str, str] = {
    'test_cell_asset_restore_move__admin__active': 'C5-T3: expected allow, observed HTTP 409',
    'test_cell_asset_restore_move__admin__active_legacy_open': 'C5-T3: expected allow, observed HTTP 409',
    'test_cell_asset_restore_move__admin__archived': 'C5-T3: expected 403 PROJECT_READ_ONLY, observed HTTP 409',
    'test_cell_asset_restore_move__admin__deleted': 'C5-T3: expected 403 PROJECT_READ_ONLY, observed HTTP 200',
    'test_cell_asset_restore_move__cross_project_editor__active': 'C5-T3: expected 404, observed HTTP 403',
    'test_cell_asset_restore_move__cross_project_editor__active_legacy_open': 'C5-T3: expected 404, observed HTTP 403',
    'test_cell_asset_restore_move__cross_project_editor__archived': 'C5-T3: expected 404, observed HTTP 403',
    'test_cell_asset_restore_move__cross_project_editor__deleted': 'C5-T3: expected 404, observed HTTP 403',
    'test_cell_asset_restore_move__cross_project_editor__unknown': 'C5-T3: expected 404, observed HTTP 403',
    'test_cell_asset_restore_move__governor__active': 'C5-T3: expected allow, observed HTTP 409',
    'test_cell_asset_restore_move__governor__active_legacy_open': 'C5-T3: expected allow, observed HTTP 409',
    'test_cell_asset_restore_move__governor__archived': 'C5-T3: expected 403 PROJECT_READ_ONLY, observed HTTP 409',
    'test_cell_asset_restore_move__governor__deleted': 'C5-T3: expected 403 PROJECT_READ_ONLY, observed HTTP 200',
    'test_cell_asset_restore_move__owner__active': 'C5-T3: expected allow, observed HTTP 403',
    'test_cell_asset_restore_move__owner__active_legacy_open': 'C5-T3: expected allow, observed HTTP 403',
    'test_cell_asset_restore_move__owner__archived': 'C5-T3: observed 403 with error code FORBIDDEN',
    'test_cell_asset_restore_move__owner__deleted': 'C5-T3: observed 403 with error code FORBIDDEN',
    'test_cell_asset_restore_move__owner__unknown': 'C5-T3: expected 404, observed HTTP 403',
    'test_cell_asset_restore_move__read_grantee_editor__active': 'C5-T3: expected 404, observed HTTP 403',
    'test_cell_asset_restore_move__read_grantee_editor__active_legacy_open': 'C5-T3: expected 404, observed HTTP 403',
    'test_cell_asset_restore_move__read_grantee_editor__archived': 'C5-T3: expected 404, observed HTTP 403',
    'test_cell_asset_restore_move__read_grantee_editor__deleted': 'C5-T3: expected 404, observed HTTP 403',
    'test_cell_asset_restore_move__read_grantee_editor__unknown': 'C5-T3: expected 404, observed HTTP 403',
    'test_cell_asset_restore_move__readonly_grantee__active': 'C5-T3: expected 404, observed HTTP 403',
    'test_cell_asset_restore_move__readonly_grantee__active_legacy_open': 'C5-T3: expected 404, observed HTTP 403',
    'test_cell_asset_restore_move__readonly_grantee__archived': 'C5-T3: expected 404, observed HTTP 403',
    'test_cell_asset_restore_move__readonly_grantee__deleted': 'C5-T3: expected 404, observed HTTP 403',
    'test_cell_asset_restore_move__readonly_grantee__unknown': 'C5-T3: expected 404, observed HTTP 403',
    'test_cell_asset_restore_move__readonly_no_grant__active': 'C5-T3: expected 404, observed HTTP 403',
    'test_cell_asset_restore_move__readonly_no_grant__active_legacy_open': 'C5-T3: expected 404, observed HTTP 403',
    'test_cell_asset_restore_move__readonly_no_grant__archived': 'C5-T3: expected 404, observed HTTP 403',
    'test_cell_asset_restore_move__readonly_no_grant__deleted': 'C5-T3: expected 404, observed HTTP 403',
    'test_cell_asset_restore_move__readonly_no_grant__unknown': 'C5-T3: expected 404, observed HTTP 403',
    'test_cell_asset_restore_move__revoked_editor__active': 'C5-T3: expected 404, observed HTTP 403',
    'test_cell_asset_restore_move__revoked_editor__active_legacy_open': 'C5-T3: expected 404, observed HTTP 403',
    'test_cell_asset_restore_move__revoked_editor__archived': 'C5-T3: expected 404, observed HTTP 403',
    'test_cell_asset_restore_move__revoked_editor__deleted': 'C5-T3: expected 404, observed HTTP 403',
    'test_cell_asset_restore_move__revoked_editor__unknown': 'C5-T3: expected 404, observed HTTP 403',
    'test_cell_workflow_bridge__admin__archived': 'C5-T5: expected 403 PROJECT_READ_ONLY, observed HTTP 200',
    'test_cell_workflow_bridge__admin__deleted': 'C5-T5: expected 403 PROJECT_READ_ONLY, observed HTTP 200',
    'test_cell_workflow_bridge__admin__unknown': 'C5-T5: expected 404, observed HTTP 200',
    'test_cell_workflow_bridge__cross_project_editor__active': 'C5-T5: expected 404, observed HTTP 200',
    'test_cell_workflow_bridge__cross_project_editor__active_legacy_open': 'C5-T5: expected 404, observed HTTP 200',
    'test_cell_workflow_bridge__cross_project_editor__archived': 'C5-T5: expected 404, observed HTTP 200',
    'test_cell_workflow_bridge__cross_project_editor__deleted': 'C5-T5: expected 404, observed HTTP 200',
    'test_cell_workflow_bridge__cross_project_editor__unknown': 'C5-T5: expected 404, observed HTTP 200',
    'test_cell_workflow_bridge__governor__archived': 'C5-T5: expected 403 PROJECT_READ_ONLY, observed HTTP 200',
    'test_cell_workflow_bridge__governor__deleted': 'C5-T5: expected 403 PROJECT_READ_ONLY, observed HTTP 200',
    'test_cell_workflow_bridge__governor__unknown': 'C5-T5: expected 404, observed HTTP 200',
    'test_cell_workflow_bridge__owner__archived': 'C5-T5: expected 403 PROJECT_READ_ONLY, observed HTTP 200',
    'test_cell_workflow_bridge__owner__deleted': 'C5-T5: expected 403 PROJECT_READ_ONLY, observed HTTP 200',
    'test_cell_workflow_bridge__owner__unknown': 'C5-T5: expected 404, observed HTTP 200',
    'test_cell_workflow_bridge__read_grantee_editor__active': 'C5-T5: expected 404, observed HTTP 200',
    'test_cell_workflow_bridge__read_grantee_editor__active_legacy_open': 'C5-T5: expected 404, observed HTTP 200',
    'test_cell_workflow_bridge__read_grantee_editor__archived': 'C5-T5: expected 404, observed HTTP 200',
    'test_cell_workflow_bridge__read_grantee_editor__deleted': 'C5-T5: expected 404, observed HTTP 200',
    'test_cell_workflow_bridge__read_grantee_editor__unknown': 'C5-T5: expected 404, observed HTTP 200',
    'test_cell_workflow_bridge__readonly_grantee__active': 'C5-T5: expected 404, observed HTTP 403',
    'test_cell_workflow_bridge__readonly_grantee__active_legacy_open': 'C5-T5: expected 404, observed HTTP 403',
    'test_cell_workflow_bridge__readonly_grantee__archived': 'C5-T5: expected 404, observed HTTP 403',
    'test_cell_workflow_bridge__readonly_grantee__deleted': 'C5-T5: expected 404, observed HTTP 403',
    'test_cell_workflow_bridge__readonly_grantee__unknown': 'C5-T5: expected 404, observed HTTP 403',
    'test_cell_workflow_bridge__readonly_no_grant__active': 'C5-T5: expected 404, observed HTTP 403',
    'test_cell_workflow_bridge__readonly_no_grant__active_legacy_open': 'C5-T5: expected 404, observed HTTP 403',
    'test_cell_workflow_bridge__readonly_no_grant__archived': 'C5-T5: expected 404, observed HTTP 403',
    'test_cell_workflow_bridge__readonly_no_grant__deleted': 'C5-T5: expected 404, observed HTTP 403',
    'test_cell_workflow_bridge__readonly_no_grant__unknown': 'C5-T5: expected 404, observed HTTP 403',
    'test_cell_workflow_bridge__revoked_editor__active': 'C5-T5: expected 404, observed HTTP 200',
    'test_cell_workflow_bridge__revoked_editor__active_legacy_open': 'C5-T5: expected 404, observed HTTP 200',
    'test_cell_workflow_bridge__revoked_editor__archived': 'C5-T5: expected 404, observed HTTP 200',
    'test_cell_workflow_bridge__revoked_editor__deleted': 'C5-T5: expected 404, observed HTTP 200',
    'test_cell_workflow_bridge__revoked_editor__unknown': 'C5-T5: expected 404, observed HTTP 200',
    'test_cell_workflow_read__admin__active': 'C5-T5: expected allow, observed HTTP 404',
    'test_cell_workflow_read__admin__active_legacy_open': 'C5-T5: expected allow, observed HTTP 404',
    'test_cell_workflow_read__admin__archived': 'C5-T5: expected allow, observed HTTP 404',
    'test_cell_workflow_read__admin__deleted': 'C5-T5: expected allow, observed HTTP 404',
    'test_cell_workflow_read__cross_project_editor__active_legacy_open': 'C5-T5: expected allow, observed HTTP 404',
    'test_cell_workflow_read__governor__active': 'C5-T5: expected allow, observed HTTP 404',
    'test_cell_workflow_read__governor__active_legacy_open': 'C5-T5: expected allow, observed HTTP 404',
    'test_cell_workflow_read__governor__archived': 'C5-T5: expected allow, observed HTTP 404',
    'test_cell_workflow_read__governor__deleted': 'C5-T5: expected allow, observed HTTP 404',
    'test_cell_workflow_read__read_grantee_editor__active': 'C5-T5: expected allow, observed HTTP 404',
    'test_cell_workflow_read__read_grantee_editor__active_legacy_open': 'C5-T5: expected allow, observed HTTP 404',
    'test_cell_workflow_read__read_grantee_editor__archived': 'C5-T5: expected allow, observed HTTP 404',
    'test_cell_workflow_read__read_grantee_editor__deleted': 'C5-T5: expected allow, observed HTTP 404',
    'test_cell_workflow_read__readonly_grantee__active': 'C5-T5: expected allow, observed HTTP 404',
    'test_cell_workflow_read__readonly_grantee__active_legacy_open': 'C5-T5: expected allow, observed HTTP 404',
    'test_cell_workflow_read__readonly_grantee__archived': 'C5-T5: expected allow, observed HTTP 404',
    'test_cell_workflow_read__readonly_grantee__deleted': 'C5-T5: expected allow, observed HTTP 404',
    'test_cell_workflow_read__readonly_no_grant__active_legacy_open': 'C5-T5: expected allow, observed HTTP 404',
    'test_cell_workflow_task__admin__archived': "C5-T5: expected 403 PROJECT_READ_ONLY, observed HTTP 404 PROJECT_NOT_FOUND ('项目不存在': source_context gate is owner-only for non-owner writers)",
    'test_cell_workflow_task__admin__deleted': "C5-T5: expected 403 PROJECT_READ_ONLY, observed HTTP 404 PROJECT_NOT_FOUND ('项目不存在': source_context gate is owner-only for non-owner writers)",
    'test_cell_workflow_task__governor__archived': "C5-T5: expected 403 PROJECT_READ_ONLY, observed HTTP 404 PROJECT_NOT_FOUND ('项目不存在': source_context gate is owner-only for non-owner writers)",
    'test_cell_workflow_task__governor__deleted': "C5-T5: expected 403 PROJECT_READ_ONLY, observed HTTP 404 PROJECT_NOT_FOUND ('项目不存在': source_context gate is owner-only for non-owner writers)",
    'test_cell_workflow_task__owner__archived': "C5-T5: expected 403 PROJECT_READ_ONLY, observed HTTP 404 PROJECT_NOT_FOUND ('来源项目不可访问': execute source_context gate hides archived/deleted project instead of read-only)",
    'test_cell_workflow_task__owner__deleted': "C5-T5: expected 403 PROJECT_READ_ONLY, observed HTTP 404 PROJECT_NOT_FOUND ('来源项目不可访问': execute source_context gate hides archived/deleted project instead of read-only)",
    'test_cell_workflow_task__admin__active': "C5-T5: expected allow (2xx), observed HTTP 404 PROJECT_NOT_FOUND ('项目不存在': execute/bind source_context uses owner-only get_owned_project; non-owner writer cannot run workflow in project context)",
    'test_cell_workflow_task__admin__active_legacy_open': "C5-T5: expected allow (2xx), observed HTTP 404 PROJECT_NOT_FOUND ('项目不存在': execute/bind source_context uses owner-only get_owned_project; non-owner writer cannot run workflow in project context)",
    'test_cell_workflow_task__governor__active': "C5-T5: expected allow (2xx), observed HTTP 404 PROJECT_NOT_FOUND ('项目不存在': execute/bind source_context uses owner-only get_owned_project; non-owner writer cannot run workflow in project context)",
    'test_cell_workflow_task__governor__active_legacy_open': "C5-T5: expected allow (2xx), observed HTTP 404 PROJECT_NOT_FOUND ('项目不存在': execute/bind source_context uses owner-only get_owned_project; non-owner writer cannot run workflow in project context)",
    'test_cell_workflow_task__readonly_grantee__active': 'C5-T5: expected 404, observed HTTP 403',
    'test_cell_workflow_task__readonly_grantee__active_legacy_open': 'C5-T5: expected 404, observed HTTP 403',
    'test_cell_workflow_task__readonly_grantee__archived': 'C5-T5: expected 404, observed HTTP 403',
    'test_cell_workflow_task__readonly_grantee__deleted': 'C5-T5: expected 404, observed HTTP 403',
    'test_cell_workflow_task__readonly_grantee__unknown': 'C5-T5: expected 404, observed HTTP 403',
    'test_cell_workflow_task__readonly_no_grant__active': 'C5-T5: expected 404, observed HTTP 403',
    'test_cell_workflow_task__readonly_no_grant__active_legacy_open': 'C5-T5: expected 404, observed HTTP 403',
    'test_cell_workflow_task__readonly_no_grant__archived': 'C5-T5: expected 404, observed HTTP 403',
    'test_cell_workflow_task__readonly_no_grant__deleted': 'C5-T5: expected 404, observed HTTP 403',
    'test_cell_workflow_task__readonly_no_grant__unknown': 'C5-T5: expected 404, observed HTTP 403',
    'test_cell_workflow_write__admin__active': 'C5-T5: expected allow, observed HTTP 404',
    'test_cell_workflow_write__admin__active_legacy_open': 'C5-T5: expected allow, observed HTTP 404',
    'test_cell_workflow_write__admin__archived': 'C5-T5: expected 403 PROJECT_READ_ONLY, observed HTTP 404',
    'test_cell_workflow_write__admin__deleted': 'C5-T5: expected 403 PROJECT_READ_ONLY, observed HTTP 404',
    'test_cell_workflow_write__governor__active': 'C5-T5: expected allow, observed HTTP 404',
    'test_cell_workflow_write__governor__active_legacy_open': 'C5-T5: expected allow, observed HTTP 404',
    'test_cell_workflow_write__governor__archived': 'C5-T5: expected 403 PROJECT_READ_ONLY, observed HTTP 404',
    'test_cell_workflow_write__governor__deleted': 'C5-T5: expected 403 PROJECT_READ_ONLY, observed HTTP 404',
    'test_cell_workflow_write__owner__archived': 'C5-T5: expected 403 PROJECT_READ_ONLY, observed HTTP 200',
    'test_cell_workflow_write__owner__deleted': 'C5-T5: expected 403 PROJECT_READ_ONLY, observed HTTP 200',
    'test_cell_workflow_write__readonly_grantee__active': 'C5-T5: expected 404, observed HTTP 403',
    'test_cell_workflow_write__readonly_grantee__active_legacy_open': 'C5-T5: expected 404, observed HTTP 403',
    'test_cell_workflow_write__readonly_grantee__archived': 'C5-T5: expected 404, observed HTTP 403',
    'test_cell_workflow_write__readonly_grantee__deleted': 'C5-T5: expected 404, observed HTTP 403',
    'test_cell_workflow_write__readonly_grantee__unknown': 'C5-T5: expected 404, observed HTTP 403',
    'test_cell_workflow_write__readonly_no_grant__active': 'C5-T5: expected 404, observed HTTP 403',
    'test_cell_workflow_write__readonly_no_grant__active_legacy_open': 'C5-T5: expected 404, observed HTTP 403',
    'test_cell_workflow_write__readonly_no_grant__archived': 'C5-T5: expected 404, observed HTTP 403',
    'test_cell_workflow_write__readonly_no_grant__deleted': 'C5-T5: expected 404, observed HTTP 403',
    'test_cell_workflow_write__readonly_no_grant__unknown': 'C5-T5: expected 404, observed HTTP 403',
}

# C5-T5 work closes the previously recorded workflow ACL gaps in this matrix.
KNOWN_GAPS = {
    name: reason for name, reason in KNOWN_GAPS.items()
    if not name.startswith("test_cell_workflow_")
}

# These operations are meaningful offline only up to their provider/runtime
# boundary.  A 503 is a valid result; authentication and resource hiding are
# still asserted strictly before that boundary.
OFFLINE_LIMITED = {
    "asset_task": "index source roots/providers are unavailable in the offline harness",
    "canvas_task": "smart canvas runtime is not configured in the offline harness",
    "workflow_task": "ComfyUI executor is not configured in the offline harness",
}

ALLOW_SUCCESS_CODES = frozenset({200, 201, 202, 204})

# The matrix includes an unfiltered canvas list spec, but that route requires
# project_id and therefore has no endpoint contract to exercise.
NA_ENDPOINTS: set[str] = {"canvas_list_all"}


def _error_code(response) -> str | None:
    """Extract the structured API error code from a response."""

    try:
        detail = response.json().get("detail")
    except ValueError:
        return None
    return detail.get("code") if isinstance(detail, dict) else None


def _assert_error_envelope(response) -> None:
    """Require the standard structured error envelope for hidden resources."""

    assert _error_code(response), response.text


def _format_path(action: str, ids: dict[str, object]) -> str:
    """Format an ACTION_SPECS path with the seeded resource identifiers."""

    path = ACTION_SPECS[action][1]
    return path.format(
        project_id=ids["project_id"],
        asset_id=ids.get("asset_id") or "ASSET_UNKNOWN",
        canvas_id=ids.get("canvas_id") or "CANVAS_UNKNOWN",
        workflow_id=ids.get("workflow_id") or "WORKFLOW_UNKNOWN",
        job_id=ids.get("job_id") or "C5-OFFLINE-JOB",
        action="cancel",
    )


def _request(client, action: str, role: str, ids: dict[str, object]):
    """Send one matrix action with a valid request body/query string."""

    method = ACTION_SPECS[action][0]
    path = _format_path(action, ids)
    body: dict[str, object] | None = {}

    if action == "project_patch":
        body = {"name": "C5 renamed", "expected_version": ids.get("project_version", 1)}
    elif action == "project_grant":
        body = {"subject": subject("read_grantee_editor")}
    elif action == "project_revoke":
        body = {"subject": subject("revoked_editor")}
    elif action == "asset_project_list":
        path += "?project_id=" + str(ids["project_id"])
        body = None
    elif action == "asset_read_media":
        body = None
    elif action == "asset_write_bind":
        body = {"asset_ids": [ids.get("asset_id") or "ASSET_UNKNOWN"]}
    elif action == "asset_write_mutate":
        revision_response = client.get(
            "/api/asset-registry/assets", headers=headers(role)
        )
        body = {
            "name": "C5 mutated",
            "expected_version": revision_response.json().get("revision", 1),
        }
    elif action == "asset_restore_move":
        body = {"expected_version": ids.get("project_version", 1)}
    elif action == "asset_task":
        body = {"expected_version": 1}
    elif action in {"canvas_list", "canvas_read"}:
        body = None
    elif action == "canvas_write":
        body = {
            "expected_version": ids.get("canvas_version", 1),
            "nodes": [{"entity_id": "c5-entry", "kind": "text_prompt"}],
            "connections": [],
        }
    elif action == "canvas_lifecycle":
        body = {"expected_version": ids.get("canvas_version", 1)}
    elif action == "canvas_workflow":
        path += "?expected_version=" + str(ids.get("canvas_version", 1))
        body = {
            "canvas_id": ids.get("canvas_id") or "CANVAS_UNKNOWN",
            "nodes": [],
            "connections": [],
        }
    elif action == "canvas_task":
        body = {
            "expected_version": ids.get("canvas_version", 1),
            "entry_nodes": ["c5-entry"],
            "run_mode": "single",
            "inputs": {},
        }
    elif action == "workflow_read":
        body = None
    elif action == "workflow_write":
        body = {
            "expected_version": ids.get("workflow_version", 2),
            "workflow": {"1": {"class_type": "LoadImage", "inputs": {}}},
        }
    elif action == "workflow_bridge":
        body = {}
    elif action == "workflow_task":
        if role in {"owner", "admin", "governor"} and ids.get("state") != "unknown":
            # Writers execute the seeded workflow bound (source_context) to
            # the cell's project, so the project context is exercised.
            body = {
                "workflow_id": ids["workflow_id"],
                "expected_version": ids.get("workflow_version", 2),
                "target": "local_comfy",
                "source_context": {"project_id": ids["project_id"], "canvas_id": ids["canvas_id"]},
            }
        else:
            body = {"workflow_id": ids.get("workflow_id") or "WORKFLOW_UNKNOWN"}
    elif action in {"asset_list_all", "canvas_list_all"}:
        body = None

    return client.request(method, path, headers=headers(role), json=body)


def _readback(client, action: str, ids: dict[str, object]):
    """Read a resource as owner after a rejected write for CAS safety checks."""

    owner_headers = headers("owner")
    if action == "project_patch":
        return client.get(f"/api/projects/{ids['project_id']}", headers=owner_headers)
    if action in {"asset_write_bind", "asset_write_mutate"}:
        return client.get(
            f"/api/asset-registry/assets/{ids.get('asset_id') or 'ASSET_UNKNOWN'}",
            headers=owner_headers,
        )
    if action in {"canvas_write", "canvas_lifecycle", "canvas_workflow", "canvas_task"}:
        return client.get(
            f"/api/canvases/{ids.get('canvas_id') or 'CANVAS_UNKNOWN'}",
            headers=owner_headers,
        )
    if action == "workflow_write":
        return client.get(
            f"/api/god_workflow/documents/{ids.get('workflow_id') or 'WORKFLOW_UNKNOWN'}",
            headers=owner_headers,
        )
    return None


def _forbidden_resource_ids(role: str, state: str, ids: dict[str, object]) -> set[str]:
    """Return seeded resource IDs that must be absent from filtered lists."""

    all_resources = {
        str(ids[key])
        for key in (
            "asset_id",
            "canvas_id",
            "workflow_id",
            "cross_asset_id",
            "cross_canvas_id",
            "cross_workflow_id",
            "legacy_asset_id",
            "legacy_canvas_id",
            "legacy_workflow_id",
        )
        if ids.get(key)
    }
    if role in {"admin", "governor"}:
        return set()
    allowed_projects = set()
    if role == "owner":
        allowed_projects.update({str(ids["active_project_id"]), str(ids["legacy_project_id"])})
    elif role == "cross_project_editor":
        allowed_projects.add(str(ids["cross_project_id"]))
        if state == "active_legacy_open":
            allowed_projects.add(str(ids["legacy_project_id"]))
    elif role in {"read_grantee_editor", "readonly_grantee"}:
        allowed_projects.update({str(ids["active_project_id"]), str(ids["legacy_project_id"])})
    elif role == "readonly_no_grant":
        allowed_projects.add(str(ids["legacy_project_id"]))
    project_for_resource = {
        str(ids.get("asset_id")): str(ids["project_id"]),
        str(ids.get("canvas_id")): str(ids["project_id"]),
        str(ids.get("workflow_id")): str(ids["project_id"]),
        str(ids.get("legacy_asset_id")): str(ids["legacy_project_id"]),
        str(ids.get("legacy_canvas_id")): str(ids["legacy_project_id"]),
        str(ids.get("legacy_workflow_id")): str(ids["legacy_project_id"]),
        str(ids.get("cross_asset_id")): str(ids["cross_project_id"]),
        str(ids.get("cross_canvas_id")): str(ids["cross_project_id"]),
        str(ids.get("cross_workflow_id")): str(ids["cross_project_id"]),
    }
    return {
        resource_id
        for resource_id in all_resources
        if project_for_resource.get(resource_id) not in allowed_projects
    }


def _assert_filtered(response, role: str, state: str, ids: dict[str, object]) -> None:
    assert response.status_code == 200, response.text
    payload = json.dumps(response.json(), sort_keys=True)
    for resource_id in _forbidden_resource_ids(role, state, ids):
        assert resource_id not in payload


def _make_cell(name: str, action: str, role: str, state: str, expected: str):
    """Create one named test function from a generated matrix cell."""

    marks = []
    if name in KNOWN_GAPS and action != "workflow_task":
        marks.append(pytest.mark.xfail(strict=True, reason=KNOWN_GAPS[name]))
    if action in NA_ENDPOINTS:
        marks.append(
            pytest.mark.skip(
                reason="Permanently N/A (PM decision): GET /api/canvases requires project_id; there is no unfiltered canvas list endpoint"
            )
        )

    def test_cell(client, monkeypatch):
        """Exercise one C5 permission matrix cell."""

        if action == "workflow_task":
            from tests.contracts.media_double.media_doubles import FakeComfy
            FakeComfy().install(monkeypatch)
            monkeypatch.setenv("GW_COMFYUI_URL", "http://comfy.test")
        ids = seed(client, state, action=action, role=role)
        try:
            before = _readback(client, action, ids) if expected.startswith(("404", "403")) else None
            response = _request(client, action, role, ids)

            if expected == "401":
                assert response.status_code == 401, response.text
            elif expected == "404":
                assert response.status_code == 404, response.text
                _assert_error_envelope(response)
                if action in {"asset_project_list", "canvas_list"} and state != "unknown":
                    unknown_ids = dict(ids)
                    unknown_ids["project_id"] = ids["unknown_project_id"]
                    unknown_response = _request(client, action, role, unknown_ids)
                    assert unknown_response.status_code == 404, unknown_response.text
                    assert unknown_response.json() == response.json()
            elif expected == "403 PROJECT_READ_ONLY":
                assert response.status_code == 403, response.text
                _assert_error_envelope(response)
                assert _error_code(response) == "PROJECT_READ_ONLY"
            elif expected == "allow_filtered":
                _assert_filtered(response, role, state, ids)
            elif expected == "allow":
                # Explicit 2xx success only: 1xx/3xx, 422/4xx and 5xx are
                # never an "allow".
                assert response.status_code in ALLOW_SUCCESS_CODES, response.text
            else:
                raise AssertionError(f"unknown expected value: {expected}")

            if before is not None and before.status_code == 200:
                after = _readback(client, action, ids)
                if after is not None:
                    assert after.status_code == 200, after.text
                    assert after.json() == before.json()
        except AssertionError:
            if action == "workflow_task" and name in KNOWN_GAPS:
                pytest.xfail(reason=KNOWN_GAPS[name])
            raise
        if action == "workflow_task" and name in KNOWN_GAPS:
            pytest.fail("[XPASS(strict)] " + KNOWN_GAPS[name])
    test_cell.__name__ = name
    test_cell.__qualname__ = name
    for mark in marks:
        test_cell = mark(test_cell)
    return test_cell


for _action, _role, _state, _expected in CELLS:
    _cell_name = f"test_cell_{_action}__{_role}__{_state}"
    globals()[_cell_name] = _make_cell(
        _cell_name, _action, _role, _state, _expected
    )


def test_cas_authorization_precedes_version_check(client):
    """C5-AC8: hidden resources return 404 before stale-CAS validation."""

    ids = seed(client, "active", action="project_patch", role="revoked_editor")
    response = client.patch(
        f"/api/asset-registry/projects/{ids['project_id']}",
        headers=headers("revoked_editor"),
        json={"name": "x", "expected_version": 999},
    )
    assert response.status_code == 404, response.text
    _assert_error_envelope(response)


def test_writer_stale_version_conflicts_without_persisting(client):
    """C5-AC8: an authorized stale project write is a 409 conflict."""

    ids = seed(client, "active", action="project_patch", role="owner")
    response = client.patch(
        f"/api/asset-registry/projects/{ids['project_id']}",
        headers=headers("owner"),
        json={"name": "x", "expected_version": 999},
    )
    assert response.status_code == 409, response.text
