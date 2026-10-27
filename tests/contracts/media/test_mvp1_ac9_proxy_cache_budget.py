"""MVP1-AC9：按需缩略图代理缓存 LRU 预算。

用临时目录和很小的 ``GW_PROXY_CACHE_BUDGET_BYTES`` 验证
``enforce_cache_budget``：超预算时按 mtime 淘汰最旧的 ``proxy-*``，
淘汰后占用不超过预算，且非 ``proxy-*`` 文件不被删除。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from gw.media.proxy import PROXY_CACHE_BUDGET_ENV, enforce_cache_budget

_MIB = 1024 * 1024
# 审核探针同量级：4MiB 预算、约 1MiB 的 proxy 文件。
_BUDGET = 4 * _MIB
# 略大于 1MiB，四份合计超过 4MiB；删掉最旧一份后三份仍落在预算内。
_ARTIFACT_BYTES = _MIB + 1


def _write_bytes(path: Path, size: int, mtime: float) -> None:
    path.write_bytes(b"\0" * size)
    os.utime(path, (mtime, mtime))


def test_enforce_cache_budget_evicts_oldest_proxy_and_keeps_others(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(PROXY_CACHE_BUDGET_ENV, str(_BUDGET))
    proxy_dir = tmp_path / "media_output" / "proxy"
    proxy_dir.mkdir(parents=True)

    oldest = "proxy-oldest-artifact.png"
    newer = (
        "proxy-second-artifact.png",
        "proxy-third-artifact.png",
        "proxy-newest-artifact.png",
    )
    base_mtime = 1_700_000_000.0
    _write_bytes(proxy_dir / oldest, _ARTIFACT_BYTES, base_mtime)
    for offset, name in enumerate(newer, start=1):
        _write_bytes(proxy_dir / name, _ARTIFACT_BYTES, base_mtime + offset)

    # 更旧、更大，但不是 proxy-*，必须原样保留。
    untouched = proxy_dir / "source-still.png"
    _write_bytes(untouched, _ARTIFACT_BYTES * 8, base_mtime - 10)
    note = proxy_dir / "readme.txt"
    note.write_text("keep", encoding="utf-8")

    result = enforce_cache_budget(proxy_dir)

    assert result["budget_bytes"] == _BUDGET
    assert result["removed"] == 1
    assert result["bytes"] == 3 * _ARTIFACT_BYTES
    assert result["bytes"] <= _BUDGET
    assert not (proxy_dir / oldest).exists()
    for name in newer:
        assert (proxy_dir / name).is_file()
    assert untouched.read_bytes() == b"\0" * (_ARTIFACT_BYTES * 8)
    assert note.read_text(encoding="utf-8") == "keep"
