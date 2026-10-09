"""Strict, source-neutral workflow payload parser."""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import html
import math
import os
import re
from typing import Any

from .models import WorkflowConnection, WorkflowDocument, WorkflowNode, WorkflowSource


MAX_UPLOAD_BYTES = 4 * 1024 * 1024
MAX_PNG_TEXT_BYTES = 1024 * 1024


def png_available() -> bool:
    try:
        import PIL.Image  # noqa: F401
        return True
    except ImportError:
        return False


def parse_upload(content: bytes, media_type: str, *, source: str = "comfyui", name: str = "workflow") -> WorkflowDocument:
    """Parse raw JSON/PNG bytes; never persist uploads or open caller-supplied paths."""
    import io
    import json

    if len(content) > MAX_UPLOAD_BYTES:
        raise WorkflowParseError("upload exceeds the size limit")
    if media_type == "application/octet-stream" and content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        media_type = "image/webp"
    elif media_type == "application/octet-stream" and content.startswith(b"\x89PNG"):
        media_type = "image/png"
    if media_type in {"application/json", "text/json", "application/octet-stream"}:
        try:
            payload = json.loads(content)
        except (ValueError, UnicodeDecodeError):
            raise WorkflowParseError("upload must contain valid JSON") from None
        return parse_workflow(payload, source=source, name=name)
    if media_type not in {"image/png", "image/webp"}:
        raise WorkflowParseError("unsupported upload media type")
    try:
        from PIL import Image
    except ImportError:
        raise WorkflowParseError("PNG metadata dependency is unavailable") from None
    try:
        with Image.open(io.BytesIO(content)) as image:
            if image.format not in {"PNG", "WEBP"}:
                raise WorkflowParseError("upload is not a PNG/WebP image")
            text = getattr(image, "text", None) or {
                key: value for key, value in image.info.items() if isinstance(value, str)
            }
            if image.format == "WEBP":
                for value in image.getexif().values():
                    if isinstance(value, bytes):
                        value = value.decode("utf-8")
                    if isinstance(value, str) and ":" in value:
                        key, encoded = value.split(":", 1)
                        if key in {"workflow", "prompt"}:
                            text[key] = encoded
            if sum(len(str(value).encode("utf-8")) for value in text.values()) > MAX_PNG_TEXT_BYTES:
                raise WorkflowParseError("PNG text exceeds the size limit")
            key = "workflow" if "workflow" in text else "prompt"
            if key not in text:
                raise WorkflowParseError("PNG contains no workflow or prompt text block")
            payload = json.loads(text[key])
        return parse_workflow(payload, source="canvas" if key == "workflow" else "comfyui", name=name)
    except WorkflowParseError:
        raise
    except Exception:
        # Pillow decoder details can contain implementation-specific paths.
        raise WorkflowParseError("invalid PNG or workflow metadata") from None


class WorkflowParseError(ValueError):
    """Input is not a supported workflow representation."""


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise WorkflowParseError(f"{label} must be an object")
    return value


def _position(value: Any) -> tuple[float, float, float | None, float | None]:
    if isinstance(value, Mapping):
        raw_x, raw_y = value.get("x"), value.get("y")
        raw_width, raw_height = value.get("width"), value.get("height")
    elif isinstance(value, (list, tuple)) and len(value) >= 2:
        raw_x, raw_y = value[0], value[1]
        raw_width = raw_height = None
    else:
        if value is None:
            return (0.0, 0.0, None, None)
        raise WorkflowParseError("node position must contain numeric x/y")
    try:
        x, y = float(raw_x), float(raw_y)
        width = None if raw_width is None else float(raw_width)
        height = None if raw_height is None else float(raw_height)
    except (TypeError, ValueError) as exc:
        raise WorkflowParseError("node position must contain numeric x/y") from exc
    if not all(math.isfinite(number) for number in (x, y) if number is not None):
        raise WorkflowParseError("node position must contain finite numeric values")
    return x, y, width, height


def _link_tuple(raw: Any) -> tuple[Any, Any, Any, Any, Any] | None:
    """Return id, source, source slot, target, target slot for canvas array links."""
    if isinstance(raw, (list, tuple)):
        if len(raw) < 4:
            return None
        return (raw[0] if len(raw) > 0 else None, raw[1], raw[2] if len(raw) > 2 else None,
                raw[3], raw[4] if len(raw) > 4 else None)
    return None


def _api_link(value: Any) -> tuple[str, str] | None:
    """Extract a Comfy-style [node id, output slot] input reference."""
    if isinstance(value, (list, tuple)) and len(value) >= 2 and not isinstance(value[0], (dict, list, tuple)):
        return str(value[0]), str(value[1])
    if isinstance(value, Mapping):
        node = value.get("node_id", value.get("node", value.get("id")))
        slot = value.get("slot", value.get("output", value.get("index")))
        if node is not None and slot is not None:
            return str(node), str(slot)
    return None


