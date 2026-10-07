# -*- coding: utf-8 -*-
"""Local-only shim so the pre-existing agent_runtime package can be imported on
the Python 3.10 test interpreter used in this sandbox.

This is NOT an implementation change: ``gw/agent_runtime`` is expected to run on
Python 3.11+ (see .local/venv/t8-lock-20260929/pyvenv.cfg).  It exists only so
the workflow contract suites can reach ``gw.api.app`` here.
"""
from __future__ import annotations

import enum

if not hasattr(enum, "StrEnum"):
    class StrEnum(str, enum.Enum):
        def __str__(self) -> str:  # pragma: no cover - trivial
            return str(self.value)

    enum.StrEnum = StrEnum  # type: ignore[attr-defined]
