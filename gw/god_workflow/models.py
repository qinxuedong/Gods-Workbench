"""Workflow value objects shared by parser, storage and API."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping


class WorkflowSource(str, Enum):
    RUNNINGHUB = "runninghub"
    LIBLIB = "liblib"
    COMFYUI = "comfyui"
    CANVAS = "canvas"


@dataclass(frozen=True)
class WorkflowNode:
    id: str
    kind: str
    title: str = ""
    position: tuple[float, float] = (0.0, 0.0)
    width: float | None = None
    height: float | None = None
    inputs: Mapping[str, Any] = field(default_factory=dict)
    outputs: Mapping[str, Any] = field(default_factory=dict)
    widgets: Any = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    asset_ids: tuple[str, ...] = ()
    raw_payload: Any = None
    extensions: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        position: dict[str, Any] = {"x": self.position[0], "y": self.position[1]}
        if self.width is not None:
            position["width"] = self.width
        if self.height is not None:
            position["height"] = self.height
        return {"id": self.id, "kind": self.kind, "title": self.title,
                "position": position, "inputs": dict(self.inputs),
                "outputs": dict(self.outputs), "widgets": self.widgets,
                "metadata": dict(self.metadata), "asset_ids": list(self.asset_ids),
                "raw_payload": self.raw_payload, "extensions": dict(self.extensions)}


@dataclass(frozen=True)
class WorkflowConnection:
    id: str
    source: str
    target: str
    source_slot: str | None = None
    target_slot: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "source": self.source, "target": self.target,
                "source_slot": self.source_slot, "target_slot": self.target_slot}


@dataclass(frozen=True)
class WorkflowDocument:
    workflow_id: str | None = None
    version: int = 1
    source: WorkflowSource = WorkflowSource.COMFYUI
    name: str = "workflow"
    folder_id: str | None = None
    nodes: tuple[WorkflowNode, ...] = ()
    connections: tuple[WorkflowConnection, ...] = ()
    viewport: Mapping[str, Any] = field(default_factory=dict)
    note: Any = None
    media: Any = None
    dependencies: Any = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()
    raw_payload: Any = None
    extensions: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"workflow_id": self.workflow_id, "version": self.version,
                "source": self.source.value, "name": self.name, "folder_id": self.folder_id,
                "nodes": [node.as_dict() for node in self.nodes],
                "connections": [link.as_dict() for link in self.connections],
                "viewport": dict(self.viewport), "note": self.note, "media": self.media,
                "dependencies": self.dependencies, "metadata": dict(self.metadata),
                "warnings": list(self.warnings), "raw_payload": self.raw_payload,
                "extensions": dict(self.extensions)}
