"""C14-T1 控时长 I2V 契约与时序适配测试。"""

from __future__ import annotations

import math

import pytest

from gw.core.errors import CleanroomException
from gw.media.duration_fitting import (
    DEFAULT_DURATION_TOLERANCE_SECONDS,
    build_i2v_request,
    build_media_fragment,
    calculate_speed_ratio,
    fit_video_duration,
    validate_duration_match,
)


FIXTURE_ID = "c14-t1-duration-fitting-001"


def test_c14_ac1_builds_i2v_request_with_target_duration_and_camera_lora_metadata():
    result = build_i2v_request(
        shot_id="SC01_SH001",
        image_asset_id="asset-storyboard-001",
        target_duration=4.2,
        prompt="角色走向窗边",
        camera_ctrl={
            "trajectory": [
                {"at": 0.0, "x": 0.0, "y": 0.0, "z": 0.0},
                {"at": 1.0, "x": 0.2, "y": 0.0, "z": 0.0},
            ]
        },
        lora=[{"name": "character-main", "weight": 0.75}],
        fixture_id=FIXTURE_ID,
    )

    assert result == {
        "operation": "i2v",
        "shot_id": "SC01_SH001",
        "image_asset_id": "asset-storyboard-001",
        "target_duration": 4.2,
        "prompt": "角色走向窗边",
        "camera_ctrl": {
            "trajectory": [
                {"at": 0.0, "x": 0.0, "y": 0.0, "z": 0.0},
                {"at": 1.0, "x": 0.2, "y": 0.0, "z": 0.0},
            ]
        },
        "lora": [{"name": "character-main", "weight": 0.75}],
        "fixture_id": FIXTURE_ID,
    }


def test_c14_ac1_calculates_speed_ratio_and_fits_generated_duration():
    result = fit_video_duration(source_duration=5.0, target_duration=4.0)

    assert result["source_duration"] == 5.0
    assert result["target_duration"] == 4.0
    assert result["speed_ratio"] == pytest.approx(0.8)
    assert result["setpts_factor"] == pytest.approx(0.8)
    assert result["playback_rate"] == pytest.approx(1.25)
    assert result["fitted_duration"] == pytest.approx(4.0)
    assert result["duration_error"] == pytest.approx(0.0)
    assert result["trim_start"] == 0.0
    assert result["trim_end"] == 4.0


def test_c14_ac1_builds_lossless_media_fragment_with_target_end():
    assert build_media_fragment("/media/take.mp4", 4.2) == "/media/take.mp4#t=0,4.2"
    assert build_media_fragment("/media/take.mp4#old", 4.2) == "/media/take.mp4#t=0,4.2"
    assert build_media_fragment("https://example.test/take.mp4?fixture=001", 4.2) == (
        "https://example.test/take.mp4?fixture=001#t=0,4.2"
    )


def test_c14_ac1_accepts_short_source_by_slowing_without_changing_target():
    result = fit_video_duration(source_duration=3.0, target_duration=4.2)

    assert result["speed_ratio"] == pytest.approx(1.4)
    assert result["playback_rate"] == pytest.approx(3.0 / 4.2)
    assert result["fitted_duration"] == pytest.approx(4.2)
    assert result["media_fragment"] == "#t=0,4.2"


def test_c14_ac1_validates_actual_duration_with_default_tolerance():
    result = validate_duration_match(actual_duration=4.18, target_duration=4.2)

    assert result["target_duration"] == 4.2
    assert result["actual_duration"] == 4.18
    assert result["error_seconds"] == pytest.approx(0.02)
    assert result["tolerance_seconds"] == DEFAULT_DURATION_TOLERANCE_SECONDS
    assert result["within_tolerance"] is True


def test_c14_ac1_rejects_duration_outside_tolerance():
    with pytest.raises(CleanroomException) as exc:
        validate_duration_match(actual_duration=4.31, target_duration=4.2, tolerance=0.1)

    assert exc.value.code == "DURATION_TOLERANCE_EXCEEDED"
    assert exc.value.status_code == 422
    assert exc.value.extra == {
        "actual_duration": 4.31,
        "target_duration": 4.2,
        "error_seconds": pytest.approx(0.11),
        "tolerance_seconds": 0.1,
    }


@pytest.mark.parametrize(
    "source_duration,target_duration",
    [
        (0, 4.2),
        (-1, 4.2),
        (3.0, 0),
        (3.0, -1),
        (3.0, math.nan),
        (3.0, math.inf),
    ],
)
def test_c14_ac1_rejects_non_positive_or_non_finite_duration(source_duration, target_duration):
    with pytest.raises(CleanroomException) as exc:
        fit_video_duration(source_duration=source_duration, target_duration=target_duration)

    assert exc.value.code in {"INVALID_SOURCE_DURATION", "INVALID_TARGET_DURATION"}


def test_c14_ac1_rejects_target_duration_above_video_task_limit():
    with pytest.raises(CleanroomException) as exc:
        build_i2v_request(
            shot_id="SC01_SH001",
            image_asset_id="asset-storyboard-001",
            target_duration=120.1,
            prompt="镜头",
        )

    assert exc.value.code == "INVALID_TARGET_DURATION"


def test_c14_ac1_rejects_invalid_camera_trajectory_and_lora_weight():
    with pytest.raises(CleanroomException) as camera_error:
        build_i2v_request(
            shot_id="SC01_SH001",
            image_asset_id="asset-storyboard-001",
            target_duration=4.2,
            prompt="镜头",
            camera_ctrl={"trajectory": [{"at": -1.0, "x": 0.0}]},
        )
    assert camera_error.value.code == "INVALID_CAMERA_CTRL"

    with pytest.raises(CleanroomException) as lora_error:
        build_i2v_request(
            shot_id="SC01_SH001",
            image_asset_id="asset-storyboard-001",
            target_duration=4.2,
            prompt="镜头",
            lora=[{"name": "character-main", "weight": math.inf}],
        )
    assert lora_error.value.code == "INVALID_LORA"


def test_c14_ac1_calculate_speed_ratio_is_stable_and_requires_positive_values():
    assert calculate_speed_ratio(3.0, 4.2) == pytest.approx(1.4)

    with pytest.raises(CleanroomException) as exc:
        calculate_speed_ratio(0, 4.2)

    assert exc.value.code == "INVALID_SOURCE_DURATION"
