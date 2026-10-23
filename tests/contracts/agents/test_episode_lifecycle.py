# Copyright 2026 Gods-Workbench Authors

"""Unit tests for episode lifecycle pure functions."""

import datetime

from gw.agent_runtime.models import (
    ArtifactLifecycle,
    ArtifactRef,
    ArtifactSourceRef,
    Identifier,
    Sha256Hex,
    UtcDateTime,
    Version,
)
from gw.agent_episode.lifecycle import (
    EpisodeArtifactVersion,
    artifact_source_refs,
    downstream_stages,
    mark_derived_versions_stale,
)
from gw.agent_episode.models import STAGE_ORDER, StageId


def test_downstream_stages():
    """Test downstream_stages function."""
    assert len(STAGE_ORDER) > 0
    first_stage = STAGE_ORDER[0]
    expected_rest = tuple(STAGE_ORDER[1:])
    assert downstream_stages(first_stage) == expected_rest

    last_stage = STAGE_ORDER[-1]
    assert downstream_stages(last_stage) == ()


def make_artifact_ref(
    artifact_id: str,
    version_id: str,
    source_refs: list[ArtifactSourceRef] | None = None,
    lifecycle: ArtifactLifecycle = ArtifactLifecycle.valid,
) -> ArtifactRef:
    """Helper to create an ArtifactRef for testing."""
    return ArtifactRef(
        schema_version=1,
        artifact_id=Identifier(artifact_id),
        version_id=Identifier(version_id),
        revision=Version(1),
        content_hash=Sha256Hex("a" * 64),
        content_ref=Identifier(f"content-{version_id}"),
        parent_version_ids=[],
        source_refs=source_refs or [],
        lifecycle=lifecycle,
        created_at=UtcDateTime(datetime.datetime.now(datetime.timezone.utc).isoformat()),
    )


def test_artifact_source_refs():
    """Test artifact_source_refs function."""
    ref_empty = make_artifact_ref("a1", "v1")
    assert artifact_source_refs(ref_empty) == ()

    source1 = ArtifactSourceRef(artifact_id=Identifier("s1"), version_id=Identifier("sv1"))
    source2 = ArtifactSourceRef(artifact_id=Identifier("s2"), version_id=Identifier("sv2"))
    ref_with_sources = make_artifact_ref("a2", "v2", source_refs=[source1, source2])

    expected = (("s1", "sv1"), ("s2", "sv2"))
    assert artifact_source_refs(ref_with_sources) == expected


def test_mark_derived_versions_stale():
    """Test mark_derived_versions_stale function."""
    # Create an initial set of versions
    # root1 -> child1 -> grandchild1
    # root2 (independent)

    root1_ref = make_artifact_ref("root1", "v1")
    root2_ref = make_artifact_ref("root2", "v1")

    child1_source = ArtifactSourceRef(artifact_id=Identifier("root1"), version_id=Identifier("v1"))
    child1_ref = make_artifact_ref("child1", "v1", source_refs=[child1_source])

    grandchild1_source = ArtifactSourceRef(artifact_id=Identifier("child1"), version_id=Identifier("v1"))
    grandchild1_ref = make_artifact_ref("grandchild1", "v1", source_refs=[grandchild1_source])

    versions = (
        EpisodeArtifactVersion(stage=STAGE_ORDER[0], artifact=root1_ref),
        EpisodeArtifactVersion(stage=STAGE_ORDER[0], artifact=root2_ref),
        EpisodeArtifactVersion(stage=STAGE_ORDER[1], artifact=child1_ref),
        EpisodeArtifactVersion(stage=STAGE_ORDER[2], artifact=grandchild1_ref),
    )

    # Mark root1 as changed
    changed_sources = [("root1", "v1")]

    result = mark_derived_versions_stale(versions, changed_sources)

    assert len(result) == 4

    # root1 itself should not be marked stale (it was marked as changed source, but its own lifecycle doesn't change unless it is derived from a stale source)
    # Actually wait, let's verify semantics. "changed_sources are exact old artifact/version pairs whose semantics have changed. Each newly-stale derived version becomes a source invalidator for its own descendants."
    # Looking at the code for mark_derived_versions_stale, it adds changed_sources to `invalid` and `stale_keys` is built from things whose sources intersect with `invalid`.
    # Let's check `root1` source_refs: empty. So it won't intersect with `invalid` containing root1. So it shouldn't be stale.

    # Let's map results back to check
    result_map = {r.artifact.artifact_id.root: r.artifact for r in result}

    assert result_map["root1"].lifecycle == ArtifactLifecycle.valid
    assert result_map["root2"].lifecycle == ArtifactLifecycle.valid

    # child1 depends on root1, so it should be stale
    assert result_map["child1"].lifecycle == ArtifactLifecycle.stale

    # grandchild1 depends on child1 (which is stale), so it should be stale transitively
    assert result_map["grandchild1"].lifecycle == ArtifactLifecycle.stale
