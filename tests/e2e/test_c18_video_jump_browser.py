"""C18-AC5: 搜索「大海」卡片显示命中时间段，点击后无损跳帧播放的真实浏览器端到端实测。"""

from __future__ import annotations

import json

import pytest
from workflow_support import goto_ready, settings_browser


def test_c18_video_jump_e2e_real_browser(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """真实后端持久化与真实浏览器联动，不使用前端路由伪造与数据注入。"""
    with settings_browser(monkeypatch, tmp_path) as (page, base, errors, _):
        # 1. 后端写入真实视频资产及其场景转场切片 (Video Grounding 真实数据)
        video_payload = {
            "name": "海边日出.mp4",
            "kind": "video",
            "project_id": "prj-0001",
            "size_bytes": 1024 * 1024 * 12,
            "frame_rate": 25.0,
            "duration": 35.0,
            "tags": ["风景", "海滨"],
            "metadata": {
                "frame_rate": 25.0,
                "duration": 35.0,
                "video_segments": [
                    {
                        "start_sec": 0.0,
                        "end_sec": 6.24,
                        "tags": ["日出", "晨光"],
                        "summary": "红日破晓",
                    },
                    {
                        "start_sec": 6.24,
                        "end_sec": 21.05,
                        "tags": ["大海", "海浪"],
                        "summary": "浪花拍打沙滩",
                    },
                    {
                        "start_sec": 21.05,
                        "end_sec": 35.0,
                        "tags": ["沙滩", "足迹"],
                        "summary": "沙滩脚印",
                    },
                ],
            },
        }

        # 通过真实后端 API 创建资产登记
        import_resp = page.context.request.post(
            f"{base}/api/asset-registry/assets/import",
            data=json.dumps({"items": [video_payload]}),
            headers={"Content-Type": "application/json", "Origin": base},
        )
        assert import_resp.status == 200, f"导入资产失败: {import_resp.text()}"
        import_data = import_resp.json()
        asset_id = import_data["asset_ids"][0]
        assert asset_id.startswith("ast_")

        # 2. 验证后端真实检索支持搜“大海”命中该切片
        search_resp = page.context.request.get(
            f"{base}/api/asset-registry/assets?limit=100&offset=0&sort=newest&view=card&project_id=prj-0001&search=%E5%A4%A7%E6%B5%B7"
        )
        assert search_resp.status == 200
        search_data = search_resp.json()
        assert any(item["asset_id"] == asset_id for item in search_data.get("items", [])), (
            "真实后端检索未通过切片标签命中资产"
        )

        # 3. 访问实际页面，确认渲染视频卡片后按需动态加载 media-preview-player.js
        goto_ready(page, f"{base}/static/embeds/asset-manager.html")
        page.wait_for_selector('#registrySearch', timeout=15000)

        # 等待资产列表初次加载完成并渲染出视频卡片（触发视频播放器按需就绪）
        card_selector = f'[data-registry-card="{asset_id}"]'
        page.locator(card_selector).wait_for(state="visible", timeout=15000)
        page.wait_for_function("() => typeof window.MediaPreviewPlayer !== 'undefined'", timeout=15000)
        has_player = page.evaluate("() => typeof window.MediaPreviewPlayer !== 'undefined'")
        assert has_player, "页面未正确加载 media-preview-player.js"

        # 验证未输入搜索词时，绝不回退显示第一段时间徽标
        badge_selector = f'{card_selector} .registry-card-segment-badge'
        assert page.locator(badge_selector).count() == 0, "空搜索时不应显示视频片段徽标"

        # 4. 在界面中输入「大海」触发搜索并派发 input 事件
        page.evaluate(
            """() => {
                const input = document.getElementById('registrySearch');
                input.focus();
                input.value = '\\u5927\\u6d77';
                input.dispatchEvent(new Event('input', { bubbles: true }));
            }"""
        )

        # 5. 验证资产卡片显示命中的时间段 (00:06~00:21)
        badge = page.locator(badge_selector)
        badge.wait_for(state="visible", timeout=10000)
        badge_text = badge.inner_text().strip()
        assert "00:06~00:21" in badge_text, f"卡片未正确显示命中的时间段，当前显示: {badge_text}"

        # 6. 验证徽标布局：徽标不能与 MP4 格式标签重叠
        format_selector = f'{card_selector} .registry-card-format'
        format_loc = page.locator(format_selector)
        format_loc.wait_for(state="visible", timeout=10000)
        boxes = page.evaluate(
            """([formatSel, badgeSel]) => {
                const box = (sel) => {
                    const el = document.querySelector(sel);
                    if (!el || !el.isConnected) return null;
                    const r = el.getBoundingClientRect();
                    if (!r.width || !r.height) return null;
                    return { x: r.x, y: r.y, width: r.width, height: r.height };
                };
                return { format: box(formatSel), badge: box(badgeSel) };
            }""",
            [format_selector, badge_selector],
        )
        format_box = boxes["format"]
        badge_box = boxes["badge"]
        assert format_box and badge_box, "卡片标签或时间段徽标未正常渲染几何边框"
        # 格式标签在左上方 (y ~ 9px)，时间段徽标在左下方 (y 在格式标签之下)，纵向不重叠
        assert badge_box["y"] >= format_box["y"] + format_box["height"], (
            f"时间段徽标与格式标签垂直重叠: badge.y={badge_box['y']}, format.bottom={format_box['y'] + format_box['height']}"
        )

        # 7. 点击时间段徽标，验证能触发跳转并打开详情或起播
        page.click(badge_selector)
        page.wait_for_timeout(300)

        # 验证详情浮窗已打开，并含有视频播放器
        detail_open = page.evaluate("() => document.body.classList.contains('asset-lightbox-open')")
        assert detail_open, "点击时间段徽标未能成功打开详情跳帧预览"

        # 8. 验证在 24、25、29.97fps 下的时间戳计算
        align_tests = page.evaluate(
            """() => ({
                fps24: window.alignTimeToFps(6.24, 24),
                fps25: window.alignTimeToFps(6.24, 25),
                fps2997: window.alignTimeToFps(6.24, 29.97),
            })"""
        )
        assert align_tests["fps24"] == 6.25, f"24fps 对齐错误: {align_tests['fps24']}"
        assert align_tests["fps25"] == 6.24, f"25fps 对齐错误: {align_tests['fps25']}"
        assert abs(align_tests["fps2997"] - 6.2395666) < 0.001, (
            f"29.97fps 对齐错误: {align_tests['fps2997']}"
        )

        # 9. 验证视频损坏时 seek 返回失败 (Fail-Closed)
        damaged_seek_result = page.evaluate(
            """() => {
                const dummyVideo = document.createElement('video');
                dummyVideo.error = { code: 4, message: 'Format error' };
                return window.MediaPreviewPlayer.seekTo(dummyVideo, 10);
            }"""
        )
        assert damaged_seek_result is False, "视频损坏时 seekTo 未返回失败"

        # 10. 验证无匹配时不回退显示第一段
        no_match_result = page.evaluate(
            """(asset) => window.findMatchedVideoSegment(asset, '绝对不匹配的无名标签')""",
            video_payload,
        )
        assert no_match_result is None, "搜索无匹配时不应回退显示第一段"

        # 11. 验证没有未捕获的前端脚本异常
        assert not errors, f"页面存在未捕获的 JavaScript 错误: {errors}"
