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

import pytest

from gw.asset_registry.collector import (
    CollectError,
    validate_collect_url,
    validate_public_addresses,
)
from gw.asset_registry import collector


@pytest.mark.parametrize("url", ["file:///a", "http://user:pass@example.com/a", "https://example.com:bad/a"])
def test_invalid_collect_urls_fail_closed(url):
    with pytest.raises(CollectError) as error:
        validate_collect_url(url)
    assert error.value.code == "unsupported_url"


def test_blob_url_reports_specific_error():
    with pytest.raises(CollectError) as error:
        validate_collect_url("blob:https://example.com/id")
    assert error.value.code == "blob_url"


def test_valid_url_and_public_addresses():
    assert validate_collect_url(" https://example.com/a.mp4 ") == "https://example.com/a.mp4"
    validate_public_addresses(["8.8.8.8"])


@pytest.mark.parametrize("addresses", [[], ["127.0.0.1"], ["8.8.8.8", "10.0.0.1"], ["::ffff:127.0.0.1"]])
def test_non_public_dns_result_is_rejected(addresses):
    with pytest.raises(CollectError):
        validate_public_addresses(addresses)


def test_redirect_to_private_address_is_rejected_before_connection(monkeypatch):
    resolved = []
    connected = []

    def resolve(host, port, **kwargs):
        resolved.append(host)
        address = "8.8.8.8" if host == "example.com" else "127.0.0.1"
        return [(2, 1, 6, "", (address, port))]

    class Response:
        status = 302

        def getheader(self, name, default=None):
            return "http://internal.invalid/secret" if name == "Location" else default

    class Connection:
        def __init__(self, host, port, address):
            connected.append(address)

        def request(self, *args, **kwargs):
            pass

        def getresponse(self):
            return Response()

        def close(self):
            pass

    monkeypatch.setattr(collector.socket, "getaddrinfo", resolve)
    monkeypatch.setattr(collector, "_PinnedHTTPConnection", Connection)
    with pytest.raises(CollectError):
        collector.download_direct("http://example.com/image.png")
    assert resolved == ["example.com", "internal.invalid"]
    assert connected == ["8.8.8.8"]


def test_content_deduplication_preserves_sources_and_file():
    from gw.asset_registry import repository
    from gw.core import storage

    first = collector.store_content(b"sample media", "first.png", "https://example.com/one", "第一条", owner="owner")
    second = collector.store_content(b"sample media", "second.png", "https://example.com/two", "第二条", owner="owner")
    assert first["duplicate"] is False
    assert second["duplicate"] is True
    assert first["asset"]["asset_id"] == second["asset"]["asset_id"]
    record = repository.state().read()["assets"][first["asset"]["asset_id"]]
    assert [entry["title"] for entry in record["metadata"]["collect_sources"]] == ["第一条", "第二条"]
    directory = storage.data_root() / "registry_uploads" / "collect_inbox" / collector.hashlib.sha256(b"owner").hexdigest()
    assert len(list(directory.iterdir())) == 1
    assert next(directory.iterdir()).read_bytes() == b"sample media"


def test_deduplication_does_not_share_asset_identity_between_owners():
    first = collector.store_content(b"same", "first.png", owner="one")
    second = collector.store_content(b"same", "second.png", owner="two")
    assert first["asset"]["asset_id"] != second["asset"]["asset_id"]
    assert second["duplicate"] is False
    assert first["asset"]["workflow_owner"] == "one"
    assert second["asset"]["workflow_owner"] == "two"
    assert first["asset"]["display_path"] != second["asset"]["display_path"]


