"""覆盖矩阵不把2xx、盘点或缺测转换成业务验收结论。"""
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]


def test_coverage_matrix_records_real_routes_but_never_auto_accepts(tmp_path):
    output = tmp_path / "matrix"
    result = subprocess.run([sys.executable, str(ROOT / "tools/phase12_coverage_matrix.py"),
        "--output-dir", str(output), "--", "tests/contracts/assets/test_phase12_index_jobs.py::test_http_index_hash_discovery_scope_and_sync"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=30,
        env={k: v for k, v in os.environ.items() if k != "GW_COVERAGE_OUTPUT"})
    assert result.returncode == 0, result.stderr + result.stdout
    paths = json.loads(result.stdout.strip())
    report = json.loads(Path(paths["report_path"]).read_text(encoding="utf-8"))
    assert report["request_count"] > 0
    assert 0 < report["operation_count_with_2xx"] < report["openapi_operation_count"]
    assert report["operation_count_with_attempts"] < report["openapi_operation_count"]
    assert report["start_snapshot"]["digest"] and report["end_snapshot"]["digest"]
    matched = [row for row in report["operations"] if row["path"] == "/api/asset-registry/workspace-jobs/{job_id}" and row["method"] == "GET"]
    assert len(matched) == 1 and matched[0]["status_counts"].get("200")
    assert matched[0]["status_counts"].get("404")
    assert all(row["business_acceptance"] in {"requires_assertion_and_independent_review", "unexecuted"} for row in report["operations"])
    assert all("request_body" not in row and "response_body" not in row for row in report["operations"])


def test_coverage_output_refuses_repository_path():
    result = subprocess.run([sys.executable, str(ROOT / "tools/phase12_coverage_matrix.py"), "--output-dir", str(ROOT)],
                            cwd=ROOT, capture_output=True, timeout=10)
    assert result.returncode == 2
