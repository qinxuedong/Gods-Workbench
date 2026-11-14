# -*- coding: utf-8 -*-
"""入库期生成元数据提取（QIN-600 / GitHub #709）。

只读图片的**元数据段**，绝不整幅解码像素：PNG 逐 chunk 读到 `IDAT` 即停，
JPEG 按段长 seek 到 `SOS`，WebP 只取 `EXIF` / `XMP ` chunk。实现只用标准库，
不依赖 Pillow（CI 与最小运行环境可能没有 PIL）。

设计口径见 Issue #709 的设计表：
- 字段结构与工具映射：`metadata.generation`，见 `_CANDIDATES` 的优先级顺序。
- 读取上限 1 MiB（`MAX_TEXT_BLOCK_BYTES`），超限截断并置 `truncated=true`。
- 落盘上限 8 KiB（`MAX_STORED_PROMPT_BYTES`，PM Q2 裁决），同样置 `truncated=true`。
- 解析失败只计 warning，不抛异常、不影响该文件入库；文件不可读的 `OSError`
  继续向上冒泡，交给既有扫描错误路径处理。
"""

from __future__ import annotations

import importlib
import json
import os
import re
import struct
import xml.etree.ElementTree as ET
import zlib
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

#: 单个文本块读取上限（票面 AC3）：超过只读前 1 MiB 并标记 truncated。
MAX_TEXT_BLOCK_BYTES = 1024 * 1024
#: 登记落盘上限（PM Q2）：prompt / negative_prompt 各截到此长度。
MAX_STORED_PROMPT_BYTES = 8 * 1024
#: 本模块处理的图片后缀；其他后缀直接跳过、不计 warning。
IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp"})

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_EXIF_PREFIX = b"Exif\x00\x00"
_XMP_PREFIX = b"http://ns.adobe.com/xap/1.0/\x00"
_MAX_IFD_ENTRIES = 512
_MAX_CONDITIONING_DEPTH = 3
_NODE_REF_TYPES = (list, tuple)

_TAG_IMAGE_DESCRIPTION = 0x010E
_TAG_USER_COMMENT = 0x9286
_TAG_EXIF_IFD = 0x8769
_TYPE_SIZES = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8}

_SAMPLER_TYPES = frozenset(
    {"KSampler", "KSamplerAdvanced", "SamplerCustom", "SamplerCustomAdvanced"}
)
_STEPS_LINE = re.compile(r"^Steps:\s*\d", re.IGNORECASE)
_NEGATIVE_LINE = "Negative prompt:"
_MIDJOURNEY_ARGS = re.compile(
    r"\s--(?:ar|v|style|niji|q|s|seed|chaos|stylize|aspect|no|tile|uplight)\b.*$",
    re.IGNORECASE,
)
_MIDJOURNEY_HINTS = ("midjourney", "--ar ", "--v ", "--niji")

#: 设计表 §2.2 的位置优先级：同一文件命中多处时只写一条，`raw_keys` 记录全部命中。
_CANDIDATES: Tuple[str, ...] = (
    "prompt",
    "exif:prompt",
    "parameters",
    "Comment",
    "Description",
    "workflow",
    "exif:workflow",
    "exif:UserComment",
    "exif:ImageDescription",
    "xmp:dc:description",
)

#: QIN-601 设计表 §2.3：只有这四个块参与 ComfyUI 图判定（PNG 文本块 / WebP EXIF）。
_COMFYUI_BLOCK_KEYS: Tuple[str, ...] = (
    "prompt",
    "exif:prompt",
    "workflow",
    "exif:workflow",
)

_RDF_NS = "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}"
_DC_NS = "{http://purl.org/dc/elements/1.1/}"


def is_supported_image(path: Path) -> bool:
    """本模块能读取元数据的图片（按后缀判定，不做内容嗅探）。"""
    return path.suffix.lower() in IMAGE_SUFFIXES


