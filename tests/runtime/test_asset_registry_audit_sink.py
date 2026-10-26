import pytest

from gw.asset_registry import audit_sink
from gw.core import storage
from gw.core.errors import CleanroomException


@pytest.fixture(autouse=True)
def isolated_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "data_root", lambda: tmp_path)


def test_digest_event_stability():
    event1 = {
        "event_id": "evt_0001",
        "schema_version": 1,
        "event_type": "asset.deleted",
        "asset_id": "asset_xyz",
        "occurred_at": "2024-01-01T00:00:00Z",
        "actor_key": "unknown",
    }
    digest1 = audit_sink.digest_event(event1)

    event2 = {
        "event_id": "evt_0001",
        "schema_version": 1,
        "event_type": "asset.deleted",
        "asset_id": "asset_xyz",
        "occurred_at": "2024-01-01T00:00:00Z",
        "actor_key": "unknown",
    }
    digest2 = audit_sink.digest_event(event2)

    assert digest1 == digest2


def test_digest_event_sensitivity():
    event1 = {
        "event_id": "evt_0001",
        "schema_version": 1,
        "event_type": "asset.deleted",
        "asset_id": "asset_xyz",
        "occurred_at": "2024-01-01T00:00:00Z",
        "actor_key": "unknown",
    }
    digest1 = audit_sink.digest_event(event1)

    event2 = event1.copy()
    event2["asset_id"] = "asset_abc"
    digest2 = audit_sink.digest_event(event2)

    assert digest1 != digest2


def test_digest_event_invalid_schema():
    event_invalid = {
        "event_id": "evt_0001",
        "schema_version": 2,  # wrong version
        "event_type": "asset.deleted",
        "asset_id": "asset_xyz",
        "occurred_at": "2024-01-01T00:00:00Z",
        "actor_key": "unknown",
    }
    with pytest.raises(CleanroomException) as excinfo:
        audit_sink.digest_event(event_invalid)
    assert excinfo.value.code == "AUDIT_EVENT_INVALID"


def test_digest_event_invalid_fields():
    event_invalid = {
        "event_id": "evt_0001",
        "schema_version": 1,
        "event_type": "asset.deleted",
        "asset_id": "asset_xyz",
        "occurred_at": "2024-01-01T00:00:00Z",
        "actor_key": "invalid_key",  # invalid key pattern
    }
    with pytest.raises(CleanroomException) as excinfo:
        audit_sink.digest_event(event_invalid)
    assert excinfo.value.code == "AUDIT_EVENT_INVALID"

    event_invalid2 = {
        "event_id": "evt_0001",
        "schema_version": 1,
        "event_type": "asset.unknown",  # unknown type
        "asset_id": "asset_xyz",
        "occurred_at": "2024-01-01T00:00:00Z",
        "actor_key": "unknown",
    }
    with pytest.raises(CleanroomException) as excinfo:
        audit_sink.digest_event(event_invalid2)
    assert excinfo.value.code == "AUDIT_EVENT_INVALID"


class DummyContext:
    def __init__(self, domain, subject):
        self.identity_domain = domain
        self.subject = subject


class InvalidDummyContext:
    def __init__(self):
        pass


def test_actor_key():
    assert audit_sink.actor_key(None) == "unknown"

    ctx = DummyContext("test_domain", "test_subject")
    key1 = audit_sink.actor_key(ctx)
    assert len(key1) == 64

    with pytest.raises(CleanroomException) as excinfo:
        audit_sink.actor_key(InvalidDummyContext())
    assert excinfo.value.code == "IDENTITY_REQUIRED"


def test_append_event():
    raw_data = {}
    ctx = DummyContext("test_domain", "test_subject")

    event_id1 = audit_sink.append_event(
        raw_data, "asset.deleted", "asset_1", context=ctx
    )
    assert event_id1 == "evt_0001"
    assert "outbox" in raw_data
    assert event_id1 in raw_data["outbox"]
    assert raw_data["outbox"][event_id1]["state"] == "pending"

    event_id2 = audit_sink.append_event(
        raw_data, "asset.unarchived", "asset_2", context=None
    )
    assert event_id2 == "evt_0002"
    assert event_id2 in raw_data["outbox"]
    assert raw_data["outbox"][event_id2]["event"]["actor_key"] == "unknown"


def test_deliver_and_overview():
    raw_data = {}
    event_id = audit_sink.append_event(
        raw_data, "asset.deleted", "asset_1", context=None
    )

    event_data = raw_data["outbox"][event_id]["event"]
    digest = raw_data["outbox"][event_id]["digest"]

    # Successful delivery
    receipt = audit_sink.deliver(event_data, digest)
    assert receipt["event_id"] == event_id
    assert receipt["digest"] == digest

    # Idempotent delivery
    receipt2 = audit_sink.deliver(event_data, digest)
    assert receipt == receipt2

    # Conflicting digest
    with pytest.raises(CleanroomException) as excinfo:
        audit_sink.deliver(event_data, "wrong_digest")
    assert excinfo.value.code == "AUDIT_DIGEST_CONFLICT"

    # Overview verification
    overview_data = audit_sink.overview()
    assert overview_data["count"] == 1
    assert len(overview_data["items"]) == 1
    assert overview_data["items"][0]["event_id"] == event_id
    assert overview_data["items"][0]["digest"] == digest
    assert overview_data["items"][0]["event_type"] == "asset.deleted"
    assert overview_data["items"][0]["asset_id"] == "asset_1"


def test_deliver_conflict():
    raw_data = {}
    event_id = audit_sink.append_event(
        raw_data, "asset.deleted", "asset_1", context=None
    )

    event_data = raw_data["outbox"][event_id]["event"]
    digest = raw_data["outbox"][event_id]["digest"]

    audit_sink.deliver(event_data, digest)

    # Different event but same id
    event_data_modified = event_data.copy()
    event_data_modified["asset_id"] = "asset_2"
    digest_modified = audit_sink.digest_event(event_data_modified)

    with pytest.raises(CleanroomException) as excinfo:
        audit_sink.deliver(event_data_modified, digest_modified)
    assert excinfo.value.code == "AUDIT_RECEIPT_CONFLICT"


def test_overview_limit():
    raw_data = {}
    for i in range(55):
        event_id = audit_sink.append_event(
            raw_data, "asset.deleted", f"asset_{i}", context=None
        )
        event_data = raw_data["outbox"][event_id]["event"]
        digest = raw_data["outbox"][event_id]["digest"]
        audit_sink.deliver(event_data, digest)

    overview_data = audit_sink.overview()
    assert overview_data["count"] == 55
    assert len(overview_data["items"]) == 50
