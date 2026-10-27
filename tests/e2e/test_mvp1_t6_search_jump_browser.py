"""MVP1-T6 / AC4 + AC7：镜头级检索命中到时间戳跳转的真实浏览器端到端实测。

真实后端持久化 + 真实媒体文件 + 真实浏览器：先经 HTTP 验证镜头级检索返回可
跳转的起止时间戳，再由壳层桥接（asset-global-search 携带 start_sec）驱动素材页
复用 C18-T5 跳帧，断言播放器实际定位到命中镜头起点。不使用前端路由伪造或
数据注入。
"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest
from workflow_support import goto_ready, settings_browser

from gw.core import storage

SHOT_API = "/api/asset-registry/search/shots"
ASSET_API = "/api/asset-registry/assets"

VIDEO_SEGMENTS = [
    {"start_sec": 0.0, "end_sec": 6.24, "tags": ["日出", "晨光"], "summary": "红日破晓"},
    {"start_sec": 6.24, "end_sec": 21.05, "tags": ["大海", "海浪"], "summary": "浪花拍打沙滩"},
    {"start_sec": 21.05, "end_sec": 35.0, "tags": ["沙滩", "足迹"], "summary": "沙滩脚印"},
]


def _make_video(path) -> None:
    """用 ffmpeg 生成可被浏览器直接播放的真实 MP4（缺失 ffmpeg 时跳过）。"""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:  # pragma: no cover - 环境缺少 ffmpeg 时如实跳过
        pytest.skip("未安装 ffmpeg，无法生成真实可播放的视频文件")
    subprocess.run(
        [
            ffmpeg, "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", "color=c=black:s=160x90:r=5",
            "-t", "35", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path),
        ],
        check=True,
    )


def test_mvp1_t6_shot_search_and_timestamp_jump_e2e(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    library = tmp_path / "library"
    library.mkdir()
    video_file = library / "海边日出.mp4"
    _make_video(video_file)
    # 真实媒体文件所在目录作为只读允许根，媒体端点据此解析并流式返回文件
    monkeypatch.setenv("GW_ALLOWED_ROOTS", str(library))
    root_id = storage.root_identity_id(library.resolve())

    with settings_browser(monkeypatch, tmp_path) as (page, base, errors, _):
        # 1. 后端写入真实视频资产及其镜头切片
        payload = {
            "name": video_file.name,
            "kind": "video",
            "project_id": "prj-0001",
            "size_bytes": video_file.stat().st_size,
            "frame_rate": 25.0,
            "duration": 35.0,
            "tags": ["风景", "海滨"],
            "metadata": {
                "frame_rate": 25.0,
                "duration": 35.0,
                "video_segments": VIDEO_SEGMENTS,
            },
        }
        imported = page.context.request.post(
            f"{base}{ASSET_API}/import",
            data=json.dumps({"items": [payload]}),
            headers={"Content-Type": "application/json", "Origin": base},
        )
        assert imported.status == 200, f"导入资产失败: {imported.text()}"
        asset_id = imported.json()["asset_ids"][0]
        bound = page.context.request.patch(
            f"{base}{ASSET_API}/{asset_id}",
            data=json.dumps({"display_path": video_file.name, "root_id": root_id}),
            headers={"Content-Type": "application/json", "Origin": base},
        )
        assert bound.status == 200, f"绑定本地媒体失败: {bound.text()}"
        media = page.context.request.get(
            f"{base}{ASSET_API}/{asset_id}/media", headers={"Origin": base}
        )
        assert media.status == 200, f"媒体端点未返回真实文件: {media.status}"

        # 2. 真实后端镜头级检索：命中「大海」镜头并带可跳转时间戳（AC4）
        shot_resp = page.context.request.get(
            f"{base}{SHOT_API}", params={"q": "大海", "limit": "10"}, headers={"Origin": base}
        )
        assert shot_resp.status == 200, f"镜头级检索失败: {shot_resp.text()}"
        shot_body = shot_resp.json()
        assert shot_body["granularity"] == "shot"
        assert shot_body["data_status"] == "ok"
        top = shot_body["items"][0]
        assert top["asset_id"] == asset_id
        assert top["granularity"] == "shot"
        assert top["shot_index"] == 1
        assert abs(top["start_sec"] - 6.24) < 1e-6
        assert abs(top["end_sec"] - 21.05) < 1e-6
        assert "大海" in top["tags"]
        assert top["lexical_hits"] >= 1

        # 3. 打开真实素材页，等待卡片与播放器就绪
        goto_ready(page, f"{base}/static/embeds/asset-manager.html")
        page.wait_for_selector("#registrySearch", timeout=15000)
        page.locator(f'[data-registry-card="{asset_id}"]').wait_for(state="visible", timeout=15000)
        page.wait_for_function(
            "() => typeof window.MediaPreviewPlayer !== 'undefined'", timeout=15000
        )

        # 4. 以壳层 Omnibar 的真实桥接载荷触发跳转（携带镜头命中时间戳）
        page.evaluate(
            "(payload) => window.postMessage(payload, location.origin)",
            {"type": "asset-global-search", "asset_id": asset_id, "start_sec": top["start_sec"]},
        )
        page.wait_for_function(
            """() => {
                const video = document.querySelector('.asset-detail-media-node-content.asset-detail-player')
                    || document.querySelector('.asset-detail-viewer video');
                return Boolean(video) && Number(video.currentTime || 0) > 1;
            }""",
            timeout=15000,
        )

        # 5. 详情打开且播放器定位到命中镜头起点（25fps 对齐后 6.24s）
        assert page.evaluate(
            "() => document.body.classList.contains('asset-lightbox-open')"
        ), "桥接载荷未能打开素材详情"
        media_state = page.evaluate(
            """() => {
                const video = document.querySelector('.asset-detail-media-node-content.asset-detail-player')
                    || document.querySelector('.asset-detail-viewer video');
                const node = video?.closest('[data-detail-media-canvas-node]');
                return {
                    loaded: Number(video?.readyState || 0),
                    currentTime: Number(video?.currentTime || 0),
                    node: node?.dataset?.detailMediaCanvasNode || '',
                };
            }"""
        )
        assert media_state["node"] == asset_id, f"详情打开的不是命中资产: {media_state}"
        assert media_state["loaded"] >= 1, f"真实视频未加载元数据: {media_state}"
        assert abs(media_state["currentTime"] - 6.24) <= 0.2, (
            f"播放器未定位到命中镜头起点: {media_state}"
        )

        # 6. 无 start_sec 的文件级打开仍然可用（不得回归成必须带时间戳）
        page.evaluate(
            "(id) => window.postMessage({type: 'asset-global-search', asset_id: id}, location.origin)",
            asset_id,
        )
        page.wait_for_timeout(500)
        assert page.evaluate(
            "() => document.body.classList.contains('asset-lightbox-open')"
        ), "文件级打开（无时间戳）不应失败"

        # 7. 全程无未捕获前端异常
        assert not errors, f"页面存在未捕获的 JavaScript 错误: {errors}"
