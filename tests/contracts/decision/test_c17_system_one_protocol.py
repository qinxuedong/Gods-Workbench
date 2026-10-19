"""C17-T1 标准 System One 协议与 AnyJev 去偏契约测试。"""

from __future__ import annotations

import asyncio

from fastapi import FastAPI
from fastapi.testclient import TestClient

from gw.api.routes_decision import router
from gw.decision.debiasing import (
    cyclic_shift,
    debias_probability_runs,
    normalize_probabilities,
    position_bias_drift,
)
from gw.decision.router import DecisionRouter


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _rotated_choice_runs() -> list[dict[str, float]]:
    options = ("alpha", "beta", "gamma", "delta")
    return [
        {option: (0.91 if option == options[shift] else 0.03) for option in options}
        for shift in range(len(options))
    ]


def test_cyclic_shift_and_normalize_are_deterministic() -> None:
    assert cyclic_shift(("a", "b", "c"), 1) == ("b", "c", "a")
    assert cyclic_shift(("a", "b", "c"), -1) == ("c", "a", "b")
    normalized = normalize_probabilities({"a": 2, "b": 1})
    assert normalized == {"a": 2 / 3, "b": 1 / 3}


def test_anyjev_cyclic_runs_remove_four_position_bias() -> None:
    debiased = debias_probability_runs(_rotated_choice_runs())

    assert set(debiased) == {"alpha", "beta", "gamma", "delta"}
    assert abs(sum(debiased.values()) - 1) < 1e-9
    assert position_bias_drift(_rotated_choice_runs()) < 1.5


def test_system_one_choice_returns_uniform_protocol_and_debiased_probabilities() -> None:
    response = _client().post(
        "/v1/systemone",
        json={
            "type": "choice",
            "prompt": "选择下一步动作",
            "options": [
                {"id": "alpha", "label": "动作 A"},
                {"id": "beta", "label": "动作 B"},
                {"id": "gamma", "label": "动作 C"},
                {"id": "delta", "label": "动作 D"},
            ],
            "samples": _rotated_choice_runs(),
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["protocol_version"] == "system-one.v1"
    assert payload["decision_type"] == "choice"
    assert payload["result"]["choice_id"] == "alpha"
    assert abs(sum(payload["probabilities"].values()) - 1) < 1e-9
    assert payload["debias"]["position_bias_drift_pct"] < 1.5


def test_system_one_scoring_and_boolean_share_one_response_shape() -> None:
    client = _client()

    scoring = client.post(
        "/v1/systemone",
        json={
            "type": "scoring",
            "prompt": "评分",
            "scores": {"clarity": 80, "risk": 20},
        },
    )
    assert scoring.status_code == 200, scoring.text
    assert scoring.json()["result"]["scores"] == {"clarity": 80.0, "risk": 20.0}

    boolean = client.post(
        "/v1/systemone",
        json={"type": "boolean", "prompt": "是否通过", "value": True},
    )
    assert boolean.status_code == 200, boolean.text
    assert boolean.json()["result"]["value"] is True


def test_system_one_rejects_unknown_decision_type() -> None:
    response = _client().post(
        "/v1/systemone",
        json={"type": "ranking", "prompt": "不支持的类型"},
    )

    assert response.status_code == 422


def test_decision_router_accepts_an_async_backend_adapter() -> None:
    class FakeBackend:
        name = "fake"

        async def decide(self, _payload):
            return {"value": False}

    result = asyncio.run(DecisionRouter(FakeBackend()).decide({"type": "boolean"}))

    assert result["provider"] == "fake"
    assert result["result"]["value"] is False


def test_system_one_rejects_out_of_range_scores_with_standard_error() -> None:
    response = _client().post(
        "/v1/systemone",
        json={"type": "scoring", "scores": {"clarity": 101}},
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "DECISION_INVALID"
