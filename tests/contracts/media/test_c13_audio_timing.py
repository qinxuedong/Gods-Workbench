"""C13-AC1 对白定长与 Auto-Timing 契约测试。"""

from __future__ import annotations

import pytest

from gw.core.errors import CleanroomException
from gw.episode_pipeline.repository import TimelineManifest
from gw.media.audio_timing import (
    DEFAULT_PAD_SECONDS,
    apply_auto_timing_to_manifest,
    calculate_shot_duration,
    calculate_timing,
    extract_audio_duration,
    normalize_word_timestamps,
)

FIXTURE_ID = "c13-t1-audio-timing-001"


def _manifest() -> dict:
    return {
        "manifest_id": "timeline-0001",
        "pipeline_id": "ep-c13",
        "project_id": "proj-c13",
        "version": 4,
        "tracks": [
            {
                "track_id": "video-01",
                "kind": "video",
                "clips": [
                    {
                        "shot_id": "SC01_SH001",
                        "asset_id": "asset-shot-001",
                        "at": 0.0,
                        "duration": 2.0,
                    },
                    {
                        "shot_id": "SC01_SH002",
                        "asset_id": "asset-shot-002",
                        "at": 2.0,
                        "duration": 3.0,
                    },
                ],
            }
        ],
    }


def test_c13_ac1_extracts_word_timestamps_and_computes_dialogue_duration():
    words = normalize_word_timestamps(
        [
            {"word": "你好", "start": 0.0, "end": 0.42},
            {"text": "世界", "start_ms": 520, "end_ms": 1_180},
        ]
    )

    assert words == [
        {"word": "你好", "start": 0.0, "end": 0.42},
        {"word": "世界", "start": 0.52, "end": 1.18},
    ]
    assert extract_audio_duration({"duration_ms": 1_500}) == 1.5
    assert calculate_shot_duration(1.5, pad=0.25) == 1.75
    assert calculate_shot_duration(1.5) == 1.5 + DEFAULT_PAD_SECONDS


def test_c13_ac1_builds_stable_timing_record_with_fixture_id():
    result = calculate_timing(
        shot_id="SC01_SH001",
        audio_asset_id="asset-vo-001",
        audio_duration=1.5,
        pad=0.25,
        word_timestamps=[{"word": "你好", "start": 0.0, "end": 1.2}],
        fixture_id=FIXTURE_ID,
    )

    assert result == {
        "shot_id": "SC01_SH001",
        "audio_asset_id": "asset-vo-001",
        "audio_duration": 1.5,
        "pad": 0.25,
        "target_duration": 1.75,
        "duration": 1.75,
        "word_timestamps": [{"word": "你好", "start": 0.0, "end": 1.2}],
        "fixture_id": FIXTURE_ID,
    }


def test_c13_ac1_applies_timing_with_manifest_cas_and_reflows_video_track():
    original = _manifest()
    result = apply_auto_timing_to_manifest(
        original,
        [
            calculate_timing(
                shot_id="SC01_SH001",
                audio_asset_id="asset-vo-001",
                audio_duration=2.5,
                pad=0.25,
                word_timestamps=[{"word": "对白", "start": 0.0, "end": 2.2}],
                fixture_id=FIXTURE_ID,
            )
        ],
        expected_version=4,
    )

    assert original == _manifest()
    assert result["version"] == 5
    clips = result["tracks"][0]["clips"]
    assert clips[0]["duration"] == 2.75
    assert clips[0]["target_duration"] == 2.75
    assert clips[1]["at"] == 2.75
    assert len(result["tracks"]) == 1
    assert clips[0]["audio_asset_id"] == "asset-vo-001"
    assert result["metadata"]["auto_timing"]["SC01_SH001"]["fixture_id"] == FIXTURE_ID


def test_c13_ac1_reuses_audio_track_across_successive_cas_updates():
    first = apply_auto_timing_to_manifest(
        _manifest(),
        [
            calculate_timing(
                shot_id="SC01_SH001",
                audio_asset_id="asset-vo-001",
                audio_duration=1.5,
                word_timestamps=[{"word": "一", "start": 0.0, "end": 1.2}],
            )
        ],
        expected_version=4,
    )

    second = apply_auto_timing_to_manifest(
        first,
        [
            calculate_timing(
                shot_id="SC01_SH002",
                audio_asset_id="asset-vo-002",
                audio_duration=2.0,
                word_timestamps=[{"word": "二", "start": 0.0, "end": 1.8}],
            )
        ],
        expected_version=5,
    )

    assert second["version"] == 6
    assert [track["kind"] for track in second["tracks"]] == ["video"]
    assert second["tracks"][0]["clips"][0]["audio_asset_id"] == "asset-vo-001"
    assert second["tracks"][0]["clips"][1]["audio_asset_id"] == "asset-vo-002"
    assert second["tracks"][0]["clips"][1]["at"] == 1.7