def parse_workflow(payload: Any, *, source: WorkflowSource | str = WorkflowSource.COMFYUI,
                   name: str = "workflow") -> WorkflowDocument:
    """Parse ComfyUI API, canvas JSON, and their public interchange variants."""
    root = _mapping(extract_source_graph(payload) or payload, "workflow")
    try:
        selected = source if isinstance(source, WorkflowSource) else WorkflowSource(str(source).lower())
    except ValueError as exc:
        raise WorkflowParseError("unsupported workflow source") from exc

    if isinstance(root.get("nodes"), list) and any("type" in n and "class_type" not in n for n in root["nodes"] if isinstance(n, dict)):
        selected = WorkflowSource.CANVAS

    # Error envelopes are never valid workflow graphs, even when they contain arbitrary metadata.
    if "error" in root and "nodes" not in root and "links" not in root and "connections" not in root:
        raise WorkflowParseError("workflow error payload is not a graph")

    raw_nodes = root.get("nodes")
    api_map = (
        raw_nodes is None
        and all(isinstance(key, str) for key in root)
        and any(
            isinstance(value, Mapping)
            and ("class_type" in value or "inputs" in value or selected == WorkflowSource.COMFYUI)
            for value in root.values()
        )
    )
    if api_map:
        raw_nodes = [
            {"id": key, **dict(_mapping(value, "node"))}
            for key, value in root.items()
            if isinstance(value, Mapping)
            and ("class_type" in value or "inputs" in value or selected == WorkflowSource.COMFYUI)
        ]
    if raw_nodes is None:
        raw_nodes = [] if not root else None
    if not isinstance(raw_nodes, list) or (selected == WorkflowSource.CANVAS and raw_nodes is None):
        raise WorkflowParseError("workflow nodes must be an array or ComfyUI node map")

    nodes: list[WorkflowNode] = []
    node_ids: set[str] = set()
    generated_links: list[WorkflowConnection] = []
    for index, raw in enumerate(raw_nodes):
        item = _mapping(raw, f"nodes[{index}]")
        node_id = str(item.get("id") or item.get("entity_id") or index)
        if node_id in node_ids:
            raise WorkflowParseError(f"duplicate node id: {node_id}")
        node_ids.add(node_id)
        kind_value = item.get("kind") or item.get("class_type") or item.get("type")
        if selected == WorkflowSource.COMFYUI and not (item.get("class_type") or item.get("kind")):
            raise WorkflowParseError(f"nodes[{index}] must contain class_type")
        kind = str(kind_value or "node")
        if selected == WorkflowSource.COMFYUI and not isinstance(item.get("inputs"), Mapping):
            raise WorkflowParseError(f"nodes[{index}] must contain inputs object")
        inputs = item.get("inputs") if isinstance(item.get("inputs"), Mapping) else {}
        position = _position(item.get("position", item.get("pos")))
        known = {"id", "entity_id", "kind", "class_type", "type", "title", "name", "position", "pos",
                 "inputs", "outputs", "widgets", "widgets_values", "metadata", "flags", "refs", "asset_ids"}
        known.update({"raw_payload", "extensions"})
        extensions = {**deepcopy(item.get("extensions") or {}),
                      **{key: deepcopy(value) for key, value in item.items() if key not in known}}
        raw_node = deepcopy(item.get("raw_payload") or dict(item))
        assets = item.get("asset_ids", ())
        if not isinstance(assets, (list, tuple)):
            raise WorkflowParseError(f"nodes[{index}].asset_ids must be an array")
        nodes.append(WorkflowNode(
            id=node_id, kind=kind, title=str(item.get("title") or item.get("name") or (item.get("_meta") or {}).get("title") or kind),
            position=position[:2], width=position[2], height=position[3],
            inputs=deepcopy(inputs), outputs=deepcopy(item.get("outputs")) if isinstance(item.get("outputs"), Mapping) else {},
            widgets=deepcopy(item.get("widgets", item.get("widgets_values"))),
            metadata=deepcopy(item.get("metadata", {key: item[key] for key in ("type", "widgets_values", "flags", "refs") if key in item})),
            asset_ids=tuple(str(asset) for asset in assets), raw_payload=raw_node, extensions=extensions))
        for input_name, value in inputs.items():
            ref = _api_link(value)
            if ref:
                generated_links.append(WorkflowConnection(
                    id=f"{ref[0]}->{node_id}:{input_name}", source=ref[0], target=node_id,
                    source_slot=ref[1], target_slot=str(input_name)))

    links = root.get("connections", root.get("links", []))
    if not isinstance(links, list):
        raise WorkflowParseError("workflow connections must be an array")
    connections: list[WorkflowConnection] = []
    seen_link_ids: set[str] = set()
    for index, raw in enumerate(links):
        tuple_link = _link_tuple(raw)
        if tuple_link is not None:
            link_id, source, source_slot, target, target_slot = tuple_link
            item = {}
        else:
            item = _mapping(raw, f"connections[{index}]")
            link_id = item.get("id") or item.get("connection_id") or index
            source = item.get("source", item.get("from_node", item.get("from", item.get("origin_id", ""))))
            target = item.get("target", item.get("to_node", item.get("to", item.get("target_id", ""))))
            source_slot = item.get("source_slot", item.get("from_slot", item.get("origin_slot")))
            target_slot = item.get("target_slot", item.get("to_slot"))
        connection_id = str(link_id)
        if not str(source).strip() or not str(target).strip():
            raise WorkflowParseError(f"connections[{index}] must contain source and target")
        if str(source) not in node_ids or str(target) not in node_ids:
            raise WorkflowParseError(f"connection references unknown node: {source}->{target}")
        if connection_id in seen_link_ids:
            raise WorkflowParseError(f"duplicate connection id: {connection_id}")
        seen_link_ids.add(connection_id)
        connections.append(WorkflowConnection(id=connection_id, source=str(source), target=str(target),
                                              source_slot=None if source_slot is None else str(source_slot),
                                              target_slot=None if target_slot is None else str(target_slot)))
    # API formats expose links inside node inputs rather than root.links.
    for link in generated_links:
        if link.source not in node_ids or link.target not in node_ids:
            raise WorkflowParseError(f"connection references unknown node: {link.source}->{link.target}")
        if link.id not in seen_link_ids:
            connections.append(link)
            seen_link_ids.add(link.id)
    metadata = {key: deepcopy(root[key]) for key in ("version", "canvas_id") if key in root}
    known_root = {"nodes", "links", "connections", "version", "canvas_id", "viewport", "note", "media", "dependencies", "metadata", "warnings", "workflow_id", "folder_id", "name", "source", "extensions", "raw_payload", "asset_id"}
    extensions = {key: deepcopy(value) for key, value in root.items() if key not in known_root}
    return WorkflowDocument(workflow_id=str(root["workflow_id"]) if root.get("workflow_id") is not None else None,
                             version=int(root.get("version", 1)), source=selected, name=name,
                             folder_id=str(root["folder_id"]) if root.get("folder_id") is not None else None,
                             nodes=tuple(nodes), connections=tuple(connections),
                             viewport=deepcopy(root.get("viewport") or {}), note=deepcopy(root.get("note")),
                             media=deepcopy(root.get("media")), dependencies=deepcopy(root.get("dependencies")),
                             metadata=deepcopy(root.get("metadata") or metadata),
                             warnings=tuple(str(item) for item in (root.get("warnings") or ())),
                             raw_payload=deepcopy(root.get("raw_payload") or payload), extensions={**extensions, **deepcopy(root.get("extensions") or {})})


