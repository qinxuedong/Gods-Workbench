"""Strict, source-neutral workflow payload parser."""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import math
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
    if media_type in {"application/json", "text/json", "application/octet-stream"}:
        try:
            payload = json.loads(content)
        except (ValueError, UnicodeDecodeError):
            raise WorkflowParseError("upload must contain valid JSON") from None
        return parse_workflow(payload, source=source, name=name)
    if media_type != "image/png":
        raise WorkflowParseError("unsupported upload media type")
    try:
        from PIL import Image
    except ImportError:
        raise WorkflowParseError("PNG metadata dependency is unavailable") from None
    try:
        with Image.open(io.BytesIO(content)) as image:
            if image.format != "PNG":
                raise WorkflowParseError("upload is not a PNG image")
            text = getattr(image, "text", None) or {
                key: value for key, value in image.info.items() if isinstance(value, str)
            }
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
    root = _mapping(payload, "workflow")
    try:
        selected = source if isinstance(source, WorkflowSource) else WorkflowSource(str(source).lower())
    except ValueError as exc:
        raise WorkflowParseError("unsupported workflow source") from exc

    # Error envelopes are never valid workflow graphs, even when they contain arbitrary metadata.
    if "error" in root and "nodes" not in root and "links" not in root and "connections" not in root:
        raise WorkflowParseError("workflow error payload is not a graph")

    raw_nodes = root.get("nodes")
    api_map = raw_nodes is None and all(isinstance(key, str) for key in root) and any(isinstance(value, Mapping) for value in root.values())
    if api_map:
        raw_nodes = [{"id": key, **dict(_mapping(value, "node"))} for key, value in root.items() if isinstance(value, Mapping)]
    if raw_nodes is None:
        raw_nodes = [] if not root else None
    if not isinstance(raw_nodes, list):
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
        if selected == WorkflowSource.COMFYUI and not item.get("class_type"):
            raise WorkflowParseError(f"nodes[{index}] must contain class_type")
        kind = str(kind_value or "node")
        if selected == WorkflowSource.COMFYUI and not isinstance(item.get("inputs"), Mapping):
            raise WorkflowParseError(f"nodes[{index}] must contain inputs object")
        inputs = item.get("inputs") if isinstance(item.get("inputs"), Mapping) else {}
        position = _position(item.get("position", item.get("pos")))
        known = {"id", "entity_id", "kind", "class_type", "type", "title", "name", "position", "pos",
                 "inputs", "outputs", "widgets", "widgets_values", "metadata", "flags", "refs", "asset_ids"}
        extensions = {key: deepcopy(value) for key, value in item.items() if key not in known}
        raw_node = deepcopy(dict(item))
        assets = item.get("asset_ids", ())
        if not isinstance(assets, (list, tuple)):
            raise WorkflowParseError(f"nodes[{index}].asset_ids must be an array")
        nodes.append(WorkflowNode(
            id=node_id, kind=kind, title=str(item.get("title") or item.get("name") or kind),
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
            source = item.get("source") or item.get("from") or item.get("origin_id") or ""
            target = item.get("target") or item.get("to") or item.get("target_id") or ""
            source_slot = item.get("source_slot", item.get("origin_slot"))
            target_slot = item.get("target_slot", item.get("target_slot"))
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
    known_root = {"nodes", "links", "connections", "version", "canvas_id", "viewport", "note", "media", "dependencies", "metadata", "warnings", "workflow_id", "folder_id", "name", "source", "extensions"}
    extensions = {key: deepcopy(value) for key, value in root.items() if key not in known_root}
    return WorkflowDocument(workflow_id=str(root["workflow_id"]) if root.get("workflow_id") is not None else None,
                             version=int(root.get("version", 1)), source=selected, name=name,
                             folder_id=str(root["folder_id"]) if root.get("folder_id") is not None else None,
                             nodes=tuple(nodes), connections=tuple(connections),
                             viewport=deepcopy(root.get("viewport") or {}), note=deepcopy(root.get("note")),
                             media=deepcopy(root.get("media")), dependencies=deepcopy(root.get("dependencies")),
                             metadata=deepcopy(root.get("metadata") or metadata),
                             warnings=tuple(str(item) for item in (root.get("warnings") or ())),
                             raw_payload=deepcopy(payload), extensions={**extensions, **deepcopy(root.get("extensions") or {})})
