"""C12-T2 Agent 分场分镜结构化拆解契约。"""

from __future__ import annotations

import json

import pytest

from gw.agent_episode.exports import (
    BREAKDOWN_TEMPLATE_ID,
    BREAKDOWN_TEMPLATE_VERSION,
    AgentBreakdownOutput,
    BreakdownCategory,
    ExportRejected,
    export_agent_breakdown,
    parse_agent_breakdown,
    structured_breakdown_schema,
)
from gw.agent_runtime.templates import BREAKDOWN_TEMPLATE


def _element(element_id: str, name: str) -> dict[str, object]:
    return {
        "element_id": element_id,
        "name": name,
        "description": f"{name} 的可执行制片描述",
    }


def _scene() -> dict[str, object]:
    return {
        "scene_id": "SC01",
        "title": "档案室夜戏",
        "slugline": "INT. ARCHIVE ROOM - NIGHT",
        "setting_type": "INT",
        "lighting_phase": "NIGHT",
        "weather": "CLEAR",
        "location": "archive_room",
        "elements": {
            "01_CAST": [_element("cast_0001", "林岚")],
            "02_EXTRAS": [],
            "03_PROPS": [_element("prop_0001", "档案盒")],
            "04_WARDROBE": [_element("wardrobe_0001", "深色风衣")],
            "05_VFX": [],
            "06_SFX": [],
            "07_LOCATION": [_element("location_0001", "档案室")],
            "08_VEHICLES": [],
        },
        "shots": [
            {
                "shot_id": "SC01_SH001",
                "scene_id": "SC01",
                "description": "林岚打开档案盒并确认标签",
                "metadata": {
                    "shot_size": "MCU",
                    "camera_angle": "EYE_LEVEL",
                    "camera_movement": "DOLLY_IN",
                    "focal_length": "STANDARD",
                    "composition": "RULE_OF_THIRDS",
                },
                "element_ids": ["cast_0001", "prop_0001"],
                "character_ids": ["cast_0001"],
                "duration_seconds": 3.5,
            }
        ],
    }


def _document() -> dict[str, object]:
    return {
        "schema_version": 1,
        "template_id": BREAKDOWN_TEMPLATE_ID,
        "template_version": BREAKDOWN_TEMPLATE_VERSION,
        "source_full_script_version_id": "full_script_v001",
        "source_scene_script_version_id": "scene_script_v001",
        "scenes": [_scene()],
    }


def test_template_and_schema_declare_eight_breakdown_categories_and_asc_fields():
    category_values = {item.value for item in BreakdownCategory}
    assert category_values == {
        "01_CAST",
        "02_EXTRAS",
        "03_PROPS",
        "04_WARDROBE",
        "05_VFX",
        "06_SFX",
        "07_LOCATION",
        "08_VEHICLES",
    }
    assert BREAKDOWN_TEMPLATE["template_id"] == BREAKDOWN_TEMPLATE_ID
    assert set(BREAKDOWN_TEMPLATE["required_categories"]) == category_values

    schema = structured_breakdown_schema()
    shot_schema = schema["$defs"]["CinematographyMetadata"]
    assert set(shot_schema["required"]) >= {
        "shot_size",
        "camera_angle",
        "camera_movement",
        "focal_length",
    }
    assert "MCU" in schema["$defs"]["ShotSize"]["enum"]


def test_agent_breakdown_round_trip_is_machine_readable_json():
    document = parse_agent_breakdown(_document())
    assert isinstance(document, AgentBreakdownOutput)
    assert document.scenes[0].shots[0].metadata.camera_movement.value == "DOLLY_IN"

    exported = export_agent_breakdown(document)
    decoded = json.loads(exported)
    assert decoded["schema_version"] == 1
    assert decoded["scenes"][0]["elements"]["03_PROPS"][0]["element_id"] == "prop_0001"
    assert decoded["scenes"][0]["shots"][0]["metadata"]["focal_length"] == "STANDARD"


def test_agent_breakdown_rejects_natural_language_camera_values_and_missing_categories():
    invalid_camera = _document()
    invalid_camera["scenes"] = [_scene()]
    invalid_camera["scenes"][0]["shots"][0]["metadata"]["shot_size"] = "特写"
    with pytest.raises(ExportRejected):
        parse_agent_breakdown(invalid_camera)

    missing_category = _document()
    del missing_category["scenes"][0]["elements"]["08_VEHICLES"]
    with pytest.raises(ExportRejected):
        parse_agent_breakdown(missing_category)