def extract_generation(path: Path) -> Tuple[Optional[Dict[str, Any]], int]:
    """读取一张图片的生成元数据。

    返回 `(generation, warnings)`：
    - `generation` 为 `None` 时不写 `metadata.generation`（无元数据 / 解析失败）；
    - `warnings` 为 0 或 1，供索引任务累加统计。

    QIN-601：返回值内附带 `comfyui` 标记（只含判定结果，不含文本）。有合法
    ComfyUI 图但取不到正向提示词时，写「仅标记」的 `generation` 且不计 warning
    （PM Q2 裁决，设计表 §2.5）。

    `OSError`（不可读、被独占）不在此吞掉，交由既有扫描错误路径计数。
    """
    if not is_supported_image(path):
        return None, 0

    blocks: Dict[str, str] = {}
    block_truncated = False
    suffix = path.suffix.lower()
    try:
        with path.open("rb") as handle:
            if suffix == ".png":
                blocks, block_truncated = _read_png_blocks(handle)
            elif suffix == ".webp":
                blocks, block_truncated = _read_webp_blocks(handle)
            else:
                blocks, block_truncated = _read_jpeg_blocks(handle)
    except OSError:
        raise
    except (ValueError, struct.error, zlib.error):
        return None, 1

    if not blocks:
        return None, 0

    # 本票只判 PNG 内嵌文本块与 WebP EXIF；JPEG 等其它格式行为与 QIN-600 一致。
    marker = (
        _comfyui_marker(blocks) if suffix in (".png", ".webp") else None
    )

    resolved = _resolve(blocks)
    if resolved is None:
        if marker is None:
            # 有候选文本块但都取不出正向文本：设计表 §2.5 记为 warning。
            return None, 1
        # 有合法 ComfyUI 图但无提示词：只写标记，不写 prompt、不计 warning。
        return {"comfyui": marker}, 0

    prompt, prompt_cut = _store_text(resolved["prompt"])
    negative, negative_cut = _store_text(resolved.get("negative_prompt") or "")
    payload: Dict[str, Any] = {
        "prompt": prompt,
        "tool": resolved["tool"],
        "source": resolved["source"],
        "raw_keys": resolved["raw_keys"],
        "truncated": bool(block_truncated or prompt_cut or negative_cut),
    }
    if negative:
        payload["negative_prompt"] = negative
    if marker is not None:
        payload["comfyui"] = marker
    return payload, 0


def _comfyui_marker(blocks: Dict[str, str]) -> Optional[Dict[str, Any]]:
    """按设计表 §2.3 判定内嵌块是否为 ComfyUI 图，返回标记字典或 `None`。

    判定权威复用 `gw.god_workflow.parser.detect_comfyui_graph`（不新写第二套图
    判定）；函数级延迟导入，避免把 `god_workflow` 拉进 `asset_registry` 的启动链。
    多个块逐块独立判定、结果取或（设计表 §2.3：不因一个块失败而否掉另一个）。
    """
    # 与 index_jobs 读视频模块同样的做法：用 importlib 延迟取模块，避免
    # `asset_registry → god_workflow` 在领域导入图里成环（god_workflow 已依赖
    # asset_registry），也避免把 god_workflow 拉进启动链。
    parser = importlib.import_module("gw.god_workflow.parser")
    detect_comfyui_graph = parser.detect_comfyui_graph

    has_workflow = False
    has_prompt_graph = False
    for key in _COMFYUI_BLOCK_KEYS:
        text = blocks.get(key)
        if not text:
            continue
        try:
            payload = json.loads(text)
        except (ValueError, TypeError):
            continue
        found = detect_comfyui_graph(payload)
        if not found:
            continue
        has_workflow = has_workflow or bool(found.get("has_workflow"))
        has_prompt_graph = has_prompt_graph or bool(found.get("has_prompt_graph"))
    if not (has_workflow or has_prompt_graph):
        return None
    return {
        "has_workflow": has_workflow,
        "has_prompt_graph": has_prompt_graph,
        "source": "embedded",
    }


