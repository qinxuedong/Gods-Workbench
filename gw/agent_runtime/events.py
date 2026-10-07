"""Sanitized, reference-rich run event construction helpers."""
from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from gw.agent_runtime.models import Identifier, RunEvent, StageId
from gw.agent_runtime.repository import utc_now


def make_event(
    run_id: Identifier,
    version: int,
    event_type: str,
    summary: str,
    *,
    stage: StageId | None = None,
    stage_attempt_id: Identifier | None = None,
    artifact_version_ids: Sequence[Identifier] = (),
    review_id: Identifier | None = None,
    approval_id: Identifier | None = None,
) -> RunEvent:
    """Construct a public-safe event; the repository allocates authoritative sequence/version."""
    if len(summary) > 512:
        summary = summary[:509] + "..."
    return RunEvent(
        schema_version=1,
        event_id=uuid.uuid4().hex,
        run_id=run_id,
        event_seq=1,
        version=max(0, version),
        event_type=event_type,
        stage=stage,
        stage_attempt_id=stage_attempt_id,
        artifact_version_ids=list(artifact_version_ids),
        review_id=review_id,
        approval_id=approval_id,
        occurred_at=utc_now(),
        summary=summary,
    )
