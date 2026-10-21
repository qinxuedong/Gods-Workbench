"""C7-T4 (QIN-170) 两条 R1 闭环的浏览器闸门与证据收口。

这个测试只编排已合并的 C7-P3/P4 浏览器用例，不触碰 T1～T3 生产实现：

* 文本链由真实 Chromium 页面完成六阶段回读、刷新恢复、导出和匿名隔离；
* 媒体链由真实 Chromium 页面完成 RunningHub 任务、统一资产回跳、刷新恢复、
  跨账户阻断和 outcome_unknown 失败关闭；
* RunningHub 返回体必须来自仓库保留的真实宿主 fixture，且在证据中保存哈希。

子测试在独立 Python 进程中运行，避免把父 pytest 的内存状态当作跨链证据。
证据目录可由 ``GW_C7_T4_EVIDENCE_DIR`` 指定；未指定时写入 pytest 临时目录。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TEXT_NODE = "tests/e2e/test_c7_text_loop_browser.py::test_c7_p3_text_loop_real_browser_readback_and_export"
MEDIA_NODE = "tests/e2e/test_c7_media_loop_browser.py"
REAL_HOST_FIXTURE = (
    REPO_ROOT
    / "tests"
    / "contracts"
    / "fixtures"
    / "runninghub"
    / "outputs_success_real_host.json"
)
REAL_HOST = "rh-images-tos.xiaoyaoyou.com"


def _evidence_root(tmp_path: Path) -> Path:
    configured = os.environ.get("GW_C7_T4_EVIDENCE_DIR", "").strip()
    target = Path(configured) if configured else tmp_path / "c7-t4-evidence"
    if not target.is_absolute():
        target = REPO_ROOT / target
    target.mkdir(parents=True, exist_ok=True)
    return target


def _child_env(*, evidence: Path, text: bool) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "PYTHONPATH": str(REPO_ROOT),
            "GW_RUNTIME_MODE": "test",
            "GW_AUTH_MODE": "local_account",
            "GW_CLI_EXECUTION": "0",
            "GW_DATA_DIR": str(
                evidence / "runtime" / ("text-data" if text else "media-data")
            ),
            "GW_LOCAL_AUTH_DB": str(
                evidence
                / "runtime"
                / ("text-auth" if text else "media-auth")
                / "auth.sqlite3"
            ),
            "GW_VIDEO_DATA_DIR": str(
                evidence / "runtime" / ("text-video" if text else "media-video")
            ),
        }
    )
    if text:
        env["GW_C7_P3_EVIDENCE_DIR"] = str(evidence / "text")
    else:
        env["GW_QA_SHOT_DIR"] = str(evidence / "media")
    return env


def _run_child(node: str, *, env: Mapping[str, str]) -> str:
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", node, "-q", "-rs"],
        cwd=REPO_ROOT,
        env=dict(env),
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    output = (completed.stdout or "") + (completed.stderr or "")
    if completed.returncode != 0:
        raise AssertionError(
            f"{node} 失败（退出码 {completed.returncode}）：\n{output}"
        )
    if re.search(r"\b\d+ skipped\b", output):
        raise AssertionError(f"{node} 未取得浏览器证据，测试被跳过：\n{output}")
    if not re.search(r"\b\d+ passed\b", output):
        raise AssertionError(f"{node} 没有可核验的通过摘要：\n{output}")
    return output


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_sha256sums(root: Path, paths: list[Path]) -> None:
    lines = [
        f"{_sha256(path)}  {path.relative_to(root).as_posix()}"
        for path in sorted(paths)
    ]
    (root / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _proof_files(root: Path) -> list[Path]:
    excluded_parts = {"runtime", "__pycache__"}
    return [
        path
        for path in root.rglob("*")
        if path.is_file()
        and path.name != "SHA256SUMS"
        and not excluded_parts.intersection(path.relative_to(root).parts)
    ]


def _assert_expected_shots(root: Path, names: list[str]) -> None:
    missing = [name for name in names if not (root / name).is_file()]
    assert not missing, f"浏览器截图证据缺失：{missing}"
    empty = [name for name in names if (root / name).stat().st_size <= 0]
    assert not empty, f"浏览器截图为空：{empty}"


def test_c7_t4_cross_loop_validation_real_browser(tmp_path):
    """C7-AC4：两条闭环必须真实跑过 Chromium，且失败/未知不能被摘要掩盖。"""

    evidence = _evidence_root(tmp_path)
    text_dir = evidence / "text"
    media_dir = evidence / "media"
    text_dir.mkdir(parents=True, exist_ok=True)
    media_dir.mkdir(parents=True, exist_ok=True)

    text_log = _run_child(TEXT_NODE, env=_child_env(evidence=evidence, text=True))
    media_log = _run_child(MEDIA_NODE, env=_child_env(evidence=evidence, text=False))

    _assert_expected_shots(
        text_dir,
        ["c7-p3-agents-text-loop.png", "c7-p3-project-artifact-versions.png"],
    )
    _assert_expected_shots(
        media_dir,
        [
            "1-workflow-browser-completed-task.png",
            "2-workflow-browser-outcome-unknown.png",
        ],
    )

    assert REAL_HOST_FIXTURE.is_file(), (
        f"RunningHub 真实宿主 fixture 不存在：{REAL_HOST_FIXTURE}"
    )
    fixture = json.loads(REAL_HOST_FIXTURE.read_text(encoding="utf-8"))
    urls = [
        item.get("fileUrl", "")
        for item in fixture.get("data", [])
        if isinstance(item, dict)
    ]
    assert urls and all(Path(url).name for url in urls)
    assert all(REAL_HOST in url for url in urls), urls
    fixture_copy = evidence / "c7-t4-runninghub-real-host-fixture.json"
    shutil.copyfile(REAL_HOST_FIXTURE, fixture_copy)

    (text_dir / "test.log").write_text(text_log, encoding="utf-8")
    (media_dir / "test.log").write_text(media_log, encoding="utf-8")
    combined_log = evidence / "test.log"
    combined_log.write_text(
        "[text] "
        + TEXT_NODE
        + "\n"
        + text_log
        + "\n[media] "
        + MEDIA_NODE
        + "\n"
        + media_log,
        encoding="utf-8",
    )

    manifest = {
        "task": "QIN-170",
        "acceptance": "C7-AC4",
        "browser": "Chromium via Playwright",
        "text_loop": {
            "test": TEXT_NODE,
            "passed": True,
            "refresh_asserted": True,
            "anonymous_status": 401,
            "screenshots": [
                "text/c7-p3-agents-text-loop.png",
                "text/c7-p3-project-artifact-versions.png",
            ],
        },
        "media_loop": {
            "test": MEDIA_NODE,
            "passed": True,
            "refresh_asserted": True,
            "cross_tenant_status": "403-or-404",
            "unknown_state_badge": "结果未知 · 待人工核实",
            "screenshots": [
                "media/1-workflow-browser-completed-task.png",
                "media/2-workflow-browser-outcome-unknown.png",
            ],
        },
        "runninghub_fixture": {
            "source": "tests/contracts/fixtures/runninghub/outputs_success_real_host.json",
            "host": REAL_HOST,
            "copied_as": fixture_copy.name,
            "fixture_sha256": _sha256(fixture_copy),
        },
        "process_boundary": "每条子测试在独立 Python 进程中执行；页面内刷新关系由对应 P3/P4 用例断言。",
        "evidence_policy": "宿主响应保留为真实宿主 fixture；浏览器截图和测试日志来自本次 Chromium 执行。",
    }
    manifest_path = evidence / "INDEX-QIN-170.md"
    manifest_path.write_text(
        "# QIN-170 C7-T4 跨链验证证据\n\n"
        "```json\n" + json.dumps(manifest, ensure_ascii=False, indent=2) + "\n```\n",
        encoding="utf-8",
    )
    _write_sha256sums(evidence, _proof_files(evidence))

    # 证据清单本身也必须可回读，避免只凭 pytest 摘要宣称完成。
    assert (
        json.loads(
            manifest_path.read_text(encoding="utf-8")
            .split("```json\n", 1)[1]
            .rsplit("\n```", 1)[0]
        )["acceptance"]
        == "C7-AC4"
    )
    assert (evidence / "SHA256SUMS").read_text(encoding="utf-8").count("  ") >= 7
