"""No-duplicate model invocation boundary with durable ledger and explicit budget."""
from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any, Awaitable, Callable

from gw.agent_runtime.errors import DomainError
from gw.agent_runtime.models import (
    BudgetOutcomeState,
    BudgetReservationRequest,
    BudgetSettlement,
    BudgetState,
    DispatchState,
    ErrorCode,
    Identifier,
    InvocationRecord,
    ModelInvocationRequest,
    ModelInvocationResult,
)
from gw.agent_runtime.ports import BudgetPort, ModelGateway
from gw.agent_runtime.repository import RuntimeRunRepository, utc_now


_GUARD_NOT_SENT = "runtime_guard_not_sent:"
_GATEWAY_NOT_SENT = "gateway_confirmed_not_sent:"


class SafeInvocationDispatcher:
    """Reserve budget, fence one send, persist outcome, and never resend unknown calls.

    A gateway exception is retried only when it explicitly proves ``not_sent``.
    Each retry is separately ledgered and atomically consumes the run's fixed
    technical retry quota. This class makes no provider selection or paid call.
    """

    def __init__(self, repository: RuntimeRunRepository, gateway: ModelGateway, budget: BudgetPort) -> None:
        self.repository = repository
        self.gateway = gateway
        self.budget = budget

    async def invoke(
        self,
        run_id: Identifier,
        request: ModelInvocationRequest,
        *,
        reservation: BudgetReservationRequest | None = None,
        lease: Any,
        max_retries: int = 2,
        before_send: Callable[[], Awaitable[Any]] | None = None,
    ) -> ModelInvocationResult:
        if request.idempotency_key.root == "":
            raise ValueError("A stable idempotency key is required.")
        if not 0 <= max_retries <= 2:
            raise DomainError(ErrorCode.VALIDATION_FAILED, "Configured technical retries must be within the hard limit 0..2.", request_id=request.idempotency_key.root)
        operation_id = request.idempotency_key
        request_fingerprint = hashlib.sha256(request.model_dump_json().encode("utf-8")).hexdigest()
        original = await self.repository.read_invocation(run_id, operation_id)
        if original is not None and original.request_fingerprint.root != request_fingerprint:
            raise DomainError(ErrorCode.IDEMPOTENCY_CONFLICT, "The invocation idempotency key was reused for different request parameters.", request_id=operation_id.root)
        saved_result = await self.repository.read_invocation_operation_result(run_id, operation_id)
        if saved_result is not None:
            if original is None:
                raise DomainError(ErrorCode.OUTCOME_UNKNOWN, "A durable operation result has no matching invocation ledger entry.", request_id=operation_id.root)
            await self._settle_unknown(operation_id, "No approved price conversion is configured; monetary settlement remains unknown.")
            return saved_result
        runtime_state = await self.repository.read_runtime_state(run_id) or {}
        retries = int(runtime_state.get("technical_retries_by_operation", {}).get(operation_id.root, 0))
        if retries > max_retries:
            raise DomainError(ErrorCode.QUOTA_EXCEEDED, "The captured configuration allows fewer technical retries than were already consumed.", request_id=operation_id.root)
        attempt_number = retries
        for prior_index in range(attempt_number + 1):
            prior_request = self._attempt_request(request, prior_index)
            prior = await self.repository.read_invocation(run_id, prior_request.idempotency_key)
            if prior is None:
                continue
            if prior.dispatch_state in {DispatchState.sent, DispatchState.unknown} and prior.result_ref is None:
                raise DomainError(ErrorCode.OUTCOME_UNKNOWN, "A prior attempt has unresolved dispatch state; it will not be sent again.", request_id=prior.invocation_id.root, dispatch_state=prior.dispatch_state)
            if prior.result_ref is not None:
                known_result = await self.repository.read_invocation_result(prior.invocation_id)
                if known_result is not None:
                    await self.repository.record_invocation_operation_result(run_id, operation_id, prior.invocation_id, known_result)
                    await self._settle_unknown(operation_id, "No approved price conversion is configured; monetary settlement remains unknown.")
                    return known_result
        actual_pending = await self.repository.read_invocation(run_id, self._attempt_request(request, attempt_number).idempotency_key)
        not_sent_reason = (
            actual_pending.usage_unavailable_reason.root
            if actual_pending is not None and actual_pending.usage_unavailable_reason is not None
            else ""
        )
        if (
            retries >= max_retries
            and actual_pending is not None
            and actual_pending.dispatch_state is DispatchState.not_sent
            and not_sent_reason.startswith(_GATEWAY_NOT_SENT)
        ):
            raise DomainError(ErrorCode.QUOTA_EXCEEDED, "The configured technical retry slots are exhausted.", request_id=operation_id.root)

        reserve = reservation or BudgetReservationRequest(
            operation_id=operation_id,
            upper_bound_amount=None,
            currency=None,
            unknown_reason="No approved price estimate was supplied to the isolated runtime.",
        )
        if reserve.operation_id.root != operation_id.root:
            raise DomainError(ErrorCode.IDEMPOTENCY_CONFLICT, "Budget reservation operation id must match the model operation.", request_id=operation_id.root)
        decision = await self.budget.reserve(reserve)
        if decision.state is not BudgetState.allowed:
            raise DomainError(ErrorCode.QUOTA_EXCEEDED, "Model dispatch requires an explicit allowed budget decision.", request_id=operation_id.root)
        if decision.amount is None and decision.reason is None:
            raise DomainError(ErrorCode.VALIDATION_FAILED, "Unknown budget decisions require a null amount and reason.", request_id=operation_id.root)
        if decision.amount is not None and decision.currency is None:
            raise DomainError(ErrorCode.VALIDATION_FAILED, "Allowed monetary amounts require a currency.", request_id=operation_id.root)
        if reserve.upper_bound_amount is None and not reserve.unknown_reason:
            raise DomainError(ErrorCode.VALIDATION_FAILED, "Unknown budget bounds must have a reason.", request_id=operation_id.root)

        # Budget reservation may await a remote or mutable authority. Recheck
        # status, permission, source versions, and the lease after that wait.
        if before_send is not None:
            lease = await before_send()

        while True:
            actual_request = self._attempt_request(request, attempt_number)
            fingerprint = hashlib.sha256(actual_request.model_dump_json().encode("utf-8")).hexdigest()
            now = utc_now()
            record = InvocationRecord(
                schema_version=1,
                invocation_id=actual_request.invocation_id,
                run_id=run_id,
                idempotency_key=actual_request.idempotency_key,
                request_fingerprint=fingerprint,
                dispatch_state=DispatchState.sent,
                upstream_request_id=None,
                host_job_id=None,
                result_ref=None,
                usage=None,
                usage_unavailable_reason="Usage will be reconciled from the provider result.",
                created_at=now,
                updated_at=now,
            )
            persisted, should_send, prior_result = await self.repository.begin_invocation(record)
            if prior_result is not None:
                await self.repository.record_invocation_operation_result(run_id, operation_id, persisted.invocation_id, prior_result)
                await self._settle_unknown(operation_id, "Price settlement is not available from token usage alone.")
                return prior_result
            if not should_send:
                raise DomainError(
                    ErrorCode.OUTCOME_UNKNOWN,
                    "This invocation was already sent or its dispatch state is unresolved; it will not be sent again.",
                    request_id=persisted.invocation_id.root,
                    dispatch_state=persisted.dispatch_state,
                )
            # The sent ledger row intentionally precedes the call boundary. If
            # a guard rejects after that await, prove no send occurred so a
            # later safe resume can retry the same durable key.
            if before_send is not None:
                try:
                    lease = await before_send()
                except BaseException:
                    await self.repository.mark_invocation_not_sent(
                        actual_request.invocation_id,
                        _GUARD_NOT_SENT + " authorization or fencing guard rejected before the gateway call.",
                    )
                    raise
            try:
                result = await self.gateway.invoke(actual_request)
            except DomainError as exc:
                if exc.dispatch_state is DispatchState.not_sent and exc.retryable:
                    await self.repository.mark_invocation_not_sent(
                        actual_request.invocation_id,
                        _GATEWAY_NOT_SENT + " the adapter confirmed that no upstream request was sent.",
                    )
                    if retries >= max_retries:
                        raise DomainError(ErrorCode.QUOTA_EXCEEDED, "The configured technical retry slots are exhausted.", request_id=operation_id.root) from exc
                    retry_reservation = Identifier(f"retry:{operation_id.root}:{retries + 1}")
                    await self.repository.reserve_retry(
                        run_id, lease, operation_id, retry_reservation, kind="technical", limit=2,
                    )
                    retries += 1
                    attempt_number += 1
                    continue
                await self.repository.mark_invocation_unknown(actual_request.invocation_id, "The provider outcome is not proven; operator reconciliation is required.")
                await self._settle_unknown(operation_id, "Provider outcome is unknown; no amount was treated as zero.")
                raise DomainError(
                    ErrorCode.OUTCOME_UNKNOWN,
                    "Provider outcome is unresolved; runtime paused before any retry.",
                    request_id=actual_request.invocation_id.root,
                    dispatch_state=DispatchState.unknown,
                ) from exc
            except Exception as exc:
                await self.repository.mark_invocation_unknown(actual_request.invocation_id, "The provider outcome is not proven; operator reconciliation is required.")
                await self._settle_unknown(operation_id, "Provider outcome is unknown; no amount was treated as zero.")
                raise DomainError(
                    ErrorCode.OUTCOME_UNKNOWN,
                    "Provider outcome is unresolved; runtime paused before any retry.",
                    request_id=actual_request.invocation_id.root,
                    dispatch_state=DispatchState.unknown,
                ) from exc
            if result.usage is None and result.usage_unavailable_reason is None:
                # The call returned; retain the response with explicit unknown-usage evidence before rejecting it.
                await self.repository.record_invocation_result(actual_request.invocation_id, result.model_copy(update={
                    "usage_unavailable_reason": "Provider returned no usage details.",
                }))
                raise DomainError(ErrorCode.VALIDATION_FAILED, "Provider result omitted required unknown-usage reason.", request_id=actual_request.invocation_id.root)
            await self.repository.record_invocation_result(actual_request.invocation_id, result)
            await self.repository.record_invocation_operation_result(run_id, operation_id, actual_request.invocation_id, result)
            # Settlement may be retried idempotently later; it must not make a known result look unknown.
            await self._settle_unknown(operation_id, "No approved price conversion is configured; monetary settlement remains unknown.")
            return result

    async def reserve_format_repair(self, run_id: Identifier, operation_id: Identifier, repair_id: Identifier, *, lease: Any) -> None:
        """Consume the single persisted format-repair slot before any repair dispatch."""
        await self.repository.reserve_retry(run_id, lease, operation_id, repair_id, kind="format", limit=1)

    async def _settle_unknown(self, operation_id: Identifier, reason: str) -> None:
        await self.budget.settle(BudgetSettlement(
            operation_id=operation_id,
            outcome_state=BudgetOutcomeState.unknown,
            actual_amount=None,
            currency=None,
            unknown_reason=reason,
        ))

    @staticmethod
    def _attempt_request(request: ModelInvocationRequest, retry_index: int) -> ModelInvocationRequest:
        if retry_index == 0:
            return request
        suffix = f":retry:{retry_index}"
        return request.model_copy(update={
            "invocation_id": Identifier(request.invocation_id.root + suffix),
            "idempotency_key": Identifier(request.idempotency_key.root + suffix),
        })
