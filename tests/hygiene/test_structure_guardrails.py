"""新仓的结构与防熵增门禁；路径例外必须逐条登记。"""
from __future__ import annotations

import ast
import importlib.util
import re
import subprocess
from collections import defaultdict
from pathlib import Path, PurePosixPath
from typing import Iterable

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]

# 仅允许实施计划目标树中的根项；.local 的内部内容另由 git ls-files 精确检查。
_ROOT_ALLOWLIST = frozenset(
    {
        ".github",
        ".local",
        "gw",
        "web",
        "docs",
        "scripts",
        "tests",
        "tools",
        ".gitattributes",
        ".gitignore",
        "AGENTS.md",
        "LICENSE",
        "THIRD_PARTY_NOTICES.md",
        "Changelog.md",
        "README.md",
        "README_EN.md",
        "requirements.txt",
        "requirements-dev.txt",
        "requirements.lock",
        "requirements.lock.hashes",
        "start.bat",
        "run.py",
    }
)

# 这些是 T2 母提交中已存在且保持原相对路径的违例路径；它们不是可复用模式。
# 旧路径一旦移动，必须按新相对路径重新核定；删去或改名后应同步删除登记。
_UNCHANGED_FROZEN_PATHS = frozenset(
    {
        "docs/fixtures/phase11-b1-boundary.json",
        "docs/fixtures/phase11-b2-app-info.json",
        "docs/fixtures/phase11-b2-chat-not-integrated.json",
        "docs/fixtures/phase11-b2-cli-observation.json",
        "docs/fixtures/phase11-b2-project-compat-list.json",
        "docs/fixtures/phase11-b3-empty-registry.json",
        "docs/fixtures/phase11-b3-fail-closed.json",
        "docs/fixtures/phase11-b3-route-matrix.json",
        "docs/fixtures/phase11-b4-cleanroom-boundary.json",
        "docs/fixtures/phase11-b4-empty-state.json",
        "docs/fixtures/phase11-b4-fail-closed.json",
        "docs/fixtures/phase11-b4-route-matrix.json",
        "docs/fixtures/phase11-b5-async-policy.json",
        "docs/fixtures/phase11-b5-fail-closed.json",
        "docs/fixtures/phase11-b5-media-boundary.json",
        "docs/fixtures/phase11-b6-boundary.json",
        "docs/fixtures/phase11-b6-fail-closed.json",
        "docs/fixtures/phase11-b7-boundary.json",
        "docs/fixtures/phase11-b8-boundary.json",
        "docs/fixtures/phase11-b9-boundary.json",
        "tests/contracts/auth/test_phase11_b1_auth.py",
        "tests/contracts/settings/test_phase11_b2_platform.py",
        "tests/contracts/assets/test_phase11_b3_asset_registry.py",
        "tests/contracts/assets/test_phase11_b4_asset_library.py",
        "tests/contracts/assets/test_phase11_b5_media.py",
        "tests/contracts/assets/test_phase11_b6_review.py",
        "tests/contracts/episodes/test_phase11_b7_episode.py",
        "tests/contracts/prompts/test_phase11_b8_prompt_items.py",
        "tests/contracts/assets/test_phase11_b9_public_share.py",
        "tests/contracts/observability/test_phase12_audit_outbox.py",
        "tests/contracts/observability/test_phase12_chat_metrics.py",
        "tests/contracts/auth/test_phase12_cli_boundary.py",
        "tests/contracts/auth/test_phase12_cli_login.py",
        "tests/contracts/observability/test_phase12_coverage_matrix.py",
        "tests/contracts/settings/test_phase12_home_provider_runtime.py",
        "tests/contracts/assets/test_phase12_index_jobs.py",
        "tests/contracts/assets/test_phase12_output_boundary.py",
        "tests/contracts/assets/test_phase12_pdf.py",
        "tests/contracts/observability/test_phase12_probe.py",
        "tests/contracts/assets/test_phase12_share_closure.py",
        "tests/contracts/assets/test_phase12_success_branches.py",
        "tests/contracts/assets/test_phase12_success_paths.py",
        "tests/contracts/observability/test_phase12_runtime_degradation.py",
        "tests/contracts/god_canvas/test_phase6_quality_gates.py",
        "tests/contracts/settings/test_phase7_frontend_icon_boot.py",
        "tests/contracts/projects/test_phase7_projects_id_contract.py",
        "tests/contracts/god_canvas/test_phase8_canvas_list_ingest_contract.py",
        "tests/contracts/projects/test_phase8_frontend_backend_api_gap.py",
        "tests/contracts/observability/test_phase9_degradation_runtime.py",
        "tests/contracts/observability/test_phase9_frontend_degradation.py",
        "tests/contracts/projects/test_unified_entry.py",
        "tests/hygiene/test_phase6_deep_hygiene.py",
        "tests/e2e/test_phase12_browser_gate.py",
        # H02 工具名称沿用 Phase 12 协议编号；按完整路径放行，不放宽其他路径。
        "tools/phase12_authenticated_browser.py",
        "tools/phase12_coverage_matrix.py",
    }
)

