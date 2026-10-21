"""过期已保存工作流的识别与离线重解析辅助。

PR #22 之前，旧前端 normalizeMatureWorkflow 在保存时优先取数字 to_slot/from_slot，
把 RH link（``source_type == "rh_link"``）导入的 API 格式工作流里本应为命名
（输入参数名）的连线槽位压成数字 0。这里只做两件事：

1. 纯函数判定已保存文档是否属于这类过期数据（读取路径只读，绝不改写存储）；
   判定只信任可核验的证据：raw_payload 重解析出的命名槽位与保存的 links 不一致。
   非 rh_link、无原始来源或无法核验的文档一律不标记，避免误判正常工作流。
2. 依据文档已保存的 raw_payload（原始来源快照）离线重新推导正确命名槽位，
   绝不访问网络。
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from typing import Any, Optional

from .parser import WorkflowParseError, extract_source_graph, parse_workflow

#: 过期数据的判定原因码；读取接口在 ``needs_reparse`` 为真时透出。
REPARSE_REASON_LEGACY_RH_LINK_SLOTS = "LEGACY_RH_LINK_SLOTS"


def _is_index_slot(slot: Any) -> bool:
    """纯数字字符串槽位即行号；命名槽位（输入参数名）永远不是纯数字。"""
    return isinstance(slot, str) and slot.isascii() and slot.isdecimal()


def _is_named_slot(slot: Any) -> bool:
    return slot is not None and not _is_index_slot(slot)


def _saved_slots(document: Mapping[str, Any]) -> dict[tuple[str, str], Counter]:
    """按 (source, target) 聚合已保存连线当前的 (source_slot, target_slot)。"""
    by_pair: dict[tuple[str, str], Counter] = {}
    for link in document.get("connections") or []:
        if not isinstance(link, Mapping):
            continue
        source_slot = link.get("source_slot")
        target_slot = link.get("target_slot")
        pair = (str(link.get("source")), str(link.get("target")))
        by_pair.setdefault(pair, Counter())[
            (
                None if source_slot is None else str(source_slot),
                None if target_slot is None else str(target_slot),
            )
        ] += 1
    return by_pair


def legacy_reparse_state(document: Mapping[str, Any]) -> tuple[bool, str | None]:
    """判定已保存文档是否需要 LEGACY_RH_LINK_SLOTS 重新解析。

    返回 ``(needs_reparse, reparse_reason)``。只有当 raw_payload 能重新解析出
    命名 target/source 槽位、而同一 (source, target) 连线对在保存的 links 里
    只剩纯数字行号（被压成 0）时才判真；槽位正确者返回假。
    """
    if not isinstance(document, Mapping) or document.get("source_type") != "rh_link":
        return (False, None)
    graph = extract_source_graph(document.get("raw_payload"))
    if graph is None:
        return (False, None)
    try:
        reparsed = parse_workflow(graph, source=document.get("source") or "comfyui")
    except WorkflowParseError:
        return (False, None)
    reference: dict[tuple[str, str], Counter] = {}
    for link in reparsed.connections:
        reference.setdefault((link.source, link.target), Counter())[
            (link.source_slot, link.target_slot)
        ] += 1
    for pair, saved_counter in _saved_slots(document).items():
        ref_counter = reference.get(pair)
        if not ref_counter:
            continue
        # 先按多重集合抵消完全一致的槽位，剩下的差异才是保存损坏或用户改线。
        matched = ref_counter & saved_counter
        remaining_ref = ref_counter - matched
        remaining_saved = saved_counter - matched
        for position in (0, 1):
            ref_named = any(
                _is_named_slot(slots[position]) for slots in remaining_ref.elements()
            )
            saved_index = any(
                _is_index_slot(slots[position]) for slots in remaining_saved.elements()
            )
            if ref_named and saved_index:
                return (True, REPARSE_REASON_LEGACY_RH_LINK_SLOTS)
    return (False, None)


__all__ = ["REPARSE_REASON_LEGACY_RH_LINK_SLOTS", "legacy_reparse_state"]
