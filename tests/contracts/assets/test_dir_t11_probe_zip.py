"""DIR-T11：媒体代理降级、图片探测分类与 ZIP 身份稳定性契约。"""

from __future__ import annotations

import errno
import hashlib
import io
import subprocess
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gw.api.app import create_app
from gw.asset_registry import repository as asset_registry_repo
from gw.core.errors import CleanroomException
from gw.media import proxy as media_proxy

AUTH = {"Authorization": "Bearer cleanroom-test", "X-User-Role": "editor"}


@pytest.mark.parametrize(
    ("error", "availability", "data_status", "next_action"),
    [
        (
            CleanroomException(503, "PIL_NOT_AVAILABLE", "Pillow 不可用"),
            "not_integrated",
            "not_integrated",
            "install_media_dependency",
        ),
        (
            CleanroomException(503, "MEDIA_NOT_AVAILABLE", "源媒体暂不可用"),
            "unavailable",
            "degraded",
            "restore_source_media",
        ),
    ],
)
def test_proxy_known_degradation_has_structured_retry(error, availability, data_status, next_action, monkeypatch):
    def fail(_payload):
        raise error

    monkeypatch.setattr(media_proxy, "build_proxy", fail)
    with TestClient(create_app()) as client:
        response = client.get("/api/media-proxy", headers=AUTH, params={"asset_id": "ast-dir-t11"})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["availability"] == availability
    assert body["data_status"] == data_status
    assert body["reason_code"] == error.code
    assert body["next_action"] == next_action
    assert body["retryable"] is True
    assert body["data_gaps"] == [error.code]


@pytest.mark.parametrize(
    ("error", "expected_code"),
    [
        (TimeoutError("timeout at C:/private/source.png"), "PROXY_GENERATION_TIMEOUT"),
        (RuntimeError("internal error at C:/private/source.png"), "PROXY_GENERATION_FAILED"),
    ],
)
def test_proxy_unexpected_failures_are_degraded_and_do_not_leak_details(error, expected_code, monkeypatch):
    def fail(_payload):
        raise error

    monkeypatch.setattr(media_proxy, "build_proxy", fail)
    with TestClient(create_app()) as client:
        response = client.get("/api/media-proxy", headers=AUTH, params={"asset_id": "ast-dir-t11"})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["availability"] == "unavailable"
    assert body["data_status"] == "degraded"
    assert body["reason_code"] == expected_code
    assert body["next_action"] == "retry_proxy_generation"
    assert body["retryable"] is True
    assert "C:/private/source.png" not in response.text
    assert "internal error" not in response.text


def test_proxy_content_error_has_standard_envelope_and_retry_metadata(monkeypatch):
    def fail(_payload):
        raise CleanroomException(503, "MEDIA_PROCESS_FAILED", "代理暂时不可读")

    monkeypatch.setattr(media_proxy, "artifact_path", fail)
    with TestClient(create_app()) as client:
        response = client.get(
            "/api/media-proxy/content",
            headers=AUTH,
            params={"artifact_id": "proxy-0123456789abcdef0123456789abcdef.jpg"},
        )

    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail["code"] == "MEDIA_PROCESS_FAILED"
    assert detail["data_status"] == "degraded"
    assert detail["next_action"] == "retry_proxy_generation"
    assert detail["retryable"] is True


def test_proxy_success_is_complete_and_keeps_source_path_private(tmp_path, monkeypatch):
    Image = pytest.importorskip("PIL.Image")
    roots = tmp_path / "roots"
    roots.mkdir()
    source = roots / "ready.png"
    Image.new("RGB", (13, 7), (7, 5, 3)).save(source)
    monkeypatch.setenv("GW_ALLOWED_ROOTS", str(roots))

    with TestClient(create_app()) as client:
        response = client.get("/api/media-proxy", headers=AUTH, params={"path": str(source)})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["availability"] == "ready"
    assert body["data_status"] == "complete"
    assert body["data_gaps"] == []
    assert body["artifact_id"].startswith("proxy-")
    assert body["proxy_url"].startswith("/api/media-proxy/content?")
    assert str(roots) not in response.text


