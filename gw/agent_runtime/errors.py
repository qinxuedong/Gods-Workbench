"""Stable framework-neutral domain errors and the single HTTP detail envelope."""
from __future__ import annotations

from typing import Any

from .models import DispatchState, ErrorCode, ErrorDetail, ErrorEnvelope


_HTTP_STATUS: dict[ErrorCode, int] = {
    ErrorCode.CONTRACT_VERSION_UNSUPPORTED: 422,
    ErrorCode.CAPABILITY_UNSUPPORTED: 422,
    ErrorCode.NOT_AUTHORIZED: 403,
    ErrorCode.PROJECT_UNAVAILABLE: 404,
    ErrorCode.REVISION_CONFLICT: 409,
    ErrorCode.IDEMPOTENCY_CONFLICT: 409,
    ErrorCode.VALIDATION_FAILED: 422,
    ErrorCode.QUOTA_EXCEEDED: 429,
    ErrorCode.UPSTREAM_UNAVAILABLE: 503,
    ErrorCode.OUTCOME_UNKNOWN: 503,
    ErrorCode.ARTIFACT_STALE: 409,
    ErrorCode.MANUAL_REVIEW_REQUIRED: 409,
}


class DomainError(Exception):
    """A sanitized domain failure with a deterministic transport mapping.

    Callers must supply a safe public message; raw provider exceptions, prompts,
    credentials, and private paths must not be forwarded in the envelope.
    """

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        request_id: str,
        retryable: bool = False,
        retry_after: float | None = None,
        dispatch_state: DispatchState | None = None,
    ) -> None:
        if not message or not request_id:
            raise ValueError("message and request_id must be non-empty")
        super().__init__(message)
        self.code = code
        self.message = message
        self.request_id = request_id
        self.retryable = retryable
        self.retry_after = retry_after
        self.dispatch_state = dispatch_state

    @property
    def http_status(self) -> int:
        return _HTTP_STATUS[self.code]

    def as_envelope(self) -> dict[str, Any]:
        """Return the only supported HTTP error shape: ``{\"detail\": ...}``."""
        detail = ErrorDetail(
            code=self.code,
            message=self.message,
            retryable=self.retryable,
            retry_after=self.retry_after,
            dispatch_state=self.dispatch_state,
            request_id=self.request_id,
        )
        return ErrorEnvelope(detail=detail).model_dump(mode="json")


def revision_conflict(*, request_id: str) -> DomainError:
    """Build a 409 for a stale public ``expected_version``."""
    return DomainError(
        ErrorCode.REVISION_CONFLICT,
        "The run version changed; reload state before deciding again.",
        request_id=request_id,
    )
