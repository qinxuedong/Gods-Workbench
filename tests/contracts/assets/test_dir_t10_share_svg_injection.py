# -*- coding: utf-8 -*-
"""DIR-T10 外部分享 SVG 注入安全契约与指定候选 spot-check。"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gw.api.app import create_app


ROOT = Path(__file__).resolve().parents[3]
AUTH = {"Authorization": "Bearer cleanroom-test", "X-User-Role": "editor"}
PUBLIC = "/api/public/shares"


def _asset_and_share(client: TestClient) -> tuple[str, str]:
    created = client.post(
        "/api/asset-registry/assets/import",
        headers=AUTH,
        json={"items": [{"name": "review.png", "kind": "image"}]},
    )
    assert created.status_code == 200, created.text
    asset_id = created.json()["asset_ids"][0]
    shared = client.post(
        "/api/asset-reviews/shares",
        headers=AUTH,
        json={"asset_ids": [asset_id], "title": "SVG 安全"},
    )
    assert shared.status_code == 201, shared.text
    return asset_id, shared.json()["token"]


def _annotation(kind: str, color: object) -> dict:
    return {
        "kind": kind,
        "geometry": {"x": 0.1, "y": 0.1, "x2": 0.2, "y2": 0.2},
        "style": {"color": color, "width": 4},
    }


@pytest.mark.parametrize(
    "color",
    [
        '"><script>window.__svg_xss=1</script>',
        "<svg onload=alert(1)>",
        "red\" onmouseover=\"alert(1)",
        "&lt;img src=x onerror=alert(1)&gt;",
        "javascript:alert(1)",
        "url(javascript:alert(1))",
        "red\n<strong>bold</strong>",
    ],
)
def test_share_annotation_malicious_colors_cannot_escape_svg_attribute(color):
    with TestClient(create_app()) as client:
        asset_id, token = _asset_and_share(client)
        ticket = client.post(f"{PUBLIC}/{token}/access", json={}).json()["ticket"]
        for kind in ("pin", "rect", "ellipse", "arrow", "freehand"):
            response = client.post(
                f"{PUBLIC}/{token}/comments",
                json={
                    "ticket": ticket,
                    "asset_id": asset_id,
                    "body": "颜色注入边界",
                    "annotations": [_annotation(kind, color)],
                },
            )
            assert response.status_code == 201, response.text
            saved = response.json()["comment"]["annotations"][0]["style"]["color"]
            assert saved == "#ff4d67"

        page = client.get(f"/share/{token}")
        assert page.status_code == 200
        module = client.get("/static/js/modules/asset-share.js")
        assert module.status_code == 200
        assert "safeAnnotationColor" in module.text
        assert "safeAnnotationColor(annotation?.style?.color" in module.text


def test_share_annotation_valid_colors_are_preserved():
    with TestClient(create_app()) as client:
        asset_id, token = _asset_and_share(client)
        ticket = client.post(f"{PUBLIC}/{token}/access", json={}).json()["ticket"]
        response = client.post(
            f"{PUBLIC}/{token}/comments",
            json={
                "ticket": ticket,
                "asset_id": asset_id,
                "body": "合法颜色",
                "annotations": [
                    _annotation("rect", " #FFB020 "),
                    _annotation("pin", "rgb(255, 77, 103)"),
                ],
            },
        )
        assert response.status_code == 201, response.text
        annotations = response.json()["comment"]["annotations"]
        assert [item["style"]["color"] for item in annotations] == [
            "#ffb020",
            "rgb(255, 77, 103)",
        ]


@pytest.mark.parametrize(
    ("relative", "needle"),
    [
        ("web/js/modules/asset-review.js", "function annotationSvg"),
        ("web/js/controllers/assets-controller.js", "function render()"),
        ("web/js/modules/directory-settings-nav.js", "panel.innerHTML"),
    ],
)
def test_da26_spot_check_boundaries(relative: str, needle: str):
    source = (ROOT / relative).read_text(encoding="utf-8")
    assert needle in source
    if relative.endswith("asset-review.js"):
        assert "const stroke = safeAnnotationColor(style.color" in source
    if relative.endswith("assets-controller.js"):
        assert "escapeText(p.project_id)" in source
        assert "escapeText(asset.asset_id)" in source
    if relative.endswith("directory-settings-nav.js"):
        assert re.search(r"button\.innerHTML = '[^']*'", source)
        assert re.search(r"panel\.innerHTML = '[^']*'", source)
        assert "${" not in re.search(r"panel\.innerHTML = '([^']*)'", source).group(1)


def test_review_annotation_malicious_colors_use_safe_default():
    """私有审阅页同名 SVG 渲染路径也必须收敛不可信颜色。"""
    node = shutil.which("node")
    if not node:
        pytest.skip("Node不可用")
    source = (ROOT / "web/js/modules/asset-review.js").read_text(encoding="utf-8")
    start = source.index("function annotationSvg")
    end = source.index("function mediaMarkup", start)
    script = f"""
const assert=require('assert');
    const attr=value=>String(value??'').replace(/[&<>"']/g,ch=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[ch]));
const safeAnnotationColor=(value, fallback)=>{{
  const color=String(value||'').trim().toLowerCase();
  return /^(?:#(?:[0-9a-f]{{3}}|[0-9a-f]{{6}})|rgba?\\([^()'"<>;&\\\\]{{1,80}}\\)|hsla?\\([^()'"<>;&\\\\]{{1,80}}\\)|(?:transparent|currentcolor)|[a-z]{{3,32}})$/.test(color) ? color : fallback;
}};
{source[start:end]}
const attack='"><script>window.__review_xss=1</script>';
for(const kind of ['pin','rect','ellipse','arrow','freehand']){{
  const markup=annotationSvg({{kind,geometry:{{x:0.1,y:0.1,x2:0.2,y2:0.2}},style:{{color:attack,width:4}}}});
  assert.equal(markup.includes(attack),false);
  assert.equal(markup.includes('<script>'),false);
}}
assert.equal(annotationSvg({{kind:'pin',geometry:{{x:0.1,y:0.1}},style:{{color:'#FFB020'}}}}).includes('#ffb020'),true);
"""
    result = subprocess.run(
        [node, "-e", script], capture_output=True, text=True, timeout=20
    )
    assert result.returncode == 0, result.stderr