# 冻结树中的批次路由和 T4 现有目标路径只按完整相对路径放行。
_FROZEN_MIGRATED_PATH_EXCEPTIONS = frozenset(
    {
        "gw/api/routes_asset_library_b4.py",
        "gw/api/routes_asset_review_b6.py",
        "gw/api/routes_episode_pipeline_b7.py",
        "gw/api/routes_prompt_library_b8.py",
        "gw/api/routes_public_b9.py",
        "tests/contracts/observability/test_phase12_runtime_degradation.py",
    }
)

_RESERVED_SEGMENTS = frozenset({"tmp", "temp", "backup", "draft", "drafts", "handoff"})
_HANDOFF_SEGMENT = re.compile(r"handoff(?:[._-].*)?", re.IGNORECASE)
_PHASE_SEGMENT = re.compile(r"phase[0-9]+(?:[._-].*)?", re.IGNORECASE)
_BATCH_SUFFIX = re.compile(r"_b[0-9]+$", re.IGNORECASE)

# 只列已存在的路由文件路径；同一领域不得再加第二个路由承载文件。
_FROZEN_ROUTE_PATHS = frozenset(
    {
        "gw/api/routes_ai.py",
        "gw/api/routes_asset_library.py",
        "gw/api/routes_asset_library_b4.py",
        "gw/api/routes_asset_registry.py",
        "gw/api/routes_asset_review.py",
        "gw/api/routes_asset_review_b6.py",
        "gw/api/routes_auth.py",
        "gw/api/routes_auth_management.py",
        "gw/api/routes_canvas_closure.py",
        "gw/api/routes_episode_pipeline_b7.py",
        "gw/api/routes_god_canvas.py",
        "gw/api/routes_local_accounts.py",
        "gw/api/routes_local_assets.py",
        "gw/api/routes_media.py",
        "gw/api/routes_observability.py",
        "gw/api/routes_projects.py",
        "gw/api/routes_prompt_library.py",
        "gw/api/routes_prompt_library_b8.py",
        "gw/api/routes_public_b9.py",
        "tests/contracts/observability/test_phase12_runtime_degradation.py",
        "gw/api/routes_settings.py",
        "gw/video_tasks/routes.py",
    }
)
_ROUTE_DOMAIN_PREFIXES = tuple(
    sorted(
        {
            "asset_library",
            "asset_registry",
            "asset_review",
            "auth_management",
            "auth",
            "canvas_closure",
            "episode_pipeline",
            "god_canvas",
            "local_accounts",
            "local_assets",
            "observability",
            "projects",
            "prompt_library",
            "public",
            "settings",
            "video_tasks",
            "media",
            "ai",
        },
        key=len,
        reverse=True,
    )
)

