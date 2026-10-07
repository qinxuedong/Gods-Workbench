from __future__ import annotations

from types import SimpleNamespace

from starlette.requests import Request

from gw.api import routes_auth


def _request_with_cookie(mode: str, value: str = "session-id") -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/asset-auth/logout",
            "headers": [(b"cookie", f"gw_session_{mode}={value}".encode("ascii"))],
            "query_string": b"",
            "server": ("testserver", 80),
            "client": ("testclient", 1234),
            "scheme": "http",
        }
    )


def test_logout_clears_runtime_cookie_for_dev_and_prod(monkeypatch):
    monkeypatch.setattr(routes_auth, "load_runtime_auth_config", lambda: SimpleNamespace(mode=routes_auth.AUTH_MODE_LOCAL_ACCOUNT))
    monkeypatch.setattr(routes_auth.local_accounts, "delete_session", lambda _session_id: True)
    monkeypatch.setattr(routes_auth.audit_log, "record_auth_event", lambda *args, **kwargs: None)

    for mode in ("dev", "prod"):
        monkeypatch.setenv("GW_RUNTIME_MODE", mode)
        response = routes_auth.auth_logout(_request_with_cookie(mode))
        set_cookie = "\\n".join(value.decode("latin-1") for key, value in response.raw_headers if key.lower() == b"set-cookie")
        assert f"gw_session_{mode}=" in set_cookie
        assert f"gw_oidc_flow_{mode}=" in set_cookie
        assert "gw_session=" not in set_cookie
        assert "gw_oidc_flow=" not in set_cookie


