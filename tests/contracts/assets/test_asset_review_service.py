import pytest
from datetime import UTC, datetime
from gw.core.errors import CleanroomException
from gw.asset_review.service import (
    validate_version_binding,
    decision_from_payload,
    next_action_for_decision,
    provider_state,
    blocker,
    closure_projection,
    apply_closure_projection,
    unavailable,
    unavailable_detail,
    is_version_bound_payload,
    now_iso
)

@pytest.mark.parametrize("payload, expected", [
    ({"version_id": "v1"}, True),
    ({"project_id": "p1"}, True),
    ({"shot_id": "s1"}, True),
    ({"job_id": "j1"}, True),
    ({"version_id": ""}, False),
    ({"version_id": None}, False),
    ({"asset_id": "a1"}, False),
    ({}, False),
    (None, False),
    ("not-a-mapping", False),
])
def test_is_version_bound_payload(payload, expected):
    assert is_version_bound_payload(payload) == expected

@pytest.mark.parametrize("payload, require_source, expected_version_id, expected_version", [
    ({"asset_id": "a1", "version_id": "v1", "version": "1"}, False, "v1", 1),
    ({"asset_id": "a1", "version_id": "v1", "version": 2, "source": {"provider": "p1"}}, True, "v1", 2),
    ({"asset_id": "a1", "version_id": "v1", "version": 3, "source": {}}, False, "v1", 3),
])
def test_validate_version_binding_success(payload, require_source, expected_version_id, expected_version):
    v_id, v = validate_version_binding(payload, require_source=require_source)
    assert v_id == expected_version_id
    assert v == expected_version

@pytest.mark.parametrize("payload, require_source, expected_error_msg", [
    (None, False, "请求体必须是对象"),
    ([], False, "请求体必须是对象"),
    ({}, False, "C8 审阅必须携带 asset_id 和 version_id"),
    ({"asset_id": "a1"}, False, "C8 审阅必须携带 asset_id 和 version_id"),
    ({"version_id": "v1"}, False, "C8 审阅必须携带 asset_id 和 version_id"),
    ({"asset_id": "a 1", "version_id": "v1"}, False, "asset_id 和 version_id 必须是无空格 ASCII 标识"),
    ({"asset_id": "a1", "version_id": "v 1"}, False, "asset_id 和 version_id 必须是无空格 ASCII 标识"),
    ({"asset_id": "a1", "version_id": "v1"}, False, "version 必须是正整数"),
    ({"asset_id": "a1", "version_id": "v1", "version": "not-int"}, False, "version 必须是正整数"),
    ({"asset_id": "a1", "version_id": "v1", "version": 0}, False, "version 必须是正整数"),
    ({"asset_id": "a1", "version_id": "v1", "version": -1}, False, "version 必须是正整数"),
    ({"asset_id": "a1", "version_id": "v1", "version": 1}, True, "C8 审阅必须携带可核验 source.provider"),
    ({"asset_id": "a1", "version_id": "v1", "version": 1, "source": {}}, True, "C8 审阅必须携带可核验 source.provider"),
    ({"asset_id": "a1", "version_id": "v1", "version": 1, "source": {"provider": ""}}, True, "C8 审阅必须携带可核验 source.provider"),
    ({"asset_id": "a1", "version_id": "v1", "version": 1, "source": "invalid"}, True, "C8 审阅必须携带可核验 source.provider"),
    ({"asset_id": "a1", "version_id": "v1", "version": 1, "source": "invalid"}, False, "source 必须是对象"),
])
def test_validate_version_binding_error(payload, require_source, expected_error_msg):
    with pytest.raises(CleanroomException) as exc:
        validate_version_binding(payload, require_source=require_source)
    assert exc.value.status_code == 400
    assert exc.value.code == "INVALID_REQUEST"
    assert exc.value.message == expected_error_msg

@pytest.mark.parametrize("payload, expected", [
    ({"decision": "approved"}, "approved"),
    ({"status": "approved"}, "approved"),
    ({"decision": "rejected"}, "changes_requested"),
    ({"status": "rejected"}, "changes_requested"),
    ({"decision": "changes_requested"}, "changes_requested"),
    ({"decision": " approved "}, "approved"),
    ({}, ""),
    ({"decision": ""}, ""),
    ({"decision": None, "status": None}, ""),
])
def test_decision_from_payload(payload, expected):
    assert decision_from_payload(payload) == expected

@pytest.mark.parametrize("decision, expected", [
    ("approved", "deliver"),
    ("changes_requested", "regenerate_version"),
    ("rejected", "review"),
    ("anything_else", "review"),
])
def test_next_action_for_decision(decision, expected):
    assert next_action_for_decision(decision) == expected

@pytest.mark.parametrize("value, expected", [
    (None, None),
    ("not-dict", None),
    ({"result_unknown": True}, "unknown"),
    ({"provider_status": "pending"}, "pending"),
    ({"provider_state": "WAITING"}, "waiting"),
    ({"execution_status": "Queued "}, "queued"),
    ({"generation_status": "accepted"}, "accepted"),
    ({"task_status": "running"}, "running"),
    ({"status": "Failed"}, "failed"),
    ({"status": "ERROR"}, "error"),
    ({"status": "unknown"}, "unknown"),
    ({"status": "outcome_unknown"}, "outcome_unknown"),
    ({"status": "approved"}, None),
    ({}, None),
])
def test_provider_state(value, expected):
    assert provider_state(value) == expected

