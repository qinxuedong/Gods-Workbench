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

"""FastAPI 洁净室应用工厂与核心中间件。"""

import os
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlencode
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from gw.api.routes_ai import router as ai_router
from gw.api.routes_asset_library import router as asset_library_router
from gw.api.routes_asset_library_b4 import router as asset_library_b4_router
from gw.api.routes_local_assets import router as local_assets_router
from gw.api.routes_media import router as media_router
from gw.api.routes_asset_review_b6 import router as asset_review_b6_router
from gw.api.routes_episode_pipeline_b7 import router as episode_pipeline_b7_router
from gw.api.routes_prompt_library_b8 import router as prompt_library_b8_router
from gw.api.routes_public_b9 import router as public_b9_router
from gw.api.routes_auth import router as auth_router
from gw.api.routes_auth_management import router as auth_management_router
from gw.api.routes_canvas_closure import router as canvas_closure_router
from gw.api.routes_god_canvas import jobs_router, router as god_canvas_router
from gw.api.routes_observability import router as observability_router
from gw.api.routes_projects import legacy_router as projects_compat_router
from gw.api.routes_projects import router as projects_router
from gw.api.routes_asset_registry import router as asset_registry_router
from gw.video_tasks.routes import router as video_tasks_router
from gw.api.routes_prompt_library import router as prompt_library_router
from gw.api.routes_settings import router as settings_router
from gw.god_workflow.routes import router as god_workflow_router
from gw.core import session as session_store
from gw.observability import telemetry as observability_telemetry
from gw.core import local_accounts
from gw.api.routes_local_accounts import router as local_accounts_router
from gw.core.config import AUTH_MODE_OIDC, AUTH_MODE_LOCAL_ACCOUNT, load_runtime_auth_config
from gw.core.errors import CleanroomException

# 同进程 Agent 领域错误处理是可选的：当运行期未提供 3.11+ StrEnum / 新版
# jsonschema 等 agent 依赖时，工作流与其余既有接口必须仍可导入与提供。
try:  # pragma: no cover - 依赖可用性分支
    from gw.agent_runtime.errors import DomainError
except ImportError:  # pragma: no cover
    class DomainError(Exception):
        """Agent 运行期依赖缺失时的占位基类，不会与业务异常混淆。"""

        def __init__(self, *args, **kwargs):
            super().__init__(*args)

STATIC_DIR = Path(__file__).resolve().parents[2] / "web"

# A-01：无限画布旧提示词已移出静态挂载目录，继续隔离。
# 只登记被拒绝的 URL，不在应用层保存或回放提示词正文。
QUARANTINED_STATIC_PATHS = frozenset({
    "system-prompts/infinite-canvas-prompt-templates.md",
})

# S-01：**OIDC** Cookie 写操作 Origin 门禁的豁免路径（OIDC 认证端点自身）。
# OIDC 登录发起/登出/首次引导不以现有 Cookie 会话为前提，必须能在缺失 Origin 时工作，
# 否则 OIDC 会话一旦失效就无法登出、也无法重新登录。
#
# 注意：本豁免**只适用于 OIDC 分支**。``local_account`` 分支的写路径门禁是既有行为
# （其登录/登出/初始化端点同样要求同源，见 tests/contracts/auth/test_local_account_login.py），
# 不得被本集合放宽。业务写接口在两种模式下都不豁免。
CSRF_ORIGIN_EXEMPT_PATHS = frozenset({
    "/api/asset-auth/login",
    "/api/asset-auth/logout",
    "/api/asset-auth/bootstrap",
})


