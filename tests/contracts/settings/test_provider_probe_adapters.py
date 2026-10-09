"""模型目录探测适配与同步结果契约；本机合成上游，运行根由tmp_path隔离。"""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time

from fastapi.testclient import TestClient
import pytest

from gw.api.app import create_app
from gw.core import cli_runtime, platform
from gw.settings import probes

AUTH = {"Authorization":"Bearer probe-fixture", "X-User-Role":"editor"}
PATHS = ("fetch-models", "test-connection", "probe-async")


@contextmanager
def upstream(*, status=200, body=None, raw=None, pages=None, delay=0):
    hits = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            hits.append({"path":self.path,"authorization":self.headers.get("Authorization"),
                         "google_key":self.headers.get("x-goog-api-key")})
            if delay:
                time.sleep(delay)
            value = (pages or {}).get(self.path, body if body is not None else {"data":[{"id":"synthetic-chat"}]})
            data = raw if raw is not None else json.dumps(value).encode()
            self.send_response(status)
            if status == 302:
                self.send_header("Location","/must-not-follow")
            self.send_header("Content-Type","application/json")
            self.send_header("Content-Length",str(len(data)))
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass
    server = ThreadingHTTPServer(("127.0.0.1",0),Handler)
    thread = threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}",hits
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("suffix", ("", "/v1"))
def test_version_segment_is_joined_once(path, suffix):
    with upstream() as (base,hits), TestClient(create_app()) as client:
        response = client.post(f"/api/providers/{path}",headers=AUTH,
            json={"base_url":base+suffix,"protocol":"openai"})
        assert response.status_code == 200, response.text
        assert [hit["path"] for hit in hits] == ["/v1/models"]
        assert response.json()["generation_verified"] is False


def test_native_gemini_uses_header_and_collects_model_pages():
    pages = {
        "/v1beta/models":{"models":[{"name":"models/gemini-fixture-chat"}],"nextPageToken":"next"},
        "/v1beta/models?pageToken=next":{"models":[{"name":"models/imagen-fixture-image"}]},
    }
    with upstream(pages=pages) as (base,hits), TestClient(create_app()) as client:
        result = client.post("/api/providers/fetch-models",headers=AUTH,
            json={"base_url":base+"/v1beta","protocol":"gemini","api_key":"synthetic-gemini"})
        assert result.status_code == 200, result.text
        assert result.json()["all"] == ["gemini-fixture-chat","imagen-fixture-image"]
        assert len(hits) == 2
        assert all(hit["google_key"] == "synthetic-gemini" and hit["authorization"] is None for hit in hits)
        assert "synthetic-gemini" not in result.text
        assert result.json()["generation_verified"] is False


@pytest.mark.parametrize("page_token", ("repeat", "synthetic-pagination-key", "x" * 4097),
                         ids=("repeat", "credential-token", "oversized"))
def test_native_gemini_rejects_unsafe_or_repeated_pagination(page_token):
    page={"models":[{"name":"models/gemini-fixture-chat"}],"nextPageToken":page_token}
    with upstream(body=page) as (base,hits), TestClient(create_app()) as client:
        result=client.post("/api/providers/fetch-models",headers=AUTH,
            json={"base_url":base,"protocol":"gemini","api_key":"synthetic-pagination-key"})
        assert result.status_code==503
        assert result.json()["detail"]["code"]=="PROVIDER_PROBE_FAILED"
        assert "synthetic-pagination-key" not in result.text
        assert "all" not in result.json()
        assert len(hits)==(2 if page_token=="repeat" else 1)


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("case", ("unauthorized","redirect","bad-json","empty"))
def test_http_or_empty_result_never_proves_protocol_success(path, case):
    options = {"unauthorized":{"status":401}, "redirect":{"status":302},
               "bad-json":{"raw":b"not-json"}, "empty":{"body":{"data":[]}}}[case]
    with upstream(**options) as (base,hits), TestClient(create_app()) as client:
        result = client.post(f"/api/providers/{path}",headers=AUTH,json={"base_url":base,"protocol":"openai"})
        if path == "probe-async":
            assert result.status_code == 200, result.text
            assert result.json()["ok"] is False
            assert result.json()["execution_mode"] == "synchronous"
            history = client.get(result.json()["poll_hint"],headers=AUTH)
            assert history.json()["status"] == "failed"
            with TestClient(create_app()) as restarted:
                assert restarted.get(result.json()["poll_hint"],headers=AUTH).json() == history.json()
        else:
            assert result.status_code == 503, result.text
        assert len(hits) == 1


