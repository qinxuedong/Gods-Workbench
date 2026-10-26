import pytest
from gw.agent_episode.prompts import (
    prompt_ref,
    prompt_ref_key,
    output_schema,
    StageId,
    PROMPT_CATALOG,
    ORCHESTRATOR_TEMPLATE,
    PROMPT_PACK_VERSION,
    _template_body,
    _template,
)
from gw.agent_runtime.models import PromptTemplateRef

def test_template_body_creator():
    body = _template_body(StageId.brief, "creator")
    # Main branches: required content present (task and contract logic)
    assert "You are the text creator" in body
    assert "Create a concise, decision-ready creative brief" in body
    assert "Brief" in body
    assert "Return exactly one JSON object" in body

def test_template_body_reviewer():
    body = _template_body(StageId.brief, "reviewer")
    # Main branches: required content present (task and dimension text logic)
    assert "You are an independent reviewer" in body
    assert "Independently assess whether the brief is clear" in body
    assert "Scoring rule:" in body
    assert "Return only the requested JSON object" in body

def test_template_generation():
    template = _template(StageId.brief, "creator")
    assert template.purpose.root == "brief.creator"
    assert template.ref.template_id.root == "episode.text.brief.creator"
    assert template.ref.version.root == PROMPT_PACK_VERSION

    # Check optional sections included
    schema = template.parameter_schema
    assert "user_goal" in schema["properties"]
    assert "human_revision_feedback" in schema["properties"]

    # Check required content present
    assert "stage" in schema["required"]
    assert "inputs" in schema["required"]
    assert "input_refs" in schema["required"]
    assert "revision_feedback" in schema["required"]
    assert "human_revision_feedback" in schema["required"]

def test_prompt_ref():
    ref = prompt_ref(StageId.brief, "creator")
    assert isinstance(ref, PromptTemplateRef)
    assert ref.template_id.root == "episode.text.brief.creator"
    assert ref.version.root == PROMPT_PACK_VERSION
    assert len(ref.content_hash.root) == 64

    # Invalid input handling
    with pytest.raises(KeyError):
        prompt_ref(StageId.brief, "invalid_role")

def test_prompt_ref_key():
    ref = prompt_ref(StageId.full_script, "reviewer")
    key = prompt_ref_key(ref)
    assert isinstance(key, tuple)
    assert len(key) == 3
    assert key[0] == ref.template_id.root
    assert key[1] == ref.version.root
    assert key[2] == ref.content_hash.root

def test_output_schema_creator():
    schema = output_schema(StageId.storyboard_text, "creator")
    assert isinstance(schema, dict)
    assert "type" in schema
    assert schema["type"] == "object"
    assert "properties" in schema
    assert "artifact_content" in schema["properties"]
    assert "revision_responses" in schema["properties"]

def test_output_schema_reviewer():
    schema = output_schema(StageId.delivery_check, "reviewer")
    assert isinstance(schema, dict)
    assert "type" in schema
    assert schema["type"] == "object"
    assert "properties" in schema
    assert "dimension_scores" in schema["properties"]
    assert "findings" in schema["properties"]
    assert "summary" in schema["properties"]
    assert "rollback_proposal" in schema["properties"]

def test_output_schema_invalid_inputs():
    # Invalid input handling
    with pytest.raises(KeyError):
        output_schema("invalid_stage", "creator")

def test_prompt_catalog_completeness():
    for stage in StageId:
        assert (stage, "creator") in PROMPT_CATALOG
        assert (stage, "reviewer") in PROMPT_CATALOG
        creator_prompt = PROMPT_CATALOG[(stage, "creator")]
        reviewer_prompt = PROMPT_CATALOG[(stage, "reviewer")]
        assert creator_prompt.purpose.root == f"{stage.value}.creator"
        assert reviewer_prompt.purpose.root == f"{stage.value}.reviewer"

def test_orchestrator_template():
    assert ORCHESTRATOR_TEMPLATE.purpose.root == "episode.orchestrator"
    assert ORCHESTRATOR_TEMPLATE.ref.template_id.root == "episode.orchestrator"
    assert "clarify" in ORCHESTRATOR_TEMPLATE.body
    assert "select_capability" in ORCHESTRATOR_TEMPLATE.body