NODE_CN_TITLES: dict[str, str] = {
    "UNETLoader": "加载 UNET 扩散模型",
    "CLIPLoader": "加载 CLIP 文本编码器",
    "VAELoader": "加载 VAE 编解码器",
    "CheckpointLoaderSimple": "加载 Checkpoint 大模型",
    "LoraLoader": "加载 LoRA 权重",
    "EmptyLatentImage": "空潜空间图像 (分辨率)",
    "EmptyFlux2LatentImage": "空 Flux2 潜空间图像",
    "EmptySD3LatentImage": "空 SD3 潜空间图像",
    "ResolutionSelector": "分辨率与画幅选择器",
    "LoadImage": "加载参考图像 (LoadImage)",
    "SaveImage": "保存生成图像 (SaveImage)",
    "PreviewImage": "实时预览图像 (PreviewImage)",
    "KSampler": "K采样器核心 (KSampler)",
    "KSamplerAdvanced": "高级K采样器 (KSamplerAdv)",
    "SamplerCustomAdvanced": "高级自定义采样器 (SamplerCustom)",
    "CFGGuider": "CFG 引导控制器 (CFGGuider)",
    "Flux2Scheduler": "Flux2 采样步数调度器",
    "KSamplerSelect": "采样算法选择器 (KSamplerSelect)",
    "RandomNoise": "随机噪声生成器 (RandomNoise)",
    "VAEDecode": "VAE 潜空间解码图像",
    "VAEEncode": "VAE 图像编码潜空间",
    "CLIPTextEncode": "CLIP 提示词编码",
    "ConditioningZeroOut": "负向条件置零 (ZeroOut)",
    "InpaintModelConditioning": "局部重绘条件编码 (Inpaint)",
    "ReferenceLatent": "参考潜空间注入 (RefLatent)",
    "DrawMaskOnImage": "图像手绘遮罩 (DrawMask)",
    "INPAINT_ExpandMask": "遮罩羽化与扩展 (ExpandMask)",
    "ImageAndMaskPreview": "图像与遮罩叠加预览",
    "ImagePadForOutpaint": "画面外扩填充 (OutpaintPad)",
    "ImageResizeKJv2": "图像尺寸缩放 (ImageResize)",
    "LayerUtility: ImageScaleByAspectRatio V2": "按比例智能缩放 (ScaleAspect)",
    "GetImageSize": "读取图像宽高尺寸",
    "ImageCompositeMasked": "遮罩区域图像合成 (Composite)",
    "ColorMatch": "色彩匹配校正 (ColorMatch)",
    "Image Comparer (rgthree)": "生成前后对比预览 (Comparer)",
    "Fast Groups Bypasser (rgthree)": "节点组快速开关 (Bypasser)",
    "PlaySound|pysssss": "任务完成提示音 (PlaySound)",
    "PrimitiveInt": "整数参数常量 (PrimitiveInt)",
    "PrimitiveStringMultiline": "多行提示词常量 (StringMultiline)",
    "PrimitiveNode": "通用常量节点 (PrimitiveNode)",
    "Reroute": "路由中继节点 (Reroute)",
    "TextEncodeQwenImage21": "Qwen-Image 2.1 多模态提示词编码",
    "QwenImage21Cache": "Qwen-Image 2.1 加速缓存",
    "ComfySwitchNode": "条件逻辑切换开关 (Switch)",
    "Note": "工作流备注说明 (Note)",
    "MarkdownNote": "Markdown 节点文档",
    "SeeThrough_LoadLayerDiffModel": "加载分层扩散模型 (LayerDiff)",
    "SeeThrough_LoadDepthModel": "加载深度估计模型 (Marigold)",
    "SeeThrough_GenerateLayers": "生成透明分层 (GenerateLayers)",
    "SeeThrough_GenerateDepth": "生成分层深度图 (GenerateDepth)",
    "SeeThrough_PostProcess": "智能切分与后处理 (PostProcess)",
    "SeeThrough_SavePSD": "导出分层PSD文件 (SavePSD)",
    "easy cleanGpuUsed": "清理显存占用 (cleanGpuUsed)",
}