def gw_task_generation(graph: Any) -> Optional[Dict[str, Any]]:
    """从 GW 任务的执行图快照取提示词，返回 `tool=gw` / `source=gw-task` 字段。

    任务的 `prompt_snapshot` 就是提交给执行器的 ComfyUI API 格式图，与 PNG
    `prompt` 文本块同构，因此复用同一套节点追溯逻辑。取不到正向提示词时返回
    `None`（不伪造空对象、不写空字符串）。
    """
    if isinstance(graph, dict):
        if not graph:
            return None
        try:
            text = json.dumps(graph, ensure_ascii=False)
        except (TypeError, ValueError):
            return None
    elif isinstance(graph, str):
        text = graph
    else:
        return None
    parsed = _comfy_api_prompt(text)
    if parsed is None:
        return None
    positive, negative, _hints = parsed
    if not positive:
        return None
    prompt, prompt_cut = _store_text(positive)
    negative_text, negative_cut = _store_text(negative)
    payload: Dict[str, Any] = {
        "prompt": prompt,
        "tool": "gw",
        "source": "gw-task",
        "raw_keys": ["prompt_snapshot"],
        "truncated": bool(prompt_cut or negative_cut),
    }
    if negative_text:
        payload["negative_prompt"] = negative_text
    return payload


# --------------------------------------------------------------------------- #
# 容器读取：只碰元数据段
# --------------------------------------------------------------------------- #
def _read_png_blocks(handle) -> Tuple[Dict[str, str], bool]:
    """读 PNG 的 tEXt / iTXt / zTXt 文本块，遇 `IDAT` 立即停止。"""
    handle.seek(0)
    if handle.read(8) != _PNG_SIGNATURE:
        raise ValueError("not a PNG file")
    blocks: Dict[str, str] = {}
    truncated = False
    while True:
        head = handle.read(8)
        if len(head) < 8:
            break
        length = struct.unpack(">I", head[:4])[0]
        kind = head[4:8]
        if kind in (b"tEXt", b"iTXt", b"zTXt"):
            overflow = length > MAX_TEXT_BLOCK_BYTES
            data = handle.read(min(length, MAX_TEXT_BLOCK_BYTES))
            if len(data) < min(length, MAX_TEXT_BLOCK_BYTES):
                break
            parsed = _png_text_block(kind, data)
            if parsed is not None:
                keyword, text = parsed
                if text:
                    blocks.setdefault(keyword, text)
            if overflow:
                # 块体超限：只保留前 1 MiB，剩余部分与 CRC 都不再读。
                truncated = True
                break
            handle.seek(4, os.SEEK_CUR)  # CRC
            continue
        if kind == b"IDAT":
            break
        handle.seek(length + 4, os.SEEK_CUR)  # 跳过数据 + CRC
    return blocks, truncated


def _png_text_block(kind: bytes, data: bytes) -> Optional[Tuple[str, str]]:
    """解析单个 PNG 文本块的 (keyword, text)；结构非法返回 None。"""
    keyword, _, rest = data.partition(b"\x00")
    if not keyword:
        return None
    if kind == b"tEXt":
        return keyword.decode("latin-1", "replace"), _decode_png_text(rest)
    if kind == b"zTXt":
        if not rest or rest[:1] != b"\x00":
            return None
        try:
            text = _zlib_limited(rest[1:])
        except zlib.error:
            return None
        return keyword.decode("latin-1", "replace"), _decode_png_text(text)
    # iTXt: compression_flag(1) compression_method(1) lang\0translated\0text
    if len(rest) < 2:
        return None
    compressed, method = rest[0], rest[1]
    remainder = rest[2:]
    _language, _, remainder = remainder.partition(b"\x00")
    _translated, _, body = remainder.partition(b"\x00")
    if not body:
        return None
    if compressed == 1:
        if method != 0:
            return None
        try:
            body = _zlib_limited(body)
        except zlib.error:
            return None
    return keyword.decode("latin-1", "replace"), body.decode("utf-8", "replace")