@pytest.mark.parametrize("item, code, scope, message, retryable, observed_at", [
    (
        {
            "review_id": "r1", "delivery_id": "d1", "project_id": "p1", "shot_id": "s1",
            "job_id": "j1", "asset_id": "a1", "version_id": "v1", "version": 2, "other": "ignored"
        },
        "C1", "S1", "M1", True, "2024-01-01T00:00:00Z"
    ),
])
def test_blocker(item, code, scope, message, retryable, observed_at):
    b = blocker(item, code=code, scope=scope, message=message, retryable=retryable, observed_at=observed_at)
    assert b["code"] == code
    assert b["scope"] == scope
    assert b["message"] == message
    assert b["retryable"] == retryable
    assert b["observed_at"] == observed_at
    assert b["subject"] == {
        "review_id": "r1", "delivery_id": "d1", "project_id": "p1", "shot_id": "s1",
        "job_id": "j1", "asset_id": "a1", "version_id": "v1", "version": 2
    }

def test_blocker_default_time(monkeypatch):
    monkeypatch.setattr("gw.asset_review.service.now_iso", lambda: "mock_time")
    b = blocker({}, code="C", scope="S", message="M", retryable=False)
    assert b["observed_at"] == "mock_time"

@pytest.mark.parametrize("item, kind, expected_status, expected_action, expected_blocker_code", [
    # Already closed/blocked
    (
        {"closure_status": "closed", "blocker": None, "recovery": None, "next_action": "deliver"},
        "review", "closed", None, None
    ),
    (
        {"closure_status": "blocked", "blocker": {"code": "B1"}, "recovery": {"id": "r1"}, "next_action": "review"},
        "review", "blocked", "review", "B1"
    ),
    (
        {"closure_status": "closed", "blocker": {"code": "B1"}},
        "review", "closed", None, "B1"
    ),
    (
        {"closure_status": "open", "next_action": "invalid_action"},
        "review", "open", "invalid_action", None
    ),
    # Provider states
    (
        {"provider_status": "pending"},
        "review", "blocked", "wait_for_provider", "PROVIDER_PENDING"
    ),
    (
        {"provider_status": "pending", "job_id": "j1"},
        "review", "blocked", "wait_for_provider", "PROVIDER_PENDING"
    ),
    (
        {"provider_status": "failed"},
        "review", "blocked", "regenerate_version", "PROVIDER_FAILED"
    ),
    (
        {"provider_status": "unknown"},
        "review", "unknown", "reconcile_unknown_job", "PROVIDER_UNKNOWN"
    ),
    # Review statuses
    (
        {"review_status": "approval_invalidated"},
        "review", "blocked", "review", "APPROVAL_INVALIDATED"
    ),
    (
        {"status": "superseded"},
        "delivery", "blocked", "review", "APPROVAL_INVALIDATED"
    ),
    (
        {"review_status": "changes_requested"},
        "review", "blocked", "regenerate_version", "INVALID_STATE_TRANSITION"
    ),
    (
        {"status": "rejected"},
        "review", "blocked", "regenerate_version", "INVALID_STATE_TRANSITION"
    ),
    (
        {"delivery_status": "delivered"},
        "review", "closed", None, None
    ),
    (
        {"status": "delivered"},
        "review", "closed", None, None
    ),
    (
        {"status": "approved"},
        "review", "open", "deliver", None
    ),
    (
        {"review_status": "approved"},
        "review", "open", "deliver", None
    ),
    (
        {"status": "pending_review"},
        "review", "open", "review", None
    ),
])
def test_closure_projection(item, kind, expected_status, expected_action, expected_blocker_code):
    proj = closure_projection(item, kind=kind, observed_at="2024-01-01T00:00:00Z")
    assert proj["closure_status"] == expected_status
    assert proj["next_action"] == expected_action
    if expected_blocker_code:
        assert proj["blocker"]["code"] == expected_blocker_code
    else:
        assert proj["blocker"] is None

def test_apply_closure_projection():
    item = {"status": "approved", "closure_status": "blocked", "blocker": {"code": "B1"}, "recovery": {"id": "1"}}
    res = apply_closure_projection(item)
    assert res["closure_status"] == "open"
    assert res["next_action"] == "deliver"
    assert res["blocker"] is None
    assert res["recovery"] is None

def test_unavailable():
    with pytest.raises(CleanroomException) as exc:
        unavailable("ep1", "cap1", "CODE1")
    assert exc.value.status_code == 503
    assert exc.value.code == "CODE1"
    assert "cap1" in exc.value.message
    assert exc.value.extra["endpoint"] == "ep1"
    assert exc.value.extra["unavailable"] is True
    assert exc.value.extra["data_status"] == "not_integrated"
    assert exc.value.expose_extra_fields == {"endpoint", "unavailable", "data_status"}

def test_unavailable_detail():
    res = unavailable_detail("ep1", "cap1", "CODE1")
    assert res["endpoint"] == "ep1"
    assert res["capability"] == "cap1"
    assert res["code"] == "CODE1"
    assert res["unavailable"] is True
    assert res["data_status"] == "not_integrated"
    assert "asset_review_backend_not_connected" in res["data_gaps"]


def test_now_iso():
    iso = now_iso()
    assert iso.endswith("Z")
    assert "+00:00" not in iso
    assert "T" in iso
