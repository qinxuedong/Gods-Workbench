"""按真实节点 Schema 转换原生图，无法确定映射时拒绝执行。"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any
from gw.core.errors import CleanroomException


def execution_fingerprint(document: dict) -> str:
    """宿主真实编译缓存只绑定执行内容；参数、模式、连线或素材改变即失效。"""
    nodes = [{key: node.get(key) for key in ('id', 'kind', 'inputs', 'widgets')}
             | {'mode': (node.get('raw_payload') or {}).get('mode', 0),
                # 自定义属性和端口顺序同样影响宿主编译；只排除纯视觉字段。
                'native': {key: value for key, value in (node.get('raw_payload') or {}).items()
                           if key not in {'pos', 'size', 'order', 'title', 'color', 'bgcolor'}},
                'input_assets': node.get('metadata', {}).get('input_assets', {})}
             for node in document.get('nodes', [])]
    links = [{key: link.get(key) for key in ('source', 'target', 'source_slot', 'target_slot')}
             for link in document.get('connections', [])]
    content = {'nodes': sorted(nodes, key=lambda node: str(node['id'])),
               'links': sorted(links, key=lambda link: json.dumps(link, sort_keys=True)),
               'definitions': (document.get('raw_payload') or {}).get('definitions')}
    return hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def compile_prompt(document: dict, schemas: dict | None = None, *, field_maps=None) -> dict[str, Any]:
    schemas = schemas or {}
    nodes = {str(node["id"]): node for node in document.get("nodes", [])}
    prompt = {}
    field_maps = field_maps if field_maps is not None else {}
    resolved = document.get('metadata', {}).get('resolved_api') or {}
    if (isinstance(resolved, dict) and isinstance(resolved.get('prompt'), dict) and resolved['prompt']
            and resolved.get('source') == 'comfyui_graph_to_prompt' and resolved.get('fingerprint') == execution_fingerprint(document)):
        field_maps.update(deepcopy(resolved.get('field_maps') or {}))
        return deepcopy(resolved['prompt'])
    root = document.get("raw_payload") or {}
    if root.get("definitions", {}).get("subgraphs"):
        raise CleanroomException(422, "UNSUPPORTED_WORKFLOW", "当前执行适配尚不支持子图；原生图仍可保存并导入 ComfyUI")
    for node_id, node in nodes.items():
        kind = node.get("kind") or node.get("class_type")
        original = node.get("raw_payload") or {}
        if kind in {"Note", "MarkdownNote"}:
            continue
        if original.get("mode", 0) in (2, 4) or kind == "Reroute":
            raise CleanroomException(422, "UNSUPPORTED_WORKFLOW", "当前执行适配尚不支持静音、旁路或 Reroute 语义；原生图仍可导入 ComfyUI")
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
                raise CleanroomException(422, "UNSUPPORTED_WIDGET_MAPPING", f"节点 {node_id} 的参数序列与真实定义不匹配，自动执行适配已停止；原生图仍可导入 ComfyUI")
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
