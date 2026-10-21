"""C17-T2 Kev 纯 CPU 后端契约：显式配置、真实 HTTP 推理与失败关闭。"""

from __future__ import annotations

import asyncio

import pytest

from gw.decision.router import KevDecisionBackend


def test_kev_backend_requires_explicit_endpoint_and_keeps_cpu_label() -> None:
    with pytest.raises(ValueError, match="KEV_ENDPOINT"):
        KevDecisionBackend(None)

    backend = KevDecisionBackend("http://127.0.0.1:18081/v1/systemone")

    assert backend.name == "kev-cpu"


def test_kev_backend_maps_system_one_request_and_response() -> None:
    captured: dict[str, object] = {}

    def transport(endpoint: str, payload: dict[str, object]) -> dict[str, object]:
        captured["endpoint"] = endpoint
        captured["payload"] = payload
        return {
            "answers": {
                "decision": {
                    "probabilities": {"alpha": 0.7, "beta": 0.2, "gamma": 0.1}
                }
            },
            "model": "kev-0.8b-cpu",
        }

    backend = KevDecisionBackend(
        "http://127.0.0.1:18081/v1/systemone",
        transport=transport,
    )
    result = asyncio.run(
        backend.decide(
            {
                "decision_type": "choice",
                "prompt": "选择下一步动作",
                "options": [{"id": "alpha"}, {"id": "beta"}, {"id": "gamma"}],
            }
        )
    )

    assert backend.endpoint.endswith("/v1/systemone")
    assert result == {
        "probabilities": {"alpha": 0.7, "beta": 0.2, "gamma": 0.1},
    }
    assert captured["endpoint"] == "http://127.0.0.1:18081/v1/systemone"
    assert captured["payload"] == {
        "state": "选择下一步动作",
        "questions": {
            "decision": {
                "type": "choice",
                "criteria": {"alpha": None, "beta": None, "gamma": None},
            }
        },
    }


def test_kev_backend_requires_option_probabilities() -> None:
    backend = KevDecisionBackend(
        "http://127.0.0.1:18081/v1/systemone",
        transport=lambda _endpoint, _payload: {"answers": {}},
    )

    with pytest.raises(RuntimeError, match="choice probabilities"):
        asyncio.run(backend.decide({"decision_type": "choice", "options": [{"id": "a"}]}))


def test_kev_cpu_provider_fails_closed_without_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KEV_ENDPOINT", raising=False)
    from gw.api.routes_decision import default_decision_router

    assert default_decision_router.kev_backend is None
    with pytest.raises(RuntimeError, match="Kev"):
        asyncio.run(
            default_decision_router.decide({"type": "choice", "provider": "kev-cpu", "options": [{"id": "a"}]})
        )


def test_kev_cpu_provider_is_configured_by_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    def transport(endpoint: str, _payload: dict[str, object]) -> dict[str, object]:
        return {
            "answers": {"decision": {"probabilities": {"a": 0.6, "b": 0.4}}},
        }

    with monkeypatch.context() as patch:
        patch.setenv("KEV_ENDPOINT", "http://127.0.0.1:18081/v1/systemone")
        import importlib

        routes = importlib.reload(importlib.import_module("gw.api.routes_decision"))
        backend = routes.default_decision_router.kev_backend
        backend._transport = transport
        assert routes.default_decision_router.kev_backend is not None
        assert routes.default_decision_router.kev_backend.name == "kev-cpu"
        result = asyncio.run(
            routes.default_decision_router.decide(
                {"type": "choice", "provider": "kev-cpu", "options": [{"id": "a"}, {"id": "b"}]}
            )
        )
        assert result["provider"] == "kev-cpu"
        assert result["result"]["choice_id"] == "a"
