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

"""核心错误模型与异常体系。

严格对齐 docs/contracts 及 docs/fixtures 中的 401、403、409 契约规范与黄金夹具。
"""

from typing import Any, Dict, Optional
from pydantic import BaseModel, Field


class ErrorDetail(BaseModel):
    """通用错误详情载荷。"""

    code: str = Field(..., description="错误标识码")
    message: str = Field(..., description="错误可读提示")
    expected_version: Optional[int] = Field(None, description="期望的 CAS 版本")
    current_version: Optional[int] = Field(None, description="当前实际的 CAS 版本")
    canvas_id: Optional[str] = Field(None, description="冲突的画布标识")
    endpoint: Optional[str] = Field(None, description="产生错误的接口路径")
    unavailable: Optional[bool] = Field(None, description="能力当前不可用或未准入")
    data_status: Optional[str] = Field(None, description="数据可用性状态")
    returncode: Optional[int] = Field(None, description="已退出CLI的真实退出码")
    result_unknown: Optional[bool] = Field(None, description="外部操作结果尚未确认，禁止自动重试")


class ErrorEnvelope(BaseModel):
    """统一外层错误包装体。"""

    detail: ErrorDetail


class CleanroomException(Exception):
    """洁净室顶层应用异常。"""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        extra: Optional[Dict[str, Any]] = None,
        expose_extra_fields: Optional[set[str]] = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.extra = extra or {}
        # 允许某些新模块显式暴露受控错误元数据；默认保持既有夹具行为。
        self.expose_extra_fields = expose_extra_fields

    def to_envelope(self) -> ErrorEnvelope:
        # 默认只保留既有 CAS/画布冲突字段，维持冻结黄金夹具的错误形状；
        # 新模块必须显式声明允许外露的元数据，避免未知字段污染既有契约。
        allowed = {"expected_version", "current_version", "canvas_id"}
        if self.expose_extra_fields is not None:
            allowed = self.expose_extra_fields
        exposed = {key: value for key, value in self.extra.items() if key in allowed}
        payload = {
            "code": self.code,
            "message": self.message,
            **exposed,
        }
        return ErrorEnvelope(detail=ErrorDetail(**payload))


class UnauthorizedException(CleanroomException):
    """401 未认证异常。"""

    def __init__(self, message: str = "会话失效，请重新登录"):
        super().__init__(status_code=401, code="UNAUTHORIZED", message=message)


class ForbiddenException(CleanroomException):
    """403 权限不足异常。"""

    def __init__(self, message: str = "无当前项目写权限"):
        super().__init__(status_code=403, code="FORBIDDEN", message=message)


class VersionConflictException(CleanroomException):
    """409 项目版本冲突异常。"""

    def __init__(self, expected_version: int, current_version: int, message: str = "expected_version 与当前版本不一致"):
        super().__init__(
            status_code=409,
            code="VERSION_CONFLICT",
            message=message,
            extra={"expected_version": expected_version, "current_version": current_version},
        )


class CanvasVersionConflictException(CleanroomException):
    """409 画布版本冲突异常。"""

    def __init__(
        self,
        expected_version: int,
        current_version: int,
        canvas_id: str,
        message: str = "画布版本冲突，请刷新后重试",
    ):
        super().__init__(
            status_code=409,
            code="CANVAS_VERSION_CONFLICT",
            message=message,
            extra={
                "expected_version": expected_version,
                "current_version": current_version,
                "canvas_id": canvas_id,
            },
        )
