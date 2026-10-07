"""Deterministic Sugiyama-style layered DAG layout.

The routine is intentionally pure: it consumes plain node/connection mappings and
returns ``{node_id: (x, y)}``.  No I/O, no mutation of the caller's structures and
no randomness, so the same graph always yields the same coordinates regardless of
dict ordering.  Nothing here fabricates workflow semantics; it only moves nodes.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

# Geometry constants (canvas units).  Kept module-level so callers can document
# the produced spacing without re-deriving it.
LAYER_GAP_X = 300.0
NODE_GAP_Y = 190.0
ORIGIN_X = 60.0
ORIGIN_Y = 60.0
NOTE_GAP_Y = 150.0
NOTE_COLUMNS = 2


def _node_ids(nodes: Sequence[Mapping[str, Any]]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for index, node in enumerate(nodes):
        raw = node.get("id", node.get("node_id", index))
        identifier = str(raw)
        if identifier in seen:
            continue
        seen.add(identifier)
        ordered.append(identifier)
    return ordered


def _edges(nodes: Sequence[Mapping[str, Any]], connections: Sequence[Mapping[str, Any]]) -> list[tuple[str, str]]:
    known = set(_node_ids(nodes))
    pairs: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for link in connections:
        source = str(link.get("source", link.get("from_node", "")) or "")
        target = str(link.get("target", link.get("to_node", "")) or "")
        if not source or not target or source == target:
            continue
        if source not in known or target not in known:
            continue
        if (source, target) in seen:
            continue
        seen.add((source, target))
        pairs.append((source, target))
    return pairs


def _longest_path_layers(order: list[str], edges: list[tuple[str, str]]) -> dict[str, int]:
    """Forward longest-path layering; cycles fall back to discovery order."""
    outgoing: dict[str, list[str]] = {node: [] for node in order}
    indegree: dict[str, int] = {node: 0 for node in order}
    for source, target in edges:
        outgoing[source].append(target)
        indegree[target] += 1
    layer = {node: 0 for node in order}
    queue = [node for node in order if indegree[node] == 0]
    processed = 0
    while queue:
        node = queue.pop(0)
        processed += 1
        for target in outgoing[node]:
            if layer[target] < layer[node] + 1:
                layer[target] = layer[node] + 1
            indegree[target] -= 1
            if indegree[target] == 0:
                queue.append(target)
    if processed != len(order):
        # A cycle exists: keep already-computed layers and leave the remainder on
        # the layer implied by their longest incoming edge from the acyclic part.
        for node in order:
            if indegree[node] > 0:
                incoming = [layer[source] + 1 for source, target in edges if target == node]
                layer[node] = max(incoming) if incoming else 0
    return layer


def _pull_back_sinks(layer: dict[str, int], order: list[str], edges: list[tuple[str, str]]) -> dict[str, int]:
    """Pull terminal nodes back toward their inputs so chains do not leave gaps."""
    incoming: dict[str, list[str]] = {node: [] for node in order}
    outgoing_count: dict[str, int] = {node: 0 for node in order}
    for source, target in edges:
        incoming[target].append(source)
        outgoing_count[source] += 1
    result = dict(layer)
    maximum = max(result.values()) if result else 0
    for node in order:
        if outgoing_count[node] == 0 and incoming[node]:
            anchored = max(result[parent] + 1 for parent in incoming[node])
            result[node] = min(result[node], max(anchored, 0))
            maximum = max(maximum, result[node])
    return result


def _barycenter_sweep(layers: dict[int, list[str]], edges: list[tuple[str, str]], sweeps: int = 4) -> dict[int, list[str]]:
    """Bidirectional barycenter ordering to reduce edge crossings."""
    incoming: dict[str, list[str]] = {}
    outgoing: dict[str, list[str]] = {}
    for source, target in edges:
        outgoing.setdefault(source, []).append(target)
        incoming.setdefault(target, []).append(source)
    if not layers:
        return layers
    max_layer = max(layers)
    order = {layer: list(nodes) for layer, nodes in layers.items()}
    for sweep in range(sweeps):
        downward = sweep % 2 == 0
        sequence = range(1, max_layer + 1) if downward else range(max_layer - 1, -1, -1)
        for layer in sequence:
            positions = {node: index for index, node in enumerate(order[layer])}
            reference_layer = layer - 1 if downward else layer + 1
            reference = order.get(reference_layer)
            if not reference:
                continue
            reference_positions = {node: index for index, node in enumerate(reference)}
            neighbours = incoming if downward else outgoing

            def key(node: str) -> tuple[float, int]:
                anchors = [reference_positions[parent] for parent in neighbours.get(node, []) if parent in reference_positions]
                if anchors:
                    return (sum(anchors) / len(anchors), positions[node])
                return (float(positions[node]), positions[node])

            order[layer] = sorted(order[layer], key=key)
    return order


def compute_layout(
    nodes: Sequence[Mapping[str, Any]],
    connections: Sequence[Mapping[str, Any]],
) -> dict[str, tuple[float, float]]:
    """Return deterministic ``(x, y)`` coordinates for every node.

    Nodes without any connection are treated as free-form notes and packed into
    their own block to the right of the layered graph, so a lone annotation can
    never perturb the DAG rows.
    """
    order = _node_ids(nodes)
    position: dict[str, tuple[float, float]] = {}
    if not order:
        return position

    connected: set[str] = set()
    edges = _edges(nodes, connections)
    for source, target in edges:
        connected.add(source)
        connected.add(target)

    layered_nodes = [node for node in order if node in connected]
    isolated = [node for node in order if node not in connected]

    layers: dict[int, list[str]] = {}
    if layered_nodes:
        edges_subset = [(source, target) for source, target in edges if source in connected and target in connected]
        layer_of = _longest_path_layers(layered_nodes, edges_subset)
        layer_of = _pull_back_sinks(layer_of, layered_nodes, edges_subset)
        for node in layered_nodes:
            layers.setdefault(layer_of[node], []).append(node)
        layers = _barycenter_sweep(layers, edges_subset)
        for layer_index in sorted(layers):
            column = layers[layer_index]
            for row, node in enumerate(column):
                position[node] = (ORIGIN_X + layer_index * LAYER_GAP_X, ORIGIN_Y + row * NODE_GAP_Y)

    if isolated:
        base_x = ORIGIN_X
        if layered_nodes:
            base_x = ORIGIN_X + (max(layers) + 1) * LAYER_GAP_X
        for index, node in enumerate(isolated):
            column = index % NOTE_COLUMNS
            row = index // NOTE_COLUMNS
            position[node] = (base_x + column * LAYER_GAP_X, ORIGIN_Y + row * NOTE_GAP_Y)
    return position


__all__ = ["compute_layout", "LAYER_GAP_X", "NODE_GAP_Y", "ORIGIN_X", "ORIGIN_Y"]