def _zlib_limited(payload: bytes) -> bytes:
    """解压但限制输出到读取上限，避免压缩炸弹撑爆内存。"""
    return zlib.decompressobj().decompress(payload, MAX_TEXT_BLOCK_BYTES)


def _decode_png_text(raw: bytes) -> str:
    """PNG 文本块解码：优先 UTF-8。

    规范上 `tEXt` 是 Latin-1，但实际工具常把 UTF-8（尤其中文提示词）直接写进
    `tEXt`；先按 UTF-8 严格解码，失败再退回 Latin-1，两种都能读。块体被 1 MiB
    上限截断时会切断多字节字符，因此先修剪末尾不完整序列再重试。
    """
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        trimmed = _trim_trailing_partial_utf8(raw)
        if trimmed is not raw:
            return trimmed.decode("utf-8")
        return raw.decode("latin-1", "replace")


def _trim_trailing_partial_utf8(raw: bytes) -> bytes:
    """去掉末尾被截断的多字节字符（最多 3 字节）；无解时原样返回。"""
    for cut in range(1, 4):
        if len(raw) <= cut:
            break
        head = raw[:-cut]
        try:
            head.decode("utf-8")
        except UnicodeDecodeError:
            continue
        return head
    return raw


def _read_jpeg_blocks(handle) -> Tuple[Dict[str, str], bool]:
    """读 JPEG 的 APP1（EXIF / XMP）与 COM，遇 `SOS` 停止。"""
    handle.seek(0)
    if handle.read(2) != b"\xff\xd8":
        raise ValueError("not a JPEG file")
    exif = b""
    xmp = b""
    truncated = False
    while True:
        marker = handle.read(1)
        if not marker:
            break
        if marker != b"\xff":
            continue
        code_byte = handle.read(1)
        while code_byte == b"\xff":  # 填充字节
            code_byte = handle.read(1)
        if not code_byte:
            break
        code = code_byte[0]
        if code == 0x00 or code == 0xD8 or 0xD0 <= code <= 0xD7:
            continue
        if code == 0xD9:
            break
        raw_length = handle.read(2)
        if len(raw_length) < 2:
            break
        length = struct.unpack(">H", raw_length)[0]
        if length < 2:
            break
        payload_length = length - 2
        if code == 0xDA:  # SOS：后面是压缩数据，不再读
            break
        oversized = payload_length > MAX_TEXT_BLOCK_BYTES
        if code == 0xE1:
            read_length = min(payload_length, MAX_TEXT_BLOCK_BYTES)
            payload = handle.read(read_length)
            if oversized:
                truncated = True
                break
            if payload.startswith(_EXIF_PREFIX):
                exif = exif or payload[len(_EXIF_PREFIX) :]
            elif payload.startswith(_XMP_PREFIX):
                xmp = xmp or payload[len(_XMP_PREFIX) :]
            else:
                handle.seek(payload_length - read_length, os.SEEK_CUR)
            continue
        handle.seek(payload_length, os.SEEK_CUR)

    blocks: Dict[str, str] = {}
    if exif:
        entries = _exif_entries(exif)
        description = _decode_exif_text(entries.get(_TAG_IMAGE_DESCRIPTION) or b"")
        if description:
            blocks["exif:ImageDescription"] = description
        user_comment = _decode_exif_text(entries.get(_TAG_USER_COMMENT) or b"")
        if user_comment:
            blocks["exif:UserComment"] = user_comment
    if xmp:
        description = _xmp_description(xmp)
        if description:
            blocks["xmp:dc:description"] = description
    return blocks, truncated


