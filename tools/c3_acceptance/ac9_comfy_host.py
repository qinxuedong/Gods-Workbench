#!/usr/bin/env python
"""C3-AC9 真实 ComfyUI 宿主验收（人工门 G3 已批准：http://127.0.0.1:8188/）。

自愿运行的本机脚本：只有当 GW_COMFYUI_URL（默认 http://127.0.0.1:8188）真实可达
且已设置 GW_COMFYUI_ROOT_DIR 时才执行。脚本以进程内方式启动应用（独立临时
GW_DATA_DIR / GW_LOCAL_AUTH_DB / GW_VIDEO_DATA_DIR，不触碰本机既有数据），然后：

1. 保存一条 LoadImage → SaveImage 工作流；
2. POST /open-in-comfy → 校验宿主文件写入、页面适配与待载入指令；
3. 校验工作流确实到达宿主（优先 ComfyUI /userdata HTTP API，其次宿主目录磁盘文件）；
4. comfy-bridge/sync-saved 携带正确 expected_version → 200 且版本 +1；
5. 再次回存携带过期 expected_version → 409 且文档不变。

结果写入 --out 指定的 JSON 文件。宿主不可达或 GW_COMFYUI_ROOT_DIR 未设置时
以 BLOCKED 退出（退出码 2）。环境变量值一律不打印、不写入结果文件。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import quote

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

DEFAULT_COMFY_URL = "http://127.0.0.1:8188"
WORKFLOW_ROOT = "/api/god_workflow"
CANVAS_DOCUMENT = {
    "nodes": [
        {
            "id": "1",
            "kind": "LoadImage",
            "title": "Load",
            "inputs": {"image": "example.png"},
        },
        {
            "id": "2",
            "kind": "SaveImage",
            "title": "Save",
            "inputs": {"images": ["1", 0]},
        },
    ],
    "connections": [],
}


def _host_reachable(base_url: str) -> bool:
    """只探测宿主 /system_stats；任何异常都按不可达处理。"""
    import httpx

    try:
        with httpx.Client(
            timeout=3.0, follow_redirects=False, trust_env=False
        ) as client:
            response = client.get(base_url.rstrip("/") + "/system_stats")
        return response.status_code == 200 and isinstance(
            response.json().get("system"), dict
        )
    except Exception:
        return False


def _userdata_names(base_url: str) -> list[str] | None:
    """列出宿主 workflows 目录；宿主不支持该 API 时返回 None（回退磁盘校验）。"""
    import httpx

    try:
        with httpx.Client(
            timeout=5.0, follow_redirects=False, trust_env=False
        ) as client:
            response = client.get(
                base_url.rstrip("/") + "/userdata",
                params={"dir": "workflows", "recurse": "true"},
            )
        if response.status_code != 200:
            return None
        entries = response.json()
    except Exception:
        return None
    names: list[str] = []
    if isinstance(entries, list):
        for entry in entries:
            if isinstance(entry, str):
                names.append(entry)
            elif isinstance(entry, dict) and isinstance(entry.get("path"), str):
                names.append(entry["path"])
    return names


def _fetch_userdata_file(base_url: str, name: str) -> bytes | None:
    import httpx

    try:
        with httpx.Client(
            timeout=5.0, follow_redirects=False, trust_env=False
        ) as client:
            response = client.get(base_url.rstrip("/") + "/userdata/" + quote(name))
        return response.content if response.status_code == 200 else None
    except Exception:
        return None


def _headers() -> dict[str, str]:
    return {
        "Authorization": "Bearer ac9-acceptance-local",
        "X-User-Role": "editor",
        "Origin": "http://testserver",
        "Content-Type": "application/json",
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="C3-AC9 真实 ComfyUI 宿主验收（自愿运行）"
    )
    parser.add_argument("--out", required=True, help="结果 JSON 输出路径")
    parser.add_argument(
        "--comfy-url",
        default=None,
        help="宿主地址；默认读 GW_COMFYUI_URL，再退回 " + DEFAULT_COMFY_URL,
    )
    args = parser.parse_args()

    comfy_url = (
        (args.comfy_url or os.environ.get("GW_COMFYUI_URL") or DEFAULT_COMFY_URL)
        .strip()
        .rstrip("/")
    )
    host_root = os.environ.get("GW_COMFYUI_ROOT_DIR", "").strip()

    result: dict = {
        "acceptance": "C3-AC9",
        "comfy_url": comfy_url,
        "steps": [],
        "result": "BLOCKED",
    }

    def finish(code: int) -> int:
        result["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"C3-AC9 验收结果: {result['result']}；证据已写入 {out_path}")
        return code

    if not host_root:
        result["reason"] = "GW_COMFYUI_ROOT_DIR 未设置"
        print(
            "BLOCKED: 需要设置 GW_COMFYUI_ROOT_DIR（本机 ComfyUI 安装根目录）后再运行。"
        )
        return finish(2)
    if not _host_reachable(comfy_url):
        result["reason"] = "comfy_host_unreachable"
        print(
            "BLOCKED: ComfyUI 宿主不可达；请先启动本机服务（右上角 CF 控件或手动启动）。"
        )
        return finish(2)
    result["steps"].append({"step": "host_reachable", "ok": True})

    tmp = Path(tempfile.mkdtemp(prefix="gw-ac9-"))
    os.environ["GW_RUNTIME_MODE"] = "test"
    os.environ["GW_AUTH_MODE"] = "local"
    os.environ["GW_DATA_DIR"] = str(tmp / "data")
    os.environ["GW_LOCAL_AUTH_DB"] = str(tmp / "auth" / "auth.sqlite3")
    os.environ["GW_VIDEO_DATA_DIR"] = str(tmp / "video")
    os.environ["GW_COMFYUI_URL"] = comfy_url
    os.environ["GW_DISABLE_AGENT_INTEGRATION"] = "1"

    from fastapi.testclient import TestClient

    from gw.api.app import create_app

    try:
        with TestClient(create_app()) as client:
            # 1. 保存工作流文档。
            saved = client.post(
                f"{WORKFLOW_ROOT}/documents?source=canvas",
                json=CANVAS_DOCUMENT,
                headers=_headers(),
            )
            if saved.status_code != 201:
                result["result"] = "FAILED"
                result["reason"] = f"document_save_http_{saved.status_code}"
                return finish(1)
            workflow = saved.json()["workflow"]
            workflow_id = workflow["workflow_id"]
            result["workflow_id"] = workflow_id

            # 2. open-in-comfy：写宿主文件、装页面适配、排队载入指令。
            opened = client.post(
                f"{WORKFLOW_ROOT}/open-in-comfy/{workflow_id}", headers=_headers()
            )
            step: dict = {"step": "open_in_comfy", "http_status": opened.status_code}
            result["steps"].append(step)
            if opened.status_code != 200 or not opened.json().get("ok"):
                step["code"] = (opened.json().get("detail") or {}).get("code")
                result["result"] = "FAILED"
                result["reason"] = "open_in_comfy_rejected"
                return finish(1)
            body = opened.json()
            step["workflow_file"] = body.get("workflow_file")
            step["workflow_format"] = body.get("workflow_format")
            step["browser_adapter"] = body.get("browser_adapter")
            if not body.get("workflow_file") or not body.get("saved_path"):
                result["result"] = "FAILED"
                result["reason"] = "host_workflow_file_missing"
                return finish(1)
            result["workflow_file"] = body["workflow_file"]
            saved_path = Path(body["saved_path"])
            if saved_path.is_relative_to(Path(host_root).resolve()):
                result["host_relative_path"] = saved_path.relative_to(
                    Path(host_root).resolve()
                ).as_posix()
            result["expected_version"] = body.get("expected_version")

            # 3. 校验工作流确实到达宿主：优先 /userdata HTTP API，其次磁盘文件。
            graph = body.get("workflow_graph")
            verified_via = None
            names = _userdata_names(comfy_url) or []
            if any(name.endswith(body["workflow_file"]) for name in names):
                content = _fetch_userdata_file(
                    comfy_url,
                    next(
                        name for name in names if name.endswith(body["workflow_file"])
                    ),
                )
                try:
                    if content is not None and json.loads(content) == graph:
                        verified_via = "http_userdata"
                except (ValueError, UnicodeDecodeError):
                    verified_via = None
            try:
                if (
                    verified_via is None
                    and saved_path.is_file()
                    and json.loads(saved_path.read_text(encoding="utf-8")) == graph
                ):
                    verified_via = "host_dir_file"
            except (ValueError, UnicodeDecodeError, OSError):
                verified_via = None
            result["steps"].append(
                {
                    "step": "workflow_reached_host",
                    "ok": verified_via is not None,
                    "verified_via": verified_via,
                }
            )
            if verified_via is None:
                result["result"] = "FAILED"
                result["reason"] = "workflow_not_confirmed_on_host"
                return finish(1)

            # 4. 回存（正确 expected_version）→ 200 且版本 +1。
            current = client.get(
                f"{WORKFLOW_ROOT}/item/{workflow_id}", headers=_headers()
            ).json()
            sync_body = {
                "workflow_id": workflow_id,
                "workflow": current,
                "expected_version": body.get("expected_version"),
                "resolve_definitions": True,
                "api_prompt": graph,
                "field_maps": {},
                "virtual_node_ids": [],
            }
            synced = client.post(
                f"{WORKFLOW_ROOT}/comfy-bridge/sync-saved",
                json=sync_body,
                headers=_headers(),
            )
            step = {
                "step": "sync_saved_correct_version",
                "http_status": synced.status_code,
            }
            result["steps"].append(step)
            if synced.status_code != 200:
                step["code"] = (synced.json().get("detail") or {}).get("code")
                result["result"] = "FAILED"
                result["reason"] = "sync_saved_rejected"
                return finish(1)
            version_after = synced.json().get("version")
            step["version_after"] = version_after
            result["version_after"] = version_after
            if version_after != int(body.get("expected_version") or 0) + 1:
                result["result"] = "FAILED"
                result["reason"] = "version_did_not_increment"
                return finish(1)

            # 5. 过期版本回存 → 409，文档保持不变。
            stale = client.post(
                f"{WORKFLOW_ROOT}/comfy-bridge/sync-saved",
                json={**sync_body, "expected_version": body.get("expected_version")},
                headers=_headers(),
            )
            step = {
                "step": "sync_saved_stale_version",
                "http_status": stale.status_code,
            }
            result["steps"].append(step)
            conflict_code = (stale.json().get("detail") or {}).get("code")
            step["code"] = conflict_code
            readback = client.get(
                f"{WORKFLOW_ROOT}/item/{workflow_id}", headers=_headers()
            ).json()
            step["version_after_conflict"] = readback.get("version")
            if (
                stale.status_code != 409
                or conflict_code != "VERSION_CONFLICT"
                or readback.get("version") != version_after
            ):
                result["result"] = "FAILED"
                result["reason"] = "stale_version_not_rejected"
                return finish(1)

            result["result"] = "PASS"
            return finish(0)
    except Exception as error:  # 任何未预期异常都如实记为 FAILED，不吞错。
        result["result"] = "FAILED"
        result["reason"] = f"unexpected_{type(error).__name__}"
        return finish(1)


if __name__ == "__main__":
    raise SystemExit(main())
