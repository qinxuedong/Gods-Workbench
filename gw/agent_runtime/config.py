"""Workspace-confined runtime path configuration with no import-time I/O."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


class PathIsolationError(ValueError):
    """Raised when configuration could escape or alias the independent work root."""


def _is_reparse_point(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    if path.is_symlink():
        return True
    # Windows junctions and other reparse-point directories are not always
    # reported as symlinks by pathlib; reject the OS reparse attribute as well.
    return bool(getattr(metadata, "st_file_attributes", 0) & 0x400)


def workspace_root() -> Path:
    """Return the package's independent checkout root, not a user data directory."""
    root = Path(__file__).absolute().parents[2]
    if _is_reparse_point(root):
        raise PathIsolationError("The workspace root must not be a symlink or junction.")
    return root.resolve(strict=True)


def _relative_to(path: Path, root: Path) -> Path:
    try:
        return path.relative_to(root)
    except ValueError as exc:
        raise PathIsolationError(f"Path is outside the isolated workspace: {path}") from exc


def _check_existing_components(path: Path, root: Path) -> None:
    relative = _relative_to(path, root)
    cursor = root
    for component in relative.parts:
        cursor = cursor / component
        if _is_reparse_point(cursor):
            raise PathIsolationError(f"Symlink/junction path is not allowed: {cursor}")


def resolve_workspace_path(candidate: str | os.PathLike[str]) -> Path:
    """Resolve a candidate below the checkout; reject traversal and reparse points.

    The function never creates a directory. Existing symlink/junction components
    are rejected even when they happen to target another path inside this root.
    """
    root = workspace_root()
    raw = Path(candidate)
    lexical = Path(os.path.abspath(raw if raw.is_absolute() else root / raw))
    _relative_to(lexical, root)
    _check_existing_components(lexical, root)
    resolved = lexical.resolve(strict=False)
    _relative_to(resolved, root)
    return resolved


def resolve_trusted_path(
    candidate: str | os.PathLike[str],
    *,
    trusted_root: str | os.PathLike[str],
) -> Path:
    """Resolve a path below an explicitly validated external runtime data root.

    The caller must obtain ``trusted_root`` from its own runtime path policy.
    This function still requires an absolute root, rejects existing symlink/Junction
    components, and confines the database path to that root.
    """
    raw_root = Path(trusted_root)
    if not raw_root.is_absolute():
        raise PathIsolationError("An injected trusted data root must be absolute.")
    root_lexical = Path(os.path.abspath(raw_root))
    _check_path_components(root_lexical)
    root = root_lexical.resolve(strict=False)

    raw_candidate = Path(candidate)
    lexical = Path(os.path.abspath(raw_candidate if raw_candidate.is_absolute() else root_lexical / raw_candidate))
    try:
        lexical.relative_to(root_lexical)
    except ValueError as exc:
        raise PathIsolationError("An injected runtime database path must remain below its trusted data root.") from exc
    _check_path_components(lexical)
    resolved = lexical.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise PathIsolationError("An injected runtime database path must remain below its trusted data root.") from exc
    return resolved


def _check_path_components(path: Path) -> None:
    """Reject existing symlink/Junction components without requiring a workspace root."""
    current = Path(path.anchor) if path.anchor else Path()
    parts = path.parts[1:] if path.anchor else path.parts
    for component in parts:
        if not component:
            continue
        current = current / component
        if _is_reparse_point(current):
            raise PathIsolationError("A trusted runtime path cannot contain symlinks or Junctions.")


def validate_pythonpath(value: str | None = None) -> None:
    """Reject external import roots instead of probing for another ``gw`` package."""
    configured = os.environ.get("PYTHONPATH", "") if value is None else value
    root = workspace_root()
    for entry in configured.split(os.pathsep):
        if not entry:
            continue
        raw = Path(entry)
        lexical = Path(os.path.abspath(raw if raw.is_absolute() else Path.cwd() / raw))
        _relative_to(lexical, root)
        _check_existing_components(lexical, root)
        _relative_to(lexical.resolve(strict=False), root)


def validate_package_origin(package_file: str | os.PathLike[str]) -> None:
    """Fail if ``gw`` was imported from a same-named host or external package."""
    resolve_workspace_path(package_file)


@dataclass(frozen=True, slots=True)
class AgentRuntimeConfig:
    workspace_root: Path
    data_dir: Path
    checkpoint_dir: Path


def load_runtime_config() -> AgentRuntimeConfig:
    """Load module-prefixed paths and prove both stay below this checkout."""
    validate_pythonpath()
    root = workspace_root()
    data_dir = resolve_workspace_path(
        os.environ.get("GW_AGENT_DATA_DIR", ".local/runtime-dev/data")
    )
    checkpoint_dir = resolve_workspace_path(
        os.environ.get("GW_AGENT_CHECKPOINT_DIR", ".local/runtime-dev/checkpoints")
    )
    return AgentRuntimeConfig(
        workspace_root=root,
        data_dir=data_dir,
        checkpoint_dir=checkpoint_dir,
    )
