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
"""本机素材收集接口。"""

from concurrent.futures import ThreadPoolExecutor
import asyncio
from ipaddress import ip_address
import secrets
import threading
import time
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import APIRouter, Header, Request
from pydantic import BaseModel

from gw.asset_registry import collector
from gw.core.asset_visibility import asset_owner_key
from gw.core.auth import require_edit_access
from gw.core.errors import CleanroomException

router = APIRouter(prefix="/api/asset-collect", tags=["asset-collect"])
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="collect")
_lock = threading.RLock()
_tokens = {}
_jobs = {}


class UrlRequest(BaseModel):
    url: str
    pair_token: str


def _local(request: Request):
    try:
        peer = ip_address(request.client.host).is_loopback
    except (AttributeError, ValueError):
        peer = False
    host = request.url.hostname or ""
    try:
        local_host = host == "localhost" or ip_address(host).is_loopback
    except ValueError:
        local_host = False
    if not peer or not local_host:
        raise CleanroomException(403, "LOCAL_ONLY", "收集接口仅允许本机访问")
    origin = request.headers.get("origin")
    if request.headers.get("sec-fetch-site", "").lower() == "cross-site" or (
        origin and origin.rstrip("/") != str(request.base_url).rstrip("/")
    ):
        raise CleanroomException(403, "ORIGIN_FORBIDDEN", "收集接口不允许跨站请求")


def _owner(authorization, role):
    return asset_owner_key(require_edit_access(authorization, role))


def _check_token(token, owner):
    with _lock:
        entry = _tokens.get(token)
        if not entry or entry[0] != owner or entry[1] <= time.monotonic():
            raise CleanroomException(401, "unauthorized", "配对令牌无效或已过期")


@router.post("/pair")
def pair(request: Request, authorization: str | None = Header(None), x_user_role: str = Header("editor")):
    _local(request)
    owner = _owner(authorization, x_user_role)
    token = secrets.token_urlsafe(32)
    with _lock:
        now = time.monotonic()
        for key, value in list(_tokens.items()):
            if value[1] <= now:
                del _tokens[key]
        _tokens[token] = (owner, now + 3600)
    return {"pair_token": token}


def _new_job(owner):
    job_id = "collect_" + uuid4().hex
    with _lock:
        _jobs[job_id] = {"job_id": job_id, "status": "queued", "progress": 0, "owner": owner}
    return job_id


def _work(job_id, owner, function):
    with _lock:
        _jobs[job_id].update(status="running", progress=1)
    try:
        function()
    except collector.CollectError as exc:
        with _lock:
            _jobs[job_id].update(status="failed", error_code=exc.code, message=str(exc))
    except Exception:
        with _lock:
            _jobs[job_id].update(status="failed", error_code="unsupported_url", message="素材收集失败")
    else:
        with _lock:
            _jobs[job_id].update(status="done", progress=100)


@router.post("/url", status_code=202)
async def collect_url(payload: UrlRequest, request: Request, authorization: str | None = Header(None), x_user_role: str = Header("editor")):
    _local(request)
    owner = _owner(authorization, x_user_role)
    _check_token(payload.pair_token, owner)
    try:
        url = collector.validate_collect_url(payload.url)
    except collector.CollectError as exc:
        raise CleanroomException(400, exc.code, str(exc)) from exc
    prepared = None
    try:
        collector.find_ytdlp()
    except collector.CollectError as exc:
        if exc.code != "ytdlp_unavailable":
            raise
        try:
            prepared = await asyncio.to_thread(collector.download_direct, url)
        except collector.CollectError as preflight_error:
            if preflight_error.code == "ytdlp_required":
                raise CleanroomException(503, exc.code, str(exc)) from exc
            raise CleanroomException(400, preflight_error.code, str(preflight_error)) from preflight_error
    job_id = _new_job(owner)

    def download():
        try:
            data, final_url = prepared if prepared is not None else collector.download_direct(url)
            name = urlsplit(final_url).path.rsplit("/", 1)[-1] or "collected.bin"
            title = name
        except collector.CollectError as exc:
            if exc.code != "ytdlp_required":
                raise
            data, name, title = collector.download_with_ytdlp(url)
        collector.store_content(data, name, url, title, owner=owner)

    _executor.submit(_work, job_id, owner, download)
    return {"job_id": job_id, "status": "queued"}


@router.post("/files", status_code=202)
async def collect_files(request: Request, authorization: str | None = Header(None), x_user_role: str = Header("editor")):
    _local(request)
    owner = _owner(authorization, x_user_role)
    form = await request.form()
    _check_token(str(form.get("pair_token") or ""), owner)
    uploads = form.getlist("files[]")
    if not uploads or len(uploads) > 20:
        raise CleanroomException(400, "INVALID_REQUEST", "请选择 1 至 20 个文件")
    files = []
    for upload in uploads:
        if not hasattr(upload, "read"):
            raise CleanroomException(400, "INVALID_REQUEST", "无效文件")
        data = await upload.read(64 * 1024 * 1024 + 1)
        if not data or len(data) > 64 * 1024 * 1024:
            raise CleanroomException(413, "PAYLOAD_TOO_LARGE", "文件为空或超过大小限制")
        files.append((str(upload.filename or "collected.bin"), data))
    job_id = _new_job(owner)

    def store():
        for name, data in files:
            collector.store_content(data, name, owner=owner)

    _executor.submit(_work, job_id, owner, store)
    return {"job_id": job_id, "accepted": [{"name": name, "size": len(data)} for name, data in files]}


@router.get("/jobs/{job_id}")
def job(job_id: str, request: Request, authorization: str | None = Header(None), x_user_role: str = Header("editor"), x_collect_token: str = Header("")):
    _local(request)
    owner = _owner(authorization, x_user_role)
    _check_token(x_collect_token, owner)
    with _lock:
        entry = _jobs.get(job_id)
        if not entry or entry["owner"] != owner:
            raise CleanroomException(404, "NOT_FOUND", "收集任务不存在")
        return {key: value for key, value in entry.items() if key != "owner"}
