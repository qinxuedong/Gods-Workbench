import uuid
from datetime import UTC, datetime, timedelta

import pytest

from gw.agent_runtime.models import Identifier, LeaseGrant
from gw.agent_runtime.storage.checkpoints import SQLiteCheckpointBackend
from gw.agent_runtime.storage.sqlite import SQLiteDatabase


@pytest.fixture
def database(tmp_path):
    return SQLiteDatabase(tmp_path / "runtime.sqlite3", trusted_root=tmp_path)


@pytest.fixture
def backend(database):
    return SQLiteCheckpointBackend(database)


@pytest.fixture
def run_id(database):
    rid = Identifier(uuid.uuid4().hex)
    with database.connect() as conn:
        conn.execute(
            "INSERT INTO runs(run_id, project_id, version, run_json, state_json) VALUES(?, ?, 0, '', '')",
            (rid.root, "proj"),
        )
    return rid


@pytest.fixture
def active_lease(database, run_id):
    owner_id = Identifier(uuid.uuid4().hex)
    expires_at = datetime.now(UTC) + timedelta(minutes=5)
    with database.connect() as conn:
        conn.execute(
            "INSERT INTO leases(run_id, owner_id, generation, expires_at) VALUES(?, ?, 1, ?)",
            (run_id.root, owner_id.root, expires_at.isoformat()),
        )
    return LeaseGrant(
        run_id=run_id,
        owner_id=owner_id,
        generation=1,
        expires_at=expires_at,
    )


@pytest.mark.anyio
async def test_save_and_load_checkpoint(backend, run_id, active_lease):
    checkpoint = {"key": "value", "nested": {"count": 42}}
    ref = await backend.save(run_id, checkpoint, lease=active_lease)

    loaded = await backend.load(ref)
    assert loaded == checkpoint

    latest = await backend.latest_for_run(run_id)
    assert latest is not None
    assert latest[0].root == ref.root
    assert latest[1] == checkpoint


@pytest.mark.anyio
async def test_overwrite_checkpoint(backend, run_id, active_lease):
    checkpoint1 = {"key": "value1"}
    ref1 = await backend.save(run_id, checkpoint1, lease=active_lease)

    checkpoint2 = {"key": "value2"}
    ref2 = await backend.save(run_id, checkpoint2, lease=active_lease)

    assert ref1.root != ref2.root

    loaded1 = await backend.load(ref1)
    assert loaded1 == checkpoint1

    loaded2 = await backend.load(ref2)
    assert loaded2 == checkpoint2

    latest = await backend.latest_for_run(run_id)
    assert latest is not None
    assert latest[0].root == ref2.root
    assert latest[1] == checkpoint2


@pytest.mark.anyio
async def test_load_nonexistent(backend):
    assert await backend.load(Identifier(uuid.uuid4().hex)) is None
    assert await backend.latest_for_run(Identifier(uuid.uuid4().hex)) is None


@pytest.mark.anyio
async def test_save_rejects_wrong_run(backend, run_id, active_lease):
    other_run = Identifier(uuid.uuid4().hex)
    with pytest.raises(ValueError, match="Checkpoint lease must be for the checkpoint run."):
        await backend.save(other_run, {}, lease=active_lease)


@pytest.mark.anyio
async def test_save_rejects_expired_lease(database, backend, run_id):
    owner_id = Identifier(uuid.uuid4().hex)
    expires_at = datetime.now(UTC) - timedelta(minutes=5)
    with database.connect() as conn:
        conn.execute(
            "INSERT INTO leases(run_id, owner_id, generation, expires_at) VALUES(?, ?, 1, ?)",
            (run_id.root, owner_id.root, expires_at.isoformat()),
        )
    expired_lease = LeaseGrant(
        run_id=run_id,
        owner_id=owner_id,
        generation=1,
        expires_at=expires_at,
    )
    with pytest.raises(ValueError, match="Cannot checkpoint with an expired or fenced lease."):
        await backend.save(run_id, {}, lease=expired_lease)
