"""Validation and reproducible execution snapshots."""

import copy
import json

import pytest
from pydantic import ValidationError

from devflow_api.schemas.workflow_template import (
    WorkflowTemplate,
    canonical_hash,
    parse_workflow_template,
)
from devflow_core.exceptions import InvalidWorkflowTemplateError


def template_data() -> dict[str, object]:
    return {
        "template_version": 1,
        "steps": [
            {
                "key": "a",
                "version": 1,
                "title": "First",
                "executor_id": "mock",
                "tool_selection": {"executor_id": "mock"},
            },
            {
                "key": "b",
                "version": 2,
                "title": "Second",
                "executor_id": "mock",
                "tool_selection": {"executor_id": "mock"},
                "depends_on": ["a"],
                "inputs": {"source": "$steps.a.document", "nested": [True, None, {"count": 3}]},
                "approvable": True,
                "protected_operation": "implementation_authorization",
            },
        ],
    }


def test_valid_workflow_template_roundtrip() -> None:
    template = WorkflowTemplate.model_validate(template_data())
    assert template.steps[1].depends_on == ["a"]
    assert template.defaults.executor_id == "mock"
    assert template.steps[0].retry_policy.max_attempts == 1
    assert WorkflowTemplate.model_validate_json(template.model_dump_json()) == template


@pytest.mark.parametrize(
    "kind", ["empty", "duplicate", "missing_dependency", "cycle", "self_cycle"]
)
def test_invalid_template_graph(kind: str) -> None:
    data = template_data()
    steps = copy.deepcopy(WorkflowTemplate.model_validate(data).model_dump(mode="json")["steps"])
    if kind == "empty":
        steps = []
    elif kind == "duplicate":
        steps[1]["key"] = "a"
    elif kind == "missing_dependency":
        steps[1]["depends_on"] = ["absent"]
    elif kind == "cycle":
        steps[0]["depends_on"] = ["b"]
    else:
        steps[0]["depends_on"] = ["a"]
    data["steps"] = steps
    with pytest.raises(InvalidWorkflowTemplateError) as error:
        parse_workflow_template(json.dumps(data))
    assert error.value.code == "INVALID_WORKFLOW_TEMPLATE"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("version", 0),
        ("timeout_s", 0),
        ("key", ""),
        ("executor_id", ""),
        ("retry_policy", {"max_attempts": 0}),
        ("tool_selection", {"executor_id": "other"}),
        ("inputs", {"invalid": float("nan")}),
        ("unexpected", True),
        ("approvable", True),
        ("protected_operation", "unknown"),
    ],
)
def test_invalid_step_metadata(field: str, value: object) -> None:
    data = WorkflowTemplate.model_validate(template_data()).model_dump(mode="json")
    data["steps"][0][field] = value
    with pytest.raises(ValidationError):
        WorkflowTemplate.model_validate(data)


def test_canonical_digest_preserves_content_and_types() -> None:
    assert canonical_hash({"b": 2, "a": "é"}) == canonical_hash({"a": "é", "b": 2})
    assert canonical_hash({"a": " value "}) != canonical_hash({"a": "value"})
    assert canonical_hash({"a": 1}) != canonical_hash({"a": "1"})


def test_malformed_output_reference_retained_for_resolution_layer() -> None:
    data = WorkflowTemplate.model_validate(template_data()).model_dump(mode="json")
    data["steps"][1]["inputs"] = {"source": "$steps.bad"}
    assert WorkflowTemplate.model_validate(data).steps[1].inputs == {"source": "$steps.bad"}
