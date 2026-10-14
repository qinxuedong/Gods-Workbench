"""RunningHub 与 LiblibAI 工作流抓取客户端。

协议来源：RH-auto 项目的 `rh_auto/rh_client.py` 与 `rh_auto/liblib_client.py`，
经用户确认为本项目自有代码（非旧仓/第三方实现），据此作为接口契约依据。

RunningHub 通道：

- 公开详情：``POST {domain}/api/workflow/getDetail``，body ``{"workflowId": id}``，
  成功判定 ``code == 0`` 且 ``data`` 为对象；备用端点 ``/api/portal/workflow/detail``。
- 网页画布：``POST {domain}/api/workflow/getContent``，需登录态 ``Rh-Accesstoken``，
  body ``{"workflowId": id, "contentType": "0"}``，画布在 ``data.workflowContent``。
- OpenAPI：``POST {domain}/api/openapi/getJsonApiFormat``，需 apiKey，
  body ``{"apiKey": key, "workflowId": id}``，API prompt 在 ``data.prompt``。
- 云端任务：``POST {domain}/task/openapi/create`` 与 ``/task/openapi/outputs``。

LiblibAI 通道（免登录）：

- ``POST https://api2.liblib.art/api/www/model/getByUuid/{modelUuid}``
- ``POST https://api2.liblib.art/api/www/comfy/version/attachment/{versionId}``
  返回 OSS 直链，``GET`` 后得到原始 ComfyUI 画布 JSON。

失败语义：凭据缺失时返回 503 且不下发请求；网络失败映射为 502/504；
绝不伪造解析结果。
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional
from urllib.parse import parse_qs, quote, unquote, urlparse

import httpx

from gw.core.errors import CleanroomException

RUNNINGHUB_DEFAULT_DOMAIN = "www.runninghub.cn"
LIBLIB_DEFAULT_DOMAIN = "www.liblib.art"
DEFAULT_TIMEOUT = 20.0
MAX_FETCH_BYTES = 8 * 1024 * 1024
# 官方明确未受理的参数/权限/资源拒绝；排队、运行中、超时与服务异常不能判失败。
RH_CREATE_REJECTION_CODES = {
    '301', '380', '412', '415', '416', '421', '433', '808', '809', '810', '811', '812', '901',
    '1001', '1002', '1003', '1007', '1008', '1009', '1014', '1101', '1501', '1505',
}

_RH_URL_PATTERN = re.compile(
    r"https?://(?P<domain>(?:www\.)?runninghub\.(?:cn|ai))/"
    r"(?P<kind>workflow|post|ai-detail|run/ai-app)/(?P<id>\d{10,25})",
    re.IGNORECASE,
)
_SNOWFLAKE_PATTERN = re.compile(r"\b(?P<id>\d{15,22})\b")
_LIBLIB_URL_PATTERN = re.compile(r"https?://(?:www\.)?liblib\.(?:art|tv|ai)/[^\s\"'<>]+", re.IGNORECASE)
_LIBLIB_MODELINFO_PATTERN = re.compile(r"/modelinfo/(?P<uuid>[0-9a-fA-F]{24,40})", re.IGNORECASE)
_LIBLIB_WORKFLOWDATA_PATTERN = re.compile(r"workflowData-(?P<id>\d+)", re.IGNORECASE)


# ---------------------------------------------------------------------------
# 链接识别
# ---------------------------------------------------------------------------
def is_runninghub_reference(text: str) -> bool:
    raw = str(text or "")
    return bool(_RH_URL_PATTERN.search(raw)) or "runninghub." in raw.lower()


def is_liblib_reference(text: str) -> bool:
    raw = str(text or "").lower()
    return "liblib.art" in raw or "liblib.tv" in raw or "opencomfy=workflowdata-" in raw


def extract_runninghub_reference(text: str) -> dict[str, str]:
    """从链接、分享口令或纯 ID 中提取 RunningHub 工作流标识。"""
    raw = str(text or "").strip()
    if not raw:
        raise CleanroomException(400, "EMPTY_INPUT", "请输入 RunningHub 工作流链接或 ID")
    match = _RH_URL_PATTERN.search(raw)
    if match:
        domain = match.group("domain").lower()
        if not domain.startswith("www."):
            domain = f"www.{domain}"
        return {"workflow_id": match.group("id"), "domain": domain,
                "kind": match.group("kind").lower(),
                "clean_url": f"https://{domain}/{match.group('kind').lower()}/{match.group('id')}"}
    if "runninghub." in raw.lower():
        for candidate in re.findall(r"https?://[^\s\"'<>]+", raw):
            host = (urlparse(candidate).hostname or "").lower()
            if host in ('runninghub.cn', 'www.runninghub.cn', 'runninghub.ai', 'www.runninghub.ai'):
                identifier = _SNOWFLAKE_PATTERN.search(urlparse(candidate).path or "")
                if identifier:
                    domain = host if host.startswith("www.") else f"www.{host}"
                    return {"workflow_id": identifier.group("id"), "domain": domain, "kind": "workflow",
                            "clean_url": f"https://{domain}/workflow/{identifier.group('id')}"}
    snowflake = _SNOWFLAKE_PATTERN.search(raw)
    if snowflake:
        domain = RUNNINGHUB_DEFAULT_DOMAIN
        return {"workflow_id": snowflake.group("id"), "domain": domain, "kind": "workflow",
                "clean_url": f"https://{domain}/workflow/{snowflake.group('id')}"}
    raise CleanroomException(400, "INVALID_RH_LINK",
                             "未能识别 RunningHub 工作流链接或 ID")


def extract_liblib_reference(text: str) -> dict[str, Optional[str]]:
    """从链接或分享文本中提取 LiblibAI 的 modelUuid / versionUuid / versionId。"""
    raw = str(text or "").strip()
    if not raw:
        raise CleanroomException(400, "EMPTY_INPUT", "请输入 Liblib 工作流链接")
    url_match = _LIBLIB_URL_PATTERN.search(raw)
    target = url_match.group(0) if url_match else raw
    parsed = urlparse(target)
    query = parse_qs(parsed.query)

    model_uuid = None
    path_match = _LIBLIB_MODELINFO_PATTERN.search(parsed.path or "")
    if path_match:
        model_uuid = path_match.group("uuid").lower()

    version_uuid = (query.get("versionUuid") or [None])[0]
    if version_uuid:
        version_uuid = version_uuid.strip().lower()
    if not model_uuid and query.get("comfyuuid"):
        model_uuid = query["comfyuuid"][0].strip().lower()
    if not version_uuid and query.get("comfyOrid"):
        version_uuid = query["comfyOrid"][0].strip().lower()

    comfy_name = None
    if query.get("comfyname"):
        try:
            comfy_name = unquote(query["comfyname"][0]).strip()
        except Exception:
            comfy_name = query["comfyname"][0].strip()

    version_id = None
    if query.get("opencomfy"):
        found = _LIBLIB_WORKFLOWDATA_PATTERN.search(query["opencomfy"][0])
        if found:
            version_id = found.group("id")
    if not version_id:
        found = _LIBLIB_WORKFLOWDATA_PATTERN.search(raw)
        if found:
            version_id = found.group("id")

    if not model_uuid and not version_id:
        raise CleanroomException(400, "INVALID_LIBLIB_LINK",
                                 "未能从 Liblib 链接中识别出 modelUuid 或 workflowData ID")

    if target.startswith(("http://", "https://")):
        clean_url = target
    elif model_uuid:
        clean_url = f"https://{LIBLIB_DEFAULT_DOMAIN}/modelinfo/{model_uuid}"
        if version_uuid:
            clean_url += f"?versionUuid={version_uuid}"
    else:
        clean_url = f"https://{LIBLIB_DEFAULT_DOMAIN}/comfy?opencomfy=workflowData-{version_id}"

    return {"model_uuid": model_uuid, "version_uuid": version_uuid,
            "version_id": version_id, "comfy_name": comfy_name, "clean_url": clean_url}


# ---------------------------------------------------------------------------
# 共享 HTTP 辅助
# ---------------------------------------------------------------------------
def _timeout() -> httpx.Timeout:
    return httpx.Timeout(DEFAULT_TIMEOUT)


async def _post_json(url: str, payload: dict[str, Any], headers: dict[str, str]) -> Any:
    try:
        async with httpx.AsyncClient(timeout=_timeout(), follow_redirects=False,
                                     trust_env=False) as client:
            response = await client.post(url, headers=headers, json=payload)
    except httpx.TimeoutException:
        raise CleanroomException(504, "WORKFLOW_PROVIDER_TIMEOUT", f"{urlparse(url).hostname} 工作流源请求超时；请检查网络后重试，或上传原始工作流 JSON") from None
    except httpx.ConnectError:
        raise CleanroomException(502, "WORKFLOW_PROVIDER_UNREACHABLE",
                                 f"无法连接 {urlparse(url).hostname} 工作流源（网络/DNS/TLS）；请检查网络，或上传原始工作流 JSON") from None
    except httpx.HTTPError:
        raise CleanroomException(502, "WORKFLOW_PROVIDER_ERROR",
                                 f"{urlparse(url).hostname} 工作流源请求失败；请重试或上传原始工作流 JSON") from None
    if 300 <= response.status_code < 400:
        raise CleanroomException(502, "WORKFLOW_PROVIDER_REDIRECT_REJECTED",
                                 "工作流源重定向已拒绝，避免凭据转发；请检查源链接，或上传原始工作流 JSON")
    if response.status_code >= 400:
        if response.status_code in (401, 403):
            raise CleanroomException(502, "RH_SOURCE_AUTH_FAILED",
                                     f"{urlparse(url).hostname} 工作流源拒绝授权（HTTP {response.status_code}）；请检查 RH 凭据或源访问权限，或上传原始工作流 JSON")
        raise CleanroomException(502, "WORKFLOW_PROVIDER_HTTP_ERROR",
                                 f"{urlparse(url).hostname} 工作流源返回 HTTP {response.status_code}；请稍后重试或上传原始工作流 JSON")
    if len(response.content) > MAX_FETCH_BYTES:
        raise CleanroomException(502, "WORKFLOW_PROVIDER_RESPONSE_TOO_LARGE", "外部响应超过大小限制")
    try:
        return response.json()
    except (ValueError, UnicodeDecodeError):
        raise CleanroomException(502, "WORKFLOW_PROVIDER_INVALID_RESPONSE", "外部服务返回非法 JSON") from None


async def _get_json(url: str, headers: dict[str, str]) -> Any:
    try:
        async with httpx.AsyncClient(timeout=_timeout(), follow_redirects=False,
                                     trust_env=False) as client:
            response = await client.get(url, headers=headers)
    except httpx.TimeoutException:
        raise CleanroomException(504, "WORKFLOW_PROVIDER_TIMEOUT", f"{urlparse(url).hostname} 工作流源请求超时；请检查网络后重试，或上传原始工作流 JSON") from None
    except httpx.ConnectError:
        raise CleanroomException(502, "WORKFLOW_PROVIDER_UNREACHABLE",
                                 f"无法连接 {urlparse(url).hostname} 工作流源（网络/DNS/TLS）；请检查网络，或上传原始工作流 JSON") from None
    except httpx.HTTPError:
        raise CleanroomException(502, "WORKFLOW_PROVIDER_ERROR",
                                 f"{urlparse(url).hostname} 工作流源请求失败；请重试或上传原始工作流 JSON") from None
    if 300 <= response.status_code < 400:
        raise CleanroomException(502, "WORKFLOW_PROVIDER_REDIRECT_REJECTED",
                                 "工作流源重定向已拒绝，避免凭据转发；请检查源链接，或上传原始工作流 JSON")
    if response.status_code >= 400:
        if response.status_code in (401, 403):
            raise CleanroomException(502, "RH_SOURCE_AUTH_FAILED",
                                     f"{urlparse(url).hostname} 工作流源拒绝授权（HTTP {response.status_code}）；请检查 RH 凭据或源访问权限，或上传原始工作流 JSON")
        raise CleanroomException(502, "WORKFLOW_PROVIDER_HTTP_ERROR",
                                 f"{urlparse(url).hostname} 工作流源返回 HTTP {response.status_code}；请稍后重试或上传原始工作流 JSON")
    if len(response.content) > MAX_FETCH_BYTES:
        raise CleanroomException(502, "WORKFLOW_PROVIDER_RESPONSE_TOO_LARGE", "外部响应超过大小限制")
    try:
        return response.json()
    except (ValueError, UnicodeDecodeError):
        raise CleanroomException(502, "WORKFLOW_PROVIDER_INVALID_RESPONSE", "外部服务返回非法 JSON") from None


def _json_field(value: Any) -> Optional[dict[str, Any]]:
    """接口常把嵌套 JSON 以字符串返回；这里统一解包为对象。"""
    if isinstance(value, dict) and value:
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except (ValueError, TypeError):
            return None
        if isinstance(parsed, dict) and parsed:
            return parsed
    return None


_RH_PROVIDER_CODE_PATTERN = re.compile(r"-?\d{1,10}")


def _rh_source_rejection(upstream_code: Any) -> CleanroomException:
    """把上游业务码映射为 502；只回显数字码本身，绝不回显供应商消息文本。"""
    code_text = str(upstream_code)
    code = "RH_SOURCE_AUTH_FAILED" if code_text in ("301", "401", "403") else "RH_SOURCE_REJECTED"
    numeric = _RH_PROVIDER_CODE_PATTERN.fullmatch(code_text)
    suffix = f"（RunningHub 错误码 {code_text}）" if numeric else ""
    message = f"RunningHub 已配置的工作流源请求被拒绝{suffix}；请检查凭据、源权限与链接，或上传原始工作流 JSON"
    return CleanroomException(502, code, message,
                              extra={"provider_code": code_text} if numeric else None,
                              expose_extra_fields={"provider_code"} if numeric else None)


# ---------------------------------------------------------------------------
# RunningHub
# ---------------------------------------------------------------------------
def _rh_headers(domain: str, bearer_token: Optional[str] = None) -> dict[str, str]:
    headers = {
        "Host": domain,
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) GodsWorkbench/1.0",
        "X-RH-Lang": "zh-CN",
        "User-Language": "zh_CN",
        "client": "WEB",
    }
    if bearer_token:
        token = bearer_token.strip()
        if token.lower().startswith("bearer "):
            token = token[7:].strip()
        headers["Authorization"] = f"Bearer {token}"
    return headers


async def fetch_runninghub_detail(workflow_id: str, domain: str = RUNNINGHUB_DEFAULT_DOMAIN) -> dict[str, Any]:
    """公开详情（无需凭证）。"""
    last_error = "未返回有效工作流数据"
    last_exception = None
    metadata_only = None
    for endpoint in ("/api/workflow/getDetail", "/api/portal/workflow/detail"):
        url = f"https://{domain}{endpoint}"
        try:
            body = await _post_json(url, {"workflowId": str(workflow_id)}, _rh_headers(domain))
        except CleanroomException as exc:
            last_exception = exc
            last_error = exc.message
            continue
        if isinstance(body, dict) and body.get("code") == 0 and isinstance(body.get("data"), dict):
            from gw.god_workflow.parser import extract_source_graph
            data = body['data']
            if extract_source_graph(data) is not None:
                return data
            metadata_only = metadata_only or data
            # 详情有效但没有图时仍查另一个已知公开源，不能提前止于节点清单。
            continue
        # 不回显供应商原始错误文本，避免泄露令牌/请求内容。
        last_exception = None
        last_error = "公开源未返回有效工作流；请确认链接可访问，或上传原始工作流 JSON"
    if metadata_only is not None:
        return metadata_only
    if last_exception is not None:
        raise last_exception
    raise CleanroomException(502, "RH_DETAIL_FETCH_FAILED",
                             f"获取 RunningHub 工作流 {workflow_id} 公开详情失败：{last_error}")


async def fetch_runninghub_canvas(workflow_id: str, access_token: str,
                                  domain: str = RUNNINGHUB_DEFAULT_DOMAIN) -> Optional[dict[str, Any]]:
    """网页端完整画布（需 Rh-Accesstoken）；无 token 时返回 None，不报错。"""
    if not str(access_token or "").strip():
        return None
    url = f"https://{domain}/api/workflow/getContent"
    try:
        body = await _post_json(url, {"workflowId": str(workflow_id), "contentType": "0"},
                                _rh_headers(domain, access_token))
    except CleanroomException:
        raise
    if not isinstance(body, dict) or str(body.get("code")) != "0":
        raise _rh_source_rejection(body.get("code") if isinstance(body, dict) else None)
    data = body.get("data")
    if not isinstance(data, dict):
        return None
    canvas = _json_field(data.get("workflowContent"))
    if canvas:
        return canvas
    if "nodes" in data and "links" in data:
        return data
    return None


async def fetch_runninghub_api_json(workflow_id: str, api_key: str,
                                    domain: str = RUNNINGHUB_DEFAULT_DOMAIN) -> Optional[dict[str, Any]]:
    """OpenAPI API prompt 格式（需 apiKey）；无 key 时返回 None，不报错。"""
    if not str(api_key or "").strip():
        return None
    key = api_key.strip()
    url = f"https://{domain}/api/openapi/getJsonApiFormat"
    try:
        body = await _post_json(url, {"apiKey": key, "workflowId": str(workflow_id)},
                                _rh_headers(domain, key))
    except CleanroomException:
        raise
    if not isinstance(body, dict) or str(body.get("code")) != "0":
        if isinstance(body, dict) and str(body.get("code")) == "810":
            # 官方 810 表示原图尚未就绪；不回显可能带凭据的供应商消息。
            raise CleanroomException(502, "RH_WORKFLOW_NOT_READY",
                                     "原图暂不可读取（RunningHub 810）；平台未提供完整工作流，解析不以运行作为前置。")
        raise _rh_source_rejection(body.get("code") if isinstance(body, dict) else None)
    data = body.get("data")
    if isinstance(data, dict):
        prompt = _json_field(data.get("prompt"))
        if prompt:
            return prompt
        if any(isinstance(value, dict) and "class_type" in value for value in data.values()):
            return data
    if isinstance(data, str):
        return _json_field(data)
    return None


async def fetch_runninghub_bundle(reference: str, *, access_token: str = "",
                                  api_key: str = "") -> dict[str, Any]:
    """先读公开完整原图，再使用既有凭据查画布/API；解析绝不启动任务。"""
    info = extract_runninghub_reference(reference)
    workflow_id = info["workflow_id"]
    domain = info["domain"]

    source_errors = []
    canvas = api_json = None
    detail = {}
    try:
        detail = await fetch_runninghub_detail(workflow_id, domain)
        from gw.god_workflow.parser import extract_source_graph
        original = extract_source_graph(detail)
        if original is not None:
            canvas = original
    except CleanroomException as exc:
        source_errors.append(exc)
    try:
        if canvas is None:
            canvas = await fetch_runninghub_canvas(workflow_id, access_token, domain)
    except CleanroomException as exc:
        source_errors.append(exc)
    if canvas is None:
        try:
            api_json = await fetch_runninghub_api_json(workflow_id, api_key, domain)
        except CleanroomException as exc:
            if exc.code == "RH_WORKFLOW_NOT_READY":
                # 具体的官方原图状态优先于网页 token 的通用失败提示。
                source_errors.insert(0, exc)
            else:
                source_errors.append(exc)
    if canvas is None and api_json is None:
        if source_errors:
            # 公开清单不是可用图，不得用它覆盖授权/网络真实失败。
            from gw.god_workflow.parser import extract_source_graph
            if extract_source_graph(detail) is None:
                raise source_errors[0]

    return {"workflow_id": workflow_id, "domain": domain, "kind": info["kind"],
            "source_url": info["clean_url"], "canvas_json": canvas, "api_json": api_json,
            "detail": detail, "name": detail.get("name")}


# ---------------------------------------------------------------------------
# LiblibAI
# ---------------------------------------------------------------------------
def _liblib_headers() -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "Accept": "application/json, text/plain, */*",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) GodsWorkbench/1.0",
        "Origin": "https://www.liblib.art",
        "Referer": "https://www.liblib.art/",
    }


async def fetch_liblib_bundle(reference: str) -> dict[str, Any]:
    """免登录拉取 Liblib 工作流：模型详情 + 版本附件 OSS 原始画布。"""
    info = extract_liblib_reference(reference)
    model_uuid = info["model_uuid"]
    version_uuid = info["version_uuid"]
    version_id = info["version_id"]
    comfy_name = info["comfy_name"]
    clean_url = info["clean_url"] or f"https://{LIBLIB_DEFAULT_DOMAIN}"

    headers = _liblib_headers()
    model_data: dict[str, Any] = {}
    matched_version: dict[str, Any] = {}
    api_json: Optional[dict[str, Any]] = None
    canvas_json: Optional[dict[str, Any]] = None

    if model_uuid:
        for host in ("https://api2.liblib.art", f"https://{LIBLIB_DEFAULT_DOMAIN}"):
            try:
                body = await _post_json(f"{host}/api/www/model/getByUuid/{model_uuid}", {}, headers)
            except CleanroomException:
                continue
            if isinstance(body, dict) and body.get("code") == 0 and isinstance(body.get("data"), dict):
                model_data = body["data"]
                break

    versions = model_data.get("versions") if isinstance(model_data.get("versions"), list) else []
    if versions:
        if version_id:
            matched_version = next(
                (item for item in versions if isinstance(item, dict) and str(item.get("id")) == str(version_id)), {})
        if not matched_version and version_uuid:
            matched_version = next(
                (item for item in versions if isinstance(item, dict)
                 and str(item.get("uuid") or "").lower() == version_uuid.lower()), {})
        if not matched_version and isinstance(versions[0], dict):
            matched_version = versions[0]

    if matched_version and not version_id and matched_version.get("id"):
        version_id = str(matched_version["id"])
    if matched_version and not version_uuid and matched_version.get("uuid"):
        version_uuid = str(matched_version["uuid"])

    image_group = matched_version.get("imageGroup") if isinstance(matched_version.get("imageGroup"), dict) else {}
    for image in image_group.get("images") if isinstance(image_group.get("images"), list) else []:
        if not isinstance(image, dict):
            continue
        gen_info = image.get("generateInfo") if isinstance(image.get("generateInfo"), dict) else image
        raw = gen_info.get("pngInfo")
        if not isinstance(raw, str) or not raw.strip():
            continue
        try:
            png_info = json.loads(raw)
        except (ValueError, TypeError):
            continue
        if not isinstance(png_info, dict):
            continue
        prompt = png_info.get("comfy-prompt")
        workflow = png_info.get("comfy-workflow")
        if api_json is None and isinstance(prompt, dict) and prompt:
            api_json = prompt
        if canvas_json is None and isinstance(workflow, dict) and isinstance(workflow.get("nodes"), list) and workflow["nodes"]:
            canvas_json = workflow

    if version_id:
        for host in ("https://api2.liblib.art", f"https://{LIBLIB_DEFAULT_DOMAIN}"):
            try:
                body = await _post_json(f"{host}/api/www/comfy/version/attachment/{version_id}", {}, headers)
            except CleanroomException:
                continue
            attachment = body.get("data") if isinstance(body, dict) and body.get("code") == 0 else None
            if isinstance(attachment, str) and attachment.startswith(("http://", "https://")):
                try:
                    fetched = await _get_json(attachment, headers)
                except CleanroomException:
                    continue
                if isinstance(fetched, dict):
                    if isinstance(fetched.get("nodes"), list):
                        canvas_json = fetched
                    elif any(isinstance(value, dict) and "class_type" in value for value in fetched.values()):
                        api_json = fetched
                break

    if canvas_json is None and api_json is None:
        raise CleanroomException(502, "LIBLIB_WORKFLOW_FETCH_FAILED",
                                 f"无法从 Liblib 获取工作流（versionId={version_id}, modelUuid={model_uuid}）")

    base_name = str(model_data.get("name") or "").strip()
    version_name = str(matched_version.get("name") or "").strip()
    if base_name and version_name and not base_name.endswith(version_name):
        final_name = f"{base_name} {version_name}"
    else:
        final_name = base_name or version_name or comfy_name or f"Liblib_Workflow_{version_id or model_uuid}"

    return {"workflow_id": f"liblib_{version_id or (model_uuid or 'wf')[:12]}",
            "name": final_name, "source_url": clean_url, "domain": LIBLIB_DEFAULT_DOMAIN,
            "canvas_json": canvas_json, "api_json": api_json,
            "detail": {"id": f"liblib_{version_id or model_uuid}",
                       "name": final_name,
                       "owner": {"name": str(model_data.get("userName") or "LiblibAI 创作者")}}}


# ---------------------------------------------------------------------------
# RunningHub 云端任务执行
# ---------------------------------------------------------------------------
async def upload_rh_input(payload: bytes, filename: str, content_type: str, api_key: str,
                          domain: str = RUNNINGHUB_DEFAULT_DOMAIN) -> str:
    """按官方 multipart 输入上传合同取得引擎文件名，不把私有素材 URL 发往云端。"""
    if domain not in {"www.runninghub.cn", "www.runninghub.ai"}:
        raise CleanroomException(400, "INVALID_RH_DOMAIN", "RunningHub 域名不在白名单中")
    if not api_key.strip():
        raise CleanroomException(503, "WORKFLOW_EXECUTOR_UNAVAILABLE", "RunningHub API Key 未配置")
    if not payload or len(payload) > 30 * 1024 * 1024:
        raise CleanroomException(413, "RH_INPUT_SIZE_LIMIT", "RunningHub 输入文件必须非空且不超过 30MB")
    try:
        async with httpx.AsyncClient(timeout=_timeout(), follow_redirects=False, trust_env=False) as client:
            response = await client.post(f"https://{domain}/task/openapi/upload",
                headers={"Authorization": f"Bearer {api_key.strip()}"},
                data={"apiKey": api_key.strip(), "fileType": "input"},
                files={"file": (filename, payload, content_type)})
        response.raise_for_status()
        body = response.json()
    except httpx.TimeoutException:
        raise CleanroomException(504, "RH_INPUT_UPLOAD_TIMEOUT", "RunningHub 输入上传超时") from None
    except (httpx.HTTPError, ValueError):
        raise CleanroomException(502, "RH_INPUT_UPLOAD_FAILED", "RunningHub 输入上传失败") from None
    name = body.get("data", {}).get("fileName") if isinstance(body, dict) and str(body.get("code")) == "0" and isinstance(body.get("data"), dict) else None
    if not isinstance(name, str) or not name or len(name) > 4096 or any(character in name for character in '\r\n'):
        raise CleanroomException(502, "RH_INPUT_UPLOAD_REJECTED", "RunningHub 未返回有效输入文件名")
    return name


async def create_rh_cloud_task(
    workflow_id: Optional[str],
    node_info_list: list[dict[str, Any]],
    api_key: str,
    domain: str = RUNNINGHUB_DEFAULT_DOMAIN,
    *, workflow: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """调用 RunningHub OpenAPI 提交工作流云端运行任务。"""
    if not api_key or not api_key.strip():
        raise CleanroomException(503, "WORKFLOW_EXECUTOR_UNAVAILABLE", "运行 RunningHub 云端任务需要配置 API Key")
    clean_key = api_key.strip()
    url = f"https://{domain}/task/openapi/create"
    payload: dict[str, Any] = {"apiKey": clean_key}
    # 官方高级接口支持完整workflow；上传与提交合为一次请求，不伪造模板ID。
    if workflow is not None:
        if not isinstance(workflow, dict) or not workflow or any(
                not isinstance(node, dict) or not isinstance(node.get('class_type'), str)
                or not isinstance(node.get('inputs'), dict) for node in workflow.values()):
            raise CleanroomException(400, 'INVALID_WORKFLOW', '缺少完整可执行工作流')
        payload['workflow'] = json.dumps(workflow, ensure_ascii=False)
        # 现场301说明必填校验仍生效；已有真实RH标识仅供传输兼容，完整图仍覆盖模板。
        if isinstance(workflow_id, str) and re.fullmatch(r'\d{10,25}', workflow_id):
            payload['workflowId'] = workflow_id
    elif isinstance(workflow_id, str) and workflow_id.strip():
        payload['workflowId'] = workflow_id.strip()
    else:
        raise CleanroomException(400, 'INVALID_WORKFLOW', '缺少完整工作流或真实RH模板标识')
    if node_info_list:
        payload["nodeInfoList"] = [
            {
                "nodeId": str(item.get("nodeId", item.get("node_id"))),
                "fieldName": str(item.get("fieldName", item.get("field_name"))),
                "fieldValue": item.get("fieldValue", item.get("field_value")),
            }
            for item in node_info_list
            if item.get("nodeId", item.get("node_id")) is not None
        ]
    body = await _post_json(url, payload, _rh_headers(domain, bearer_token=clean_key))
    if not isinstance(body, dict) or str(body.get("code")) != "0":
        # 明确的业务拒绝与响应格式未知分开，不能回显可能带凭据的自由文本。
        if (isinstance(body, dict) and type(body.get('code')) in (int, str)
                and str(body['code']) in RH_CREATE_REJECTION_CODES):
            code = str(body['code'])
            raise CleanroomException(400, "RH_TASK_CREATE_REJECTED", f"RunningHub 拒绝创建任务（错误码 {code}）",
                                     extra={'provider_code': code})
        raise CleanroomException(502, 'RH_TASK_CREATE_UNCONFIRMED', 'RunningHub 未返回可确认的任务创建结果')
    data = body.get("data")
    if isinstance(data, dict):
        task_id = data.get('taskId') or data.get('task_id')
        if task_id is not None and (type(task_id) not in (str, int) or not str(task_id).strip() or len(str(task_id)) > 128):
            raise CleanroomException(502, 'RH_TASK_CREATE_UNCONFIRMED', 'RunningHub 返回无效任务标识，提交结果未确认')
        return data
    if type(data) in (str, int) and str(data).strip() and len(str(data)) <= 128:
        return {"taskId": str(data)}
    return {}


async def query_rh_task_outputs(
    task_id: str,
    api_key: str,
    domain: str = RUNNINGHUB_DEFAULT_DOMAIN,
) -> dict[str, Any]:
    """查询 RunningHub 云端任务状态及输出产物。"""
    if not api_key or not api_key.strip():
        raise CleanroomException(503, "WORKFLOW_EXECUTOR_UNAVAILABLE", "查询云端任务需要配置 API Key")
    clean_key = api_key.strip()
    url = f"https://{domain}/task/openapi/outputs"
    payload = {"apiKey": clean_key, "taskId": str(task_id)}
    body = await _post_json(url, payload, _rh_headers(domain, bearer_token=clean_key))
    if not isinstance(body, dict):
        raise CleanroomException(502, "RH_TASK_STATUS_UNKNOWN", "RunningHub 查询返回格式无效，请稍后续查")
    code_text = str(body.get("code"))
    data = body.get("data")
    if code_text == "0" and isinstance(data, list):
        return {"status": "SUCCESS", "outputs": data, "raw": body}
    if code_text in ("804", "813") or (code_text == "0" and not data):
        return {"status": "RUNNING", "outputs": [], "message": body.get("msg") or "任务运行中", "raw": body}
    if code_text == "805":
        return {"status": "FAILED", "outputs": [], "message": "RunningHub 远端任务状态异常", "raw": body}
    # 授权、服务故障及未知码是查询失败，不是远端执行终态；不用自由文本猜状态。
    raise CleanroomException(502, "RH_TASK_QUERY_REJECTED", "RunningHub 未确认任务结果，请核对配置后续查")


__all__ = [
    "create_rh_cloud_task",
    "extract_liblib_reference",
    "extract_runninghub_reference",
    "fetch_liblib_bundle",
    "fetch_runninghub_api_json",
    "fetch_runninghub_bundle",
    "fetch_runninghub_canvas",
    "fetch_runninghub_detail",
    "is_liblib_reference",
    "is_runninghub_reference",
    "query_rh_task_outputs",
]