def test_proxy_missing_pillow_dependency_is_not_integrated_and_retryable(tmp_path, monkeypatch):
    Image = pytest.importorskip("PIL.Image")
    roots = tmp_path / "roots"
    roots.mkdir()
    source = roots / "ready.png"
    Image.new("RGB", (5, 5), (0, 0, 0)).save(source)
    monkeypatch.setenv("GW_ALLOWED_ROOTS", str(roots))
    monkeypatch.setattr(media_proxy, "_pil", lambda: None)

    with TestClient(create_app()) as client:
        response = client.get("/api/media-proxy", headers=AUTH, params={"path": str(source)})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["availability"] == "not_integrated"
    assert body["data_status"] == "not_integrated"
    assert body["reason_code"] == "PIL_NOT_AVAILABLE"
    assert body["next_action"] == "install_media_dependency"
    assert body["retryable"] is True
    assert body["data_status"] != "complete"


def test_proxy_corrupt_source_is_degraded_and_never_complete(tmp_path, monkeypatch):
    pytest.importorskip("PIL.Image")
    roots = tmp_path / "roots"
    roots.mkdir()
    source = roots / "corrupt.png"
    source.write_bytes(b"not a readable png")
    monkeypatch.setenv("GW_ALLOWED_ROOTS", str(roots))

    with TestClient(create_app()) as client:
        response = client.get("/api/media-proxy", headers=AUTH, params={"path": str(source)})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["availability"] == "unavailable"
    assert body["data_status"] == "degraded"
    assert body["reason_code"] == "SOURCE_MEDIA_CORRUPT"
    assert body["next_action"] == "replace_source_media"
    assert body["retryable"] is True
    assert str(roots) not in response.text


def test_video_proxy_timeout_has_its_own_degraded_code(tmp_path, monkeypatch):
    roots = tmp_path / "roots"
    roots.mkdir()
    source = roots / "slow.mp4"
    source.write_bytes(b"video source")
    output = tmp_path / "proxy"
    output.mkdir()
    monkeypatch.setenv("GW_ALLOWED_ROOTS", str(roots))
    monkeypatch.setattr(media_proxy, "_output_dir", lambda: output)
    monkeypatch.setattr(media_proxy.shutil, "which", lambda _name: "ffmpeg")

    def timeout(cmd, **kwargs):
        partial = Path(cmd[-1])
        partial.write_bytes(b"partial proxy")
        raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])

    monkeypatch.setattr(media_proxy.subprocess, "run", timeout)
    with TestClient(create_app()) as client:
        response = client.get("/api/media-proxy", headers=AUTH, params={"path": str(source)})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["availability"] == "unavailable"
    assert body["data_status"] == "degraded"
    assert body["reason_code"] == "PROXY_GENERATION_TIMEOUT"
    assert body["data_gaps"] == ["PROXY_GENERATION_TIMEOUT"]
    assert body["next_action"] == "retry_proxy_generation"
    assert body["retryable"] is True
    assert list(output.iterdir()) == []


def test_proxy_permission_error_keeps_403_and_structured_error(monkeypatch, tmp_path):
    Image = pytest.importorskip("PIL.Image")
    roots = tmp_path / "roots"
    roots.mkdir()
    source = roots / "private.png"
    Image.new("RGB", (5, 5), (0, 0, 0)).save(source)
    monkeypatch.setenv("GW_ALLOWED_ROOTS", str(roots))

    def denied(*_args, **_kwargs):
        raise PermissionError("private source path")

    monkeypatch.setattr(Image, "open", denied)
    with TestClient(create_app()) as client:
        response = client.get("/api/media-proxy", headers=AUTH, params={"path": str(source)})

    assert response.status_code == 403
    detail = response.json()["detail"]
    assert detail["code"] == "MEDIA_PERMISSION_DENIED"
    assert detail["message"]
    assert str(roots) not in response.text
    assert "private source path" not in response.text


def test_asset_file_info_reports_valid_corrupt_and_unsupported_images(tmp_path, monkeypatch):
    Image = pytest.importorskip("PIL.Image")
    roots = tmp_path / "roots"
    roots.mkdir()
    valid = roots / "valid.png"
    Image.new("RGB", (23, 11), (3, 5, 7)).save(valid)
    corrupt = roots / "corrupt.png"
    corrupt.write_bytes(b"not a readable png")
    unsupported = roots / "unsupported.avif"
    unsupported.write_bytes(b"unavailable avif payload")
    monkeypatch.setenv("GW_ALLOWED_ROOTS", str(roots))

    with TestClient(create_app()) as client:
        valid_response = client.get("/api/asset-file-info", headers=AUTH, params={"path": str(valid)})
        corrupt_response = client.get("/api/asset-file-info", headers=AUTH, params={"path": str(corrupt)})
        unsupported_response = client.get("/api/asset-file-info", headers=AUTH, params={"path": str(unsupported)})

    valid_body = valid_response.json()
    assert valid_response.status_code == 200
    assert valid_body["data_status"] == "ok"
    assert (valid_body["width"], valid_body["height"]) == (23, 11)

    corrupt_body = corrupt_response.json()
    assert corrupt_response.status_code == 200
    assert corrupt_body["data_status"] == "partial"
    assert corrupt_body["data_gaps"] == ["image_probe_corrupt"]
    assert "width" not in corrupt_body and "height" not in corrupt_body

    unsupported_body = unsupported_response.json()
    assert unsupported_response.status_code == 200
    assert unsupported_body["data_status"] == "partial"
    assert unsupported_body["data_gaps"] == ["image_probe_unsupported_format"]


