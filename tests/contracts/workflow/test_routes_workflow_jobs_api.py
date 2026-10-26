from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from gw.api.routes_workflow_jobs import create_video_jobs_router
from gw.core.errors import CleanroomException


class FakeVideoExecutor:
    def __init__(self) -> None:
        self.jobs: dict[str, dict[str, Any]] = {}
        self.should_fail_submit = False
        self.should_fail_with_value_error = False

    async def submit(self, **kwargs: Any) -> dict[str, Any]:
        if self.should_fail_submit:
            raise CleanroomException(
                status_code=502, code="UPSTREAM_ERROR", message="Upstream failed"
            )
        if self.should_fail_with_value_error:
            raise ValueError("Invalid target duration")

        job_id = "fake-job-123"
        job = {"job_id": job_id, "status": "accepted", **kwargs}
        self.jobs[job_id] = job
        return job

    async def poll(self, job_id: str) -> dict[str, Any]:
        if job_id not in self.jobs:
            raise CleanroomException(
                status_code=404, code="NOT_FOUND", message="Job not found"
            )
        return self.jobs[job_id]

    async def cancel(self, job_id: str) -> dict[str, Any]:
        if job_id not in self.jobs:
            raise CleanroomException(
                status_code=404, code="NOT_FOUND", message="Job not found"
            )
        job = self.jobs[job_id]
        job["status"] = "cancelled"
        return job


@pytest.fixture
def fake_executor() -> FakeVideoExecutor:
    return FakeVideoExecutor()


@pytest.fixture
def client(fake_executor: FakeVideoExecutor) -> TestClient:
    app = FastAPI()
    router = create_video_jobs_router(fake_executor, prefix="/video-jobs")
    app.include_router(router)
    return TestClient(app)


def test_submit_video_job_success(client: TestClient) -> None:
    response = client.post("/video-jobs", json={"shot_id": "SH001", "prompt": "test"})
    assert response.status_code == 202
    data = response.json()
    assert data["job_id"] == "fake-job-123"
    assert data["status"] == "accepted"


def test_submit_video_job_cleanroom_error(
    client: TestClient, fake_executor: FakeVideoExecutor
) -> None:
    fake_executor.should_fail_submit = True
    response = client.post("/video-jobs", json={"shot_id": "SH001"})
    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "UPSTREAM_ERROR"


def test_submit_video_job_value_error(
    client: TestClient, fake_executor: FakeVideoExecutor
) -> None:
    fake_executor.should_fail_with_value_error = True
    response = client.post("/video-jobs", json={"shot_id": "SH001"})
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "INVALID_REQUEST"


def test_poll_video_job_success(
    client: TestClient, fake_executor: FakeVideoExecutor
) -> None:
    fake_executor.jobs["job-1"] = {"job_id": "job-1", "status": "running"}
    response = client.get("/video-jobs/job-1")
    assert response.status_code == 200
    assert response.json()["status"] == "running"


def test_poll_video_job_not_found(client: TestClient) -> None:
    response = client.get("/video-jobs/missing-job")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "NOT_FOUND"


def test_cancel_video_job_success(
    client: TestClient, fake_executor: FakeVideoExecutor
) -> None:
    fake_executor.jobs["job-1"] = {"job_id": "job-1", "status": "running"}
    response = client.post("/video-jobs/job-1/cancel")
    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"


def test_cancel_video_job_not_found(client: TestClient) -> None:
    response = client.post("/video-jobs/missing-job/cancel")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "NOT_FOUND"
