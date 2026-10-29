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

"""FCPXML/EDL 时间线导出纯序列化器契约测试。"""

from pathlib import Path

import pytest

from gw.core.errors import CleanroomException
from gw.media.nle_export import export_edl, export_fcpxml, seconds_to_timecode

SAMPLE_TIMELINE = {
    "manifest_id": "timeline-0001",
    "pipeline_id": "ep-0001",
    "version": 1,
    "tracks": [
        {
            "track_id": "trk-v1",
            "kind": "video",
            "clips": [
                {
                    "clip_id": "clip-v1",
                    "asset_id": "asset-video-01",
                    "name": "Scene 1 Shot A",
                    "at": 0.0,
                    "duration": 5.0,
                    "in_point": 0.0,
                },
                {
                    "clip_id": "clip-v2",
                    "asset_id": "asset-video-02",
                    "name": "Scene 1 Shot B",
                    "at": 5.0,
                    "duration": 4.5,
                    "in_point": 1.0,
                },
            ],
        },
        {
            "track_id": "trk-a1",
            "kind": "audio",
            "clips": [
                {
                    "clip_id": "clip-a1",
                    "asset_id": "asset-audio-01",
                    "name": "BGM Track",
                    "at": 0.0,
                    "duration": 9.5,
                    "in_point": 0.0,
                }
            ],
        },
    ],
}


def test_ac1_golden_fixture_byte_exactness():
    """AC1: 黄金夹具输出逐字节一致。"""
    edl_path = Path("docs/fixtures/nle-export-sample-edl.edl")
    fcpxml_path = Path("docs/fixtures/nle-export-sample-fcpxml.fcpxml")

    assert edl_path.exists()
    assert fcpxml_path.exists()

    expected_edl = edl_path.read_text(encoding="utf-8")
    expected_fcpxml = fcpxml_path.read_text(encoding="utf-8")

    actual_edl = export_edl(SAMPLE_TIMELINE, title="TEST_TIMELINE", fps=24.0)
    actual_fcpxml = export_fcpxml(SAMPLE_TIMELINE, title="TEST_TIMELINE", fps=24.0)

    assert actual_edl == expected_edl
    assert actual_fcpxml == expected_fcpxml


def test_ac2_frame_rate_timecode_accuracy():
    """AC2: 帧率 23.976/25/29.97 时间码正确。"""
    # 23.976 fps
    assert seconds_to_timecode(0.0, fps=23.976) == "00:00:00:00"
    assert seconds_to_timecode(1.0, fps=23.976) == "00:00:01:00"
    assert seconds_to_timecode(10.0, fps=23.976) == "00:00:10:00"

    # 25 fps
    assert seconds_to_timecode(0.0, fps=25.0) == "00:00:00:00"
    assert seconds_to_timecode(1.0, fps=25.0) == "00:00:01:00"
    assert seconds_to_timecode(1.04, fps=25.0) == "00:00:01:01"
    assert seconds_to_timecode(3661.0, fps=25.0) == "01:01:01:00"

    # 29.97 NDF (Non-Drop Frame)
    assert seconds_to_timecode(0.0, fps=29.97, drop_frame=False) == "00:00:00:00"
    assert seconds_to_timecode(1.0, fps=29.97, drop_frame=False) == "00:00:01:00"
    assert seconds_to_timecode(60.0, fps=29.97, drop_frame=False) == "00:01:00:00"

    # 29.97 DF (Drop Frame)
    assert seconds_to_timecode(0.0, fps=29.97, drop_frame=True) == "00:00:00;00"
    # At 1 minute (minute 1), frame numbers 0 and 1 are dropped in DF timecode rendering
    tc_1m_df = seconds_to_timecode(60.0, fps=29.97, drop_frame=True)
    assert tc_1m_df == "00:01:00;02"


def test_ac3_invalid_input_error_handling():
    """AC3: 非法输入报错不崩。"""
    # Non-dict timeline
    with pytest.raises(CleanroomException) as exc_info:
        export_edl("invalid_string")  # type: ignore[arg-type]
    assert exc_info.value.status_code == 400
    assert exc_info.value.code == "INVALID_TIMELINE_MANIFEST"

    # Missing or non-list tracks
    with pytest.raises(CleanroomException) as exc_info:
        export_fcpxml({"tracks": "not_a_list"})
    assert exc_info.value.status_code == 400
    assert exc_info.value.code == "INVALID_TIMELINE_MANIFEST"

    # Negative duration in clip
    bad_clip_timeline = {
        "tracks": [
            {
                "kind": "video",
                "clips": [
                    {
                        "asset_id": "asset-1",
                        "at": 0.0,
                        "duration": -1.0,
                    }
                ],
            }
        ]
    }
    with pytest.raises(CleanroomException) as exc_info:
        export_edl(bad_clip_timeline)
    assert exc_info.value.status_code == 400
    assert exc_info.value.code == "INVALID_TIMELINE_MANIFEST"

    # Invalid fps
    with pytest.raises(CleanroomException) as exc_info:
        export_edl(SAMPLE_TIMELINE, fps=0.0)
    assert exc_info.value.status_code == 400

    # Incompatible drop_frame fps
    with pytest.raises(CleanroomException) as exc_info:
        seconds_to_timecode(10.0, fps=25.0, drop_frame=True)
    assert exc_info.value.status_code == 400
    assert exc_info.value.code == "INVALID_FRAME_RATE"
