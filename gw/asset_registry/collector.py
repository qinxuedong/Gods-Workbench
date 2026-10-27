# -*- coding: utf-8 -*-
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

"""MVP1 网络素材收集器 Core (QIN-436)。

针对不支持的来源（blob:、data: 超限、.m3u8/.mpd 清单 URL、已知 DRM 标记）
给出明确失败，不静默或产生 dirty/inbox 文件。
"""

from __future__ import annotations

import re
import urllib.parse
from pathlib import Path
from typing import Any

from gw.core.errors import CleanroomException

COLLECT_UNSUPPORTED_SOURCE = "COLLECT_UNSUPPORTED_SOURCE"
MAX_DATA_URL_LENGTH = 1_000_000  # 1MB limit for data: URLs

# DRM 标记关键字模式（URL / query / headers / manifests）
_DRM_PATTERNS = (
    r"\bdrm\b",
    r"widevine",
    r"playready",
    r"fairplay",
    r"clearkey",
    r"license_server",
    r"ezdrm\.com",
    r"drm_token",
    r"#ext-x-key",
    r"#ext-x-fairplay",
    r"urn:uuid:edef8ba9",  # Widevine UUID
    r"urn:uuid:9a04f079",  # PlayReady UUID
)
_DRM_REGEX = re.compile("|".join(_DRM_PATTERNS), re.IGNORECASE)


class UnsupportedSourceError(CleanroomException):
    """收集不支持来源异常 (COLLECT_UNSUPPORTED_SOURCE)。"""

    def __init__(self, message: str, code: str = COLLECT_UNSUPPORTED_SOURCE) -> None:
        super().__init__(
            status_code=400,
            code=code,
            message=message,
        )


def validate_source(
    url: str, content: bytes | None = None, headers: dict[str, str] | None = None
) -> None:
    """校验收集来源是否支持。

    - blob: 协议地址
    - data: 超限 URL
    - .m3u8 / .mpd 流媒体清单
    - 已知 DRM 标记

    若不支持，抛出 UnsupportedSourceError，不产生任何 inbox 文件或发起外网请求。
    """
    if not isinstance(url, str) or not url.strip():
        raise UnsupportedSourceError("素材链接不能为空", code=COLLECT_UNSUPPORTED_SOURCE)

    stripped_url = url.strip()
    lowered_url = stripped_url.lower()

    # 1. blob: 协议地址
    if lowered_url.startswith("blob:"):
        raise UnsupportedSourceError(
            "不支持收集 blob: 协议地址，浏览器内部地址无法收集",
            code=COLLECT_UNSUPPORTED_SOURCE,
        )

    # 2. data: URL 超限
    if lowered_url.startswith("data:"):
        if len(stripped_url) > MAX_DATA_URL_LENGTH:
            raise UnsupportedSourceError(
                f"data: URL 长度超过限制（最大 {MAX_DATA_URL_LENGTH} 字符），无法收集",
                code=COLLECT_UNSUPPORTED_SOURCE,
            )

    # 解析 URL 路径
    parsed = urllib.parse.urlparse(stripped_url)
    path = parsed.path.lower()

    # 3. .m3u8 / .mpd 流媒体清单
    if (
        path.endswith(".m3u8")
        or path.endswith(".mpd")
        or ".m3u8" in lowered_url
        or ".mpd" in lowered_url
    ):
        raise UnsupportedSourceError(
            "流媒体清单 URL (.m3u8 / .mpd) 暂不支持收集",
            code=COLLECT_UNSUPPORTED_SOURCE,
        )

    # 4. 已知 DRM 标记 (URL / query)
    if _DRM_REGEX.search(stripped_url):
        raise UnsupportedSourceError(
            "该内容受 DRM 版权保护或包含 DRM 标记，无法收集",
            code=COLLECT_UNSUPPORTED_SOURCE,
        )

    # 5. 校验 content / headers 中是否包含 DRM 标记或 manifest 标记
    if headers:
        header_str = " ".join(f"{k}:{v}" for k, v in headers.items())
        if _DRM_REGEX.search(header_str):
            raise UnsupportedSourceError(
                "响应标头包含 DRM 保护标记，无法收集",
                code=COLLECT_UNSUPPORTED_SOURCE,
            )

    if content:
        try:
            text_sample = content[:10000].decode("utf-8", errors="ignore")
        except Exception:
            text_sample = ""

        if _DRM_REGEX.search(text_sample):
            raise UnsupportedSourceError(
                "内容包含 DRM 版权保护或加密标记，无法收集",
                code=COLLECT_UNSUPPORTED_SOURCE,
            )
        if "#EXTM3U" in text_sample or "<MPD" in text_sample.upper():
            raise UnsupportedSourceError(
                "流媒体清单内容 (.m3u8 / .mpd) 暂不支持收集",
                code=COLLECT_UNSUPPORTED_SOURCE,
            )


def collect_url(url: str, inbox_dir: Path | str | None = None) -> dict[str, Any]:
    """收集 URL 素材（前端入口）。

    在写入 inbox 或进行下一步前，严格先调用 validate_source(url)。
    若为不支持来源，直接抛出 UnsupportedSourceError，保证 AC2（零 inbox 文件）与 AC3。
    """
    validate_source(url)
    return {
        "status": "queued",
        "url": url,
        "message": "收集任务已提交",
    }
