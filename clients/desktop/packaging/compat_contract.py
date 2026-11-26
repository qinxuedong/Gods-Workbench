# Copyright 2026 qinxuedong (Gods-Workbench Authors)
# SPDX-License-Identifier: FSL-1.1-Apache-2.0
#
# Licensed under the Functional Source License, Version 1.1, Apache 2.0
# Future License (the "License"); you may not use this file except in
# compliance with the License. You may obtain a copy of the License at
#     https://fsl.software/
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""gw-compat-v1 能力契约的单一规范化定义。

桌面 Rust 探针与构建脚本必须生成完全相同的规范 JSON，再做 SHA-256。
本模块不导入运行时应用，可被构建、测试和文档脚本安全复用。
"""

from __future__ import annotations

import hashlib
import json

CAPABILITY_PROFILE = "gw-compat-v1"
OPENAPI_TITLE = "Gods-Workbench Cleanroom API"
HEALTH_REQUIRED_KEYS = (
    "auth_mode",
    "frozen_contracts",
    "mode",
    "oidc_ready",
    "release_authorized",
    "status",
)
STATUS_REQUIRED_KEYS = (
    "auth_mode",
    "authenticated",
    "login_available",
    "logout_available",
    "oidc_ready",
    "reason",
    "release_authorized",
)
REQUIRED_OPERATIONS = (
    "GET /api/asset-auth/status",
    "GET /api/asset-registry/projects",
    "GET /api/canvases",
    "GET /healthz",
    "PATCH /api/asset-registry/projects/{project_id}",
    "PATCH /api/canvases/{canvas_id}",
    "POST /api/asset-auth/local/login",
    "POST /api/asset-auth/local/setup",
    "POST /api/asset-registry/projects",
    "POST /api/canvases",
)
TRUSTED_BOOTSTRAP_PATH = "/api/asset-auth/local/trusted-bootstrap"


def canonical_payload() -> dict[str, object]:
    """返回只包含能力形状的规范对象；版本号不参与 digest。"""
    return {
        "health": {
            "mode": "cleanroom",
            "required_keys": list(HEALTH_REQUIRED_KEYS),
            "status": "ok",
        },
        "openapi": {
            "operations": list(REQUIRED_OPERATIONS),
            "title": OPENAPI_TITLE,
        },
        "profile": CAPABILITY_PROFILE,
        "projects_unauth": {"code": "UNAUTHORIZED", "status": 401},
        "status": {
            "authenticated": False,
            "required_keys": list(STATUS_REQUIRED_KEYS),
        },
        "version_provenance": True,
    }


def canonical_json() -> str:
    return json.dumps(
        canonical_payload(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def capability_digest() -> str:
    return hashlib.sha256(canonical_json().encode("utf-8")).hexdigest()


if __name__ == "__main__":
    print(capability_digest())
