"""Immutable artifact-history helpers for derived episode text versions."""
from __future__ import annotations

from collections.abc import Iterable, Sequence

from gw.agent_runtime.models import ArtifactLifecycle, ArtifactRef, ArtifactSourceRef
from pydantic import BaseModel, ConfigDict

from .models import STAGE_ORDER, StageId


class EpisodeArtifactVersion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    stage: StageId
    artifact: ArtifactRef


def _ref_key(artifact_id: str, version_id: str) -> tuple[str, str]:
    return (artifact_id, version_id)


def mark_derived_versions_stale(
    versions: Sequence[EpisodeArtifactVersion],
    changed_sources: Iterable[ArtifactSourceRef | tuple[str, str]],
) -> tuple[EpisodeArtifactVersion, ...]:
    """Mark transitive descendants stale while retaining every immutable version.

    ``changed_sources`` are exact old artifact/version pairs whose semantics have
    changed (for example a replaced full-script version). Each newly-stale
    derived version becomes a source invalidator for its own descendants.
    """
    invalid: set[tuple[str, str]] = set()
    for source in changed_sources:
        if isinstance(source, tuple):
            invalid.add(source)
        else:
            invalid.add((source.artifact_id.root, source.version_id.root))
    stale_keys: set[tuple[str, str]] = set()
    changed = True
    while changed:
        changed = False
        for record in versions:
            ref = record.artifact
            own_key = _ref_key(ref.artifact_id.root, ref.version_id.root)
            if own_key in stale_keys:
                continue
            source_keys = {
                _ref_key(source.artifact_id.root, source.version_id.root)
                for source in ref.source_refs
            }
            if source_keys.intersection(invalid):
                stale_keys.add(own_key)
                invalid.add(own_key)
                changed = True
    result: list[EpisodeArtifactVersion] = []
    for record in versions:
        ref = record.artifact
        own_key = _ref_key(ref.artifact_id.root, ref.version_id.root)
        if own_key in stale_keys and ref.lifecycle is not ArtifactLifecycle.stale:
            ref = ref.model_copy(update={"lifecycle": ArtifactLifecycle.stale})
            record = record.model_copy(update={"artifact": ref})
        result.append(record)
    return tuple(result)


def downstream_stages(stage: StageId) -> tuple[StageId, ...]:
    """Return stages later in the frozen linear text pipeline."""
    index = STAGE_ORDER.index(stage)
    return STAGE_ORDER[index + 1 :]


def artifact_source_refs(artifact: ArtifactRef) -> tuple[tuple[str, str], ...]:
    return tuple((ref.artifact_id.root, ref.version_id.root) for ref in artifact.source_refs)


__all__ = [
    "EpisodeArtifactVersion",
    "artifact_source_refs",
    "downstream_stages",
    "mark_derived_versions_stale",
]