def test_failed_publication_leaves_no_partial_file_or_asset(monkeypatch):
    from gw.asset_registry import repository
    from gw.core import storage

    replace = collector.os.replace
    monkeypatch.setattr(collector.os, "replace", lambda *args: (_ for _ in ()).throw(OSError("injected")))
    with pytest.raises(OSError):
        collector.store_content(b"incomplete", "image.png", owner="owner")
    directory = storage.data_root() / "registry_uploads" / "collect_inbox" / collector.hashlib.sha256(b"owner").hexdigest()
    assert not list(directory.iterdir())
    assert not repository.state().read()["assets"]
    monkeypatch.setattr(collector.os, "replace", replace)
    result = collector.store_content(b"incomplete", "image.png", owner="owner")
    assert result["duplicate"] is False
    assert len(list(directory.iterdir())) == 1


def test_collected_video_registers_scene_metadata_and_shot_index(monkeypatch):
    import sqlite3
    from gw.asset_registry import index_jobs, repository
    from gw.core import storage

    segments = [{"start_sec": 0.0, "end_sec": 2.0, "tags": [], "summary": ""}]
    monkeypatch.setattr(index_jobs, "_video_index_data", lambda path: ({"video_segments": segments}, 2.0))
    result = collector.store_content(b"video", "clip.mp4", owner="one")
    asset = result["asset"]
    assert asset["metadata"]["video_segments"] == segments
    assert asset["duration"] == 2.0
    with sqlite3.connect(storage.data_root() / repository.ASSET_SEARCH_INDEX_FILENAME) as db:
        rows = db.execute("SELECT asset_id, start_sec, end_sec FROM mvp1_shots").fetchall()
    assert rows == [(asset["asset_id"], 0.0, 2.0)]


def test_video_collect_extracts_and_indexes_shots_without_external_binaries(monkeypatch):
    import sqlite3
    from gw.asset_registry import repository
    from gw.core import storage
    from gw.media import keyframe_extractor, scene_detector

    scenes = [{
        "start_sec": 0.0,
        "end_sec": 1.0,
        "tags": [],
        "summary": "",
        "width": 64,
        "height": 64,
        "codec": "mpeg4",
        "bit_rate": 50000,
        "container": "mp4",
        "source_url": None,
    }]
    decoded_midpoints = []
    monkeypatch.setattr(scene_detector, "detect_scenes", lambda path: scenes)
    monkeypatch.setattr(
        keyframe_extractor,
        "decode_keyframe_to_memory",
        lambda path, timestamp: decoded_midpoints.append(timestamp) or object(),
    )
    result = collector.store_content(b"synthetic video data", "clip.mp4", owner="owner")
    asset = result["asset"]
    assert asset["kind"] == "video"
    assert asset["metadata"]["video_metadata"]["scene_detection_status"] == "complete"
    assert asset["metadata"]["video_metadata"]["keyframe_status"] == "decoded_in_memory"
    assert asset["metadata"]["video_segments"] == scenes
    assert asset["duration"] == 1.0
    assert decoded_midpoints == [0.5]
    with sqlite3.connect(storage.data_root() / repository.ASSET_SEARCH_INDEX_FILENAME) as db:
        count = db.execute("SELECT count(*) FROM mvp1_shots WHERE asset_id = ?", (asset["asset_id"],)).fetchone()[0]
    assert count == len(asset["metadata"]["video_segments"])


def test_ytdlp_detection_prefers_configured_path(tmp_path, monkeypatch):
    executable = tmp_path / "yt-dlp"
    executable.write_bytes(b"test")
    monkeypatch.setenv("GW_YTDLP_PATH", str(executable))
    monkeypatch.setattr(collector.shutil, "which", lambda _: "ignored")
    assert collector.find_ytdlp() == str(executable)


def test_missing_ytdlp_reports_unavailable(monkeypatch):
    monkeypatch.delenv("GW_YTDLP_PATH", raising=False)
    monkeypatch.setattr(collector.shutil, "which", lambda _: None)
    with pytest.raises(CollectError) as error:
        collector.find_ytdlp()
    assert error.value.code == "ytdlp_unavailable"