def _read_webp_blocks(handle) -> Tuple[Dict[str, str], bool]:
    """读 WebP 的 `EXIF` / `XMP ` chunk（RIFF 结构，逐 chunk 跳读）。"""
    handle.seek(0)
    header = handle.read(12)
    if len(header) < 12 or header[:4] != b"RIFF" or header[8:12] != b"WEBP":
        raise ValueError("not a WebP file")
    exif = b""
    xmp = b""
    truncated = False
    while True:
        head = handle.read(8)
        if len(head) < 8:
            break
        fourcc = head[:4]
        size = struct.unpack("<I", head[4:])[0]
        if fourcc in (b"EXIF", b"XMP "):
            oversized = size > MAX_TEXT_BLOCK_BYTES
            payload = handle.read(min(size, MAX_TEXT_BLOCK_BYTES))
            if oversized:
                truncated = True
                break
            if fourcc == b"EXIF":
                exif = exif or payload
            else:
                xmp = xmp or payload
        else:
            handle.seek(size, os.SEEK_CUR)
        if size % 2:
            handle.seek(1, os.SEEK_CUR)

    blocks: Dict[str, str] = {}
    if exif:
        blocks.update(_webp_exif_blocks(exif))
    if xmp:
        description = _xmp_description(xmp)
        if description:
            blocks["xmp:dc:description"] = description
    return blocks, truncated


def _webp_exif_blocks(exif: bytes) -> Dict[str, str]:
    """WebP EXIF 既可能是 TIFF 结构，也可能是 `prompt:{json}` 形式文本。"""
    blocks: Dict[str, str] = {}
    tiff = exif[len(_EXIF_PREFIX) :] if exif.startswith(_EXIF_PREFIX) else exif
    try:
        entries = _exif_entries(tiff)
    except (ValueError, struct.error):
        entries = {}
    description = _decode_exif_text(entries.get(_TAG_IMAGE_DESCRIPTION) or b"")
    if description:
        blocks["exif:ImageDescription"] = description
    user_comment = _decode_exif_text(entries.get(_TAG_USER_COMMENT) or b"")
    if user_comment:
        blocks["exif:UserComment"] = user_comment
    text = tiff.decode("utf-8", "replace")
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if key in {"prompt", "workflow"} and value:
            blocks[f"exif:{key}"] = value
    return blocks


# --------------------------------------------------------------------------- #
# EXIF / XMP
# --------------------------------------------------------------------------- #
def _exif_entries(tiff: bytes) -> Dict[int, bytes]:
    """遍历 TIFF IFD（含 EXIF 子 IFD），返回关注的标签原始字节。"""
    if len(tiff) < 8:
        raise ValueError("EXIF payload too short")
    order = tiff[:2]
    if order == b"II":
        endian = "<"
    elif order == b"MM":
        endian = ">"
    else:
        raise ValueError("invalid EXIF byte order")
    first_ifd = struct.unpack(endian + "I", tiff[4:8])[0]
    entries: Dict[int, bytes] = {}
    visited = set()
    pending = [first_ifd]
    while pending:
        offset = pending.pop()
        if offset in visited or offset < 8 or offset + 2 > len(tiff):
            continue
        visited.add(offset)
        count = struct.unpack(endian + "H", tiff[offset : offset + 2])[0]
        if count > _MAX_IFD_ENTRIES:
            continue
        cursor = offset + 2
        for _ in range(count):
            if cursor + 12 > len(tiff):
                break
            tag, value_type, item_count = struct.unpack(
                endian + "HHI", tiff[cursor : cursor + 8]
            )
            size = _TYPE_SIZES.get(value_type, 0) * item_count
            value_at = cursor + 8
            if size and size <= 4:
                data = tiff[value_at : value_at + size]
            elif size:
                pointer = struct.unpack(endian + "I", tiff[value_at : value_at + 4])[0]
                data = tiff[pointer : pointer + size]
            else:
                data = b""
            if tag in (_TAG_USER_COMMENT, _TAG_IMAGE_DESCRIPTION) and data:
                entries.setdefault(tag, data)
            elif tag == _TAG_EXIF_IFD and len(data) >= 4:
                pending.append(struct.unpack(endian + "I", data[:4])[0])
            cursor += 12
    return entries