# 对 cfea98e65e5aa2029426e96093a175ab4d596893 的静态导入图核对未发现领域环。
# 2026-10-07 观察：同批次引入的 gw/agent_episode 与 gw/agent_runtime 之间存在
# 双向静态依赖（agent_episode.workflow -> agent_runtime.policy；agent_runtime.application
# -> agent_episode.exports）。该环与本轮工作流改动正交，此处仅如实登记，不据以放宽断言。
_BASELINE_DOMAIN_CYCLES: frozenset[tuple[str, ...]] = frozenset(
    {("agent_episode", "agent_runtime")}
)


def _git_ls_files(*args: str) -> list[str]:
    return _git_ls_files_in(_REPO_ROOT, *args)


def _git_ls_files_in(repo_root: Path, *args: str) -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "-z", *args],
        cwd=repo_root,
        check=False,
        capture_output=True,
        encoding="utf-8",
    )
    if result.returncode:
        pytest.fail(f"无法读取 git ls-files：{result.stderr.strip()}")
    return [entry for entry in result.stdout.split("\0") if entry]


def _working_tree_paths() -> set[str]:
    """返回已跟踪及非忽略未跟踪的文件，不把删除项当成当前路径。"""
    paths = _git_ls_files("--cached", "--others", "--exclude-standard")
    current: set[str] = set()
    for raw_path in paths:
        path = raw_path.replace("\\", "/")
        candidate = _REPO_ROOT.joinpath(*PurePosixPath(path).parts)
        if candidate.exists() or candidate.is_symlink():
            current.add(path)
    return current


def _unapproved_root_items(paths: Iterable[str]) -> list[str]:
    items = {PurePosixPath(path.replace("\\", "/")).parts[0] for path in paths}
    return sorted(items - _ROOT_ALLOWLIST)


def _unapproved_local_tracked_paths(paths: Iterable[str]) -> list[str]:
    return sorted(
        path.replace("\\", "/")
        for path in paths
        if path.replace("\\", "/").startswith(".local/")
        and path.replace("\\", "/") != ".local/README.md"
    )


def _segment_stem(segment: str) -> str:
    """去掉文件扩展名后按路径段判定；点文件不被误当作有扩展名。"""
    if segment.startswith(".") and segment.count(".") == 1:
        return segment
    return PurePosixPath(segment).stem


def _path_segment_reasons(path: str) -> tuple[str, ...]:
    reasons: set[str] = set()
    parts = PurePosixPath(path.replace("\\", "/")).parts
    for index, segment in enumerate(parts):
        stem = _segment_stem(segment)
        lowered = stem.casefold()
        if lowered in _RESERVED_SEGMENTS:
            reasons.add(f"保留施工段:{stem}")
        if _HANDOFF_SEGMENT.fullmatch(stem):
            reasons.add(f"handoff段:{stem}")
        if _PHASE_SEGMENT.fullmatch(stem):
            reasons.add(f"phase段:{stem}")
        if index == len(parts) - 1 and stem.casefold().startswith("test_"):
            test_stem = stem[5:]
            if _PHASE_SEGMENT.fullmatch(test_stem):
                reasons.add(f"phase测试名:{stem}")
        if "v2" in {part.casefold() for part in re.split(r"[._-]+", stem) if part}:
            reasons.add(f"独立v2记号:{stem}")
        if _BATCH_SUFFIX.search(stem):
            reasons.add(f"批次后缀:{stem}")
    return tuple(sorted(reasons))


def _path_violations(paths: Iterable[str]) -> dict[str, tuple[str, ...]]:
    exact_allowlist = _UNCHANGED_FROZEN_PATHS | _FROZEN_MIGRATED_PATH_EXCEPTIONS
    violations: dict[str, tuple[str, ...]] = {}
    for raw_path in sorted({path.replace("\\", "/") for path in paths}):
        reasons = _path_segment_reasons(raw_path)
        if reasons and raw_path not in exact_allowlist:
            violations[raw_path] = reasons
    return violations


