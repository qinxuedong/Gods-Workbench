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


def export_to_comfy_litegraph(document: dict[str, Any]) -> dict[str, Any]:
    """将工作流文档转换为合法的 ComfyUI LiteGraph JSON。"""
    if not isinstance(document, dict):
        return {}

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
            # API 图的编辑列表可能只包含一个改过的参数；其余输入仍必须保留。
            values = {widget['name']: widget.get('value') for widget in (raw_widgets or [])
                      if isinstance(widget, dict) and 'name' in widget} if isinstance(raw_widgets, list) else {}
            for name, value in node['inputs'].items():
                if not isinstance(value, (list, tuple)):
                    widgets_values.append(deepcopy(values.get(name, value)))
        elif isinstance(raw_widgets, list):
            for w in raw_widgets:
                if isinstance(w, dict) and "value" in w:
                    widgets_values.append(w["value"])
                elif not isinstance(w, dict):
                    widgets_values.append(deepcopy(w))
        elif isinstance(node.get("inputs"), dict):
            for k, v in node["inputs"].items():
                if not (isinstance(v, (list, tuple)) and len(v) >= 2 and not isinstance(v[0], (dict, list))):
                    widgets_values.append(v)

        original = node.get("raw_payload") or {}
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
