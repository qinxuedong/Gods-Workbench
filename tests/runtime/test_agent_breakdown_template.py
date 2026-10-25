# Copyright 2026 Gods-Workbench Authors

from types import MappingProxyType

import pytest

from gw.agent_runtime.templates.agent_breakdown import (
    ASC_METADATA_ENUMS,
    BREAKDOWN_CATEGORIES,
    BREAKDOWN_PROMPT,
    BREAKDOWN_TEMPLATE,
    BREAKDOWN_TEMPLATE_ID,
    BREAKDOWN_TEMPLATE_VERSION,
)


def test_asc_metadata_enums_immutability():
    # Must be a MappingProxyType to be immutable
    assert isinstance(ASC_METADATA_ENUMS, MappingProxyType)
    with pytest.raises(TypeError):
        ASC_METADATA_ENUMS["shot_size"] = ("new_size",)


def test_breakdown_template_structure():
    assert BREAKDOWN_TEMPLATE["template_id"] == BREAKDOWN_TEMPLATE_ID
    assert BREAKDOWN_TEMPLATE["version"] == BREAKDOWN_TEMPLATE_VERSION
    assert BREAKDOWN_TEMPLATE["schema_version"] == 1
    assert BREAKDOWN_TEMPLATE["required_categories"] == list(BREAKDOWN_CATEGORIES)

    # Check that asc_metadata in the template matches the enums mapping
    expected_asc_metadata = {key: list(values) for key, values in ASC_METADATA_ENUMS.items()}
    assert BREAKDOWN_TEMPLATE["asc_metadata"] == expected_asc_metadata

    # Check that prompt is present and matches
    assert BREAKDOWN_TEMPLATE["prompt"] == BREAKDOWN_PROMPT
    assert isinstance(BREAKDOWN_TEMPLATE["prompt"], str)
    assert len(BREAKDOWN_TEMPLATE["prompt"]) > 0


def test_breakdown_categories_content():
    expected_categories = (
        "01_CAST",
        "02_EXTRAS",
        "03_PROPS",
        "04_WARDROBE",
        "05_VFX",
        "06_SFX",
        "07_LOCATION",
        "08_VEHICLES",
    )
    assert expected_categories == BREAKDOWN_CATEGORIES
