import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from gw.api.routes_asset_review import router
from gw.core.errors import CleanroomException

def cleanroom_exception_handler(request: Request, exc: CleanroomException):
    content = {"error": exc.message, "code": exc.code}
    if exc.extra and exc.expose_extra_fields:
        for field in exc.expose_extra_fields:
            if field in exc.extra:
                content[field] = exc.extra[field]
    return JSONResponse(status_code=exc.status_code, content=content)

def test_asset_review_routes_return_503(monkeypatch):
    monkeypatch.setattr("gw.api.routes_asset_review.require_authenticated", lambda *args, **kwargs: "mocked_user")
    monkeypatch.setattr("gw.api.routes_asset_review.require_edit_access", lambda *args, **kwargs: "mocked_user")

    app = FastAPI()
    app.include_router(router)
    app.add_exception_handler(CleanroomException, cleanroom_exception_handler)
    client = TestClient(app)

    # 1. PATCH /api/asset-reviews/comments/{comment_id}
    response = client.patch("/api/asset-reviews/comments/comment123", headers={"X-User-Role": "editor", "Authorization": "Bearer fake"})
    assert response.status_code == 503
    assert response.json()["error"] == "资产审查评论能力尚未接入已批准的数据后端"

    # 2. POST /api/asset-reviews/deliveries/{delivery_id}/export
    response = client.post("/api/asset-reviews/deliveries/del123/export", headers={"X-User-Role": "editor", "Authorization": "Bearer fake"})
    assert response.status_code == 503
    assert response.json()["error"] == "资产交付导出能力尚未接入已批准的数据后端"

    # 3. GET /api/asset-reviews/sessions
    response = client.get("/api/asset-reviews/sessions", headers={"X-User-Role": "editor", "Authorization": "Bearer fake"})
    assert response.status_code == 503
    assert response.json()["error"] == "资产审查会话能力尚未接入已批准的数据后端"

    # 4. POST /api/asset-reviews/sessions
    response = client.post("/api/asset-reviews/sessions", headers={"X-User-Role": "editor", "Authorization": "Bearer fake"})
    assert response.status_code == 503
    assert response.json()["error"] == "资产审查会话能力尚未接入已批准的数据后端"

    # 5. GET /api/asset-reviews/sessions/{session_id}
    response = client.get("/api/asset-reviews/sessions/session123", headers={"X-User-Role": "editor", "Authorization": "Bearer fake"})
    assert response.status_code == 503
    assert response.json()["error"] == "资产审查会话能力尚未接入已批准的数据后端"

    # 6. PUT /api/asset-reviews/sessions/{session_id}/approval
    response = client.put("/api/asset-reviews/sessions/session123/approval", headers={"X-User-Role": "editor", "Authorization": "Bearer fake"})
    assert response.status_code == 503
    assert response.json()["error"] == "资产审查审批能力尚未接入已批准的数据后端"

    # 7. POST /api/asset-reviews/sessions/{session_id}/comments
    response = client.post("/api/asset-reviews/sessions/session123/comments", headers={"X-User-Role": "editor", "Authorization": "Bearer fake"})
    assert response.status_code == 503
    assert response.json()["error"] == "资产审查评论能力尚未接入已批准的数据后端"

    # 8. POST /api/asset-reviews/sessions/{session_id}/delivery
    response = client.post("/api/asset-reviews/sessions/session123/delivery", headers={"X-User-Role": "editor", "Authorization": "Bearer fake"})
    assert response.status_code == 503
    assert response.json()["error"] == "资产交付能力尚未接入已批准的数据后端"

    # 9. POST /api/asset-reviews/shares
    response = client.post("/api/asset-reviews/shares", headers={"X-User-Role": "editor", "Authorization": "Bearer fake"})
    assert response.status_code == 503
    assert response.json()["error"] == "资产审查分享能力尚未接入已批准的数据后端"
