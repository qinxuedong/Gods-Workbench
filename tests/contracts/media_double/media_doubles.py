"""C3 媒体链测试替身：只在 tests 里使用，不进入 gw/ 运行时代码。

替身在模块函数这一层替换 RunningHub / ComfyUI 的外部调用，routes、registry、
asset_hub_bridge 全部走真实代码。行为完全确定：不用随机数、不读真实时间，
远端 ID 用计数器生成（fake-rh-task-0001、fake-prompt-0001）。
"""
from __future__ import annotations

import base64
import hashlib
from pathlib import Path
from typing import Any, Optional

from fastapi.testclient import TestClient

import httpx

from gw.core.errors import CleanroomException
from gw.god_workflow import comfy_executor, platforms, routes

# 1×1 RGBA PNG，固定字节，SHA256 写进夹具供断言。
FIXED_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGD4DwABBAEAwS2OUAAAAABJRU5ErkJggg=="
)
FIXED_PNG_SHA256 = hashlib.sha256(FIXED_PNG).hexdigest()
APPROVED_HOST = "approved.example"
TEST_API_KEY = "never-expose-test-key"

HEADERS = {"Authorization": "Bearer c3-media-double", "Origin": "http://testserver"}
GOVERNOR = {**HEADERS, "X-User-Role": "governor"}


class NetworkBlocked(AssertionError):
    pass


MODES = ("success", "fail_remote", "submit_timeout", "no_remote_id", "query_error",
         "unknown_status", "partial_outputs", "upload_fail", "upload_timeout")


class _Recorder:
    prefix = "fake"

    def __init__(self, mode: str = "success") -> None:
        if mode not in MODES:
            raise ValueError(f"unknown fake mode: {mode}")
        self.mode = mode
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._counters: dict[str, int] = {}

    def _next(self, name: str) -> str:
        self._counters[name] = self._counters.get(name, 0) + 1
        return f"{name}-{self._counters[name]:04d}"

    def _record(self, name: str, **kwargs: Any) -> None:
        self.calls.append((name, kwargs))

    def count(self, name: str) -> int:
        return sum(1 for call, _ in self.calls if call == name)

    def names(self) -> list[str]:
        return [call for call, _ in self.calls]


