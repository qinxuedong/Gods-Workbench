from __future__ import annotations

import pytest

from gw.god_workflow.video_executor import (
    VideoJobExecutor,
    build_storage_path,
    build_video_filename,
)


class Provider:
    def __init__(self):
        self.cancelled = False
        self.polls = 0

    async def submit(self, payload):
        assert payload["shot_id"] == "SH001"
        return {"prompt_id": "remote-1"}

    async def poll(self, prompt_id):
        self.polls += 1
        return {"status": "completed", "outputs": [{"filename": "provider.mp4"}]}

    async def cancel(self, prompt_id):
        self.cancelled = True
        return {"remote_cancelled": True}


def test_gns_video_name_and_storage_path():
    assert (
        build_video_filename("EP01", "SC01", "SH001", 1)
        == "EP01_SC01_SH001_v001_raw.mp4"
    )
    assert build_storage_path("job-1") == "artifacts/job-1.mp4"


def test_gns_video_name_rejects_noncanonical_values():
    with pytest.raises(ValueError):
        build_video_filename("EP1", "SC01", "SH001", 1)
    with pytest.raises(ValueError):
        build_video_filename("EP01", "SC01", "SH001", 1, variant="master.tmp")


@pytest.mark.anyio
async def test_async_submit_poll_and_asset_source_provenance():
    provider = Provider()
    executor = VideoJobExecutor(provider)
    job = await executor.submit(
        shot_id="SH001",
        episode="EP01",
        scene="SC01",
        version=1,
        image_asset_id="asset-image",
        target_duration=4,
        prompt="camera move",
    )
    assert job["status"] == "accepted"
    assert job["job_id"]
    result = await executor.poll(job["job_id"])
    assert result["status"] == "completed"
    assert result["asset"]["source_shot_id"] == "SH001"
    assert result["asset"]["source_version"] == "v001"
    assert result["asset"]["filename"] == "EP01_SC01_SH001_v001_raw.mp4"
    assert result["asset"]["path"] == f"artifacts/{job['job_id']}.mp4"


@pytest.mark.anyio
async def test_cancel_reports_remote_result():
    provider = Provider()
    executor = VideoJobExecutor(provider)
    job = await executor.submit(
        shot_id="SH001",
        episode="EP01",
        scene="SC01",
        version=1,
        image_asset_id="asset-image",
        target_duration=4,
        prompt="camera move",
    )
    result = await executor.cancel(job["job_id"])
    assert result["status"] == "cancelled"
    assert result["remote_cancelled"] is True
    assert provider.cancelled is True
