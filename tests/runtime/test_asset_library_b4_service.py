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

from gw.asset_library.b4_service import (
    DATA_STATUS_EMPTY,
    DATA_STATUS_NOT_INTEGRATED,
    empty_response,
    not_integrated_response,
    unavailable,
)
from gw.core.errors import CleanroomException

def test_unavailable_raises_cleanroom_exception():
    with pytest.raises(CleanroomException) as exc_info:
        unavailable("test_endpoint", "test_capability")

    exc = exc_info.value
    assert exc.status_code == 503
    assert exc.code == "ASSET_LIBRARY_NOT_INTEGRATED"
    assert "test_capability" in exc.message
    assert exc.extra == {
        "endpoint": "test_endpoint",
        "unavailable": True,
        "data_status": DATA_STATUS_NOT_INTEGRATED,
    }
    assert exc.expose_extra_fields == {"endpoint", "unavailable", "data_status"}

def test_unavailable_custom_code():
    with pytest.raises(CleanroomException) as exc_info:
        unavailable("test_endpoint", "test_capability", code="CUSTOM_CODE")

    assert exc_info.value.code == "CUSTOM_CODE"

def test_empty_response():
    resp = empty_response("test_endpoint", custom_field="val")

    assert resp["endpoint"] == "test_endpoint"
    assert resp["data_status"] == DATA_STATUS_EMPTY
    assert resp["data_gaps"] == ["asset_library_source_not_connected"]
    assert resp["custom_field"] == "val"

def test_not_integrated_response():
    resp = not_integrated_response("test_endpoint", custom_field="val")

    assert resp["endpoint"] == "test_endpoint"
    assert resp["data_status"] == DATA_STATUS_NOT_INTEGRATED
    assert resp["data_gaps"] == ["asset_library_source_not_connected"]
    assert resp["unavailable"] is True
    assert resp["custom_field"] == "val"
