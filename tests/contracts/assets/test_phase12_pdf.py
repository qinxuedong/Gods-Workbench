"""三类中文PDF导出：完整分页、源文无损、错误边界和真实渲染。"""
import math
import pytest
from fastapi.testclient import TestClient
from gw.api.app import create_app
from gw.core.errors import CleanroomException
from gw.core.text_pdf import build_text_pdf, _physical_lines

AUTH = {"Authorization": "Bearer cleanroom-test", "X-User-Role": "editor"}
READONLY = {**AUTH, "X-User-Role": "readonly"}


def inspect_pdf(content, source, expected_pages=None):
    pymupdf = pytest.importorskip("pymupdf", reason="PDF独立解析/渲染依赖未安装，不能据跳过声称验收")
    with pymupdf.open(stream=content, filetype="pdf") as doc:
        assert doc.embfile_names() == ["source.txt"]
        assert doc.embfile_get("source.txt") == source.encode("utf-8")
        expected = _physical_lines(source)
        assert doc.page_count == (expected_pages or math.ceil(len(expected) / 42))
        actual = []
        for page in doc:
            actual.extend(page.get_text().splitlines())
            for block in page.get_text("dict")["blocks"]:
                for line in block.get("lines", []):
                    for span in line["spans"]:
                        x0,y0,x1,y1 = span["bbox"]
                        assert 47.5 <= x0 <= x1 <= 547.5
                        assert 47.5 <= y0 <= y1 <= 794.5
            if page.get_text().strip():
                pixels = page.get_pixmap().samples
                assert min(pixels) < 100
        assert actual == [line for line in expected if line.strip()]


@pytest.mark.parametrize("count", [1, 41, 42, 43, 100])
def test_pdf_physical_line_pagination_and_visible_tail(count):
    text = "\n".join(f"第{i:03d}项中文（标点）ABC\\" for i in range(count))
    inspect_pdf(build_text_pdf(text), text, math.ceil(count / 42))


def test_pdf_wraps_after_escape_and_preserves_original_newlines():
    text = "中文" * 70 + "\r\nASCII " + "a" * 99 + "\t括号()\\，。😀𠀀\r尾项核实"
    inspect_pdf(build_text_pdf(text), text)


@pytest.mark.parametrize("text, status", [("\ud800", 400), ("x" * (1024 * 1024 + 1), 413),
                                          ("x" * 180001, 413), ("\n" * 4200, 413)], ids=["invalid-unicode", "source-limit", "display-limit", "page-limit"])
def test_pdf_rejects_invalid_unicode_and_limits_without_truncation(text, status):
    with pytest.raises(CleanroomException) as error:
        build_text_pdf(text)
    assert error.value.status_code == status


@pytest.mark.parametrize("count", [1, 41, 100])
def test_registry_pdf_full_records_duplicate_count_and_unknown_id(count):
    path = "/api/asset-registry/assets"
    with TestClient(create_app()) as client:
        created = client.post(path + "/import", headers=AUTH,
            json={"items": [{"name": f"中文素材{i:03d}", "kind": "image"} for i in range(count)]})
        assert created.status_code == 200, created.text
        records = created.json()["assets"]
        ids = [item["asset_id"] for item in records]
        expected = "\n".join(f"{a['asset_id']} | {a['name']} | {a['kind']}" for a in records)
        response = client.post(path + "/export-pdf", headers=AUTH, json={"asset_ids": ids + ids[:1], "name": "中文清单"})
        assert response.status_code == 200, response.text
        assert response.headers["X-PDF-Exported"] == str(count)
        assert response.headers["X-PDF-Skipped"] == "0"
        assert "filename*=UTF-8''" in response.headers["Content-Disposition"]
        inspect_pdf(response.content, expected)
        assert client.post(path + "/export-pdf", headers=AUTH, json={"asset_ids": [ids[0], "missing"]}).status_code == 404
        assert client.post(path + "/export-pdf", json={"asset_ids": ids}).status_code == 401
        assert client.post(path + "/export-pdf", headers=READONLY, json={"asset_ids": ids}).status_code == 200


def test_content_and_delivery_pdf_real_chinese_http_exports():
    with TestClient(create_app()) as client:
        asset = client.post("/api/asset-registry/assets/import", headers=AUTH,
                           json={"items": [{"name": "交付文档", "kind": "document"}]}).json()["asset_ids"][0]
        text = "正文\r\n" + "\n".join(f"第{i:03d}行中文原文" for i in range(100)) + "\n末尾😀"
        assert client.patch("/api/asset-content", params={"asset_id": asset}, headers=AUTH, json={"content": text}).status_code == 200
        response = client.get("/api/asset-content/pdf", params={"asset_id": asset}, headers=AUTH)
        assert response.status_code == 200, response.text
        assert "filename*=UTF-8''" in response.headers["Content-Disposition"]
        inspect_pdf(response.content, text)
        assert client.get("/api/asset-content/pdf", params={"asset_id": asset}).status_code == 401
        sid = client.post("/api/asset-reviews/sessions", headers=AUTH,
                          json={"asset_id": asset, "title": "中文审阅"}).json()["session"]["session_id"]
        did = client.post(f"/api/asset-reviews/sessions/{sid}/delivery", headers=AUTH,
                          json={"title": "最终交付（中文）😀"}).json()["delivery"]["delivery_id"]
        response = client.post(f"/api/asset-reviews/deliveries/{did}/export", headers=AUTH)
        assert response.status_code == 200, response.text
        pymupdf = pytest.importorskip("pymupdf")
        with pymupdf.open(stream=response.content, filetype="pdf") as doc:
            original = doc.embfile_get("source.txt").decode()
        assert "标题: 最终交付（中文）😀" in original
        assert f"素材编号: {asset}" in original
        inspect_pdf(response.content, original)
        assert client.post(f"/api/asset-reviews/deliveries/{did}/export").status_code == 401
        assert client.post(f"/api/asset-reviews/deliveries/{did}/export", headers=READONLY).status_code == 403