class FakeRunningHub(_Recorder):
    """替换 platforms.upload_rh_input / create_rh_cloud_task / query_rh_task_outputs。"""

    async def upload_rh_input(self, payload: bytes, filename: str, content_type: str, api_key: str,
                              domain: str = platforms.RUNNINGHUB_DEFAULT_DOMAIN) -> str:
        self._record("upload_rh_input", filename=filename, content_type=content_type, size=len(payload),
                     sha256=hashlib.sha256(payload).hexdigest(), domain=domain)
        if self.mode == "upload_timeout":
            raise CleanroomException(504, "RH_INPUT_UPLOAD_TIMEOUT", "RunningHub 输入上传超时")
        if self.mode == "upload_fail":
            raise CleanroomException(502, "RH_INPUT_UPLOAD_FAILED", "RunningHub 输入上传失败")
        return "fake-rh/" + self._next("input") + ".png"

    async def create_rh_cloud_task(self, workflow_id: Optional[str], node_info_list: list[dict[str, Any]],
                                   api_key: str, domain: str = platforms.RUNNINGHUB_DEFAULT_DOMAIN,
                                   *, workflow: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        self._record("create_rh_cloud_task", workflow_id=workflow_id, node_info_list=node_info_list,
                     workflow=workflow, domain=domain)
        if self.mode == "submit_timeout":
            # 与真实 _post_json 在 httpx.TimeoutException 时抛出的异常一致。
            raise CleanroomException(504, "WORKFLOW_PROVIDER_TIMEOUT", "上游请求超时")
        if self.mode == "no_remote_id":
            return {}
        return {"taskId": self._next("fake-rh-task")}

    async def query_rh_task_outputs(self, task_id: str, api_key: str,
                                    domain: str = platforms.RUNNINGHUB_DEFAULT_DOMAIN) -> dict[str, Any]:
        self._record("query_rh_task_outputs", task_id=task_id, domain=domain)
        if self.mode == "query_error":
            raise CleanroomException(502, "RH_TASK_QUERY_REJECTED", "RunningHub 查询被拒绝")
        if self.mode == "unknown_status":
            return {"status": "UNKNOWN", "outputs": [], "raw": {}}
        if self.count("query_rh_task_outputs") == 1:
            return {"status": "RUNNING", "outputs": [], "raw": {}}
        if self.mode == "fail_remote":
            return {"status": "FAILED", "outputs": [], "message": "fake remote failure", "raw": {}}
        outputs = [{"fileUrl": f"https://{APPROVED_HOST}/out-0001.png", "fileName": "out-0001.png"}]
        if self.mode == "partial_outputs" and self.count("query_rh_task_outputs") == 2:
            outputs.append({"fileUrl": "https://not-approved.example/out-0002.png", "fileName": "out-0002.png"})
        return {"status": "SUCCESS", "outputs": outputs, "raw": {}}

    def install(self, monkeypatch) -> None:
        monkeypatch.setattr(platforms, "upload_rh_input", self.upload_rh_input)
        monkeypatch.setattr(platforms, "create_rh_cloud_task", self.create_rh_cloud_task)
        monkeypatch.setattr(platforms, "query_rh_task_outputs", self.query_rh_task_outputs)


class FakeComfy(_Recorder):
    """替换 comfy_executor 的 probe / submit / history / upload_input / interrupt / fetch_output。"""

    async def probe(self) -> dict[str, Any]:
        self._record("probe")
        return {"online": True, "object_info": {}}

    async def submit(self, prompt: dict[str, Any], *, client_id: Optional[str] = None) -> dict[str, Any]:
        self._record("submit", prompt=prompt, client_id=client_id)
        if self.mode == "submit_timeout":
            # 与真实 _request_json 的超时映射一致。
            raise CleanroomException(504, "WORKFLOW_PROVIDER_TIMEOUT", "ComfyUI request timed out")
        if self.mode == "no_remote_id":
            raise CleanroomException(502, "WORKFLOW_PROVIDER_ERROR", "ComfyUI response is missing prompt_id")
        return {"prompt_id": self._next("fake-prompt"), "number": 1, "node_errors": {}}

    async def history(self, prompt_id: str) -> dict[str, Any]:
        self._record("history", prompt_id=prompt_id)
        if self.mode == "query_error":
            raise CleanroomException(502, "WORKFLOW_PROVIDER_ERROR", "ComfyUI history request failed")
        if self.count("history") == 1:
            return {"found": False, "entry": None}
        if self.mode == "fail_remote":
            # ComfyUI 执行出错时替身返回 outputs 为空的 error 条目；带残留输出的
            # 失败 history 由 C4-AC9 在 fixtures/comfyui/ 用 HTTP 层替身覆盖真实代码。
            return {"found": True, "entry": {"outputs": {}, "status": {"status_str": "error", "messages": []}}}
        status = {"status_str": "success"}
        images = [{"filename": "out-0001.png", "subfolder": "", "type": "output"}]
        if self.mode == "partial_outputs" and self.count("history") == 2:
            images.append({"filename": "missing-0002.png", "subfolder": "", "type": "output"})
        return {"found": True, "entry": {"outputs": {"9": {"images": images}}, "status": status}}

    async def upload_input(self, payload: bytes, filename: str, content_type: str, *, subfolder: str) -> str:
        self._record("upload_input", filename=filename, content_type=content_type, size=len(payload),
                     sha256=hashlib.sha256(payload).hexdigest())
        if self.mode in ("upload_fail", "upload_timeout"):
            raise CleanroomException(502, "INPUT_UPLOAD_FAILED", "ComfyUI 输入上传失败")
        return self._next("fake-input") + ".png"

    async def interrupt(self) -> dict[str, Any]:
        self._record("interrupt")
        return {"remote_cancelled": False, "remote_may_continue_or_bill": True, "http_status": 200}

    async def fetch_output(self, filename: str, *, subfolder: str = "", folder_type: str = "output") -> tuple[bytes, str]:
        self._record("fetch_output", filename=filename, subfolder=subfolder, folder_type=folder_type)
        if filename != "out-0001.png":
            raise CleanroomException(502, "WORKFLOW_PROVIDER_ERROR", "ComfyUI could not return the output file")
        return FIXED_PNG, "image/png"

    def install(self, monkeypatch) -> None:
        for name in ("probe", "submit", "history", "upload_input", "interrupt", "fetch_output"):
            monkeypatch.setattr(comfy_executor, name, getattr(self, name))


def output_transport(allowed_hosts: frozenset[str] | None = None, verify: bool = True) -> httpx.MockTransport:
    """只对获准主机名的下载请求返回固定 PNG；其它主机名一律报错。

    形参与真实 routes._output_transport(allowed_hosts, verify) 对齐；替身忽略白名单。
    下载请求按新契约指向钉住后的 IP 字面量，原主机名保留在 Host 头，因此按 Host 头判定。
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.headers.get("host") != APPROVED_HOST:
            raise httpx.ConnectError(f"fake transport refuses host {request.headers.get('host')}")
        return httpx.Response(200, content=FIXED_PNG, headers={"content-type": "image/png"})

    return httpx.MockTransport(handler)


def install_output_transport(monkeypatch) -> None:
    monkeypatch.setattr(routes, "_output_transport", output_transport)


def install_fake_resolver(monkeypatch, address: str = "198.18.0.7") -> None:
    """替身 DNS：任何主机名都解析到 fake-ip 网段地址；是否放行由真实地址判定决定。"""
    import ipaddress
    import socket

    family = socket.AF_INET6 if ipaddress.ip_address(address).version == 6 else socket.AF_INET
    sockaddr = (address, 0, 0, 0) if family == socket.AF_INET6 else (address, 0)

    async def fake_getaddrinfo(host: str, port: int) -> list[tuple]:
        info = (family, socket.SOCK_STREAM, 6, "", (sockaddr[0], port) + sockaddr[2:])
        return [info]

    monkeypatch.setattr(routes, "_getaddrinfo", fake_getaddrinfo)


def count_assets(client: TestClient, headers=HEADERS) -> dict:
    """分别数两个存储：资产注册表条目数、资产库 image 分类条目数。"""
    registry = client.get("/api/asset-registry/assets", params={"limit": 500}, headers=headers)
    assert registry.status_code == 200, registry.text
    body = registry.json()
    items = body.get("items", body.get("assets", []))
    library = client.get("/api/asset-library", headers=headers)
    assert library.status_code == 200, library.text
    images = [item for lib in library.json()["library"]["libraries"] for cat in lib["categories"]
              if cat["type"] == "image" for item in cat["items"]]
    return {"registry": len(items), "library_image": len(images),
            "registry_ids": sorted(item["asset_id"] for item in items),
            "library_image_ids": sorted(item["asset_id"] for item in images)}


def iter_persisted_files(root: Path) -> list[Path]:
    """返回可读的业务持久化文件，排除运行中的数据目录锁文件。

    Windows 会拒绝另一个句柄持有独占锁时的读取；``.gw-data.lock`` 只记录
    进程占用状态，不属于任务持久化内容，因此不能纳入密钥扫描。
    """
    return sorted(
        (path for path in root.rglob("*") if path.is_file() and path.name != ".gw-data.lock"),
        key=lambda path: path.as_posix(),
    )


# ---------------------------------------------------------------------------
# 媒体闭环共用步骤（PR-2..PR-4 复用；全部走真实路由）
# ---------------------------------------------------------------------------
TASKS = "/api/god_workflow/tasks"


def upload_input_asset(client: TestClient, headers=HEADERS) -> dict:
    response = client.post("/api/god_workflow/assets/upload",
                           files={"file": ("local-input.png", FIXED_PNG, "image/png")}, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def save_image_workflow(client: TestClient, input_asset_id: str, headers=HEADERS) -> dict:
    """保存一个带 metadata.input_assets 的 LoadImage → SaveImage 工作流文档（没有 RH 标识）。"""
    document = {"nodes": [
        {"id": "1", "kind": "LoadImage", "title": "Load", "inputs": {"image": "placeholder.png"},
         "metadata": {"input_assets": {"image": input_asset_id}}},
        {"id": "2", "kind": "SaveImage", "title": "Save", "inputs": {"images": ["1", 0], "filename_prefix": "c3"}},
    ], "connections": []}
    response = client.post("/api/god_workflow/documents?source=canvas", json=document, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()["workflow"]


def prepare_workflow(client: TestClient, headers=HEADERS) -> tuple[dict, dict]:
    asset = upload_input_asset(client, headers)
    return asset, save_image_workflow(client, asset["asset_id"], headers)


def execute(client: TestClient, workflow: dict, target: str, key: Optional[str] = "c3-key-1",
            headers=HEADERS, **extra: Any):
    body = {"workflow_id": workflow["workflow_id"], "target": target,
            "expected_version": workflow["version"], **extra}
    request_headers = {**headers, **({"Idempotency-Key": key} if key else {})}
    return client.post(f"{TASKS}/execute", json=body, headers=request_headers)