def test_asset_file_info_classifies_truncated_recognized_image_as_corrupt(tmp_path, monkeypatch):
    Image = pytest.importorskip("PIL.Image")
    roots = tmp_path / "roots"
    roots.mkdir()
    complete = roots / "complete.png"
    Image.new("RGB", (12, 8), (9, 8, 7)).save(complete)
    truncated = roots / "truncated.png"
    truncated.write_bytes(complete.read_bytes()[:-20])
    monkeypatch.setenv("GW_ALLOWED_ROOTS", str(roots))

    with TestClient(create_app()) as client:
        response = client.get(
            "/api/asset-file-info", headers=AUTH, params={"path": str(truncated)}
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["data_status"] == "partial"
    assert body["data_gaps"] == ["image_probe_corrupt"]
    assert "width" not in body and "height" not in body


def test_asset_file_info_classifies_verify_io_error_as_unreadable(tmp_path, monkeypatch):
    Image = pytest.importorskip("PIL.Image")
    roots = tmp_path / "roots"
    roots.mkdir()
    source = roots / "image.png"
    source.write_bytes(b"image placeholder")
    monkeypatch.setenv("GW_ALLOWED_ROOTS", str(roots))

    class VerifyEioImage:
        size = (5, 5)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def verify(self):
            raise OSError(errno.EIO, "simulated image read failure")

    monkeypatch.setattr(Image, "open", lambda *_args, **_kwargs: VerifyEioImage())
    with TestClient(create_app()) as client:
        response = client.get(
            "/api/asset-file-info", headers=AUTH, params={"path": str(source)}
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["data_status"] == "partial"
    assert body["data_gaps"] == ["image_probe_unreadable"]
    assert "width" not in body and "height" not in body


@pytest.mark.parametrize(
    ("error", "expected_gap"),
    [
        (PermissionError("permission denied"), "image_probe_permission_denied"),
        (OSError("unreadable file"), "image_probe_unreadable"),
        (RuntimeError("internal probe failure"), "image_probe_internal_error"),
    ],
)
def test_asset_file_info_classifies_probe_errors_without_exception_text(error, expected_gap, tmp_path, monkeypatch):
    Image = pytest.importorskip("PIL.Image")
    roots = tmp_path / "roots"
    roots.mkdir()
    source = roots / "image.png"
    Image.new("RGB", (5, 5), (0, 0, 0)).save(source)
    monkeypatch.setenv("GW_ALLOWED_ROOTS", str(roots))

    def fail(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(Image, "open", fail)
    with TestClient(create_app()) as client:
        response = client.get("/api/asset-file-info", headers=AUTH, params={"path": str(source)})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["data_status"] == "partial"
    assert body["data_gaps"] == [expected_gap]
    assert "width" not in body and "height" not in body
    assert str(roots) not in response.text
    assert str(error) not in response.text


def test_media_proxy_permissions_keep_standard_403_envelope(tmp_path, monkeypatch):
    roots = tmp_path / "allowed"
    roots.mkdir()
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"private")
    monkeypatch.setenv("GW_ALLOWED_ROOTS", str(roots))

    with TestClient(create_app()) as client:
        response = client.get("/api/media-proxy", headers=AUTH, params={"path": str(outside)})

    assert response.status_code == 403
    assert set(response.json()) == {"detail"}
    assert response.json()["detail"]["code"]
    assert response.json()["detail"]["message"]
    assert str(outside) not in response.text


def test_archive_deduplicates_asset_ids_and_stabilizes_duplicate_names(tmp_path, monkeypatch, client):
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    first_dir.mkdir()
    second_dir.mkdir()
    first = first_dir / "same.txt"
    second = second_dir / "same.txt"
    first.write_bytes(b"first asset")
    second.write_bytes(b"second asset")
    paths = {"asset-a": first, "asset-b": second}
    calls = []

    def media_file(asset_id):
        calls.append(asset_id)
        return paths.get(asset_id)

    monkeypatch.setattr(asset_registry_repo, "media_file", media_file)
    payload = {"asset_ids": ["asset-a", "asset-a", "asset-b"]}
    response = client.post("/api/asset-registry/assets/archive", headers=AUTH, json=payload)

    assert response.status_code == 200, response.text
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = archive.namelist()
        assert len(names) == len(set(names)) == 2
        assert names[0] == "same.txt"
        assert "asset-b" in names[1]
        assert archive.read(names[0]) == b"first asset"
        assert archive.read(names[1]) == b"second asset"
    assert calls == ["asset-a", "asset-b"]

    repeated = client.post("/api/asset-registry/assets/archive", headers=AUTH, json=payload)
    assert repeated.status_code == 200, repeated.text
    with zipfile.ZipFile(io.BytesIO(repeated.content)) as archive:
        assert archive.namelist() == names
    assert hashlib.sha256(response.content).digest() == hashlib.sha256(repeated.content).digest()


def test_archive_avoids_casefold_name_collisions(tmp_path, monkeypatch, client):
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    first_dir.mkdir()
    second_dir.mkdir()
    first = first_dir / "Scene.png"
    second = second_dir / "scene.png"
    first.write_bytes(b"upper-case name")
    second.write_bytes(b"lower-case name")
    paths = {"asset-a": first, "asset-b": second}
    monkeypatch.setattr(asset_registry_repo, "media_file", lambda asset_id: paths[asset_id])

    response = client.post(
        "/api/asset-registry/assets/archive",
        headers=AUTH,
        json={"asset_ids": ["asset-a", "asset-b"]},
    )

    assert response.status_code == 200, response.text
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = archive.namelist()
        assert len(names) == 2
        assert len({name.casefold() for name in names}) == 2
        assert names[0] == "Scene.png"
        assert "asset-b" in names[1]
        assert archive.read(names[0]) == b"upper-case name"
        assert archive.read(names[1]) == b"lower-case name"

    repeated = client.post(
        "/api/asset-registry/assets/archive",
        headers=AUTH,
        json={"asset_ids": ["asset-a", "asset-b"]},
    )
    assert repeated.status_code == 200, repeated.text
    with zipfile.ZipFile(io.BytesIO(repeated.content)) as archive:
        assert archive.namelist() == names


def test_archive_reports_unavailable_asset_id_instead_of_silent_partial_zip(
    tmp_path, monkeypatch, client
):
    source = tmp_path / "known.txt"
    source.write_bytes(b"known file")
    paths = {"asset-known": source, "asset-missing-file": None}
    monkeypatch.setattr(asset_registry_repo, "media_file", lambda asset_id: paths[asset_id])

    response = client.post(
        "/api/asset-registry/assets/archive",
        headers=AUTH,
        json={"asset_ids": ["asset-known", "asset-missing-file"]},
    )

    assert response.status_code == 503
    assert set(response.json()) == {"detail"}
    detail = response.json()["detail"]
    assert detail["code"] == "ARCHIVE_ITEM_UNAVAILABLE"
    assert detail["asset_id"] == "asset-missing-file"
    assert detail["data_status"] == "partial"
    assert "asset-missing-file" in detail["message"]
    assert str(tmp_path) not in response.text


def test_archive_reports_asset_id_when_file_fails_during_zip_read(
    tmp_path, monkeypatch, client
):
    source = tmp_path / "private.txt"
    source.write_bytes(b"private content")
    monkeypatch.setattr(asset_registry_repo, "media_file", lambda _asset_id: source)

    def fail_write(*_args, **_kwargs):
        raise PermissionError(f"cannot read {source}")

    monkeypatch.setattr(asset_registry_repo.zipfile.ZipFile, "write", fail_write)
    response = client.post(
        "/api/asset-registry/assets/archive",
        headers=AUTH,
        json={"asset_ids": ["asset-permission-denied"]},
    )

    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail["code"] == "ARCHIVE_ITEM_UNREADABLE"
    assert detail["asset_id"] == "asset-permission-denied"
    assert detail["data_status"] == "partial"
    assert str(source) not in response.text
    assert "cannot read" not in response.text
