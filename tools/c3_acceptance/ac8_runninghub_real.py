#!/usr/bin/env python
"""C3-AC8 真实 RunningHub 验收（人工门 G2，cut 2026-10-13 10:38：仅秦先生本人在本机运行；团队一律不碰 key）。

自愿运行的脚本，三道门槛缺一不可，否则以 BLOCKED 退出（退出码 2，并写 BLOCKED JSON）：
1. 环境变量 GW_RUNNINGHUB_API_KEY 已设置（值只留在本机环境，脚本不打印、不写入任何文件）；
2. 命令行显式携带 --i-accept-cost（确认本次真实任务会产生 RunningHub 计费）；
3. 携带 --workflow-id（RunningHub 工作流数字 ID）。

满足门槛后，脚本以进程内方式启动应用（独立临时 GW_DATA_DIR / GW_LOCAL_AUTH_DB /
GW_VIDEO_DATA_DIR，不触碰本机既有数据），全部通过应用公开 API 完成：

1. parse-link 导入该 RunningHub 工作流（真实协议取回，不编造 workflowId）；
2. POST /tasks/execute（target=rh_cloud）发起一条真实任务；
3. GET /tasks/{id} 按 --poll-interval 轮询，直到终态或 --timeout-seconds 超时；
4. 完成后 collect，并再 collect 一次验证幂等（不重复登记）；
5. 校验统一资产：任务 outputs 含 asset_id，注册表资产 task_id=job_id、
   metadata.workflow_id 指向导入的工作流，资产内容可读回。

结果 JSON 写入 --out；key 的值绝不打印、绝不写入结果文件。PASS 退出码 0，FAILED 1，BLOCKED 2。

产物下载主机：服务进程不设默认信任来源。本脚本在未设置 GW_WORKFLOW_OUTPUT_HOSTS 时，
默认把它设为 rh-images.xiaoyaoyou.com（RunningHub 官方产物主机）；已设置则保持原值。
可用 --output-hosts 显式覆盖（逗号分隔主机名，不含协议和路径）。

只重试回收、不再次提交：同时给出 --recollect-job-id 与 --data-dir（上次运行的
gw-ac8 临时目录根，内含 data/、auth/、video/）。此模式仍要求 key 环境变量，
不要求 --i-accept-cost，也不要求 --workflow-id；只调用 GET /tasks/{job} 与
POST /tasks/{job}/collect，然后按任务记录里的 workflow_id 做资产核验。
结果 JSON 的 mode 为 recollect。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import time
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

WORKFLOW_ROOT = "/api/god_workflow"
KEY_ENV = "GW_RUNNINGHUB_API_KEY"
KEY_NOTE = KEY_ENV + "（值不记录在结果里）"
OUTPUT_HOSTS_ENV = "GW_WORKFLOW_OUTPUT_HOSTS"
DEFAULT_OUTPUT_HOST = "rh-images.xiaoyaoyou.com"


def _headers() -> dict[str, str]:
    return {"Authorization": "Bearer ac8-acceptance-local", "X-User-Role": "editor",
            "Origin": "http://testserver", "Content-Type": "application/json"}


def _ok(status: int, allowed: tuple[int, ...]) -> bool:
    """HTTP 状态是否属于允许的成功码。"""
    return status in allowed


def _failure_detail(response) -> dict | None:
    """从错误响应取出 detail 字典；非 JSON、detail 非 dict 时返回 None，不抛异常。"""
    if not getattr(response, "text", ""):
        return None
    try:
        body = response.json()
    except (json.JSONDecodeError, ValueError, TypeError):
        return None
    if not isinstance(body, dict):
        return None
    detail = body.get("detail")
    return detail if isinstance(detail, dict) else None


def _failure_code(response) -> str | None:
    """从错误响应取出 detail.code；非 JSON、detail 为字符串或缺少 code 时返回 None，不抛异常。"""
    detail = _failure_detail(response)
    code = detail.get("code") if detail else None
    return code if isinstance(code, str) else None


def _record_step_failure(step: dict, response) -> None:
    """失败响应记录错误码与供应商数字码；供应商消息文本绝不回显，非 JSON 响应也不崩溃。

    RunningHub 拒绝/鉴权失败时额外在终端打印上游错误码，便于所有者直接定位。
    """
    step["code"] = _failure_code(response)
    provider_code = (_failure_detail(response) or {}).get("provider_code")
    if provider_code:
        step["provider_code"] = provider_code
    if step["code"] in ("RH_SOURCE_REJECTED", "RH_SOURCE_AUTH_FAILED"):
        label = "鉴权失败/用户不存在" if step["code"] == "RH_SOURCE_AUTH_FAILED" else "拒绝"
        print(f"FAILED: RunningHub {label}（{step['code']}，上游错误码 {provider_code or '未知'}）")


def _apply_output_hosts(explicit: str | None) -> None:
    """显式 --output-hosts 覆盖；否则仅在环境变量为空时写入官方产物主机。"""
    if explicit is not None:
        os.environ[OUTPUT_HOSTS_ENV] = explicit
        return
    if not str(os.environ.get(OUTPUT_HOSTS_ENV, "")).strip():
        os.environ[OUTPUT_HOSTS_ENV] = DEFAULT_OUTPUT_HOST


def _arm_runtime(root: Path) -> None:
    """把验收数据根指到 root 下的 data/auth/video，不碰本机既有数据。"""
    os.environ["GW_RUNTIME_MODE"] = "test"
    os.environ["GW_AUTH_MODE"] = "local"
    os.environ["GW_DATA_DIR"] = str(root / "data")
    os.environ["GW_LOCAL_AUTH_DB"] = str(root / "auth" / "auth.sqlite3")
    os.environ["GW_VIDEO_DATA_DIR"] = str(root / "video")
    os.environ["GW_DISABLE_AGENT_INTEGRATION"] = "1"


def _verify_asset(client, result: dict, job_id: str, workflow_id, outputs: list) -> str | None:
    """核验统一资产可回跳。成功返回 None，失败返回 reason。"""
    asset_ids = [item.get("asset_id") for item in outputs if isinstance(item, dict) and item.get("asset_id")]
    if not asset_ids or not all(isinstance(item, dict) and item.get("registered") for item in outputs):
        return "outputs_not_registered"
    asset_id = asset_ids[0]
    result["asset_id"] = asset_id
    registry = client.get(f"/api/asset-registry/assets/{asset_id}", headers=_headers())
    step = {"step": "verify_asset", "http_status": registry.status_code}
    result["steps"].append(step)
    if not _ok(registry.status_code, (200,)):
        return "asset_not_in_registry"
    asset = registry.json().get("asset") or {}
    metadata = asset.get("metadata") or {}
    verify = {"task_id_matches": asset.get("task_id") == job_id,
              "workflow_id_matches": metadata.get("workflow_id") == workflow_id,
              "workflow_version": metadata.get("workflow_version")}
    content = client.get(f"{WORKFLOW_ROOT}/assets/{asset_id}/content", headers=_headers())
    verify["content_http_status"] = content.status_code
    step.update(verify)
    result["asset_verification"] = verify
    if not (verify["task_id_matches"] and verify["workflow_id_matches"] and _ok(content.status_code, (200,))):
        return "asset_traceability_mismatch"
    return None


def _recollect(client, result: dict, finish, job_id: str) -> int:
    """读取已有任务并只调用 collect；不解析链接、不提交新任务。"""
    poll = client.get(f"{WORKFLOW_ROOT}/tasks/{job_id}", headers=_headers())
    step = {"step": "read_task", "http_status": poll.status_code}
    result["steps"].append(step)
    if not _ok(poll.status_code, (200,)):
        result["result"] = "FAILED"
        result["reason"] = f"poll_http_{poll.status_code}"
        return finish(1)
    task = poll.json()
    result["workflow_id"] = task.get("workflow_id")
    collected = client.post(f"{WORKFLOW_ROOT}/tasks/{job_id}/collect", headers=_headers())
    step = {"step": "collect", "http_status": collected.status_code}
    result["steps"].append(step)
    if not _ok(collected.status_code, (200,)):
        _record_step_failure(step, collected)
        result["result"] = "FAILED"
        result["reason"] = "collect_rejected"
        return finish(1)
    collected_body = collected.json()
    if collected_body.get("status") != "completed" or collected_body.get("collection_status") != "completed":
        result["result"] = "FAILED"
        result["reason"] = "task_not_completed"
        result["last_status"] = collected_body.get("status")
        result["last_collection_status"] = collected_body.get("collection_status")
        result["messages"] = collected_body.get("messages")
        return finish(1)
    workflow_id = collected_body.get("workflow_id") or task.get("workflow_id")
    result["workflow_id"] = workflow_id
    reason = _verify_asset(client, result, job_id, workflow_id, collected_body.get("outputs") or [])
    if reason:
        result["result"] = "FAILED"
        result["reason"] = reason
        return finish(1)
    result["result"] = "PASS"
    print(f"PASS: 重新回收任务 {job_id} 完成；统一资产 {result['asset_id']} 已登记且可回跳。")
    return finish(0)


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="C3-AC8 真实 RunningHub 验收（仅限所有者本机自愿运行）")
    parser.add_argument("--out", required=True, help="结果 JSON 输出路径")
    parser.add_argument("--workflow-id", default=None, help="RunningHub 工作流数字 ID（10-25 位）")
    parser.add_argument("--i-accept-cost", action="store_true",
                        help="确认本次真实任务会消耗 RunningHub 付费额度")
    parser.add_argument("--domain", default=None,
                        help="RunningHub 域名；默认读 GW_RUNNINGHUB_DOMAIN，再退回平台默认 www.runninghub.cn")
    parser.add_argument("--timeout-seconds", type=int, default=1800, help="轮询总超时（默认 1800 秒）")
    parser.add_argument("--poll-interval", type=int, default=10, help="轮询间隔秒数（默认 10 秒）")
    parser.add_argument("--output-hosts", default=None,
                        help="覆盖 GW_WORKFLOW_OUTPUT_HOSTS（逗号分隔主机名）；省略时仅在该变量为空才默认 rh-images.xiaoyaoyou.com")
    parser.add_argument("--recollect-job-id", default=None,
                        help="只重试回收该 job_id，不再次 parse-link 或提交任务")
    parser.add_argument("--data-dir", default=None,
                        help="已有 gw-ac8 临时目录根（含 data、auth、video）；与 --recollect-job-id 成对使用")
    args = parser.parse_args()

    recollect = bool(args.recollect_job_id or args.data_dir)
    result: dict = {"acceptance": "C3-AC8", "mode": "recollect" if recollect else "real", "steps": [], "result": "BLOCKED"}

    def finish(code: int) -> int:
        result["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"C3-AC8 验收结果: {result['result']}；证据已写入 {out_path}")
        return code

    # ---- 三道门槛：任何一道不满足都不联网、不启动应用、不花钱 ----
    if not str(os.environ.get(KEY_ENV, "")).strip():
        result["reason"] = "env_key_missing"
        print(f"BLOCKED: 需要先在本机设置环境变量 {KEY_ENV}（只设置到环境，不要写进任何文件）。")
        return finish(2)
    result["key_source"] = KEY_NOTE
    if recollect:
        if not args.recollect_job_id or not str(args.recollect_job_id).strip() or not args.data_dir:
            result["reason"] = "recollect_args_required"
            print("BLOCKED: 只回收模式需要同时提供 --recollect-job-id 与 --data-dir（已有 gw-ac8 临时目录根）。")
            return finish(2)
        data_root = Path(args.data_dir)
        if not data_root.is_dir():
            result["reason"] = "data_dir_missing"
            print("BLOCKED: --data-dir 不是已有目录；请使用上次验收的 gw-ac8 临时目录根。")
            return finish(2)
        job_id = str(args.recollect_job_id).strip()
        result["job_id"] = job_id
        result["data_dir"] = str(data_root)
    else:
        if not args.i_accept_cost:
            result["reason"] = "cost_not_accepted"
            print("BLOCKED: 真实任务会产生 RunningHub 计费；确认预算后请加 --i-accept-cost 重新运行。")
            return finish(2)
        if not args.workflow_id or not re.fullmatch(r"\d{10,25}", str(args.workflow_id).strip()):
            result["reason"] = "workflow_id_required"
            print("BLOCKED: 需要 --workflow-id（RunningHub 工作流数字 ID，10-25 位）。")
            return finish(2)
        rh_workflow_id = str(args.workflow_id).strip()
        result["rh_workflow_id"] = rh_workflow_id
        data_root = Path(tempfile.mkdtemp(prefix="gw-ac8-"))
        result["data_dir"] = str(data_root)

    _apply_output_hosts(args.output_hosts)
    _arm_runtime(data_root)
    domain = (args.domain or os.environ.get("GW_RUNNINGHUB_DOMAIN") or "www.runninghub.cn").strip().strip("/")

    from fastapi.testclient import TestClient

    from gw.api.app import create_app

    try:
        with TestClient(create_app()) as client:
            if recollect:
                return _recollect(client, result, finish, job_id)
            # 1. 导入真实 RunningHub 工作流（真实协议取回；导入失败即如实 FAILED）。
            imported = client.post(f"{WORKFLOW_ROOT}/parse-link", headers=_headers(),
                                   json={"url_or_text": f"https://{domain}/workflow/{rh_workflow_id}",
                                         "name": f"C3-AC8-RH-{rh_workflow_id}"})
            step = {"step": "import_workflow", "http_status": imported.status_code}
            result["steps"].append(step)
            if not _ok(imported.status_code, (200, 201)):
                _record_step_failure(step, imported)
                result["result"] = "FAILED"
                result["reason"] = "import_rejected"
                return finish(1)
            workflow = imported.json()
            workflow_id = workflow["workflow_id"]
            result["workflow_id"] = workflow_id
            result["workflow_version"] = workflow.get("version")

            # 2. 发起一条真实 RH 任务；幂等键只防本进程内重复提交。
            started = client.post(f"{WORKFLOW_ROOT}/tasks/execute", headers={**_headers(), "Idempotency-Key": str(uuid.uuid4())},
                                  json={"workflow_id": workflow_id, "expected_version": workflow.get("version"),
                                        "target": "rh_cloud"})
            step = {"step": "execute_task", "http_status": started.status_code}
            result["steps"].append(step)
            if not _ok(started.status_code, (200, 201, 202)):
                _record_step_failure(step, started)
                result["result"] = "FAILED"
                result["reason"] = "execute_rejected"
                return finish(1)
            started_body = started.json()
            job_id = started_body["job_id"]
            result["job_id"] = job_id
            result["remote_task_id"] = started_body.get("remote_task_id")
            if started_body.get("status") == "outcome_unknown":
                step["status"] = "outcome_unknown"
                result["messages"] = list(started_body.get("messages") or [])

            # 3. 轮询真实任务状态直到终态或超时；超时不是 PASS，也不伪造终态。
            deadline = time.monotonic() + max(args.timeout_seconds, 1)
            task: dict = {}
            while True:
                poll = client.get(f"{WORKFLOW_ROOT}/tasks/{job_id}", headers=_headers())
                if not _ok(poll.status_code, (200,)):
                    result["result"] = "FAILED"
                    result["reason"] = f"poll_http_{poll.status_code}"
                    return finish(1)
                task = poll.json()
                if task.get("status") in ("completed", "failed", "cancelled", "outcome_unknown"):
                    break
                if time.monotonic() >= deadline:
                    result["result"] = "FAILED"
                    result["reason"] = "poll_timeout"
                    result["last_status"] = task.get("status")
                    print(f"FAILED: 轮询超时（{args.timeout_seconds}s）；任务 {job_id} 仍为 {task.get('status')}，未伪造终态。")
                    return finish(1)
                time.sleep(max(args.poll_interval, 1))
            result["steps"].append({"step": "poll_terminal", "status": task.get("status"),
                                    "collection_status": task.get("collection_status")})
            if task.get("status") != "completed" or task.get("collection_status") != "completed":
                result["result"] = "FAILED"
                result["reason"] = "task_not_completed"
                result["last_status"] = task.get("status")
                result["last_collection_status"] = task.get("collection_status")
                result["messages"] = task.get("messages")
                if task.get("collection_status") == "partial_failed":
                    print("FAILED: 回收未完成。请用结果 JSON 里的 job_id 与 data_dir 运行 --recollect-job-id，不会再次提交。")
                return finish(1)

            # 4. collect：完成任务后重复回收必须幂等（返回同一 outputs，不重复登记）。
            outputs = task.get("outputs") or []
            collected = client.post(f"{WORKFLOW_ROOT}/tasks/{job_id}/collect", headers=_headers())
            step = {"step": "collect_repeat", "http_status": collected.status_code}
            result["steps"].append(step)
            if not _ok(collected.status_code, (200,)):
                _record_step_failure(step, collected)
                result["result"] = "FAILED"
                result["reason"] = "collect_rejected"
                return finish(1)
            same = [item.get("asset_id") for item in collected.json().get("outputs") or []] == \
                   [item.get("asset_id") for item in outputs]
            step["same_outputs_as_poll"] = same
            if not same:
                result["result"] = "FAILED"
                result["reason"] = "repeat_collect_changed_outputs"
                return finish(1)

            # 5. 统一资产核验：outputs 含 asset_id，注册表可回跳（task_id=job_id、metadata.workflow_id）。
            reason = _verify_asset(client, result, job_id, workflow_id, outputs)
            if reason:
                result["result"] = "FAILED"
                result["reason"] = reason
                return finish(1)

            result["result"] = "PASS"
            print(f"PASS: 真实任务 {job_id} 完成；统一资产 {result['asset_id']} 已登记且可回跳。")
            return finish(0)
    except Exception as error:  # 任何未预期异常都如实记为 FAILED，不吞错、不打印环境变量。
        result["result"] = "FAILED"
        result["reason"] = f"unexpected_{type(error).__name__}"
        print(f"FAILED: 未预期异常 {type(error).__name__}；详情见结果 JSON。")
        return finish(1)


if __name__ == "__main__":
    raise SystemExit(main())
