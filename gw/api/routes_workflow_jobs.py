"""C14-T2 视频生成任务路由。

路由工厂接收已配置的执行器，避免在 API 层读取凭据或猜测供应商地址。
宿主可以把 ``create_video_jobs_router(executor)`` 挂载到现有工作流 API 边界。
"""
from __future__ import annotations

from typing import Any

from gw.core.errors import CleanroomException
from gw.god_workflow.video_executor import VideoJobExecutor


def _http_error(error: CleanroomException) -> Exception:
    """创建 FastAPI 错误对象；延迟导入保持本模块可被纯契约测试使用。"""
    from importlib import import_module

    http_exception = getattr(import_module("fastapi"), "HTTPException")
    return http_exception(status_code=error.status_code, detail=error.to_envelope().model_dump()["detail"])


def create_video_jobs_router(executor: VideoJobExecutor, *, prefix: str = "/api/god_workflow/video-jobs") -> Any:
    """创建异步视频任务 API 路由。

    ``POST /`` 提交，``GET /{job_id}`` 轮询，``POST /{job_id}/cancel`` 取消。
    """
    from importlib import import_module

    router_class = getattr(import_module("fastapi"), "APIRouter")
    router = router_class(prefix=prefix, tags=["workflow-video-jobs"])

    async def submit_video_job(body: dict[str, Any]) -> dict[str, Any]:
        try:
            return await executor.submit(**body)
        except CleanroomException as error:
            raise _http_error(error) from error
        except (TypeError, ValueError) as error:
            http_exception = getattr(import_module("fastapi"), "HTTPException")
            raise http_exception(status_code=400, detail={"code": "INVALID_REQUEST", "message": str(error)}) from error

    async def poll_video_job(job_id: str) -> dict[str, Any]:
        try:
            return await executor.poll(job_id)
        except CleanroomException as error:
            raise _http_error(error) from error

    async def cancel_video_job(job_id: str) -> dict[str, Any]:
        try:
            return await executor.cancel(job_id)
        except CleanroomException as error:
            raise _http_error(error) from error

    add_route = getattr(router, "add_api_route")
    add_route("", submit_video_job, methods=["POST"], status_code=202)
    add_route("/{job_id}", poll_video_job, methods=["GET"])
    add_route("/{job_id}/cancel", cancel_video_job, methods=["POST"])
    return router


__all__ = ["create_video_jobs_router"]
