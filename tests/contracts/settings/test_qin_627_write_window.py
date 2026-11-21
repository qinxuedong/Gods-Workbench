# Copyright 2026 Gods-Workbench Authors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""QIN-627 写窗口五层拦截契约测试。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from gw.core.write_window import (
    STATE_FROZEN,
    STATE_OPEN,
    STATE_QUIESCING,
    STATE_READ_ONLY,
    STATE_SWITCHING,
    WriteWindowClosed,
    WriteWindowStateError,
    get_write_window,
    install_write_window_middleware,
    open_sqlite,
    reset_write_window,
)
from gw.media import repository as media_repository


@pytest.fixture(autouse=True)
def _fresh_window():
    reset_write_window()
    yield
    reset_write_window()


def _window():
    return get_write_window()


def _build_app(tmp_path: Path) -> FastAPI:
    app = FastAPI()
    marker = tmp_path / "http-write.txt"

    @app.get("/read")
    def read():
        return {"ok": True}

    @app.post("/write")
    def write():
        marker.write_text("written", encoding="utf-8")
        return {"ok": True}

    install_write_window_middleware(app)
    return app


def test_state_machine_rejects_illegal_transitions():
    window = _window()
    token = window.begin_quiescing(reason="test")
    assert token
    assert window.state == STATE_QUIESCING
    window.mark_frozen()
    assert window.state == STATE_FROZEN
    with pytest.raises(WriteWindowStateError) as exc:
        window.mark_frozen()
    assert exc.value.code == "WRITE_WINDOW_STATE_INVALID"
    window.mark_switching()
    assert window.state == STATE_SWITCHING
    window.mark_read_only(reason="done")
    assert window.state == STATE_READ_ONLY
    with pytest.raises(WriteWindowStateError):
        window.begin_quiescing(reason="again")
    window.reset_open(reason="test-reset")
    assert window.state == STATE_OPEN


def test_guard_write_codes_per_state():
    window = _window()
    window.begin_quiescing(reason="test")
    with pytest.raises(WriteWindowClosed) as exc:
        window.guard_write()
    assert exc.value.code == "DATA_LOCATION_QUIESCING"
    assert exc.value.retry_after == 2
    window.mark_frozen()
    window.mark_switching()
    window.mark_read_only()
    with pytest.raises(WriteWindowClosed) as exc:
        window.guard_write()
    assert exc.value.code == "DATA_LOCATION_RESTART_REQUIRED"


def test_l1_middleware_blocks_writes_and_keeps_get(tmp_path):
    app = _build_app(tmp_path)
    client = TestClient(app)
    assert client.get("/read").status_code == 200

    window = _window()
    window.begin_quiescing(reason="test")
    response = client.post("/write")
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "DATA_LOCATION_QUIESCING"
    assert response.headers.get("retry-after") == "2"
    assert client.get("/read").status_code == 200
    assert not (tmp_path / "http-write.txt").exists()

    window.mark_frozen()
    window.mark_switching()
    window.mark_read_only()
    response = client.post("/write")
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "DATA_LOCATION_RESTART_REQUIRED"
    assert client.get("/read").status_code == 200
    assert not (tmp_path / "http-write.txt").exists()

    window.reset_open(reason="filled")
    response = client.post("/write")
    assert response.status_code == 200
    assert (tmp_path / "http-write.txt").read_text(encoding="utf-8") == "written"


def test_l1_whitelist_allows_cancel_and_rollback_only(tmp_path):
    from gw.core.write_window import decide_write

    assert decide_write("POST", "/api/data-location/migrations/m1/cancel", STATE_QUIESCING) is None
    assert decide_write("POST", "/api/data-location/migrations/m1/cancel", STATE_READ_ONLY) is not None
    assert decide_write("POST", "/api/data-location/rollback", STATE_READ_ONLY) is None
    assert decide_write("POST", "/api/data-location/rollback", STATE_FROZEN) is not None
    assert decide_write("POST", "/api/data-location/migrations", STATE_OPEN) is None
    assert decide_write("POST", "/api/data-location/migrations", STATE_QUIESCING) is not None
    assert decide_write("GET", "/api/data-location", STATE_READ_ONLY) is None


def test_l2_storage_save_is_gated(monkeypatch):
    from gw.core import storage

    window = _window()
    window.begin_quiescing(reason="test")
    with pytest.raises(WriteWindowClosed) as exc:
        storage.save("qin627_probe", {"any": True})
    assert exc.value.code == "DATA_LOCATION_QUIESCING"
    assert not storage.file_for("qin627_probe").exists()


def test_l3_sqlite_connections_become_read_only(tmp_path):
    database = tmp_path / "ro.sqlite3"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE t (value TEXT)")
    connection.execute("INSERT INTO t VALUES ('x')")
    connection.commit()
    connection.close()

    window = _window()
    window.begin_quiescing(reason="test")
    window.mark_frozen()
    window.mark_switching()
    window.mark_read_only()
    readonly = open_sqlite(database, timeout=10)
    try:
        assert readonly.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 1
        with pytest.raises(sqlite3.OperationalError):
            readonly.execute("INSERT INTO t VALUES ('y')")
    finally:
        readonly.close()

    window.reset_open(reason="test")
    writer = open_sqlite(database, timeout=10)
    try:
        writer.execute("INSERT INTO t VALUES ('y')")
        writer.commit()
    finally:
        writer.close()


def test_l4_cache_write_is_skipped_in_read_only(tmp_path):
    window = _window()
    window.begin_quiescing(reason="test")
    window.mark_frozen()
    window.mark_switching()
    window.mark_read_only()
    target = tmp_path / "thumb.jpg.png"
    media_repository._atomic_write_bytes(target, b"binary-cache", durable=False)
    assert not target.exists()
    window.reset_open(reason="test")
    media_repository._atomic_write_bytes(target, b"binary-cache", durable=False)
    assert target.read_bytes() == b"binary-cache"


def test_l5_host_registry_counts_and_quiesce():
    window = _window()
    state = {"active": 1}
    quiesced = {"called": False}

    def quiesce():
        quiesced["called"] = True

    window.register_host("fake-host", lambda: state["active"], quiesce=quiesce)
    assert window.active_jobs() == {"fake-host": 1}
    assert window.total_active() == 1
    token = window.begin_quiescing(reason="test")
    assert token
    assert quiesced["called"] is True
    state["active"] = 0
    assert window.total_active() == 0
    window.reset_open(reason="test")
    window.unregister_host("fake-host")

    failing = {"boom": True}

    def bad_quiesce():
        if failing["boom"]:
            raise RuntimeError("cannot stop")

    window.register_host("bad-host", lambda: 0, quiesce=bad_quiesce)
    with pytest.raises(WriteWindowStateError) as exc:
        window.begin_quiescing(reason="test")
    assert exc.value.code == "HOST_QUIESCE_FAILED"
    assert window.state == STATE_OPEN
    window.unregister_host("bad-host")


def test_request_write_drain_waits_for_inflight():
    import threading
    import time

    window = _window()
    finished = threading.Event()

    def writer():
        with window.request_write():
            time.sleep(0.3)
        finished.set()

    thread = threading.Thread(target=writer, daemon=True)
    thread.start()
    time.sleep(0.05)
    assert window.begin_quiescing(reason="test")
    assert window.wait_drained(timeout=2.0) is True
    assert finished.is_set()
    window.reset_open(reason="test")