def test_ytdlp_success_uses_single_item_and_ignores_user_config(monkeypatch):
    from types import SimpleNamespace
    from pathlib import Path
    monkeypatch.setattr(collector, "find_ytdlp", lambda: "yt-dlp")

    def run(args, **kwargs):
        assert "--ignore-config" in args and "--no-playlist" in args
        assert "--proxy" in args and args[args.index("--downloader") + 1] == "native"
        assert args[args.index("--print") + 1] == "after_move:%(title)j"
        target = args[args.index("--output") + 1].replace("%(ext)s", "mp4")
        Path(target).write_bytes(b"video")
        return SimpleNamespace(returncode=0, stderr=b"", stdout='"页面标题"\n'.encode())

    monkeypatch.setattr(collector.subprocess, "run", run)
    assert collector.download_with_ytdlp("https://example.com/watch") == (b"video", "media.mp4", "页面标题")


def test_ytdlp_proxy_denies_private_connect():
    import socket
    import threading

    with collector._PublicProxy(("127.0.0.1", 0), collector._PublicProxyHandler) as proxy:
        thread = threading.Thread(target=proxy.serve_forever, daemon=True)
        thread.start()
        try:
            with socket.create_connection(proxy.server_address, timeout=2) as connection:
                connection.sendall(b"CONNECT 127.0.0.1:80 HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
                assert b"200" not in connection.recv(256)
        finally:
            proxy.shutdown()
            thread.join()


@pytest.mark.parametrize("mode,code", [("error", "ytdlp_failed"), ("timeout", "ytdlp_timeout"), ("drm", "drm_protected")])
def test_ytdlp_failure_and_timeout_are_structured(monkeypatch, mode, code):
    from types import SimpleNamespace
    monkeypatch.setattr(collector, "find_ytdlp", lambda: "yt-dlp")

    def run(args, **kwargs):
        if mode == "timeout":
            raise collector.subprocess.TimeoutExpired(args, 1)
        return SimpleNamespace(returncode=1, stderr=b"DRM" if mode == "drm" else b"failed")

    monkeypatch.setattr(collector.subprocess, "run", run)
    with pytest.raises(CollectError) as error:
        collector.download_with_ytdlp("https://example.com/watch")
    assert error.value.code == code


def test_local_pair_and_file_collection(monkeypatch):
    import time
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from gw.api.routes_asset_collect import router

    app = FastAPI()
    app.include_router(router)
    from gw.core.errors import CleanroomException
    from fastapi.responses import JSONResponse

    @app.exception_handler(CleanroomException)
    async def error_handler(request, exc):
        return JSONResponse(status_code=exc.status_code, content=exc.to_envelope().model_dump(exclude_none=True))

    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 5000)) as client:
        headers = {"Authorization": "Bearer local-test"}
        cross_site = client.post("/api/asset-collect/pair", headers={**headers, "Origin": "https://example.com"})
        assert cross_site.status_code == 403
        token = client.post("/api/asset-collect/pair", headers=headers).json()["pair_token"]
        denied = client.post("/api/asset-collect/files", headers=headers, data={"pair_token": "bad"}, files=[("files[]", ("a.png", b"abc"))])
        assert denied.status_code == 401
        result = client.post("/api/asset-collect/files", headers=headers, data={"pair_token": token}, files=[("files[]", ("a.png", b"abc"))])
        assert result.status_code == 202
        job_id = result.json()["job_id"]
        assert client.get(f"/api/asset-collect/jobs/{job_id}", headers=headers).status_code == 401
        assert client.get(f"/api/asset-collect/jobs/{job_id}", headers={**headers, "X-Collect-Token": "bad"}).status_code == 401
        for _ in range(100):
            job = client.get(f"/api/asset-collect/jobs/{job_id}", headers={**headers, "X-Collect-Token": token}).json()
            if job["status"] in {"done", "failed"}:
                break
            time.sleep(0.01)
        assert job["status"] == "done"

    with TestClient(app, base_url="http://127.0.0.1", client=("192.0.2.1", 5000)) as remote:
        assert remote.post("/api/asset-collect/pair", headers=headers).status_code == 403


