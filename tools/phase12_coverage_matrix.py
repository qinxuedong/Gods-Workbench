"""Phase 12 OpenAPI 覆盖矩阵生成器（洁净室最小实现）。

脚本运行指定的本地测试目标，并在隔离临时目录中对少量真实本地路由进行观测。
报告只保存方法、模板路径和状态码；不保存请求体、响应体、凭据或 Cookie。
业务通过永远需要断言和独立复核，脚本不会自动接受任何业务结果。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
# 直接以脚本路径运行时，Python 默认只把 tools/ 放入 sys.path。
# 显式加入仓库根目录，确保只导入当前新仓 gw/ 包。
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
_HTTP_METHODS = {"get", "post", "put", "patch", "delete", "options", "head"}
_AUTH = {"Authorization": "Bearer index-owner", "X-User-Role": "editor"}


def _operations(app: Any) -> list[tuple[str, str]]:
    spec = app.openapi()
    result: list[tuple[str, str]] = []
    for path, item in spec.get("paths", {}).items():
        for method in item:
            if method.lower() in _HTTP_METHODS:
                result.append((method.upper(), path))
    return sorted(set(result), key=lambda pair: (pair[1], pair[0]))


def _digest(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _record(rows: dict[tuple[str, str], dict[str, Any]], method: str, path: str, status: int | None) -> None:
    if not isinstance(status, int):
        return
    template = path
    # 只有本工具明确知道的实例路径才归一化为 OpenAPI 模板；未知路径不写入报告。
    if path.startswith("/api/asset-registry/workspace-jobs/"):
        template = "/api/asset-registry/workspace-jobs/{job_id}"
    key = (method.upper(), template)
    row = rows.get(key)
    if row is None:
        return
    counts = row.setdefault("status_counts", {})
    code = str(status)
    counts[code] = int(counts.get(code, 0)) + 1


@contextmanager
def _temporary_environment(values: dict[str, str]):
    old = {key: os.environ.get(key) for key in values}
    try:
        os.environ.update(values)
        yield
    finally:
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _observe_local_routes(rows: dict[tuple[str, str], dict[str, Any]]) -> int:
    """在临时数据根中观测认证、健康和索引任务读取路由。"""
    from fastapi.testclient import TestClient
    from gw.api.app import create_app
    from gw.asset_registry import index_jobs as jobs

    request_count = 0
    with tempfile.TemporaryDirectory(prefix="gw-coverage-") as temp:
        base = Path(temp)
        roots = base / "sources"
        roots.mkdir()
        (roots / "coverage.txt").write_text("coverage", encoding="utf-8")
        values = {
            "GW_AUTH_MODE": "local",
            "GW_ALLOWED_ROOTS": str(roots),
            "GW_DATA_DIR": str(base / "data"),
            "GW_CLI_EXECUTION": "0",
        }
        if jobs._default:
            jobs._default.shutdown()
        jobs._default = None
        try:
            with _temporary_environment(values):
                with TestClient(create_app()) as client:
                    response = client.get("/healthz")
                    request_count += 1
                    _record(rows, "GET", "/healthz", response.status_code)
                    response = client.get("/api/asset-auth/status")
                    request_count += 1
                    _record(rows, "GET", "/api/asset-auth/status", response.status_code)
                    created = client.post(
                        "/api/asset-registry/reindex",
                        headers={**_AUTH, "Idempotency-Key": "coverage-matrix"},
                        json={"background": True, "hash_files": True},
                    )
                    request_count += 1
                    _record(rows, "POST", "/api/asset-registry/reindex", created.status_code)
                    if created.status_code == 202:
                        job_id = created.json().get("job_id")
                        deadline = time.monotonic() + 8
                        while isinstance(job_id, str) and time.monotonic() < deadline:
                            current = client.get(f"/api/asset-registry/workspace-jobs/{job_id}", headers=_AUTH)
                            request_count += 1
                            _record(rows, "GET", f"/api/asset-registry/workspace-jobs/{job_id}", current.status_code)
                            if current.status_code != 200:
                                break
                            status = current.json().get("job", {}).get("status")
                            if status in jobs.TERMINAL:
                                break
                            time.sleep(0.02)
                        missing = client.get("/api/asset-registry/workspace-jobs/coverage-missing", headers=_AUTH)
                        request_count += 1
                        _record(rows, "GET", "/api/asset-registry/workspace-jobs/coverage-missing", missing.status_code)
        finally:
            if jobs._default:
                jobs._default.shutdown()
            jobs._default = None
    return request_count


def _run_target(targets: list[str]) -> tuple[int, str, str]:
    if not targets:
        return 0, "", ""
    env = dict(os.environ)
    env.pop("GW_COVERAGE_OUTPUT", None)
    current_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(ROOT) + (os.pathsep + current_pythonpath if current_pythonpath else "")
    command = [sys.executable, "-m", "pytest", "-q", *targets]
    result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", timeout=25)
    return result.returncode, result.stdout, result.stderr


def build_report(output_dir: Path, targets: list[str]) -> tuple[dict[str, Any], int, str, str]:
    from gw.api.app import create_app

    app = create_app()
    operations = _operations(app)
    rows = {
        pair: {
            "method": pair[0],
            "path": pair[1],
            "status_counts": {},
            "business_acceptance": "unexecuted",
        }
        for pair in operations
    }
    start = {"operations": operations, "digest": _digest(operations)}
    target_code, target_stdout, target_stderr = _run_target(targets)
    request_count = _observe_local_routes(rows)
    for row in rows.values():
        if any(200 <= int(status) < 300 for status in row["status_counts"]):
            row["business_acceptance"] = "requires_assertion_and_independent_review"
    operation_rows = list(rows.values())
    end_material = [(row["method"], row["path"], row["status_counts"]) for row in operation_rows]
    end = {"operations": end_material, "digest": _digest(end_material)}
    report = {
        "schema_version": 1,
        "generated_by": "tools/phase12_coverage_matrix.py",
        "target": targets,
        "target_exit_code": target_code,
        "request_count": request_count,
        "openapi_operation_count": len(operation_rows),
        "operation_count_with_2xx": sum(1 for row in operation_rows if any(200 <= int(status) < 300 for status in row["status_counts"])),
        "operation_count_with_attempts": sum(1 for row in operation_rows if row["status_counts"]),
        "start_snapshot": start,
        "end_snapshot": end,
        "operations": operation_rows,
    }
    return report, target_code, target_stdout, target_stderr


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成本地 OpenAPI 覆盖矩阵")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("targets", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    output = Path(args.output_dir).expanduser().resolve()
    try:
        output.relative_to(ROOT)
    except ValueError:
        pass
    else:
        print("输出目录必须位于仓库之外", file=sys.stderr)
        return 2
    targets = list(args.targets)
    if targets and targets[0] == "--":
        targets = targets[1:]
    output.mkdir(parents=True, exist_ok=True)
    report, target_code, target_stdout, target_stderr = build_report(output, targets)
    if target_code != 0:
        (output / "pytest.stdout.txt").write_text(target_stdout, encoding="utf-8")
        (output / "pytest.stderr.txt").write_text(target_stderr, encoding="utf-8")
        return target_code
    report_path = output / "phase12-coverage-matrix.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"report_path": str(report_path), "request_count": report["request_count"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
