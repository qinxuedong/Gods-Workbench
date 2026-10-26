"""三类中文PDF导出：完整分页、源文无损、错误边界和真实渲染。"""

import math
import re

import pytest
from fastapi.testclient import TestClient

from gw.api.app import create_app
from gw.core.errors import CleanroomException
from gw.core.text_pdf import _physical_lines, build_text_pdf

AUTH = {"Authorization": "Bearer cleanroom-test", "X-User-Role": "editor"}
READONLY = {**AUTH, "X-User-Role": "readonly"}


def test_pdf_structure_inspector_extracts_exact_embedded_source():
    source = "正文\n尾项😀"
    content = build_text_pdf(source)

    assert _extract_pdf_attachment(content, "source.txt") == source.encode("utf-8")


def _parse_pdf_objects(content):
    assert content.startswith(b"%PDF-1.7\n")
    assert content.endswith(b"%%EOF\n")
    startxref = re.search(rb"startxref\n(\d+)\n%%EOF\n\Z", content)
    assert startxref
    xref_offset = int(startxref.group(1))
    xref_lines = content[xref_offset:].splitlines()
    assert xref_lines[0] == b"xref"
    subsection = re.fullmatch(rb"0 (\d+)", xref_lines[1])
    assert subsection
    size = int(subsection.group(1))
    entries = xref_lines[2 : 2 + size]
    trailer_index = 2 + size
    assert len(entries) == size
    assert entries[0].strip() == b"0000000000 65535 f"
    assert xref_lines[trailer_index] == b"trailer"
    trailer = xref_lines[trailer_index + 1]
    trailer_size = re.fullmatch(rb"<< /Size (\d+) /Root 1 0 R >>", trailer)
    assert trailer_size and int(trailer_size.group(1)) == size

    objects = {}
    for number, entry in enumerate(entries[1:], start=1):
        xref_entry = re.fullmatch(rb"(\d{10}) \d{5} n\s*", entry)
        assert xref_entry
        offset = int(xref_entry.group(1))
        header = re.match(rb"(\d+) 0 obj\n", content[offset:])
        assert header and int(header.group(1)) == number
        body_start = offset + header.end()
        stream_marker = content.find(b"\nstream\n", body_start)
        object_end = content.find(b"\nendobj\n", body_start)
        assert object_end >= 0
        if stream_marker >= 0 and stream_marker < object_end:
            dictionary = content[body_start:stream_marker]
            length = re.search(rb"/Length (\d+)", dictionary)
            assert length
            stream_start = stream_marker + len(b"\nstream\n")
            stream_end = stream_start + int(length.group(1))
            assert content[stream_end:].startswith(b"\nendstream\nendobj\n")
            objects[number] = (dictionary, content[stream_start:stream_end])
        else:
            objects[number] = (content[body_start:object_end], None)
    return objects


def _extract_pdf_attachment(content, name):
    objects = _parse_pdf_objects(content)
    catalog = objects[1][0]
    assert b"/Type /Catalog" in catalog
    encoded_name = name.encode("ascii")
    file_spec_ref = re.search(
        rb"\(" + re.escape(encoded_name) + rb"\) (\d+) 0 R", catalog
    )
    assert file_spec_ref
    file_spec = objects[int(file_spec_ref.group(1))][0]
    file_refs = re.search(rb"/EF << /F (\d+) 0 R /UF (\d+) 0 R >>", file_spec)
    assert file_refs and file_refs.group(1) == file_refs.group(2)
    embedded_dictionary, embedded = objects[int(file_refs.group(1))]
    assert embedded is not None and b"/Type /EmbeddedFile" in embedded_dictionary
    declared_size = re.search(rb"/Params << /Size (\d+) >>", embedded_dictionary)
    assert declared_size and int(declared_size.group(1)) == len(embedded)
    return embedded


def inspect_pdf(content, source, expected_pages=None):
    objects = _parse_pdf_objects(content)
    assert _extract_pdf_attachment(content, "source.txt") == source.encode("utf-8")
    expected = _physical_lines(source)
    page_count = expected_pages or math.ceil(len(expected) / 42)

    pages_tree = objects[2][0]
    count = re.search(rb"/Count (\d+)", pages_tree)
    kids = re.search(rb"/Kids \[(.*?)\]", pages_tree)
    assert count and int(count.group(1)) == page_count
    assert kids
    page_numbers = [int(number) for number in re.findall(rb"(\d+) 0 R", kids.group(1))]
    assert len(page_numbers) == page_count

    type0_font = objects[3][0]
    descendant_ref = re.search(rb"/DescendantFonts \[(\d+) 0 R\]", type0_font)
    cmap_ref = re.search(rb"/ToUnicode (\d+) 0 R", type0_font)
    assert descendant_ref and cmap_ref
    descendant_font = objects[int(descendant_ref.group(1))][0]
    assert b"/DW 1000" in descendant_font
    cmap = objects[int(cmap_ref.group(1))][1]
    assert cmap is not None
    mappings = set(re.findall(rb"<([0-9A-F]+)> <([0-9A-F]+)", cmap))

    actual = []
    for page_number in page_numbers:
        page = objects[page_number][0]
        assert b"/Type /Page " in page
        assert b"/MediaBox [0 0 595 842]" in page
        content_ref = re.search(rb"/Contents (\d+) 0 R", page)
        assert content_ref
        page_stream = objects[int(content_ref.group(1))][1]
        assert page_stream is not None
        text_body = re.fullmatch(
            rb"BT /F1 11 Tf 17 TL 48 780 Td\n(.*)\nET", page_stream, re.DOTALL
        )
        assert text_body
        commands = text_body.group(1)
        encoded_lines = re.findall(rb"<([0-9A-F]*)> Tj\nT\*", commands)
        reconstructed = b"\n".join(
            command for value in encoded_lines for command in (b"<" + value + b"> Tj", b"T*")
        )
        assert commands == reconstructed
        assert len(encoded_lines) <= 42

        for line_index, encoded_line in enumerate(encoded_lines):
            line = bytes.fromhex(encoded_line.decode("ascii")).decode("utf-16-be")
            assert len(line) <= 45
            left, right = 48, 48 + len(line) * 11
            baseline = 780 - line_index * 17
            top, bottom = 842 - baseline - 9.68, 842 - baseline + 2.794
            assert 47.5 <= left <= right <= 547.5
            assert 47.5 <= top <= bottom <= 794.5
            actual.extend([line] if line.strip() else [])
            for char in set(line):
                code = char.encode("utf-16-be").hex().upper().encode("ascii")
                assert (code, code) in mappings

    assert actual == [line for line in expected if line.strip()]