def test_failed_job_preserves_actionable_missing_tool_message():
    from gw.api import routes_asset_collect as routes

    job_id = routes._new_job("owner")

    def fail():
        raise CollectError("ytdlp_unavailable", "未检测到 yt-dlp，请安装后重试")

    routes._work(job_id, "owner", fail)
    assert routes._jobs[job_id]["status"] == "failed"
    assert routes._jobs[job_id]["error_code"] == "ytdlp_unavailable"
    assert routes._jobs[job_id]["message"] == "未检测到 yt-dlp，请安装后重试"


def test_missing_ytdlp_rejects_page_but_allows_direct_url(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from fastapi.responses import JSONResponse
    from gw.api.routes_asset_collect import router
    from gw.core.errors import CleanroomException

    app = FastAPI()
    app.include_router(router)

    @app.exception_handler(CleanroomException)
    async def error_handler(request, exc):
        return JSONResponse(status_code=exc.status_code, content=exc.to_envelope().model_dump(exclude_none=True))

    def no_tool():
        raise CollectError("ytdlp_unavailable", "未检测到 yt-dlp，请安装后重试")

    def download(url):
        if url.endswith("/page"):
            raise CollectError("ytdlp_required", "需要 yt-dlp")
        return b"image", url

    monkeypatch.setattr(collector, "find_ytdlp", no_tool)
    monkeypatch.setattr(collector, "download_direct", download)
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 5000)) as client:
        headers = {"Authorization": "Bearer local-test"}
        token = client.post("/api/asset-collect/pair", headers=headers).json()["pair_token"]
        page = client.post("/api/asset-collect/url", headers=headers, json={"url": "https://example.com/page", "pair_token": token})
        assert page.status_code == 503
        assert page.json()["detail"]["code"] == "ytdlp_unavailable"
        direct = client.post("/api/asset-collect/url", headers=headers, json={"url": "https://example.com/image.png", "pair_token": token})
        assert direct.status_code == 202


def test_ytdlp_page_title_reaches_collector(monkeypatch):
    import time
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse
    from fastapi.testclient import TestClient
    from gw.api.routes_asset_collect import router
    from gw.core.errors import CleanroomException

    app = FastAPI()
    app.include_router(router)

    @app.exception_handler(CleanroomException)
    async def error_handler(request, exc):
        return JSONResponse(status_code=exc.status_code, content=exc.to_envelope().model_dump(exclude_none=True))

    def page_url(url):
        raise CollectError("ytdlp_required", "页面或流媒体需要 yt-dlp")

    monkeypatch.setattr(collector, "find_ytdlp", lambda: "yt-dlp")
    monkeypatch.setattr(collector, "download_direct", page_url)
    monkeypatch.setattr(collector, "download_with_ytdlp", lambda _url: (b"video", "media.mp4", "页面标题"))
    stored = {}

    def store_content(data, name, source, title, *, owner):
        stored.update(data=data, name=name, source=source, title=title, owner=owner)

    monkeypatch.setattr(collector, "store_content", store_content)
    source_url = "https://example.com/watch"
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 5000)) as client:
        headers = {"Authorization": "Bearer local-test"}
        token = client.post("/api/asset-collect/pair", headers=headers).json()["pair_token"]
        response = client.post("/api/asset-collect/url", headers=headers, json={"url": source_url, "pair_token": token})
        assert response.status_code == 202
        job_id = response.json()["job_id"]
        for _ in range(100):
            job = client.get(f"/api/asset-collect/jobs/{job_id}", headers={**headers, "X-Collect-Token": token}).json()
            if job["status"] in {"done", "failed"}:
                break
            time.sleep(0.01)
        assert job["status"] == "done"

    assert stored["data"] == b"video"
    assert stored["name"] == "media.mp4"
    assert stored["source"] == source_url
    assert stored["title"] == "页面标题"