@pytest.mark.parametrize("value", ("http://user:synthetic@127.0.0.1", "http://127.0.0.1?key=synthetic",
                                  "http://127.0.0.1#fragment", "http://127.0.0.1:invalid"))
def test_url_credentials_query_fragment_and_invalid_port_are_rejected(value):
    with TestClient(create_app()) as client:
        response = client.post("/api/providers/fetch-models",headers=AUTH,json={"base_url":value})
        assert response.status_code == 400, response.text
        assert response.json()["detail"]["code"] == "INVALID_URL"
        assert "synthetic" not in response.text


@pytest.mark.parametrize("protocol", ("codex","gemini-cli","jimeng","volcengine"))
def test_non_http_discovery_is_explicit_without_a_request(protocol, monkeypatch):
    monkeypatch.setattr(probes, "_httpx", lambda: pytest.fail("未接入的目录发现不应发HTTP请求"))
    with TestClient(create_app()) as client:
        response = client.post("/api/providers/fetch-models",headers=AUTH,json={"protocol":protocol,"base_url":""})
        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "PROVIDER_MODEL_DISCOVERY_NOT_INTEGRATED"


def test_sync_probe_is_a_completed_history_record_not_queued_work():
    with upstream(body={"data":[{"id":"apimart-name-alone-does-not-prove-tasks"}]}) as (base,hits), TestClient(create_app()) as client:
        response = client.post("/api/providers/probe-async",headers=AUTH,json={"base_url":base,"protocol":"openai"})
        value=response.json()
        assert response.status_code == 200
        assert value["protocol"] == "openai"
        assert value["execution_mode"] == "synchronous"
        assert value["completed"] is True
        assert value["generation_verified"] is False
        assert client.get(value["poll_hint"],headers=AUTH).json()["task"]["execution_mode"] == "synchronous"
        assert len(hits) == 1


def test_loopback_timeout_does_not_return_success_or_retry(monkeypatch):
    monkeypatch.setattr(probes,"PROBE_TIMEOUT_SECONDS",.05)
    with upstream(delay=.3) as (base,hits), TestClient(create_app()) as client:
        result=client.post("/api/providers/test-connection",headers=AUTH,
            json={"base_url":base,"api_key":"synthetic-timeout-key"})
        assert result.status_code == 503
        assert result.json()["detail"]["code"] == "PROVIDER_PROBE_FAILED"
        assert "synthetic-timeout-key" not in result.text
        assert "latency_ms" not in result.text
        assert len(hits) == 1


def test_cli_observation_uses_execution_candidates_but_does_not_run(monkeypatch):
    monkeypatch.setenv("GW_CLI_EXECUTION","1")
    seen = []
    monkeypatch.setattr(platform, "_find_cli_path", lambda candidates: seen.append(tuple(candidates)) or "C:/synthetic-cli.exe")
    monkeypatch.setattr(cli_runtime.subprocess,"run",lambda *a,**k:pytest.fail("观察不应执行CLI"))
    for protocol in ("codex","gemini-cli","jimeng"):
        result=platform.cli_status(protocol)
        assert seen[-1] == tuple(cli_runtime.CLI_CANDIDATES[protocol])
        assert result["execution_enabled"] is True
        assert result["logged_in"] is None
        assert result["generation_ready"] is False
        assert result["generation_status"] == "not_integrated"