def _decode_exif_text(raw: bytes) -> str:
    """解码 EXIF 文本，去掉 UserComment 的 8 字节字符集前缀与尾部 NUL。"""
    if not raw:
        return ""
    prefix, body = raw[:8], raw[8:]
    if prefix.startswith(b"UNICODE"):
        if body[:2] in (b"\xff\xfe", b"\xfe\xff"):
            return _clean_exif_text(body.decode("utf-16", "replace"))
        return _clean_exif_text(body.decode("utf-16-le", "replace"))
    if prefix.startswith(b"ASCII"):
        return _clean_exif_text(body.decode("ascii", "replace"))
    if prefix.startswith(b"JIS"):
        try:
            return _clean_exif_text(body.decode("shift_jis"))
        except UnicodeDecodeError:
            return _clean_exif_text(body.decode("latin-1", "replace"))
    return _clean_exif_text(raw.decode("utf-8", "replace"))


def _clean_exif_text(text: str) -> str:
    """EXIF 的 ASCII/UNDEFINED 字段常以 NUL 补齐，取文本前去掉尾部空字符。"""
    return text.strip("\x00").strip()


def _xmp_description(xmp: bytes) -> str:
    """取 XMP `dc:description`；含 DOCTYPE 的包直接放弃（杜绝实体膨胀）。"""
    text = xmp.decode("utf-8", "replace")
    if not text.strip() or "<!DOCTYPE" in text:
        return ""
    try:
        # 上面已拒绝含 DOCTYPE 的包体；ElementTree 不加载外部实体，
        # 且 XMP 包体在读取阶段就被限制在 1 MiB 内。
        root = ET.fromstring(text)  # noqa: S314
    except ET.ParseError:
        return ""
    for description in root.iter(_RDF_NS + "Description"):
        attribute = description.get(_DC_NS + "description")
        if attribute and attribute.strip():
            return attribute.strip()
        for child in description:
            if child.tag != _DC_NS + "description":
                continue
            for item in child.iter(_RDF_NS + "li"):
                value = (item.text or "").strip()
                if value:
                    return value
    return ""


# --------------------------------------------------------------------------- #
# 各工具的提示词取法
# --------------------------------------------------------------------------- #
def _resolve(blocks: Dict[str, str]) -> Optional[Dict[str, Any]]:
    """按设计表 §2.2 的优先级挑选一条结果。"""
    raw_keys = [key for key in _CANDIDATES if key in blocks]
    for key in _CANDIDATES:
        text = blocks.get(key)
        if not text:
            continue
        parsed = _parse_position(key, text)
        if parsed is None:
            continue
        positive, negative, tool, extra_keys = parsed
        if not positive:
            continue
        return {
            "prompt": positive,
            "negative_prompt": negative,
            "tool": tool,
            "source": key,
            "raw_keys": raw_keys + [item for item in extra_keys if item not in raw_keys],
        }
    return None


def _parse_position(
    key: str, text: str
) -> Optional[Tuple[str, str, str, List[str]]]:
    """把某个位置上的文本解析成 (正向, 反向, tool, 追加的 raw_keys)。"""
    if key in ("prompt", "exif:prompt"):
        parsed = _comfy_api_prompt(text)
        if parsed is None:
            return None
        positive, negative, hints = parsed
        return positive, negative, "comfyui", hints
    if key in ("workflow", "exif:workflow"):
        parsed = _comfy_ui_prompt(text)
        if parsed is None:
            return None
        positive, negative = parsed
        return positive, negative, "comfyui", []
    if key == "parameters":
        positive, negative = _a1111_parts(text)
        if not positive:
            return None
        return positive, negative, "a1111", []
    if key == "Comment":
        parsed = _novelai_parts(text)
        if parsed is None:
            return None
        positive, negative = parsed
        return positive, negative, "novelai", []
    plain = text.strip()
    if not plain:
        return None
    # PNG `Description` 是 NovelAI 的 `Comment` 缺失回退位置（设计表 §2.2）。
    if key == "Description" and not _looks_like_midjourney(plain):
        return plain, "", "novelai", []
    tool = "midjourney" if _looks_like_midjourney(plain) else "unknown"
    return _strip_midjourney_args(plain), "", tool, []


