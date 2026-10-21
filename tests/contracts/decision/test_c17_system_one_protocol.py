"""C17-T1 标准 System One 协议与 AnyJev 去偏契约测试。"""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from itertools import permutations
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from gw.api.routes_decision import router
from gw.decision.debiasing import (
    cyclic_shift,
    debias_probability_runs,
    normalize_probabilities,
    position_bias_drift,
)
from gw.decision.router import DecisionRouter, RemoteDecisionBackend


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
    assert normalize_probabilities({"a": 2, "b": 1}) == {"a": 2 / 3, "b": 1 / 3}


@pytest.mark.parametrize("values", [{"a": -1}, {"a": float("nan")}, {"a": float("inf")}])
def test_normalize_rejects_negative_and_non_finite_scores(values: dict[str, float]) -> None:
    with pytest.raises(ValueError):
        normalize_probabilities(values)


def test_anyjev_all_four_option_permutations_remove_position_bias() -> None:
    options = ("alpha", "beta", "gamma", "delta")
    runs = [
        {option: float(index == 0) for index, option in enumerate(order)}
        for order in permutations(options)
    ]

    debiased = debias_probability_runs(runs)

    assert set(debiased) == set(options)
    assert all(probability == pytest.approx(0.25) for probability in debiased.values())
    assert sum(debiased.values()) == pytest.approx(1.0)
    assert position_bias_drift(runs) < 1.5


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
    assert sum(payload["probabilities"].values()) == pytest.approx(1.0)
    assert payload["debias"]["position_bias_drift_pct"] < 1.5


def test_system_one_scoring_and_boolean_share_one_response_shape() -> None:
    client = _client()
    scoring = client.post(
        "/v1/systemone",
        json={"type": "scoring", "prompt": "评分", "scores": {"clarity": 80, "risk": 20}},
    )
    assert scoring.status_code == 200, scoring.text
    assert scoring.json()["protocol_version"] == "system-one.v1"
    assert scoring.json()["result"]["scores"] == {"clarity": 80.0, "risk": 20.0}

    boolean = client.post(
        "/v1/systemone",
        json={"type": "boolean", "prompt": "是否通过", "value": True},
    )
    assert boolean.status_code == 200, boolean.text
    assert boolean.json()["protocol_version"] == "system-one.v1"
    assert boolean.json()["result"]["value"] is True


def test_system_one_rejects_unknown_type_and_out_of_range_scores() -> None:
    client = _client()
    unknown = client.post("/v1/systemone", json={"type": "ranking"})
    assert unknown.status_code == 422

    invalid_score = client.post(
        "/v1/systemone",
        json={"type": "scoring", "scores": {"clarity": 101}},
    )
    assert invalid_score.status_code == 400
    assert invalid_score.json()["detail"]["code"] == "DECISION_INVALID"


def test_c17_ac1_fixed_200_case_fixture_runs_through_system_one_api() -> None:
    fixture_path = (
        Path(__file__).resolve().parents[2]
        / "fixtures"
        / "systemone"
        / "c17_ac1_regression_200.json"
    )
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))

    assert fixture["fixture_id"] == "c17-ac1-systemone-v1-200"
    assert fixture["item_count"] == 200
    items = fixture["items"]
    assert len(items) == 200
    assert len({item["item_id"] for item in items}) == 200

    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    for item in items:
        grouped[item["scenario_id"]].append(item)
    assert len(grouped) == 50

    client = _client()
    for scenario_id, runs in grouped.items():
        assert len(runs) == 4, scenario_id
        option_ids = runs[0]["option_order"]
        assert len(option_ids) == 4
        assert [tuple(run["option_order"]) for run in runs] == [
            tuple(option_ids[position:] + option_ids[:position]) for position in range(4)
        ]
        assert all(set(run["option_order"]) == set(option_ids) for run in runs)
        assert all(len(run["position_scores"]) == 4 for run in runs)
        assert all(
            run["position_scores"] == pytest.approx([0.91, 0.03, 0.03, 0.03])
            for run in runs
        )

        samples = [
            dict(zip(run["option_order"], run["position_scores"], strict=True))
            for run in runs
        ]
        response = client.post(
            "/v1/systemone",
            json={
                "type": "choice",
                "prompt": runs[0]["prompt"],
                "options": [{"id": option_id} for option_id in option_ids],
                "samples": samples,
            },
        )

        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["protocol_version"] == "system-one.v1"
        assert payload["result"]["choice_id"] == fixture["expected_choice_id"]
        assert sum(payload["probabilities"].values()) == pytest.approx(1.0)
        assert set(payload["probabilities"]) == set(option_ids)
        assert all(value == pytest.approx(0.25) for value in payload["probabilities"].values())
        assert payload["debias"]["position_bias_drift_pct"] < 1.5


def test_decision_router_accepts_an_async_backend_adapter() -> None:
    class FakeBackend:
        name = "fake"

        async def decide(self, _payload: dict[str, object]) -> dict[str, bool]:
            return {"value": False}

    result = asyncio.run(DecisionRouter(FakeBackend()).decide({"type": "boolean"}))

    assert result["provider"] == "fake"
    assert result["result"]["value"] is False


def test_remote_adapter_uses_injected_transport_without_network() -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    def transport(endpoint: str, payload: dict[str, object]) -> dict[str, bool]:
        calls.append((endpoint, payload))
        return {"value": True}

    backend = RemoteDecisionBackend("https://decision.example/v1", transport=transport)
    result = asyncio.run(
        DecisionRouter(remote_backend=backend).decide({"type": "boolean", "provider": "remote"})
    )

    assert result["provider"] == "remote"
    assert result["result"]["value"] is True
    assert calls == [
        (
            "https://decision.example/v1",
            {"type": "boolean", "provider": "remote", "decision_type": "boolean"},
        )
    ]


def test_systemone_post_route_is_registered_in_application() -> None:
    from gw.api.app import create_app

    response = TestClient(create_app()).post(
        "/v1/systemone",
        json={"type": "boolean", "prompt": "路由已接入正式应用？", "value": True},
    )

    assert response.status_code == 200, response.text
    assert response.json()["protocol_version"] == "system-one.v1"
    assert response.json()["result"]["value"] is True
