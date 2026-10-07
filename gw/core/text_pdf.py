# Copyright 2026 Gods-Workbench Authors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""无运行依赖的中文文本PDF：明确字形范围、固定字宽、完整分页与UTF-8原文附件。"""
from __future__ import annotations

from gw.core.errors import CleanroomException

MAX_SOURCE_BYTES = 1024 * 1024
MAX_DISPLAY_CHARACTERS = 180000
MAX_PAGES = 100
LINES_PER_PAGE = 42
COLUMNS = 45


def _display_character(char: str) -> str:
    """GB2312印刷字符交由Adobe-GB1字体；其余显式转义，不问号替换。"""
    number = ord(char)
    if 32 <= number <= 126:
        return char
    if number >= 160:
        try:
            char.encode("gb2312", "strict")
            return char
        except UnicodeEncodeError:
            pass
    return ("\\u%04X" if number <= 0xFFFF else "\\U%08X") % number


def _physical_lines(text: str) -> list[str]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = []
    count = 0
    for original_line in normalized.split("\n"):
        displayed = "".join("    " if char == "\t" else _display_character(char) for char in original_line)
        count += len(displayed)
        if count > MAX_DISPLAY_CHARACTERS:
            raise CleanroomException(413, "PDF_EXPORT_LIMIT", "PDF显示字符数超出上限")
        lines.extend([displayed[start:start + COLUMNS] for start in range(0, len(displayed), COLUMNS)] or [""])
        if len(lines) > MAX_PAGES * LINES_PER_PAGE:
            raise CleanroomException(413, "PDF_EXPORT_LIMIT", "PDF页数超出上限")
    return lines


def _stream(data: bytes, entries: bytes = b"") -> bytes:
    return b"<< /Length " + str(len(data)).encode() + b" " + entries + b" >>\nstream\n" + data + b"\nendstream"


def build_text_pdf(text: str) -> bytes:
    """原文附件保存转换前字节；页面以11点固定字符格排版，最多100页。"""
    try:
        source = text.encode("utf-8", "strict")
    except (UnicodeEncodeError, AttributeError):
        raise CleanroomException(400, "INVALID_PDF_TEXT", "PDF原文包含无效Unicode字符") from None
    if len(source) > MAX_SOURCE_BYTES:
        raise CleanroomException(413, "PDF_EXPORT_LIMIT", "PDF原文超过1MiB上限")
    lines = _physical_lines(text)
    characters = sorted(set("".join(lines)))
    if len(characters) > 8192:
        raise CleanroomException(413, "PDF_EXPORT_LIMIT", "PDF字符映射数量超出上限")
    cmap = ["/CIDInit /ProcSet findresource begin", "12 dict begin", "begincmap",
            "/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def",
            "/CMapName /GWTextUnicode def", "/CMapType 2 def", "1 begincodespacerange",
            "<0000> <FFFF>", "endcodespacerange"]
    for start in range(0, len(characters), 100):
        group = characters[start:start + 100]
        cmap.append(f"{len(group)} beginbfchar")
        for char in group:
            code = char.encode("utf-16-be").hex().upper()
            cmap.append(f"<{code}> <{code}>")
        cmap.append("endbfchar")
    cmap.extend(["endcmap", "CMapName currentdict /CMap defineresource pop", "end", "end"])
    pages = [lines[start:start + LINES_PER_PAGE] for start in range(0, len(lines), LINES_PER_PAGE)]
    children = " ".join(f"{9 + 2 * number} 0 R" for number in range(len(pages)))
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R /Names << /EmbeddedFiles << /Names [(source.txt) 7 0 R] >> >> >>",
        f"<< /Type /Pages /Kids [{children}] /Count {len(pages)} >>".encode(),
        b"<< /Type /Font /Subtype /Type0 /BaseFont /STSong-Light /Encoding /UniGB-UTF16-H /DescendantFonts [4 0 R] /ToUnicode 6 0 R >>",
        b"<< /Type /Font /Subtype /CIDFontType0 /BaseFont /STSong-Light /CIDSystemInfo << /Registry (Adobe) /Ordering (GB1) /Supplement 4 >> /FontDescriptor 5 0 R /DW 1000 >>",
        b"<< /Type /FontDescriptor /FontName /STSong-Light /Flags 6 /FontBBox [-25 -254 1000 880] /ItalicAngle 0 /Ascent 880 /Descent -254 /CapHeight 880 /StemV 80 /MissingWidth 1000 >>",
        _stream("\n".join(cmap).encode("ascii")),
        b"<< /Type /Filespec /F (source.txt) /UF (source.txt) /EF << /F 8 0 R /UF 8 0 R >> >>",
        _stream(source, b"/Type /EmbeddedFile /Subtype /text#2Fplain /Params << /Size " + str(len(source)).encode() + b" >>"),
    ]
    for number, page in enumerate(pages):
        page_number = 9 + 2 * number
        content_number = page_number + 1
        objects.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 3 0 R >> >> /Contents {content_number} 0 R >>".encode())
        commands = ["BT /F1 11 Tf 17 TL 48 780 Td"]
        for line in page:
            commands.extend([f"<{line.encode('utf-16-be').hex().upper()}> Tj", "T*"])
        commands.append("ET")
        objects.append(_stream("\n".join(commands).encode("ascii")))
    output = bytearray(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for number, body in enumerate(objects, 1):
        offsets.append(len(output))
        output.extend(f"{number} 0 obj\n".encode() + body + b"\nendobj\n")
    xref = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode())
    output.extend(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return bytes(output)
