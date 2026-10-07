"""洁净室卫生自检与防污染自动化测试。

保障仓库零旧仓实现泄漏、零未授权二进制资源、零插件协议隐式实现。
"""

from pathlib import Path, PurePosixPath
import hashlib
import json
import re
import subprocess
import pytest


# 禁用扩展名 / 白名单的**唯一事实来源**：`tests/hygiene/cleanroom_extensions.py`。
# 历史教训：同一份清单曾在三个位置各写一遍，并已实际漂移（39 / 39 / 27 项）。
# 因此此处**禁止再写字面量**，一律从单一来源导入；CI 侧由下述契约用例反查比对。
from cleanroom_extensions import (  # noqa: E402
    ALLOWED_BINARY_ALLOWLIST,
    BANNED_EXTENSIONS,
    REQUIRED_BANNED_EXTENSIONS,
)


def _pytest_cleanroom_allowlist_is_consistent() -> None:
    """唯一来源自检：必需集合不得超出实际清单（防止来源文件自身被削）。"""
    assert REQUIRED_BANNED_EXTENSIONS <= BANNED_EXTENSIONS
    assert ALLOWED_BINARY_ALLOWLIST == {
        "start.bat",
        "web/vendor/fonts/SourceHanSansCN-Bold.otf",
        "web/vendor/fonts/SourceHanSansCN-Medium.otf",
        "web/vendor/fonts/SourceHanSansCN-Normal.otf",
    }


_pytest_cleanroom_allowlist_is_consistent()


def _canonical_sha256(path: Path) -> str:
    """按内容规范化（CRLF/CR 归一为 LF）后计算 SHA-256。

    登记表绑定的是**文件内容**，而不是某个平台的检出行尾表示；Windows 工作树
    与 Linux CI 检出必须得到同一结论，因此比较前统一归一化行尾。
    """
    raw = path.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return hashlib.sha256(raw).hexdigest()


def _git_visible_paths(repo_root: Path) -> list[str]:
    """列出 Git 已跟踪及非忽略未跟踪路径，包括被忽略但已跟踪的文件。"""
    result = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=repo_root,
        check=False,
        capture_output=True,
    )
    if result.returncode:
        pytest.fail(f"无法读取 Git 可见文件清单：{result.stderr.decode('utf-8', errors='replace')}")
    return [entry.decode("utf-8") for entry in result.stdout.split(b"\0") if entry]


def _banned_binary_paths(repo_root: Path) -> list[str]:
    """按 Git 可见文件集合检查二进制；忽略的运行产物不扫描，已跟踪文件仍检查。"""
    found_banned = []
    for raw_path in _git_visible_paths(repo_root):
        relative_path = raw_path.replace("\\", "/")
        candidate = repo_root.joinpath(*Path(relative_path).parts)
        if not candidate.is_file() or candidate.suffix.lower() not in BANNED_EXTENSIONS:
            continue
        if relative_path in ALLOWED_BINARY_ALLOWLIST:
            continue
        found_banned.append(relative_path)
    return sorted(found_banned)


def test_no_banned_binary_assets(repo_root: Path):
    """确保 Git 可见文件中绝不存在白名单外的受限二进制资源。"""
    found_banned = _banned_binary_paths(repo_root)
    assert not found_banned, (
        "发现受限二进制资源进入仓库"
        f"（不在白名单内）: {found_banned}"
    )



def _init_binary_scan_repo(root: Path) -> None:
    subprocess.run(["git", "init", "--quiet"], cwd=root, check=True, capture_output=True)


def test_binary_allowlist_is_path_exact(tmp_path: Path) -> None:
    """仅根 start.bat 与三个指定字体路径可通过，不按扩展名放行。"""
    _init_binary_scan_repo(tmp_path)
    for relative in ALLOWED_BINARY_ALLOWLIST:
        candidate = tmp_path.joinpath(*Path(relative).parts)
        candidate.parent.mkdir(parents=True, exist_ok=True)
        candidate.write_bytes(b"approved path placeholder")

    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "start.bat").write_bytes(b"not approved here")
    (tmp_path / "web" / "vendor" / "fonts" / "Other.otf").write_bytes(b"not approved font")

    assert _banned_binary_paths(tmp_path) == [
        "tools/start.bat",
        "web/vendor/fonts/Other.otf",
    ]


