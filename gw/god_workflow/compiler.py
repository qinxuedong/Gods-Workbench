"""按真实节点 Schema 转换原生图，无法确定映射时拒绝执行。"""
from __future__ import annotations

from copy import deepcopy
from typing import Any
from gw.core.errors import CleanroomException


def compile_prompt(document: dict, schemas: dict | None = None, *, field_maps=None) -> dict[str, Any]:
    schemas = schemas or {}
    nodes = {str(node["id"]): node for node in document.get("nodes", [])}
    prompt = {}
    field_maps = field_maps if field_maps is not None else {}
    root = document.get("raw_payload") or {}
    if root.get("definitions", {}).get("subgraphs"):
        raise CleanroomException(422, "UNSUPPORTED_WORKFLOW", "子图需在 ComfyUI 中导出 API 格式后执行；原图可保存和打开")
    for node_id, node in nodes.items():
        kind = node.get("kind") or node.get("class_type")
        original = node.get("raw_payload") or {}
        if kind in {"Note", "MarkdownNote"}:
            continue
        if original.get("mode", 0) in (2, 4) or kind == "Reroute":
            raise CleanroomException(422, "UNSUPPORTED_WORKFLOW", "静音、旁路或 Reroute 节点需先导出 API 格式，避免改变执行语义")
        inputs = deepcopy(node.get("inputs") or {})
        widgets = node.get("widgets")
        native = isinstance(original.get("inputs"), list)
        field_maps[node_id] = {}
        if native and isinstance(widgets, list) and any(not isinstance(value, dict) for value in widgets):
            schema = schemas.get(kind)
            if not isinstance(schema, dict):
                raise CleanroomException(422, "NODE_SCHEMA_REQUIRED", f"节点 {node_id} ({kind}) 缺少真实参数 Schema")
            index = 0
            for section in ("required", "optional"):
                definitions = schema.get("input", {}).get(section, {})
                order = schema.get("input_order", {}).get(section, list(definitions))
                for field in order:
                    spec = definitions.get(field)
                    if not isinstance(spec, (list, tuple)) or not spec:
                        continue
                    options = spec[1] if len(spec) > 1 and isinstance(spec[1], dict) else {}
                    if options.get("forceInput") or not (isinstance(spec[0], list) or spec[0] in ("STRING", "INT", "FLOAT", "BOOLEAN")):
                        continue
                    if index >= len(widgets):
                        if field not in inputs and section == "required":
                            raise CleanroomException(422, "INVALID_WORKFLOW", f"节点 {node_id} 缺少参数 {field}")
                        continue
                    inputs.setdefault(field, deepcopy(widgets[index]))
                    field_maps[node_id][f"widget_{index}"] = field
                    index += 1
                    if options.get("control_after_generate") and index < len(widgets):
                        index += 1
            if index != len([value for value in widgets if not isinstance(value, dict)]):
                raise CleanroomException(422, "UNSUPPORTED_WIDGET_MAPPING", f"节点 {node_id} 的参数序列与 Schema 不匹配，请导出 API 格式")
        if isinstance(widgets, list):
            for widget in widgets:
                if isinstance(widget, dict) and widget.get("name") is not None:
                    field = field_maps[node_id].get(str(widget["name"]), str(widget["name"]))
                    inputs[field] = deepcopy(widget.get("value"))
        prompt[node_id] = {"class_type": kind, "inputs": inputs}
    for link in document.get("connections", []):
        source, target = str(link["source"]), str(link["target"])
        if source not in prompt or target not in prompt:
            raise CleanroomException(422, "UNSUPPORTED_WORKFLOW", "连线连接了不可执行节点")
        field = str(link.get("target_slot") or "0")
        original = nodes[target].get("raw_payload") or {}
        if field.isdigit() and isinstance(original.get("inputs"), list):
            slot = int(field)
            ports = original["inputs"]
            if slot >= len(ports) or not ports[slot].get("name"):
                raise CleanroomException(422, "INVALID_WORKFLOW", "连线输入槽位无效")
            field = ports[slot]["name"]
        source_slot = str(link.get("source_slot") or "0")
        if not source_slot.isdigit():
            outputs = (nodes[source].get("raw_payload") or {}).get("outputs", [])
            source_slot = next((str(i) for i, port in enumerate(outputs) if port.get("name") == source_slot), "")
        if not source_slot.isdigit():
            raise CleanroomException(422, "INVALID_WORKFLOW", "连线输出槽位无效")
        prompt[target]["inputs"][field] = [source, int(source_slot)]
    return prompt
