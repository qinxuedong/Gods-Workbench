"""ComfyUI 原生 LiteGraph 工作流导出器。

将神工坊内部的 WorkflowDocument 规范化结构转换为 ComfyUI Web UI (LiteGraph)
所能直接解析与加载的原生 JSON 格式，根除因 inputs 字典类型导致的
`TypeError: t.inputs?.map is not a function` 错误。
"""
from __future__ import annotations

from copy import deepcopy
import re
from typing import Any, Mapping
import uuid
from gw.core.errors import CleanroomException


def _infer_slot_type(name: str) -> str:
    lower = str(name or "").lower()
    if any(k in lower for k in ("positive", "negative", "conditioning")):
        return "CONDITIONING"
    if "clip" in lower:
        return "CLIP"
    if "model" in lower:
        return "MODEL"
    if any(k in lower for k in ("latent", "samples")):
        return "LATENT"
    if "vae" in lower:
        return "VAE"
    if "image" in lower:
        return "IMAGE"
    if "mask" in lower:
        return "MASK"
    if "conditioning" in lower:
        return "CONDITIONING"
    return "*"


def _api_widgets(node: dict[str, Any], schema: Any) -> list[Any]:
    """API 字段只有经真实 Schema 排序后才能变成宿主的位置参数。"""
    kind = node.get("kind") or node.get("class_type")
    if not isinstance(schema, dict):
        raise CleanroomException(422, "NODE_SCHEMA_REQUIRED", f"节点 {node.get('id')} ({kind}) 缺少真实参数 Schema，请连接 ComfyUI 后导出")
    values = deepcopy(node.get("inputs") or {})
    for widget in node.get("widgets") or []:
        if isinstance(widget, dict) and "name" in widget:
            values[widget["name"]] = deepcopy(widget.get("value"))
    widgets = []
    mapped = set()
    missing_optional = False
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
            mapped.add(field)
            if field not in values:
                if section == "required":
                    raise CleanroomException(422, "INVALID_WORKFLOW", f"节点 {node.get('id')} 缺少参数 {field}")
                missing_optional = True
                continue
            if missing_optional or isinstance(values[field], (list, tuple, dict)):
                raise CleanroomException(422, "UNSUPPORTED_WIDGET_MAPPING", f"节点 {node.get('id')} 的参数无法无损转换，请使用原生图")
            widgets.append(deepcopy(values[field]))
            if options.get("control_after_generate"):
                widgets.append("fixed")
    if any(field not in mapped and not isinstance(value, (list, tuple)) for field, value in values.items()):
        raise CleanroomException(422, "UNSUPPORTED_WIDGET_MAPPING", f"节点 {node.get('id')} 存在 Schema 未定义的参数")
    return widgets