def test_ignored_local_runtime_video_passes_until_force_tracked(tmp_path: Path) -> None:
    """忽略的开发视频不扫描；一旦加入 Git 索引就必须被二进制门禁拒绝。"""
    _init_binary_scan_repo(tmp_path)
    (tmp_path / ".gitignore").write_text(".local/*\n!.local/README.md\n", encoding="utf-8")
    runtime_dir = tmp_path / ".local" / "runtime-dev"
    runtime_dir.mkdir(parents=True)
    video = runtime_dir / "sample.mp4"
    video.write_bytes(b"runtime video placeholder")

    assert _banned_binary_paths(tmp_path) == []
    subprocess.run(
        ["git", "add", "--force", "--", ".local/runtime-dev/sample.mp4"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    assert _banned_binary_paths(tmp_path) == [".local/runtime-dev/sample.mp4"]


def test_banned_extensions_cover_required_categories():
    """防止禁用扩展名清单被静默删减（回归护栏）。

    AGENTS.md 1.2 条把压缩包与可执行文件同列为禁提交项；此处用最小必需集合
    反查实际清单，任何被删掉的后缀都会立刻让门禁变红，而不是静默放行。
    """
    missing = sorted(REQUIRED_BANNED_EXTENSIONS - BANNED_EXTENSIONS)
    assert not missing, f"禁用扩展名清单缺少必需项: {missing}"


def test_ci_workflow_banned_extensions_match_single_source(repo_root: Path):
    """CI 侧清单必须与 `cleanroom_extensions` 唯一来源逐项一致。

    历史缺陷：CI heredoc 与 Python 用例各自维护一份清单，已实际漂移，
    导致「本地绿 / CI 空窗」。此用例把 CI 清单也钉在唯一来源上，
    任何一侧单独改动都会立刻变红。
    """
    workflow = (repo_root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    block = re.search(r"banned_extensions = \{(.*?)\}", workflow, re.S)
    assert block, "CI 工作流中未找到 banned_extensions 清单"
    ci_set = set(re.findall(r'"(\.[a-z0-9]+)"', block.group(1)))
    assert ci_set == BANNED_EXTENSIONS, (
        "CI 清单与唯一来源不一致："
        f"仅 CI 有 {sorted(ci_set - BANNED_EXTENSIONS)}；"
        f"仅来源有 {sorted(BANNED_EXTENSIONS - ci_set)}"
    )

    allowlist_block = re.search(r"allowlist = \{(.*?)\}", workflow, re.S)
    assert allowlist_block, "CI 工作流中未找到二进制白名单"
    ci_allowlist = set(re.findall(r'"([^"\n]+)"', allowlist_block.group(1)))
    assert ci_allowlist == ALLOWED_BINARY_ALLOWLIST, (
        "CI 二进制白名单与唯一来源不一致："
        f"仅 CI 有 {sorted(ci_allowlist - ALLOWED_BINARY_ALLOWLIST)}；"
        f"仅来源有 {sorted(ALLOWED_BINARY_ALLOWLIST - ci_allowlist)}"
    )


def test_phase6_deep_hygiene_uses_single_source(repo_root: Path):
    """Phase 6 深度审计套件不得再自带一份禁用扩展名清单。"""
    other = (repo_root / "tests" / "hygiene" / "test_phase6_deep_hygiene.py").read_text(
        encoding="utf-8"
    )
    assert "cleanroom_extensions" in other, "Phase 6 套件必须导入唯一来源，禁止自带字面量清单"
    literal = re.search(r"banned_extensions\s*=\s*\{", other)
    assert not literal, "Phase 6 套件仍存在自带 banned_extensions 字面量"


def test_src_has_no_legacy_code_artifacts(repo_root: Path):
    """确保 src/ 下没有引入旧仓私有实现命名或旧仓库硬编码路径。"""
    src_dir = repo_root / "gw"
    assert src_dir.exists(), "src 目录必须存在"

    for py_file in src_dir.rglob("*.py"):
        text = py_file.read_text(encoding="utf-8")
        # 严禁在实现代码中硬编码旧仓路径
        assert "Gods-Workbench-release" not in text, f"{py_file.name} 中不得出现旧仓路径"
        assert "import tools.photoshop" not in text
        assert "import asset_registry.canvas_engine" not in text


def test_plugin_protocol_exclusion(repo_root: Path):
    """验证插件协议当前保持排除，未被 src 代码隐式实现。"""
    src_dir = repo_root / "gw"
    for py_file in src_dir.rglob("*.py"):
        text = py_file.read_text(encoding="utf-8")
        assert "PluginProtocol" not in text, f"{py_file.name} 不得实现待审的插件协议"
        assert "plugin_connector" not in text


def test_static_layer_has_no_legacy_integration_markers(repo_root: Path):
    """确保重写的静态层没有旧路径、经典页面或不健康集成。

    口径（用户 2026-09-20 裁决）：
      * 保留范围：V2 前端整体保留（static/v2/**）；画布/工具仅保留“入口首页”内容。
        V2 页面互相引用的 storyboard.html / production.html / agents.html /
        collab.html / settings.html、CDN 图标库 lucide、unsplash.com 占位图源、
        window.V2Projects 命名空间，以及仍在 V2 链路内合法使用的 asset-manager.html
        等保留内容不作为禁用标记。
      * 禁用范围：
        1. 已删除的 V1 经典页面入口：/static/home.html、/static/index.html、
           /static/gpt-chat.html、/static/project-board.html、/static/settings.html；
        2. 已删除的经典集成端点关键字：chrome-local；
        3. 已删除的 comfyui / runninghub 文件与路径不得重现，也不得被保留页面引用。

    依据：AGENTS.md（洁净室铁律与 Lucide CDN 合规）、
    docs/migration/CLASSIC-REMOVAL-PLAN-2026-09-18.md、
    docs/governance/TASK-NOTES-2026-09-18.md 第 4 节（T13）。
    """
    static_dir = repo_root / "web"
    deleted_paths = (
        static_dir / "comfyui-settings.html",
        static_dir / "css" / "comfyui-settings.css",
        static_dir / "js" / "comfyui-settings.js",
        static_dir / "js" / "i18n" / "comfyui-settings.js",
        static_dir / "runninghub",
    )
    reappeared = [
        path.relative_to(repo_root).as_posix()
        for path in deleted_paths
        if path.exists()
    ]
    assert not reappeared, f"已删除的 comfyui/runninghub 文件或目录重现: {reappeared}"

    forbidden_markers = (
        "/static/home.html",
        "/static/index.html",
        "/static/gpt-chat.html",
        "/static/project-board.html",
        "/static/settings.html",
        "chrome-local",
    )
    violations = []
    for path in static_dir.rglob("*"):
        if path.is_file() and path.suffix.lower() in {".html", ".css", ".js"}:
            content = path.read_text(encoding="utf-8").lower()
            for marker in forbidden_markers:
                if marker in content:
                    violations.append(f"{path.relative_to(repo_root)}: {marker}")
    assert not violations, f"静态层发现旧集成残留: {violations}"

    deleted_reference_markers = (
        "/static/runninghub/",
        "comfyui-settings.html",
        "comfyui-settings.js",
        "comfyui-settings.css",
    )
    reference_violations = []
    for path in static_dir.rglob("*"):
        if path.is_file() and path.suffix.lower() in {".html", ".css", ".js"}:
            content = path.read_text(encoding="utf-8").lower()
            for marker in deleted_reference_markers:
                if marker in content:
                    reference_violations.append(f"{path.relative_to(repo_root)}: {marker}")
    assert not reference_violations, f"保留页面引用已删除路径: {reference_violations}"

    # 工作流工作台是本轮明确准入的新领域页面；其 provider 标识属于真实工作流
    # 契约，不是被删除的经典集成入口。仅对精确的工作流页面/控制器放行，其他静态层
    # 仍保持旧集成标记禁入。
    workflow_scope_paths = {
        Path("web/pages/workflow-workbench.html"),
        Path("web/js/controllers/workflow-controller.js"),
    }
    scope_marker_violations = []
    for path in static_dir.rglob("*"):
        relative = path.relative_to(repo_root)
        if relative in workflow_scope_paths:
            continue
        if path.is_file() and path.suffix.lower() in {".html", ".css", ".js"}:
            content = path.read_text(encoding="utf-8").lower()
            for marker in ("comfyui", "runninghub", "running-hub"):
                if marker in content:
                    scope_marker_violations.append(f"{relative}: {marker}")
    assert not scope_marker_violations, (
        "静态层不得保留 comfyui/runninghub 业务标识或调用: "
        f"{scope_marker_violations}"
    )


# H03：来源/授权边界采用不含正文的机器可读登记。
# 登记只保存当前新仓的分类、准入依据和规范化哈希；不保存旧仓源码、提示词正文、
# 凭据或完整历史授权账本。历史材料继续留在洁净源仓，不因测试需要复制进新仓。
ADMISSION_METADATA_PATH = "tests/hygiene/fixtures/cleanroom_admission_metadata.json"
ADMISSION_METADATA_SHA256 = "49e66bb46c73a8484c2675fdd0ecd0b3c6aa83c82bd1341644da4c4021e83caf"
QUARANTINED_PROMPT_ORIGINAL_PATH = "web/system-prompts/infinite-canvas-prompt-templates.md"
QUARANTINED_PROMPT_URL = "/static/system-prompts/infinite-canvas-prompt-templates.md"


def _load_admission_metadata(repo_root: Path) -> dict:
    """读取当前新仓的最小准入元数据；缺失或损坏必须使测试失败。"""
    metadata_path = repo_root / ADMISSION_METADATA_PATH
    assert metadata_path.is_file(), f"缺少 H03 准入元数据: {ADMISSION_METADATA_PATH}"
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        pytest.fail(f"H03 准入元数据不可读取或不是合法 JSON: {exc}")
    assert metadata.get("schema") == "gw.cleanroom-admission/v1"
    assert metadata.get("release_authorized") is True
    assert _canonical_sha256(metadata_path) == ADMISSION_METADATA_SHA256
    return metadata


def test_quarantined_prompt_is_absent_from_static_tree(repo_root: Path):
    """隔离提示词不进入静态树；新仓不保存正文副本。"""
    static_dir = repo_root / "web"
    original = repo_root / QUARANTINED_PROMPT_ORIGINAL_PATH
    assert not original.exists(), (
        f"隔离文件仍在静态挂载目录内，会被 /static 作为运行制品提供: {QUARANTINED_PROMPT_ORIGINAL_PATH}"
    )

    leaked = [
        path.relative_to(repo_root).as_posix()
        for path in static_dir.rglob("*")
        if path.is_file() and path.name == "infinite-canvas-prompt-templates.md"
    ]
    assert not leaked, f"隔离提示词文件名出现在静态挂载目录内: {leaked}"

    metadata = _load_admission_metadata(repo_root)
    records = [
        item for item in metadata.get("excluded_artifacts", [])
        if item.get("source_path") == QUARANTINED_PROMPT_ORIGINAL_PATH
    ]
    assert len(records) == 1, "隔离提示词必须有且仅有一条不含正文的排除登记"
    assert records[0].get("status") == "excluded_and_absent"
    assert records[0].get("body_present") is False


def test_quarantined_prompt_url_is_rejected_by_real_http(client):
    """隔离 URL 的 GET/HEAD 经真实应用路由拒绝；不能仅以磁盘缺失代替验收。"""
    urls = (
        QUARANTINED_PROMPT_URL,
        QUARANTINED_PROMPT_URL + "?download=1",
        QUARANTINED_PROMPT_URL.upper().replace("/STATIC/", "/static/"),
    )
    for url in urls:
        for method in ("GET", "HEAD"):
            response = client.request(method, url, follow_redirects=False)
            assert not response.is_success, f"隔离 URL 被提供: {method} {url}"
            assert not response.is_redirect, f"隔离 URL 不得通过跳转逃逸: {method} {url}"
            if method == "GET":
                assert "无限画布" not in response.text and "v2.0" not in response.text


def test_quarantine_registry_records_metadata_without_prompt_body(repo_root: Path):
    """隔离登记只含路径、状态和裁决，不得内嵌提示词正文或正文副本。"""
    metadata = _load_admission_metadata(repo_root)
    records = [
        item for item in metadata.get("excluded_artifacts", [])
        if item.get("source_path") == QUARANTINED_PROMPT_ORIGINAL_PATH
    ]
    assert len(records) == 1
    record = records[0]
    assert record.get("delivery_url") == QUARANTINED_PROMPT_URL
    assert record.get("body_present") is False
    assert "body" not in record and "content" not in record and "prompt" not in record
    assert QUARANTINED_PROMPT_ORIGINAL_PATH not in _metadata_admitted_paths(metadata)


def test_quarantined_prompt_excluded_from_delivery_export_manifest(repo_root: Path):
    """机器登记和实际文件集合都不得把隔离提示词列为准入或交付内容。"""
    metadata = _load_admission_metadata(repo_root)
    admitted_paths = {entry.get("path") for entry in metadata.get("admitted_files", [])}
    assert QUARANTINED_PROMPT_ORIGINAL_PATH not in admitted_paths
    assert all("infinite-canvas-prompt-templates.md" not in path for path in admitted_paths)
    assert not (repo_root / QUARANTINED_PROMPT_ORIGINAL_PATH).exists()


def _metadata_admitted_paths(metadata: dict) -> set[str]:
    entries = metadata.get("admitted_files")
    assert isinstance(entries, list) and entries, "H03 准入登记不得为空"
    paths = [entry.get("path") for entry in entries]
    assert all(isinstance(path, str) and path for path in paths)
    assert len(paths) == len(set(paths)), "H03 准入文件路径不得重复"
    return set(paths)


def test_accepted_non_canvas_slices_match_migration_manifest(repo_root: Path):
    """冻结输入根目录必须逐项登记，不能依赖旧仓迁移清单。"""
    metadata = _load_admission_metadata(repo_root)
    accepted_roots = {
        item.get("path")
        for item in metadata.get("accepted_roots", [])
        if isinstance(item, dict)
    }
    assert accepted_roots == {"docs/behavior", "docs/contracts", "docs/fixtures"}
    admitted_paths = _metadata_admitted_paths(metadata)
    for root in accepted_roots:
        actual = {
            path.relative_to(repo_root).as_posix()
            for path in (repo_root / root).rglob("*")
            if path.is_file()
        }
        assert actual <= admitted_paths, f"冻结输入未登记: {sorted(actual - admitted_paths)}"


def test_classification_target_hashes_match_manifest_and_files(repo_root: Path):
    """准入分类必须来自当前新仓边界，且每条登记都绑定当前文件哈希。"""
    metadata = _load_admission_metadata(repo_root)
    admitted_paths = _metadata_admitted_paths(metadata)
    root_classes = {
        item["path"]: item["classification"]
        for item in metadata.get("accepted_roots", []) + metadata.get("curated_files", [])
        if isinstance(item, dict) and "path" in item and "classification" in item
    }
    allowed_classes = {
        "frozen_behavior_contract",
        "frozen_interface_contract",
        "golden_fixture",
        "curated_design_system",
        "curated_project_governance",
        "curated_provider_boundary",
    }
    assert root_classes
    for entry in metadata["admitted_files"]:
        relative = entry["path"]
        path = repo_root / relative
        assert ".." not in Path(relative).parts and not Path(relative).is_absolute()
        assert relative in admitted_paths and path.is_file(), f"准入文件不存在: {relative}"
        matching = [
            root for root in root_classes
            if relative == root or relative.startswith(root.rstrip("/") + "/")
        ]
        assert matching, f"准入文件没有边界分类: {relative}"
        assert entry.get("classification") in allowed_classes
        assert entry.get("authorization_basis")
        assert re.fullmatch(r"[0-9a-f]{64}", entry.get("sha256", ""))
        assert _canonical_sha256(path) == entry["sha256"]


def test_admission_metadata_hashes_match_current_files(repo_root: Path):
    """当前准入文件哈希必须与机器登记一致；不复用旧 Phase-2 账本。"""
    metadata = _load_admission_metadata(repo_root)
    entries = metadata.get("admitted_files", [])
    assert len(entries) == 91, "当前准入登记数量必须与 H01 实物集合一致"
    for entry in entries:
        path = repo_root / entry["path"]
        assert path.is_file(), f"准入文件缺失: {entry['path']}"
        assert _canonical_sha256(path) == entry["sha256"], f"准入哈希不匹配: {entry['path']}"


_DOCS_SCAN_IGNORED_DIRS = frozenset({
    ".git", "__pycache__", ".pytest_cache", "node_modules", ".venv", "venv", "env",
    "build", "dist", ".mypy_cache", ".ruff_cache",
})

def _docs_file_paths(repo_root: Path, git_visible_paths: list[str] | None = None) -> set[str]:
    """合并 Git 可见与实际 docs 文件；缓存段只排除 Git 不可见的本地产物。"""
    docs_root = repo_root / "docs"
    candidates = set()
    if docs_root.is_dir():
        candidates.update(
            path.relative_to(repo_root).as_posix()
            for path in docs_root.rglob("*")
            if path.is_file()
        )
    if git_visible_paths is None:
        git_visible_paths = _git_visible_paths(repo_root)
    git_visible_docs = {
        raw_path.replace("\\", "/")
        for raw_path in git_visible_paths
        if raw_path.replace("\\", "/").startswith("docs/")
    }
    candidates.update(git_visible_docs)

    result = set()
    for relative in candidates:
        parts = PurePosixPath(relative).parts
        if not parts or parts[0] != "docs" or ".." in parts or PurePosixPath(relative).is_absolute():
            continue
        normalized = PurePosixPath(*parts).as_posix()
        is_git_visible = normalized in git_visible_docs
        if not is_git_visible and any(part in _DOCS_SCAN_IGNORED_DIRS for part in parts[1:-1]):
            continue
        if repo_root.joinpath(*parts).is_file():
            result.add(normalized)
    return result


def _docs_admission_delta(
    repo_root: Path,
    metadata: dict,
    git_visible_paths: list[str] | None = None,
) -> tuple[list[str], list[str]]:
    """返回未登记文件与准入登记缺失文件，比较范围为逐文件而非目录名。"""
    actual_paths = _docs_file_paths(repo_root, git_visible_paths)
    admitted_paths = {
        entry["path"].replace("\\", "/")
        for entry in metadata.get("admitted_files", [])
        if isinstance(entry, dict)
        and isinstance(entry.get("path"), str)
        and entry["path"].replace("\\", "/").startswith("docs/")
    }
    unadmitted = actual_paths - admitted_paths
    missing = admitted_paths - actual_paths
    return sorted(unadmitted), sorted(missing)


def test_accepted_doc_slices_match_migration_manifest(repo_root: Path):
    """H01 文档准入必须与 H03 文件清单逐路径一致，不按目录名放宽准入。"""
    metadata = _load_admission_metadata(repo_root)
    curated = metadata.get("curated_files", [])
    curated_paths = {entry.get("path") for entry in curated}
    assert curated_paths == {
        "docs/design/DESIGN-SYSTEM-GUIDE.md",
        "docs/governance/PROJECT-GOVERNANCE.md",
        "docs/contracts/PROVIDER-PROTOCOL-BOUNDARIES.md",
    }
    admitted_paths = _metadata_admitted_paths(metadata)
    assert curated_paths <= admitted_paths

    unadmitted, missing = _docs_admission_delta(repo_root, metadata)
    assert not missing, f"H03 docs 准入登记文件缺失于 Git 可见/实际文件集合: {missing}"
    assert not unadmitted, f"发现未按 H03 admitted_files 精确登记的 docs 文件: {unadmitted}"


def test_docs_admission_rejects_unregistered_design_and_governance_files(tmp_path: Path):
    """设计/治理目录不构成目录级准入；新增文件必须逐路径登记。"""
    admitted = [
        "docs/design/approved.md",
        "docs/governance/approved.md",
    ]
    unregistered = [
        "docs/design/unapproved.md",
        "docs/governance/unapproved.md",
        "docs/governance/build/unapproved.md",
        "docs/design/env/unapproved.md",
        "docs/README.md",
    ]
    local_cache = "docs/design/.pytest_cache/local-only.md"
    for relative in admitted + unregistered + [local_cache]:
        path = tmp_path / Path(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("test", encoding="utf-8")

    metadata = {"admitted_files": [{"path": relative} for relative in admitted]}
    visible = admitted + unregistered + ["docs/design/deleted.md"]
    unadmitted, missing = _docs_admission_delta(tmp_path, metadata, visible)

    assert unadmitted == sorted(unregistered)
    assert missing == []


# ---------------------------------------------------------------------------
# 同形字（homoglyph）防污染守卫
# ---------------------------------------------------------------------------

# 可疑码点区间：西里尔（与拉丁同形）、零宽字符、双向控制/隐形字符、软连字符。
# 说明：中文全角标点（U+FF00 段）属正常书写，不在本守卫范围。
_SUSPICIOUS_RANGES = (
    (0x0400, 0x04FF),   # 西里尔字母（U+0441 / U+0435 / U+043E / U+0430 / U+0440 / U+0445 等与拉丁同形）
    (0x200B, 0x200F),   # 零宽空格/零宽连接符/双向控制
    (0x2060, 0x2064),   # 词连接符、不可见分隔符
    (0xFE00, 0xFE0F),   # 变体选择符
    (0x00AD, 0x00AD),   # 软连字符
)

# 扫描范围：仓库自有文本（代码/文档/测试/配置）。
# 排除项：
#   * web/vendor/            —— 上游不可变制品；
#   * web/prompt-registry/sources/ —— 第三方内容数据（含合法双向标记）。
_HOMOGLYPH_TEXT_SUFFIXES = {
    ".py", ".js", ".html", ".css", ".json",
    ".yml", ".yaml", ".md", ".txt", ".toml", ".cfg", ".ini", ".sh", ".ps1",
}
_HOMOGLYPH_EXCLUDED_PREFIXES = (
    "web/vendor/",
    "web/prompt-registry/sources/",
)


def _is_suspicious_codepoint(codepoint: int) -> bool:
    """判断码点是否属于同形字/隐形字符区间。"""
    return any(low <= codepoint <= high for low, high in _SUSPICIOUS_RANGES)


def _is_token_char(char: str) -> bool:
    """标识符 token 的字符：ASCII 字母数字、下划线/美元符、或可疑字符。"""
    if char in "_$":
        return True
    if char.isascii() and char.isalnum():
        return True
    return _is_suspicious_codepoint(ord(char))


def _scan_homoglyph_tokens(text: str) -> list:
    """返回文中「ASCII 标识符内混入可疑字符」的 (token, 起始偏移) 列表。

    返回偏移而非仅 token，是为了让调用方计算**该次命中自身**所在的行号，
    避免同一 token 多次出现时因 find() 取首次位置而报错行。
    """
    found = []
    index = 0
    length = len(text)
    while index < length:
        if not _is_token_char(text[index]):
            index += 1
            continue
        start = index
        end = index
        while end < length and _is_token_char(text[end]):
            end += 1
        token = text[start:end]
        if any(_is_suspicious_codepoint(ord(c)) for c in token) and any(
            c.isascii() and c.isalpha() for c in token
        ):
            found.append((token, start))
        index = end
    return found


def test_no_homoglyph_confusables(repo_root: Path):
    """确保仓库自有文本中不存在混入 ASCII 标识符的同形字/隐形字符。

    背景：多次审查发现文档或代码内出现「西里尔字母 + 零宽空格」伪装的标识符
    （如把 ``credential`` 写成形近串）。这类字符肉眼不可辨、可绕过字符串比对，
    属于典型的内容污染。本用例把它变成持续门禁，防止回归。
    """
    violations = []
    for path in repo_root.rglob("*"):
        if not path.is_file():
            continue
        if any(
            part in {
                ".git", "__pycache__", ".pytest_cache", "node_modules",
                ".venv", "venv", "env", "build", "dist", ".mypy_cache", ".ruff_cache",
            }
            for part in path.parts
        ):
            continue
        relative = path.relative_to(repo_root).as_posix()
        if relative.startswith(_HOMOGLYPH_EXCLUDED_PREFIXES):
            continue
        suffix = path.suffix.lower()
        if suffix not in _HOMOGLYPH_TEXT_SUFFIXES and path.name not in {".gitattributes", ".gitignore"}:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for token, offset in _scan_homoglyph_tokens(text):
            line = text.count("\n", 0, offset) + 1
            codepoints = " ".join(f"U+{ord(c):04X}" for c in token if _is_suspicious_codepoint(ord(c)))
            violations.append(f"{relative}:{line} token={token!r} 可疑码点={codepoints}")
    assert not violations, "发现同形字/隐形字符污染:\n" + "\n".join(violations)


def test_homoglyph_guard_detects_injected_pollution():
    """反向自检：证明上面的守卫不是恒真。

    用 chr() 在运行时构造一个「西里尔 U+0441 + 零宽空格 U+200B」伪装的 ``credential``，
    守卫必须命中；否则守卫失效（例如区间写错、匹配逻辑恒假）。
    """
    polluted = chr(0x0441) + "redential"          # U+0441 CYRILLIC SMALL LETTER ES
    polluted_zwsp = "cred" + chr(0x200B) + "ential"  # 零宽空格
    assert _scan_homoglyph_tokens(polluted), "守卫未能识别西里尔同形字"
    assert _scan_homoglyph_tokens(polluted_zwsp), "守卫未能识别零宽空格"
    assert all(isinstance(item, tuple) and len(item) == 2 for item in _scan_homoglyph_tokens(polluted))
    # 反向对照：纯 ASCII 与正常中文不得误报
    assert not _scan_homoglyph_tokens("credential"), "纯 ASCII 被误判"
    assert not _scan_homoglyph_tokens("会话失效，请重新登录。"), "正常中文被误判"