@pytest.mark.parametrize("count", [1, 41, 42, 43, 100])
def test_pdf_physical_line_pagination_and_visible_tail(count):
    text = "\n".join(f"第{i:03d}项中文（标点）ABC\\" for i in range(count))
    inspect_pdf(build_text_pdf(text), text, math.ceil(count / 42))


def test_pdf_wraps_after_escape_and_preserves_original_newlines():
    text = "中文" * 70 + "\r\nASCII " + "a" * 99 + "\t括号()\\，。😀𠀀\r尾项核实"
    inspect_pdf(build_text_pdf(text), text)


@pytest.mark.parametrize(
    "text, status",
    [
        ("\ud800", 400),
        ("x" * (1024 * 1024 + 1), 413),
        ("x" * 180001, 413),
        ("\n" * 4200, 413),
    ],
    ids=["invalid-unicode", "source-limit", "display-limit", "page-limit"],
)
def test_pdf_rejects_invalid_unicode_and_limits_without_truncation(text, status):
    with pytest.raises(CleanroomException) as error:
        build_text_pdf(text)
    assert error.value.status_code == status


@pytest.mark.parametrize("count", [1, 41, 100])
def test_registry_pdf_full_records_duplicate_count_and_unknown_id(count):
    path = "/api/asset-registry/assets"
    with TestClient(create_app()) as client:
        created = client.post(
            path + "/import",
            headers=AUTH,
            json={
                "items": [
                    {"name": f"中文素材{i:03d}", "kind": "image"} for i in range(count)
                ]
            },
        )
        assert created.status_code == 200, created.text
        records = created.json()["assets"]
        ids = [item["asset_id"] for item in records]
        expected = "\n".join(
            f"{a['asset_id']} | {a['name']} | {a['kind']}" for a in records
        )
        response = client.post(
            path + "/export-pdf",
            headers=AUTH,
            json={"asset_ids": ids + ids[:1], "name": "中文清单"},
        )
        assert response.status_code == 200, response.text
        assert response.headers["X-PDF-Exported"] == str(count)
        assert response.headers["X-PDF-Skipped"] == "0"
        assert "filename*=UTF-8''" in response.headers["Content-Disposition"]
        inspect_pdf(response.content, expected)
        assert (
            client.post(
                path + "/export-pdf",
                headers=AUTH,
                json={"asset_ids": [ids[0], "missing"]},
            ).status_code
            == 404
        )
        assert (
            client.post(path + "/export-pdf", json={"asset_ids": ids}).status_code
            == 401
        )
        assert (
            client.post(
                path + "/export-pdf", headers=READONLY, json={"asset_ids": ids}
            ).status_code
            == 200
        )


def test_content_and_delivery_pdf_real_chinese_http_exports():
    with TestClient(create_app()) as client:
        asset = client.post(
            "/api/asset-registry/assets/import",
            headers=AUTH,
            json={"items": [{"name": "交付文档", "kind": "document"}]},
        ).json()["asset_ids"][0]
        text = (
            "正文\r\n"
            + "\n".join(f"第{i:03d}行中文原文" for i in range(100))
            + "\n末尾😀"
        )
        assert (
            client.patch(
                "/api/asset-content",
                params={"asset_id": asset},
                headers=AUTH,
                json={"content": text},
            ).status_code
            == 200
        )
        response = client.get(
            "/api/asset-content/pdf", params={"asset_id": asset}, headers=AUTH
        )
        assert response.status_code == 200, response.text
        assert "filename*=UTF-8''" in response.headers["Content-Disposition"]
        inspect_pdf(response.content, text)
        assert (
            client.get("/api/asset-content/pdf", params={"asset_id": asset}).status_code
            == 401
        )
        sid = client.post(
            "/api/asset-reviews/sessions",
            headers=AUTH,
            json={"asset_id": asset, "title": "中文审阅"},
        ).json()["session"]["session_id"]
        did = client.post(
            f"/api/asset-reviews/sessions/{sid}/delivery",
            headers=AUTH,
            json={"title": "最终交付（中文）😀"},
        ).json()["delivery"]["delivery_id"]
        response = client.post(
            f"/api/asset-reviews/deliveries/{did}/export", headers=AUTH
        )
        assert response.status_code == 200, response.text
        original = _extract_pdf_attachment(response.content, "source.txt").decode(
            "utf-8"
        )
        assert "标题: 最终交付（中文）😀" in original
        assert f"素材编号: {asset}" in original
        inspect_pdf(response.content, original)
        assert (
            client.post(f"/api/asset-reviews/deliveries/{did}/export").status_code
            == 401
        )
        assert (
            client.post(
                f"/api/asset-reviews/deliveries/{did}/export", headers=READONLY
            ).status_code
            == 403
        )