def _a1111_parts(text: str) -> Tuple[str, str]:
    """A1111 / Forge 的 `parameters`：末行参数行之前的正/反向提示词。"""
    lines = text.splitlines()
    parameter_index = len(lines)
    for index in range(len(lines) - 1, -1, -1):
        if _STEPS_LINE.match(lines[index].strip()):
            parameter_index = index
            break
    positive: List[str] = []
    negative: List[str] = []
    in_negative = False
    for line in lines[:parameter_index]:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.lower().startswith(_NEGATIVE_LINE.lower()):
            in_negative = True
            remainder = stripped[len(_NEGATIVE_LINE) :].strip()
            if remainder:
                negative.append(remainder)
            continue
        if in_negative:
            negative.append(stripped)
        else:
            positive.append(stripped)
    return "\n".join(positive).strip(), "\n".join(negative).strip()


def _novelai_parts(text: str) -> Optional[Tuple[str, str]]:
    """NovelAI 的 `Comment` / `Description`：JSON 里的 `prompt` 与 `uc`。"""
    try:
        payload = json.loads(text)
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    positive = payload.get("prompt")
    if not isinstance(positive, str) or not positive.strip():
        return None
    negative = payload.get("uc")
    return positive.strip(), negative.strip() if isinstance(negative, str) else ""


def _looks_like_midjourney(text: str) -> bool:
    lowered = text.lower()
    return any(hint in lowered for hint in _MIDJOURNEY_HINTS)


def _strip_midjourney_args(text: str) -> str:
    """去掉 Midjourney 的 `--ar/--v/--style` 参数后缀，只留首段提示词。"""
    first_line = text.splitlines()[0] if text.splitlines() else text
    return _MIDJOURNEY_ARGS.sub("", first_line).strip()


def _comfy_api_prompt(text: str) -> Optional[Tuple[str, str, List[str]]]:
    """ComfyUI API 格式：从采样器的 `inputs.positive` 追到文本编码节点。"""
    try:
        payload = json.loads(text)
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, dict) or not payload:
        return None
    nodes: Dict[str, Dict[str, Any]] = {}
    for node_id, node in payload.items():
        if isinstance(node, dict) and "class_type" in node:
            nodes[str(node_id)] = node
    if not nodes:
        return None
    ordered = sorted(nodes, key=_node_sort_key)

    for node_id in ordered:
        node = nodes[node_id]
        if str(node.get("class_type")) not in _SAMPLER_TYPES:
            continue
        inputs = node.get("inputs") or {}
        positive = _follow_conditioning(nodes, inputs.get("positive"))
        if positive is None:
            continue
        negative = _follow_conditioning(nodes, inputs.get("negative"))
        return positive[0], negative[0] if negative else "", []

    # 回退：链路断开 / 无采样器时按节点 id 升序取文本编码节点。
    encoded = [
        (node_id, _encode_text(nodes[node_id]))
        for node_id in ordered
        if "CLIPTextEncode" in str(nodes[node_id].get("class_type"))
    ]
    encoded = [(node_id, value) for node_id, value in encoded if value]
    if not encoded:
        return None
    positive_id, positive_text = encoded[0]
    negative_text = encoded[1][1] if len(encoded) > 1 else ""
    return positive_text, negative_text, [f"prompt#node:{positive_id}"]


def _follow_conditioning(
    nodes: Dict[str, Dict[str, Any]], reference: Any, depth: int = 0
) -> Optional[Tuple[str, str]]:
    """沿 conditioning 引用向上追到文本编码节点。"""
    if depth > _MAX_CONDITIONING_DEPTH:
        return None
    node_id = _reference_node_id(reference)
    if node_id is None:
        return None
    node = nodes.get(node_id)
    if node is None:
        return None
    if "CLIPTextEncode" in str(node.get("class_type")):
        value = _encode_text(node)
        return (value, node_id) if value else None
    inputs = node.get("inputs") or {}
    for key in sorted(inputs):
        if not str(key).startswith("conditioning"):
            continue
        found = _follow_conditioning(nodes, inputs[key], depth + 1)
        if found is not None:
            return found
    return None