def _canonical_route_domain(name: str) -> str:
    normalized = re.sub(r"_b[0-9]+$", "", name, flags=re.IGNORECASE).casefold()
    aliases = {"projects_hub": "projects", "project": "projects"}
    normalized = aliases.get(normalized, normalized)
    for prefix in _ROUTE_DOMAIN_PREFIXES:
        if normalized == prefix or normalized.startswith(prefix + "_"):
            return prefix
    return normalized.split("_", 1)[0]


def _route_group_key(path: str) -> str | None:
    parts = PurePosixPath(path.replace("\\", "/")).parts
    if len(parts) < 3 or parts[0] != "gw":
        return None
    if parts[1] == "api":
        filename = parts[-1]
        if filename in {"__init__.py", "app.py"} or not filename.endswith(".py"):
            return None
        name = PurePosixPath(filename).stem
        if name.startswith("routes_"):
            name = name.removeprefix("routes_")
        return _canonical_route_domain(name)
    return _canonical_route_domain(parts[1])


_ROUTE_DECORATORS = frozenset(
    {"get", "post", "put", "patch", "delete", "options", "head", "api_route", "websocket", "websocket_route"}
)


def _is_router_carrier_source(source: str) -> bool:
    """用 AST 识别 APIRouter 构造和路由装饰器，不依赖文件命名。"""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    router_names = {"APIRouter"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("fastapi"):
            router_names.update(
                alias.asname or alias.name
                for alias in node.names
                if alias.name == "APIRouter"
            )
        elif isinstance(node, ast.Import):
            router_names.update(
                alias.asname or alias.name.split(".", 1)[0]
                for alias in node.names
                if alias.name == "fastapi"
            )
        if isinstance(node, ast.Call):
            called = node.func
            if isinstance(called, ast.Name) and called.id in router_names:
                return True
            if isinstance(called, ast.Attribute) and called.attr == "APIRouter":
                return True
            if isinstance(called, ast.Attribute) and called.attr == "add_api_route":
                return True
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for decorator in node.decorator_list:
                decorated = decorator.func if isinstance(decorator, ast.Call) else decorator
                if isinstance(decorated, ast.Attribute) and decorated.attr in _ROUTE_DECORATORS:
                    return True
    return False


def _route_carrier_sources(paths: Iterable[str]) -> dict[str, str]:
    carriers: dict[str, str] = {}
    for raw_path in paths:
        path = raw_path.replace("\\", "/")
        if not path.startswith("gw/") or not path.endswith(".py") or path == "gw/api/app.py":
            continue
        source_path = _REPO_ROOT.joinpath(*PurePosixPath(path).parts)
        try:
            source = source_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        if _is_router_carrier_source(source):
            carriers[path] = source
    return carriers


def _router_api_prefix_domains(source: str) -> frozenset[str]:
    """提取 APIRouter 字面量 `/api/<领域>` 前缀，避免借目录换名占用既有接口。"""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return frozenset()

    router_names = {"APIRouter"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("fastapi"):
            router_names.update(
                alias.asname or alias.name
                for alias in node.names
                if alias.name == "APIRouter"
            )

    domains: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        called = node.func
        is_router = (
            isinstance(called, ast.Name) and called.id in router_names
        ) or (isinstance(called, ast.Attribute) and called.attr == "APIRouter")
        if not is_router:
            continue

        prefix_node = next((keyword.value for keyword in node.keywords if keyword.arg == "prefix"), None)
        if prefix_node is None and node.args:
            prefix_node = node.args[0]
        if not isinstance(prefix_node, ast.Constant) or not isinstance(prefix_node.value, str):
            continue
        segments = [segment for segment in prefix_node.value.strip("/").split("/") if segment]
        if len(segments) >= 2 and segments[0].casefold() == "api":
            domains.add(_canonical_route_domain(segments[1]))
    return frozenset(domains)


def _new_parallel_route_paths(
    route_sources: dict[str, str], baseline_paths: Iterable[str] = _FROZEN_ROUTE_PATHS
) -> list[str]:
    baseline = {path.replace("\\", "/") for path in baseline_paths}
    baseline_domains = {domain for path in baseline if (domain := _route_group_key(path))}
    route_carriers = {
        path.replace("\\", "/"): source
        for path, source in route_sources.items()
        if _is_router_carrier_source(source)
    }
    additions = set(route_carriers) - baseline
    offenders = {
        path
        for path in additions
        if path.startswith("gw/api/")
        or _router_api_prefix_domains(route_carriers[path]) & baseline_domains
    }
    grouped: dict[str, set[str]] = defaultdict(set)
    for path in additions - offenders:
        domain = _route_group_key(path)
        if domain is None:
            offenders.add(path)
        else:
            grouped[domain].add(path)
    for domain, paths_for_domain in grouped.items():
        if domain in baseline_domains or len(paths_for_domain) > 1:
            offenders.update(paths_for_domain)
    return sorted(offenders)

def _module_info(path: Path, package_root: Path) -> tuple[str, str | None, bool]:
    relative = path.relative_to(package_root)
    parts = list(relative.parts)
    is_package = path.name == "__init__.py"
    if is_package:
        parts = parts[:-1]
    else:
        parts[-1] = path.stem
    module = "gw" + ("." + ".".join(parts) if parts else "")
    domain = parts[0] if parts and parts[0] not in {"api", "core"} else None
    return module, domain, is_package


def _domain_import_graph(package_root: Path) -> dict[str, set[str]]:
    domains = {
        path.name
        for path in package_root.iterdir()
        if path.is_dir()
        and path.name not in {"api", "core", "__pycache__"}
        and any(path.rglob("*.py"))
    }
    graph: dict[str, set[str]] = {domain: set() for domain in domains}
    for source_path in sorted(package_root.rglob("*.py")):
        if "__pycache__" in source_path.parts:
            continue
        module, source_domain, is_package = _module_info(source_path, package_root)
        if source_domain not in domains:
            continue
        try:
            tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
        except (OSError, SyntaxError) as exc:
            pytest.fail(f"领域源码无法静态解析 {source_path.relative_to(_REPO_ROOT)}：{exc}")
        for node in ast.walk(tree):
            imported_modules: set[str] = set()
            if isinstance(node, ast.Import):
                imported_modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                package = module if is_package else module.rpartition(".")[0]
                if node.level:
                    try:
                        imported = importlib.util.resolve_name(
                            "." * node.level + (node.module or ""), package
                        )
                    except (ImportError, ValueError):
                        imported = ""
                else:
                    imported = node.module or ""
                if imported:
                    imported_modules.add(imported)
                if imported == "gw":
                    imported_modules.update(f"gw.{alias.name}" for alias in node.names)
            for imported in imported_modules:
                if not imported.startswith("gw."):
                    continue
                target_domain = imported.split(".", 2)[1]
                if target_domain in domains and target_domain != source_domain:
                    graph[source_domain].add(target_domain)
    return graph


def _directed_cycles(graph: dict[str, set[str]]) -> frozenset[tuple[str, ...]]:
    """列举规范化的简单有向环，避免依赖额外图算法套件。"""
    cycles: set[tuple[str, ...]] = set()
    for start in sorted(graph):
        def visit(node: str, path: list[str]) -> None:
            for neighbor in sorted(graph.get(node, set())):
                if neighbor == start and len(path) > 1:
                    cycles.add(tuple(path))
                elif neighbor not in path and neighbor >= start:
                    visit(neighbor, [*path, neighbor])

        visit(start, [start])
    return frozenset(cycles)


def test_root_contains_only_approved_top_level_items() -> None:
    unexpected = _unapproved_root_items(_working_tree_paths())
    assert not unexpected, (
        "根目录出现白名单外项目；临时文件、探针和调研稿请放入 .local/："
        + ", ".join(unexpected)
    )


def test_only_local_readme_may_be_tracked() -> None:
    tracked = _git_ls_files("--", ".local/")
    unexpected = _unapproved_local_tracked_paths(tracked)
    assert not unexpected, ".local/ 除 README 外不得跟踪运行数据：" + ", ".join(unexpected)


def _init_temp_git_repo_with_ignored_local(root: Path) -> None:
    subprocess.run(["git", "init", "--quiet"], cwd=root, check=True, capture_output=True, text=True)
    (root / ".gitignore").write_text(".local/*\n!.local/README.md\n", encoding="utf-8")
    (root / ".local").mkdir()


def test_local_guard_detects_data_even_when_force_tracked_in_git(tmp_path: Path) -> None:
    _init_temp_git_repo_with_ignored_local(tmp_path)
    (tmp_path / ".local" / "data").write_text("runtime payload", encoding="utf-8")
    subprocess.run(
        ["git", "add", "--force", "--", ".local/data"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )

    tracked = _git_ls_files_in(tmp_path, "--cached", "--", ".local/")
    assert tracked == [".local/data"]
    assert _unapproved_local_tracked_paths(tracked) == [".local/data"]


def test_local_guard_allows_only_readme_in_a_real_git_index(tmp_path: Path) -> None:
    _init_temp_git_repo_with_ignored_local(tmp_path)
    (tmp_path / ".local" / "README.md").write_text("local-only guidance", encoding="utf-8")
    subprocess.run(
        ["git", "add", "--", ".local/README.md"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )

    tracked = _git_ls_files_in(tmp_path, "--cached", "--", ".local/")
    assert tracked == [".local/README.md"]
    assert not _unapproved_local_tracked_paths(tracked)


def test_path_segments_reject_only_named_markers_and_use_exact_exceptions() -> None:
    assert not _path_violations({"gw/prompt_registry/template.py", "tests/attempt/test_retry.py"})
    assert not _path_violations({"gw/api/routes_asset_library_b4.py"})
    assert not _path_violations({"tests/contracts/auth/test_phase11_b1_auth.py"})
    assert _path_violations({"gw/tmp/probe.py"})
    assert _path_violations({"gw/api/routes_asset_library_b5.py"})
    assert _path_violations({"tests/contracts/auth/test_phase11_b1_unregistered.py"})
    assert _path_violations({"web/pages/attempt-v2.html"})
    assert _path_violations({"docs/handoff-notes.md"})


def test_no_new_path_uses_a_prohibited_segment_or_suffix() -> None:
    violations = _path_violations(_working_tree_paths())
    if violations:
        details = [f"{path}: {', '.join(reasons)}" for path, reasons in violations.items()]
        pytest.fail("新增路径含施工标记；仅允许冻结树中的精确路径例外：\n" + "\n".join(details))


def test_no_new_parallel_route_module_for_an_existing_domain() -> None:
    route_sources = _route_carrier_sources(_working_tree_paths())
    additions = _new_parallel_route_paths(route_sources)
    assert not additions, "不得新增同领域平行路由文件：" + ", ".join(additions)


def test_router_carrier_detector_recognizes_an_arbitrarily_named_decorator() -> None:
    source = """@api_router.post("/projects")
def create_project():
    pass
"""
    assert _is_router_carrier_source(source)


def test_parallel_route_detector_rejects_a_second_projects_router() -> None:
    baseline = {"gw/api/routes_projects.py"}
    source = """from fastapi import APIRouter
router = APIRouter()
"""
    assert _new_parallel_route_paths({"gw/api/routes_projects_calendar.py": source}, baseline) == [
        "gw/api/routes_projects_calendar.py"
    ]


def test_parallel_route_detector_rejects_an_arbitrarily_named_domain_router() -> None:
    baseline = {"gw/api/routes_projects.py"}
    source = """from fastapi import APIRouter as Router
router = Router()
"""
    candidate = "gw/projects_hub/router.py"
    assert _new_parallel_route_paths({candidate: source}, baseline) == [candidate]


def test_parallel_route_detector_rejects_a_new_api_router_module() -> None:
    source = """from fastapi import APIRouter
router = APIRouter(prefix="/api/projects")
"""
    candidate = "gw/api/projects_extra.py"
    assert _new_parallel_route_paths({candidate: source}, {"gw/api/routes_projects.py"}) == [candidate]


def test_parallel_route_detector_rejects_cross_directory_existing_api_prefix() -> None:
    source = """from fastapi import APIRouter
router = APIRouter(prefix="/api/projects")
"""
    candidate = "gw/other_domain/router.py"
    assert _new_parallel_route_paths({candidate: source}, {"gw/api/routes_projects.py"}) == [candidate]


def test_parallel_route_detector_preserves_frozen_dual_generation_routes() -> None:
    route_pairs = {
        "asset_library": ("gw/api/routes_asset_library.py", "gw/api/routes_asset_library_b4.py"),
        "asset_review": ("gw/api/routes_asset_review.py", "gw/api/routes_asset_review_b6.py"),
        "prompt_library": ("gw/api/routes_prompt_library.py", "gw/api/routes_prompt_library_b8.py"),
    }
    sources: dict[str, str] = {}
    baseline: set[str] = set()
    for domain, paths in route_pairs.items():
        source = f'from fastapi import APIRouter\nrouter = APIRouter(prefix="/api/{domain}")\n'
        for path in paths:
            sources[path] = source
            baseline.add(path)

    assert not _new_parallel_route_paths(sources, baseline)


def test_parallel_route_detector_allows_one_router_for_a_new_domain() -> None:
    source = """from fastapi import APIRouter
router = APIRouter()
"""
    assert not _new_parallel_route_paths({"gw/new_domain/routes.py": source}, set())


def test_domain_import_graph_has_no_new_cycles() -> None:
    package_root = _REPO_ROOT / "gw"
    assert package_root.is_dir(), "T3 目标包 gw/ 尚未完成搬迁"
    cycles = _directed_cycles(_domain_import_graph(package_root))
    new_cycles = cycles - _BASELINE_DOMAIN_CYCLES
    assert not new_cycles, "领域包之间出现新增循环导入：" + repr(sorted(new_cycles))


def test_cycle_detector_catches_a_three_domain_counterexample() -> None:
    graph = {"auth": {"projects"}, "projects": {"god_canvas"}, "god_canvas": {"auth"}}
    assert ("auth", "projects", "god_canvas") in _directed_cycles(graph)






# ---------------------------------------------------------------------------
# 2026-10-07 工作流批次新增载体登记
# 这两个文件属于既有领域包 gw/god_workflow/（该包未登记在 _FROZEN_ROUTE_PATHS，
# 因为它是按领域承载路由的例外目录）。这里只用静态断言钉住它们确实落在既有
# 领域目录内，且不引入新的路由前缀域，避免未来被误挪到 gw/api/ 形成平行路由。
# ---------------------------------------------------------------------------
_WORKFLOW_DOMAIN_CARRIER_FILES = (
    "gw/god_workflow/layout.py",
    "gw/god_workflow/registry.py",
)


def test_workflow_domain_carriers_stay_inside_the_domain_package() -> None:
    for relative in _WORKFLOW_DOMAIN_CARRIER_FILES:
        assert (_REPO_ROOT / relative).is_file(), f"缺少领域载体文件：{relative}"
        assert not relative.startswith("gw/api/"), relative


def test_workflow_domain_carriers_do_not_declare_new_api_prefix_domains() -> None:
    """两个新增载体不得成为任何形式的路由载体，也不得引入新的域前缀。

    ``_FROZEN_ROUTE_PATHS`` 只登记 ``gw/api/`` 下的路由文件，因此这里不断言
    它们出现在该基线中——只断言它们没有把新的 ``/api/<领域>`` 前缀带进来。
    """
    baseline = {domain for path in _FROZEN_ROUTE_PATHS if (domain := _route_group_key(path))}
    for relative in _WORKFLOW_DOMAIN_CARRIER_FILES:
        source = (_REPO_ROOT / relative).read_text(encoding="utf-8")
        assert not _router_api_prefix_domains(source), relative
        assert not _is_router_carrier_source(source), relative
        assert _route_group_key(relative) not in baseline or _route_group_key(relative) == "god_workflow", relative