def test_c13_ac1_validates_direct_timing_record_timestamps_against_audio_duration():
    with pytest.raises(CleanroomException) as exc:
        apply_auto_timing_to_manifest(
            _manifest(),
            [
                {
                    "shot_id": "SC01_SH001",
                    "audio_duration": 1.0,
                    "target_duration": 1.2,
                    "word_timestamps": [{"word": "超出", "start": 0.0, "end": 1.1}],
                }
            ],
            expected_version=4,
        )

    assert exc.value.code == "INVALID_AUDIO_TIMESTAMPS"


def test_c13_ac1_can_update_duration_without_reflowing_existing_offsets():
    original = _manifest()
    original["tracks"][0]["clips"][0]["at"] = 4.0
    original["tracks"][0]["clips"][1]["at"] = 9.0
    result = apply_auto_timing_to_manifest(
        original,
        [
            calculate_timing(
                shot_id="SC01_SH001",
                audio_duration=2.5,
                word_timestamps=[{"word": "对白", "start": 0.0, "end": 2.2}],
            )
        ],
        expected_version=4,
        reflow=False,
    )

    assert result["tracks"][0]["clips"][0]["duration"] == 2.7
    assert result["tracks"][0]["clips"][0]["at"] == 4.0
    assert result["tracks"][0]["clips"][1]["at"] == 9.0


def test_c13_ac1_result_remains_persistable_by_timeline_manifest_contract():
    result = apply_auto_timing_to_manifest(
        _manifest(),
        [
            calculate_timing(
                shot_id="SC01_SH001",
                audio_asset_id="asset-vo-001",
                audio_duration=1.5,
                word_timestamps=[{"word": "一", "start": 0.0, "end": 1.2}],
                fixture_id=FIXTURE_ID,
            )
        ],
        expected_version=4,
    )

    persisted = TimelineManifest.model_validate(result)
    assert persisted.version == 5
    assert persisted.tracks[0].clips[0].duration == 1.7
    assert persisted.tracks[0].clips[0].audio_asset_id == "asset-vo-001"


def test_c13_ac1_rejects_missing_or_stale_cas_without_mutating_manifest():
    original = _manifest()
    timing = calculate_timing(
        shot_id="SC01_SH001",
        audio_duration=1.0,
        pad=0.2,
        word_timestamps=[{"word": "a", "start": 0.0, "end": 0.8}],
    )

    with pytest.raises(CleanroomException) as missing:
        apply_auto_timing_to_manifest(original, [timing])
    assert missing.value.status_code == 400
    assert missing.value.code == "EXPECTED_VERSION_REQUIRED"

    with pytest.raises(CleanroomException) as stale:
        apply_auto_timing_to_manifest(original, [timing], expected_version=3)
    assert stale.value.status_code == 409
    assert stale.value.code == "VERSION_CONFLICT"
    assert stale.value.extra == {"expected_version": 3, "current_version": 4}
    assert original == _manifest()


@pytest.mark.parametrize(
    "timestamps",
    [
        [],
        [{"word": "x", "start": 0.4, "end": 0.2}],
        [
            {"word": "x", "start": 0.0, "end": 0.2},
            {"word": "y", "start": 0.1, "end": 0.3},
        ],
    ],
)
def test_c13_ac1_rejects_invalid_word_timestamps(timestamps):
    with pytest.raises(CleanroomException) as exc:
        normalize_word_timestamps(timestamps)
    assert exc.value.status_code == 400
    assert exc.value.code == "INVALID_AUDIO_TIMESTAMPS"


def test_c13_ac1_rejects_unknown_shots_and_invalid_audio_duration():
    with pytest.raises(CleanroomException) as unknown:
        apply_auto_timing_to_manifest(
            _manifest(),
            [
                calculate_timing(
                    shot_id="SC99_SH999",
                    audio_duration=1.0,
                    word_timestamps=[{"word": "x", "start": 0.0, "end": 0.8}],
                )
            ],
            expected_version=4,
        )
    assert unknown.value.code == "SHOT_NOT_FOUND"

    with pytest.raises(CleanroomException) as invalid:
        calculate_timing(
            shot_id="SC01_SH001",
            audio_duration=0,
            word_timestamps=[{"word": "x", "start": 0.0, "end": 0.8}],
        )
    assert invalid.value.code == "INVALID_AUDIO_DURATION"