def _reference_node_id(reference: Any) -> Optional[str]:
    if isinstance(reference, str):
        return reference.strip() or None
    if isinstance(reference, _NODE_REF_TYPES) and reference:
        return str(reference[0])
    if isinstance(reference, int):
        return str(reference)
    return None


def _encode_text(node: Dict[str, Any]) -> str:
    """文本编码节点的正向文本；SDXL 优先取 `text_g`。"""
    inputs = node.get("inputs") or {}
    keys = ("text_g", "text_l", "text") if "SDXL" in str(node.get("class_type")) else ("text", "text_g")
    for key in keys:
        value = inputs.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _comfy_ui_prompt(text: str) -> Optional[Tuple[str, str]]:
    """ComfyUI UI 格式（`workflow` 块）：`nodes[].widgets_values[0]`。"""
    try:
        payload = json.loads(text)
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    nodes = payload.get("nodes")
    if not isinstance(nodes, list):
        return None
    by_id = {
        str(node.get("id")): node
        for node in nodes
        if isinstance(node, dict) and node.get("id") is not None
    }
    links = payload.get("links")
    ordered = sorted(by_id, key=_node_sort_key)

    for node_id in ordered:
        node = by_id[node_id]
        if str(node.get("type")) not in _SAMPLER_TYPES:
            continue
        source_id = _ui_link_source(links, _ui_input_link(node, "positive"))
        if source_id is None:
            continue
        positive = _ui_widget_text(by_id.get(source_id))
        if not positive:
            continue
        negative = ""
        negative_source = _ui_link_source(links, _ui_input_link(node, "negative"))
        if negative_source is not None:
            negative = _ui_widget_text(by_id.get(negative_source))
        return positive, negative

    encoded = [
        (node_id, _ui_widget_text(by_id[node_id]))
        for node_id in ordered
        if "CLIPTextEncode" in str(by_id[node_id].get("type"))
    ]
    encoded = [(node_id, value) for node_id, value in encoded if value]
    if not encoded:
        return None
    return encoded[0][1], encoded[1][1] if len(encoded) > 1 else ""


def _ui_input_link(node: Dict[str, Any], name: str) -> Any:
    inputs = node.get("inputs")
    if not isinstance(inputs, list):
        return None
    for item in inputs:
        if isinstance(item, dict) and str(item.get("name")) == name:
            return item.get("link")
    return None


def _ui_link_source(links: Any, link_id: Any) -> Optional[str]:
    if link_id is None or not isinstance(links, list):
        return None
    for link in links:
        if isinstance(link, _NODE_REF_TYPES) and len(link) >= 2 and link[0] == link_id:
            return str(link[1])
    return None


def _ui_widget_text(node: Optional[Dict[str, Any]]) -> str:
    if not isinstance(node, dict):
        return ""
    widgets = node.get("widgets_values")
    if isinstance(widgets, list) and widgets:
        first = widgets[0]
        if isinstance(first, str) and first.strip():
            return first.strip()
    return ""


def _node_sort_key(node_id: str) -> Tuple[int, int, str]:
    """节点 id 升序：纯数字按数值，其余按字符串，保证遍历确定性。"""
    return (0, int(node_id), "") if node_id.isdigit() else (1, 0, node_id)


# --------------------------------------------------------------------------- #
# 落盘截断
# --------------------------------------------------------------------------- #
def _store_text(text: str) -> Tuple[str, bool]:
    """把提示词截到登记上限（8 KiB），按 UTF-8 边界安全截断。"""
    if not text:
        return "", False
    raw = text.encode("utf-8")
    if len(raw) <= MAX_STORED_PROMPT_BYTES:
        return text, False
    clipped = raw[:MAX_STORED_PROMPT_BYTES]
    while clipped:
        try:
            return clipped.decode("utf-8"), True
        except UnicodeDecodeError:
            clipped = clipped[:-1]
    return "", True