def _clean_html_to_note_text(raw_html: Any, detail: dict[str, Any]) -> str:
    s = str(raw_html or "").strip()
    if s:
        s = re.sub(r"<\s*br\s*/?\s*>", "\n", s, flags=re.IGNORECASE)
        s = re.sub(r"</\s*(p|div|li|h[1-6])\s*>", "\n", s, flags=re.IGNORECASE)
        s = re.sub(r"<[^>]+>", "", s)
        s = html.unescape(s)
        lines = [ln.strip() for ln in s.splitlines()]
        cleaned = "\n".join(ln for ln in lines if ln)
        if cleaned:
            return cleaned
    wf_name = str(detail.get("name") or "未命名工作流")
    owner = detail.get("owner") if isinstance(detail.get("owner"), dict) else {}
    author = str(owner.get("name") or "创作者")
    return f"【工作流说明】\n名称：{wf_name}\n作者：{author}\n基于公开元数据自动推断重建拓扑。"


def extract_source_graph(payload: Any) -> dict[str, Any] | None:
    """只解包已知 RH 源字段，不从节点类型清单猜测连线或参数。"""
    import json
    candidates = [payload]
    for _ in range(8):
        next_candidates = []
        for candidate in candidates:
            value = candidate
            if isinstance(value, str):
                try:
                    value = json.loads(value)
                except (ValueError, TypeError):
                    continue
            if not isinstance(value, dict) or not value:
                continue
            if isinstance(value.get("nodes"), list):
                if "links" in value or "connections" in value:
                    return deepcopy(value)
                continue
            if any(isinstance(v, dict) and "class_type" in v for v in value.values()):
                return deepcopy(value)
            for key in ("workflowContent", "workflow", "prompt", "json", "data"):
                if isinstance(value.get(key), (dict, str)) and value.get(key):
                    next_candidates.append(value[key])
        if not next_candidates:
            return None
        candidates = next_candidates
    return None


def build_topology_from_public_detail(detail: dict[str, Any]) -> dict[str, Any]:
    """公开详情不是源图：缺少真实源时明确失败，禁止生成虚构拓扑。"""
    graph = extract_source_graph(detail)
    if graph is None:
        raise WorkflowParseError("公开详情只有节点类型/模型清单，缺少原始拓扑；请配置 RH 登录 Token/API Key 或上传原始工作流 JSON")
    return graph
