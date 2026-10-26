import os
import sys
from collections.abc import Generator
from pathlib import Path
from unittest import mock

import pytest

from gw.core.errors import CleanroomException
from gw.core.runtime_paths import RuntimePaths
from gw.settings.credentials import protect, reveal

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="Testing non-Windows Fernet path only")


@pytest.fixture
def mock_runtime_paths(tmp_path: Path) -> Generator[None, None, None]:
    auth_db = tmp_path / "auth.sqlite3"
    paths = RuntimePaths(
        mode="test",
        repo_root=tmp_path,
        runtime_root=tmp_path,
        data_root=tmp_path,
        auth_db=auth_db,
        video_root=tmp_path,
    )
    with mock.patch("gw.settings.credentials.resolve_runtime_paths", return_value=paths):
        yield


def test_credentials_roundtrip_happy_path(mock_runtime_paths: None) -> None:
    encrypted = protect("provider1", "key1", "super_secret")
    assert encrypted.startswith("fernet:")

    decrypted = reveal("provider1", "key1", encrypted)
    assert decrypted == "super_secret"


def test_credentials_wrong_provider_or_field(mock_runtime_paths: None) -> None:
    encrypted = protect("provider1", "key1", "super_secret")

    with pytest.raises(CleanroomException) as excinfo:
        reveal("provider2", "key1", encrypted)
    assert excinfo.value.code == "PROVIDER_CREDENTIAL_UNAVAILABLE"

    with pytest.raises(CleanroomException) as excinfo:
        reveal("provider1", "key2", encrypted)
    assert excinfo.value.code == "PROVIDER_CREDENTIAL_UNAVAILABLE"


def test_credentials_corrupted_data(mock_runtime_paths: None) -> None:
    with pytest.raises(CleanroomException) as excinfo:
        reveal("provider1", "key1", "fernet:invalid_base64_or_data")
    assert excinfo.value.code == "PROVIDER_CREDENTIAL_UNAVAILABLE"

    with pytest.raises(CleanroomException) as excinfo:
        reveal("provider1", "key1", "invalid:prefix")
    assert excinfo.value.code == "PROVIDER_CREDENTIAL_UNAVAILABLE"


def test_credentials_missing_key(mock_runtime_paths: None, tmp_path: Path) -> None:
    encrypted = protect("provider1", "key1", "super_secret")

    key_file = tmp_path / "provider-vault.key"
    assert key_file.exists()
    key_file.unlink()

    with pytest.raises(CleanroomException) as excinfo:
        reveal("provider1", "key1", encrypted)
    assert excinfo.value.code == "PROVIDER_CREDENTIAL_UNAVAILABLE"