@asynccontextmanager
async def _app_lifespan(app: FastAPI):
    """启动同进程Agent工作器，并在关闭时停止全部托管后台执行。

    GW_DISABLE_AGENT_INTEGRATION=1 让纯工作流/契约测试在缺少可选的 agent
    运行期依赖（Python 3.11+ StrEnum、新版 jsonschema）时仍能导入应用；
    生产与开发默认保持启用，且该开关不会改变任何路由契约或错误处理形状。
    """
    integration_enabled = os.getenv("GW_DISABLE_AGENT_INTEGRATION", "").strip() != "1"
    if integration_enabled:
        from gw.api.agent_integration import start_agent_integration, stop_agent_integration
        await start_agent_integration(app)
    try:
        yield
    finally:
        try:
            if integration_enabled:
                await stop_agent_integration(app)
        finally:
            # FastAPI 0.141 起应用对象不再提供 add_event_handler。
            from gw.asset_registry.index_jobs import shutdown as shutdown_index_jobs
            shutdown_index_jobs()


def create_app(*, agent_integration_factory=None) -> FastAPI:
    """构建并配置洁净应用实例。"""
    app = FastAPI(
        title="Gods-Workbench Cleanroom API",
        version="0.1.0",
        description="基于本轮修复输入与黄金夹具构建的洁净室服务骨架",
        lifespan=_app_lifespan,
    )
    app.state.agent_integration_factory = agent_integration_factory

    @app.exception_handler(CleanroomException)
    async def cleanroom_exception_handler(request: Request, exc: CleanroomException):
        """统一处理契约异常并返回标准错误包。"""
        envelope = exc.to_envelope()
        return JSONResponse(
            status_code=exc.status_code,
            content=envelope.model_dump(exclude_none=True),
        )

    @app.exception_handler(DomainError)
    async def agent_domain_exception_handler(request: Request, exc: DomainError):
        """将领域错误转换为契约定义的标准 detail 包。"""
        return JSONResponse(status_code=exc.http_status, content=exc.as_envelope())

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        """把 FastAPI 参数校验错误统一为契约要求的 400 错误包。"""
        return JSONResponse(
            status_code=400,
            content={
                "detail": {
                    "code": "INVALID_REQUEST",
                    "message": "请求参数不合法",
                    "errors": [
                        {
                            "loc": list(error.get("loc", ())),
                            "msg": error.get("msg", "请求参数不合法"),
                            "type": error.get("type", "invalid_request"),
                        }
                        for error in exc.errors()
                    ],
                }
            },
        )

    @app.get("/healthz", tags=["governance"])
    def health_check():
        """健康检查端点；如实暴露认证模式与发布授权状态，便于部署核验。"""
        auth_runtime = load_runtime_auth_config()
        return {
            "status": "ok",
            "mode": "cleanroom",
            "auth_mode": auth_runtime.mode,
            "oidc_ready": bool(auth_runtime.ready),
            "frozen_contracts": False,
            "release_authorized": False,
        }

    @app.get("/", include_in_schema=False)
    def index_redirect():
        """根路径重定向至 Workbench 项目中心。"""
        return RedirectResponse(url="/static/pages/projects.html", status_code=status.HTTP_307_TEMPORARY_REDIRECT)

    @app.middleware("http")
    async def quarantined_static_guard(request: Request, call_next):
        """在任何挂载之前拦下已隔离的静态 URL，大小写不敏感。

        A-01：精确路由只能拦住逐字匹配的 URL；在大小写不敏感的平台上
        `StaticFiles` 仍会把 `/static/SYSTEM-PROMPTS/…MD` 解析到同一文件。
        因此这里按「规范化路径后缀」判断，确保任何大小写、重复斜杠或
        `./` 变体都返回非 200，而不是回落到静态挂载。
        """
        if request.url.path.startswith("/static/"):
            normalized = request.url.path[len("/static/"):].lower().replace("//", "/")
            while normalized.startswith("./"):
                normalized = normalized[2:]
            normalized = normalized.rstrip("/")
            if any(
                normalized == quarantined.lower()
                or normalized.endswith("/" + quarantined.lower())
                for quarantined in QUARANTINED_STATIC_PATHS
            ):
                return _reject_quarantined_static()
        return await call_next(request)

    @app.middleware("http")
    async def observability_http_middleware(request: Request, call_next):
        """把真实 /api 请求的响应字节数与耗时计入观测采样（只测量真实值）。

        不使用任何估算或随机值：字节数取响应声明的 Content-Length 或可读 body 长度，
        耗时用单调时钟实测；无法测量时计 0，绝不编造。
        """
        if not request.url.path.startswith("/api/"):
            return await call_next(request)
        import time as _time
        started = _time.perf_counter()
        response = await call_next(request)
        elapsed_ms = (_time.perf_counter() - started) * 1000.0
        size = 0
        try:
            raw_length = response.headers.get("content-length")
            if raw_length is not None:
                size = int(raw_length)
            else:
                body = getattr(response, "body", None)
                if isinstance(body, (bytes, bytearray)):
                    size = len(body)
        except Exception:
            size = 0
        try:
            observability_telemetry.record_http_observation(
                request.url.path, response_bytes=size, duration_ms=elapsed_ms
            )
        except Exception:
            pass
        return response

    @app.middleware("http")
    async def session_principal_middleware(request: Request, call_next):
        """按当前运行态 Cookie 解析出的服务端身份写入请求上下文。

        只在 OIDC 或数据库账户模式下解析；未知/过期会话等价于未认证（不拒绝请求本身，
        由各路由的权限检查决定 401/403）。上下文变量在响应后必须重置，
        避免跨请求泄漏。

        Cookie 写操作的 **Origin 门禁**（S-01）：与 ``local_account`` 同口径，
        OIDC 模式的写接口要求 ``Origin`` 与本站点一致且不得带跨站标记。

        例外（``CSRF_ORIGIN_EXEMPT_PATHS``）：认证端点本身**不依赖** Cookie 会话，
        它们的写操作必须能在无 ``Origin`` 时工作，否则会把「重新登录 / 登出」
        自己锁死（用户会话一旦失效就再也发不出登出与再次登录请求）：

        - ``POST /api/asset-auth/login``  登录发起：此时本就无有效 Cookie 会话；
        - ``POST /api/asset-auth/logout`` 登出：会话可能已失效，仍必须可幂等清除；
        - ``POST /api/asset-auth/bootstrap`` 首次建管理员：尚无任何会话。

        真正依赖 Cookie 会话的业务写接口（项目、画布、素材等）**不放行**该例外。

        **门禁只在请求真的携带会话 Cookie 时生效**：门禁保护的是"用受害者的
        Cookie 会话发起跨站写操作"这一路径；未携带会话 Cookie 的请求没有任何
        可被滥用的身份，此时必须让路由自身的 401 契约生效，而不是提前用 403
        顶掉它（否则 401/403 契约语义会被本中间件改写）。
        """
        token = None
        mode = load_runtime_auth_config().mode
        if request.url.path.startswith("/api/"):
            # 仅 OIDC 分支使用：写方法 + 非豁免路径。是否真的施加门禁还要看请求有没有
            # 携带会话 Cookie（见 OIDC 分支内的 principal is not None 判断）。
            oidc_origin_gate_eligible = (
                request.method not in {"GET", "HEAD", "OPTIONS"}
                and request.url.path not in CSRF_ORIGIN_EXEMPT_PATHS
            )
            if mode == AUTH_MODE_LOCAL_ACCOUNT:
                if request.method not in {"GET", "HEAD", "OPTIONS"}:
                    # Cookie 登录的全部写接口要求同源，拒绝跨站请求及缺失来源。
                    # 既有行为：不套用 OIDC 的豁免集合（local/setup 与 local 登出同样受门禁约束）。
                    origin = request.headers.get("origin")
                    expected = str(request.base_url).rstrip("/")
                    if origin != expected or request.headers.get("sec-fetch-site") == "cross-site":
                        return JSONResponse(status_code=403, content={"detail": {
                            "code": "CSRF_ORIGIN_REJECTED", "message": "请从当前应用页面执行操作（请求来源校验失败）"}})
                session_id = session_store.cookie_value(request.cookies, "session")
                principal = local_accounts.get_session(session_id)
                if principal is not None:
                    principal = dict(principal)
                    principal["session_id"] = session_id
                token = session_store.set_current_principal(principal)
            elif mode == AUTH_MODE_OIDC:
                # 先解析会话身份，再按"是否真的带会话"决定门禁。
                session_id = session_store.cookie_value(request.cookies, "session")
                principal = session_store.get_session(session_id)
                if oidc_origin_gate_eligible and principal is not None:
                    # 与本地账户写路径同一口径：OIDC Cookie 写操作也要求同源，
                    # 缺失来源或跨站标记一律拒绝。SameSite=Lax 是否足以挡住
                    # 未实证的跨站请求，只作为条件性观察，不在此放宽门禁。
                    origin = request.headers.get("origin")
                    expected = str(request.base_url).rstrip("/")
                    if origin != expected or request.headers.get("sec-fetch-site") == "cross-site":
                        return JSONResponse(status_code=403, content={"detail": {
                            "code": "CSRF_ORIGIN_REJECTED", "message": "请从当前应用页面执行操作（请求来源校验失败）"}})
                if principal is not None:
                    principal = dict(principal)
                    principal["session_id"] = session_id
                token = session_store.set_current_principal(principal)
        try:
            return await call_next(request)
        finally:
            if token is not None:
                session_store.reset_current_principal(token)

    # 挂载 API 路由
    app.include_router(auth_router)
    app.include_router(local_accounts_router)
    app.include_router(ai_router)
    app.include_router(auth_management_router)
    app.include_router(projects_router)
    app.include_router(projects_compat_router)
    # 视频端点在应用层独立挂载，避免与画布兼容路由重复注册。
    app.include_router(video_tasks_router)
    # 必须先于 god_canvas_router 注册：/api/canvases/trash 不能被 /{canvas_id} 抢先匹配。
    app.include_router(canvas_closure_router)
    app.include_router(god_canvas_router)
    app.include_router(asset_library_router)
    app.include_router(asset_library_b4_router)
    app.include_router(local_assets_router)
    app.include_router(media_router)
    app.include_router(asset_review_b6_router)
    app.include_router(episode_pipeline_b7_router)
    app.include_router(prompt_library_b8_router)
    app.include_router(public_b9_router)
    app.include_router(asset_registry_router)
    app.include_router(jobs_router)
    app.include_router(observability_router)
    app.include_router(prompt_library_router)
    app.include_router(settings_router)
    app.include_router(god_workflow_router, prefix="/api/god_workflow")

    @app.get("/share/{share_token}", include_in_schema=False)
    async def public_share_page(share_token: str):
        """公开分享只返回页面外壳；令牌/票据与资产授权由公开API验证。"""
        return FileResponse(STATIC_DIR / "asset-share.html", headers={
            "Cache-Control": "private, no-store", "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
        })

    # 根级旧页只为 embedded=1 内嵌兼容；普通访问保留查询白名单后转至主页面。
    legacy_page_targets = {
        "asset-manager": ("/static/pages/assets.html", "asset-manager.html", ("project_id", "pipeline_id", "asset_id")),
        "api-settings": ("/static/pages/settings.html", "api-settings.html", ("section", "project_id")),
        "canvas-list": ("/static/pages/storyboard.html", "canvas-list.html", ("project_id", "entity_id", "canvas_id", "view", "filter", "layout")),
        "episode-pipeline": ("/static/pages/workshop.html", "episode-pipeline.html", ("project_id", "pipeline_id", "step", "agent")),
        "task-center": ("/static/pages/collab.html", "task-center.html", ("view", "project_id")),
        "governance": ("/static/pages/projects.html", "governance.html", ("openTrash", "project_id")),
    }

    @app.get("/static/{legacy_name}.html", include_in_schema=False)
    async def legacy_page_entry(request: Request, legacy_name: str):
        """根级旧页 embedded=1 时原样提供，普通访问仅重定向到主页面。"""
        item = legacy_page_targets.get(legacy_name)
        if item is None:
            public_page = STATIC_DIR / f"{legacy_name}.html"
            return FileResponse(public_page) if public_page.is_file() else JSONResponse({"detail": "Not Found"}, status_code=404)
        target, embed_name, keys = item
        source = STATIC_DIR / "embeds" / embed_name
        if not source.is_file():
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        if request.query_params.get("embedded") == "1":
            return FileResponse(source)
        params = []
        for key in keys:
            value = request.query_params.get(key)
            if value is not None and value != "":
                params.append((key, value))
        query = urlencode(params)
        return RedirectResponse(f"{target}?{query}" if query else target, status_code=status.HTTP_307_TEMPORARY_REDIRECT)

    # 旧版静态地址采用逐条精确映射；不接受路径参数，避免目录遍历。
    legacy_static_redirects = {
        "/static/v2/agents.html": "/static/pages/agents.html",
        "/static/v2/assets.html": "/static/pages/assets.html",
        "/static/v2/collab.html": "/static/pages/collab.html",
        "/static/v2/index.html": "/static/pages/index.html",
        "/static/v2/production.html": "/static/pages/production.html",
        "/static/v2/projects.html": "/static/pages/projects.html",
        "/static/v2/settings.html": "/static/pages/settings.html",
        "/static/v2/storyboard.html": "/static/pages/storyboard.html",
        "/static/v2/workshop.html": "/static/pages/workshop.html",
        "/static/v2/css/project-date-range.css": "/static/css/pages/project-date-range.css",
        "/static/v2/js/agents-controller.js": "/static/js/controllers/agents-controller.js",
        "/static/v2/js/assets-controller.js": "/static/js/controllers/assets-controller.js",
        "/static/v2/js/collab-controller.js": "/static/js/controllers/collab-controller.js",
        "/static/v2/js/home-controller.js": "/static/js/controllers/home-controller.js",
        "/static/v2/js/production-controller.js": "/static/js/controllers/production-controller.js",
        "/static/v2/js/projects-controller.js": "/static/js/controllers/projects-controller.js",
        "/static/v2/js/storyboard-controller.js": "/static/js/controllers/storyboard-controller.js",
        "/static/v2/js/project-date-range.js": "/static/js/modules/project-date-range.js",
        "/static/v2/js/v2-shell.js": "/static/js/core/workbench-shell.js",
        "/static/js/v2-task-queue.js": "/static/js/core/task-queue.js",
    }

    def _make_static_compat_redirect(target: str):
        async def static_compat_redirect(request: Request):
            query = request.url.query
            location = f"{target}?{query}" if query else target
            return RedirectResponse(location, status_code=status.HTTP_307_TEMPORARY_REDIRECT)
        return static_compat_redirect

    for legacy_path, target_path in legacy_static_redirects.items():
        route_name = "static_compat_" + legacy_path.removeprefix("/static/").replace("/", "_").replace(".", "_")
        app.add_api_route(
            legacy_path,
            _make_static_compat_redirect(target_path),
            methods=["GET", "HEAD"],
            include_in_schema=False,
            name=route_name,
        )

    def _reject_quarantined_static():
        """明确拒绝已隔离的静态路径；不得回落到 StaticFiles 返回 200。"""
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content={
                "detail": {
                    "code": "STATIC_ASSET_QUARANTINED",
                    "message": "该静态资源已隔离，不提供运行访问",
                }
            },
        )

    for _quarantined_path in sorted(QUARANTINED_STATIC_PATHS):
        app.add_api_route(
            "/static/" + _quarantined_path,
            _reject_quarantined_static,
            methods=["GET", "HEAD"],
            include_in_schema=False,
            name="reject_quarantined_static_" + _quarantined_path.replace("/", "_").replace(".", "_"),
        )

    # 挂载静态文件目录。隔离路径已由上方显式路由拦截，不会进入本挂载。
    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")

    return app


app = create_app()