def export_to_comfy_litegraph(document: dict[str, Any], schemas: dict | None = None) -> dict[str, Any]:
    """将工作流文档转换为合法的 ComfyUI LiteGraph JSON。"""
    if not isinstance(document, dict):
        return {}
    schemas = schemas if schemas is not None else document.get("metadata", {}).get("node_schemas", {})

    # 若已经是符合 LiteGraph 标准的结构（nodes 且 node.inputs 为 list，且带有 links 数组）
    raw_nodes = document.get("nodes", [])
    raw_links = document.get("links", [])
    if isinstance(raw_nodes, list) and raw_nodes and isinstance(raw_links, list):
        first_node = raw_nodes[0]
        if isinstance(first_node, dict) and isinstance(first_node.get("inputs"), list) and "pos" in first_node:
            return deepcopy(document)

    # 1. 整理全部连接 connections
    connections_list = []
    seen_conn_keys = set()
    raw_connections = document.get("connections", document.get("links", []))
    if isinstance(raw_connections, list):
        for c in raw_connections:
            if isinstance(c, dict):
                src = str(c.get("source") or c.get("from_node") or "")
                tgt = str(c.get("target") or c.get("to_node") or "")
                src_slot = str(c.get("source_slot") or c.get("from_slot") or "0")
                tgt_slot = str(c.get("target_slot") or c.get("to_slot") or "0")
                if src and tgt:
                    key = (src, src_slot, tgt, tgt_slot)
                    if key not in seen_conn_keys:
                        seen_conn_keys.add(key)
                        connections_list.append({
                            "id": c.get("id"),
                            "source": src,
                            "target": tgt,
                            "source_slot": src_slot,
                            "target_slot": tgt_slot,
                        })

    # 从节点的 inputs 字典中补齐隐式连线引用（如 "clip": ["1", 0]）
    for node in (raw_nodes if isinstance(raw_nodes, list) else []):
        if not isinstance(node, dict):
            continue
        tgt_id = str(node.get("id") or "")
        inputs_dict = node.get("inputs")
        if isinstance(inputs_dict, dict):
            for slot_name, val in inputs_dict.items():
                if isinstance(val, (list, tuple)) and len(val) >= 2 and not isinstance(val[0], (dict, list)):
                    src_id = str(val[0])
                    src_slot = str(val[1])
                    key = (src_id, src_slot, tgt_id, str(slot_name))
                    if key not in seen_conn_keys:
                        seen_conn_keys.add(key)
                        connections_list.append({
                            "source": src_id,
                            "target": tgt_id,
                            "source_slot": src_slot,
                            "target_slot": str(slot_name),
                        })

    # 2. 映射每个连接的整数 link_id 与槽位索引
    links_payload: list[list[Any]] = []
    # node_id -> list of input slots: [{"name": ..., "type": ..., "link": link_id}]
    node_inputs_map: dict[str, list[dict[str, Any]]] = {}
    # node_id -> dict of source_slot_index -> {"name": ..., "type": ..., "links": [...], "slot_index": ...}
    node_outputs_map: dict[str, dict[int, dict[str, Any]]] = {}
    for node in raw_nodes:
        original = node.get("raw_payload") or {}
        nid = str(node.get("id"))
        if isinstance(original.get("inputs"), list):
            node_inputs_map[nid] = deepcopy(original["inputs"])
            for port in node_inputs_map[nid]:
                port["link"] = None
        if isinstance(original.get("outputs"), list):
            node_outputs_map[nid] = {i: {**deepcopy(port), "links": []} for i, port in enumerate(original["outputs"])}

    preserved_ids = {int(c['id']) for c in connections_list if str(c.get('id', '')).isdigit()}
    used_ids = set()
    next_link_id = max(preserved_ids or {0}) + 1
    for conn in connections_list:
        candidate = conn.get('id')
        link_id = int(candidate) if str(candidate).isdigit() and int(candidate) not in used_ids else next_link_id
        if link_id == next_link_id:
            next_link_id += 1
        used_ids.add(link_id)
        src_id_str = conn["source"]
        tgt_id_str = conn["target"]
        tgt_slot_name = conn["target_slot"]
        src_slot_raw = conn["source_slot"]

        slot_type = _infer_slot_type(tgt_slot_name)
        try:
            src_slot_idx = int(src_slot_raw)
        except (ValueError, TypeError):
            ports = node_outputs_map.get(src_id_str, {})
            src_slot_idx = next((slot for slot, port in ports.items() if port.get('name') == src_slot_raw), -1)
        if not 0 <= src_slot_idx <= 1024:
            raise CleanroomException(422, 'INVALID_WORKFLOW', '输出端口索引无效')

        # 下游节点的 inputs 列表
        in_slots = node_inputs_map.setdefault(tgt_id_str, [])
        if tgt_slot_name.isdigit():
            to_slot_idx = int(tgt_slot_name)
            if to_slot_idx > 1024:
                raise CleanroomException(422, 'INVALID_WORKFLOW', '输入端口索引无效')
            while len(in_slots) <= to_slot_idx:
                in_slots.append({"name": f"input_{len(in_slots)}", "type": "*", "link": None})
        else:
            to_slot_idx = next((i for i, slot in enumerate(in_slots) if slot.get("name") == tgt_slot_name), len(in_slots))
            if to_slot_idx == len(in_slots):
                in_slots.append({"name": tgt_slot_name, "type": slot_type, "link": None})
        in_slots[to_slot_idx]["link"] = link_id
        slot_type = in_slots[to_slot_idx].get("type", slot_type)

        # 上游节点的 outputs 字典
        outputs_dict = node_outputs_map.setdefault(src_id_str, {})
        if src_slot_idx not in outputs_dict:
            outputs_dict[src_slot_idx] = {
                "name": slot_type if slot_type != "*" else f"output_{src_slot_idx}",
                "type": slot_type,
                "links": [link_id],
                "slot_index": src_slot_idx,
            }
        else:
            outputs_dict[src_slot_idx]["links"].append(link_id)
            slot_type = outputs_dict[src_slot_idx].get('type') or slot_type

        # LiteGraph link 格式: [link_id, from_node, from_slot, to_node, to_slot, type]
        src_num = int(src_id_str) if src_id_str.isdigit() else src_id_str
        tgt_num = int(tgt_id_str) if tgt_id_str.isdigit() else tgt_id_str
        links_payload.append([link_id, src_num, src_slot_idx, tgt_num, to_slot_idx, slot_type])

    # 3. 构造 LiteGraph nodes
    litegraph_nodes: list[dict[str, Any]] = []
    max_node_id = 0

    for order_idx, node in enumerate(raw_nodes if isinstance(raw_nodes, list) else []):
        if not isinstance(node, dict):
            continue
        nid_str = str(node.get("id") or order_idx + 1)
        nid_num = int(nid_str) if nid_str.isdigit() else nid_str
        if isinstance(nid_num, int) and nid_num > max_node_id:
            max_node_id = nid_num

        class_name = str(node.get("kind") or node.get("class_type") or node.get("type") or "Node")
        title_text = str(node.get("title") or class_name)

        # 坐标与尺寸
        pos_dict = node.get("position")
        native_size = (node.get('raw_payload') or {}).get('size', [210, 120])
        if not isinstance(native_size, (list, tuple)) or len(native_size) < 2:
            native_size = [210, 120]
        if isinstance(pos_dict, dict):
            x = float(pos_dict.get("x", 100))
            y = float(pos_dict.get("y", 100))
            w = float(pos_dict.get("width", native_size[0]) or 210)
            h = float(pos_dict.get("height", native_size[1]) or 120)
        elif isinstance(node.get("pos"), (list, tuple)) and len(node["pos"]) >= 2:
            x = float(node["pos"][0])
            y = float(node["pos"][1])
            w = float(node.get("size", [210, 120])[0] if isinstance(node.get("size"), (list, tuple)) else 210)
            h = float(node.get("size", [210, 120])[1] if isinstance(node.get("size"), (list, tuple)) else 120)
        else:
            x, y, w, h = 100.0 + order_idx * 40.0, 100.0 + order_idx * 40.0, 210.0, 120.0

        # inputs: 保证必须是 list！这是根治 TypeError: t.inputs?.map is not a function 的关键！
        in_slots = node_inputs_map.get(nid_str, [])

        # outputs: 保证必须是 list！按 slot_index 顺序排列
        out_slots_dict = node_outputs_map.get(nid_str, {})
        if out_slots_dict:
            out_slots = [out_slots_dict.get(i, {"name": f"output_{i}", "type": "*", "links": [], "slot_index": i})
                         for i in range(max(out_slots_dict) + 1)]
        else:
            out_slots = []

        # widgets_values 提取标量输入
        widgets_values: list[Any] = []
        raw_widgets = node.get("widgets")
        native_inputs = (node.get('raw_payload') or {}).get('inputs')
        if not isinstance(native_inputs, list) and isinstance(node.get('inputs'), dict):
            if any(not isinstance(value, (list, tuple)) for value in node['inputs'].values()):
                widgets_values = _api_widgets(node, (schemas or {}).get(class_name))
        elif isinstance(raw_widgets, list):
            for widget_value in raw_widgets:
                if isinstance(widget_value, dict) and "value" in widget_value:
                    widgets_values.append(widget_value["value"])
                elif not isinstance(widget_value, dict):
                    widgets_values.append(deepcopy(widget_value))
        elif isinstance(node.get("inputs"), dict):
            for k, v in node["inputs"].items():
                if not (isinstance(v, (list, tuple)) and len(v) >= 2 and not isinstance(v[0], (dict, list))):
                    widgets_values.append(v)

        original = deepcopy(node.get("raw_payload") or {})
        # 原生节点只以 type/widgets_values 表达语义，不能夹带旧 API 分类或编辑值。
        for internal_field in ("class_type", "kind", "widgets"):
            original.pop(internal_field, None)
        litegraph_nodes.append({
            **deepcopy(original),
            "id": nid_num,
            "type": class_name,
            "title": title_text,
            "pos": [x, y],
            "size": [w, h],
            "flags": deepcopy(original.get("flags", {})),
            "order": original.get("order", order_idx),
            "mode": original.get("mode", 0),
            "inputs": in_slots,
            "outputs": out_slots,
            "properties": deepcopy(original.get("properties", {"Node name for S&R": class_name})),
            "widgets_values": widgets_values,
        })

    wf_id = str(document.get("workflow_id") or uuid.uuid4())
    original_root = document.get("raw_payload") or {}
    return {
        **deepcopy(original_root),
        "id": wf_id,
        "revision": 0,
        "last_node_id": max_node_id or len(litegraph_nodes),
        "last_link_id": max(used_ids or {0}),
        "nodes": litegraph_nodes,
        "links": links_payload,
        "groups": deepcopy(original_root.get("groups", [])),
        "config": deepcopy(original_root.get("config", {})),
        "extra": deepcopy(original_root.get("extra", {"ds": {"scale": 1.0, "offset": [0, 0]}})),
        "version": 0.4,
    }
