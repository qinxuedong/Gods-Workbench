"""黄金夹具与契约模型一致性自动化测试。

读取 docs/fixtures/GOLDEN-FIXTURE-MANIFEST.json，对清单登记的全部黄金夹具执行严格的反序列化和契约断言（数量以清单为准）。
"""

import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from gw.god_canvas.godmap import parse_godmap_content, export_to_godmap
from gw.god_canvas.models import CanvasTopology
from gw.god_canvas.tasks import SmartCanvasTaskResponse
from gw.core.errors import (
    ErrorEnvelope,
    UnauthorizedException,
    ForbiddenException,
    VersionConflictException,
    CanvasVersionConflictException,
)
from gw.asset_library.models import (
    AssetLibraryCreateRequest,
    AssetLibraryResponse,
    CategoryCreateRequest,
    CategoryType,
)
from gw.prompt_library.models import (
    PromptLibraryCreateRequest,
    PromptLibrarySnapshot,
)
from gw.settings.models import (
    ProviderSnapshot,
    StorageSettingsSnapshot,
)
from gw.core.errors import CleanroomException
from gw.canvas_closure.models import (
    CanvasAssetIndexResponse,
    CanvasTrashResponse,
    SharedFolderListResponse,
    VideoTaskListResponse,
)
from gw.projects_hub.models import (
    ProjectCreateRequest,
    ProjectListResponse,
    ProjectType,
)


def test_manifest_integrity(fixtures_dir: Path):
    """验证黄金夹具清单完整性与文件存在性。"""
    manifest_path = fixtures_dir / "GOLDEN-FIXTURE-MANIFEST.json"
    assert manifest_path.exists(), "GOLDEN-FIXTURE-MANIFEST.json 必须存在"
    with open(manifest_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert data["meta"]["distribution"] == "NOT AUTHORIZED FOR PUBLIC DISTRIBUTION"
    # 2026-09-22 Phase 10A：素材库阶段新增 6 个黄金夹具（9 -> 15）。
    # 2026-09-22 Phase 10B：观测阶段新增 2 个黄金夹具（15 -> 17）。
    # 2026-09-22 Phase 10C：提示词库阶段新增 5 个黄金夹具（17 -> 22）。
    # 2026-09-22 Phase 10D：设置页阶段新增 5 个黄金夹具（22 -> 27）。
    # 2026-09-22 Phase 10E：画布闭环阶段新增 6 个黄金夹具（27 -> 33）。
    # 2026-09-25 Phase 11 B1：认证边界新增 1 个黄金夹具（33 -> 34）。
    assert len(data["fixtures"]) == 34
    for item in data["fixtures"]:
        file_path = fixtures_dir / item["file"]
        assert file_path.exists(), f"夹具文件不存在: {item['file']}"


def test_fixture_projects_hub_list_active(fixtures_dir: Path):
    """验证 projects-hub-list-active.json 契约匹配。"""
    file_path = fixtures_dir / "projects-hub-list-active.json"
    with open(file_path, "r", encoding="utf-8") as f:
        content = json.load(f)
    res = ProjectListResponse.model_validate(content)
    assert len(res.projects) == 1
    p = res.projects[0]
    assert p.project_id == "prj-0001"
    assert p.project_type == ProjectType.FILM
    assert p.version == 3
    assert p.progress == 68


def test_fixture_projects_hub_create_request(fixtures_dir: Path):
    """验证 projects-hub-create-request.json 契约匹配。"""
    file_path = fixtures_dir / "projects-hub-create-request.json"
    with open(file_path, "r", encoding="utf-8") as f:
        content = json.load(f)
    req = ProjectCreateRequest.model_validate(content)
    assert req.name == "示例项目 B"
    assert req.project_type == ProjectType.SERIES
    assert req.start_at == 1782000000000


def test_fixture_projects_hub_update_conflict_409(fixtures_dir: Path):
    """验证 projects-hub-update-conflict-409.json 错误语义匹配。"""
    file_path = fixtures_dir / "projects-hub-update-conflict-409.json"
    with open(file_path, "r", encoding="utf-8") as f:
        content = json.load(f)
    envelope = ErrorEnvelope.model_validate(content)
    assert envelope.detail.code == "VERSION_CONFLICT"
    assert envelope.detail.expected_version == 5
    assert envelope.detail.current_version == 6

    # 验证与自定义异常生成的一致性
    exc = VersionConflictException(expected_version=5, current_version=6)
    assert exc.to_envelope().detail.code == envelope.detail.code
    assert exc.to_envelope().detail.expected_version == envelope.detail.expected_version


def test_fixture_canvas_workflow_minimal_json(fixtures_dir: Path):
    """验证 canvas-workflow-minimal.json 契约匹配。"""
    file_path = fixtures_dir / "canvas-workflow-minimal.json"
    with open(file_path, "r", encoding="utf-8") as f:
        content = json.load(f)
    top = CanvasTopology.model_validate(content)
    assert top.canvas_id == "cv-0001"
    assert top.version == 1
    assert len(top.nodes) == 2
    assert len(top.connections) == 1
    assert top.nodes[0].entity_id == "nd-0001"
    assert top.connections[0].from_node == "nd-0001"
    assert top.connections[0].to_node == "nd-0002"


def test_fixture_canvas_workflow_minimal_godmap(fixtures_dir: Path):
    """验证 canvas-workflow-minimal.godmap 编解码契约。"""
    file_path = fixtures_dir / "canvas-workflow-minimal.godmap"
    with open(file_path, "r", encoding="utf-8") as f:
        raw_str = f.read()
    doc = parse_godmap_content(raw_str)
    assert doc.format == "godmap"
    assert doc.version == "1.0"
    assert doc.payload.canvas_id == "cv-0001"
    assert len(doc.payload.nodes) == 2
    assert len(doc.payload.connections) == 1

    # 验证拓扑可再导出
    re_exported = export_to_godmap(
        CanvasTopology(
            canvas_id=doc.payload.canvas_id,
            version=1,
            nodes=doc.payload.nodes,
            connections=doc.payload.connections,
        )
    )
    doc_re = parse_godmap_content(re_exported)
    assert doc_re.payload.canvas_id == "cv-0001"


def test_fixture_canvas_save_conflict_409(fixtures_dir: Path):
    """验证 canvas-save-conflict-409.json 错误语义匹配。"""
    file_path = fixtures_dir / "canvas-save-conflict-409.json"
    with open(file_path, "r", encoding="utf-8") as f:
        content = json.load(f)
    envelope = ErrorEnvelope.model_validate(content)
    assert envelope.detail.code == "CANVAS_VERSION_CONFLICT"
    assert envelope.detail.canvas_id == "cv-0001"
    assert envelope.detail.expected_version == 11
    assert envelope.detail.current_version == 12

    exc = CanvasVersionConflictException(expected_version=11, current_version=12, canvas_id="cv-0001")
    assert exc.to_envelope().detail.code == envelope.detail.code


def test_fixture_canvas_task_accepted_202(fixtures_dir: Path):
    """验证 canvas-task-accepted-202.json 异步契约匹配。"""
    file_path = fixtures_dir / "canvas-task-accepted-202.json"
    with open(file_path, "r", encoding="utf-8") as f:
        content = json.load(f)
    res = SmartCanvasTaskResponse.model_validate(content)
    assert res.job_id == "job-0001"
    assert res.state == "accepted"
    assert res.poll_hint == "/api/jobs/job-0001"


def test_fixture_canvas_auth_401(fixtures_dir: Path):
    """验证 canvas-auth-401.json 错误语义匹配。"""
    file_path = fixtures_dir / "canvas-auth-401.json"
    with open(file_path, "r", encoding="utf-8") as f:
        content = json.load(f)
    envelope = ErrorEnvelope.model_validate(content)
    assert envelope.detail.code == "UNAUTHORIZED"

    exc = UnauthorizedException()
    assert exc.to_envelope().detail.code == envelope.detail.code


def test_fixture_canvas_forbidden_403(fixtures_dir: Path):
    """验证 canvas-forbidden-403.json 错误语义匹配。"""
    file_path = fixtures_dir / "canvas-forbidden-403.json"
    with open(file_path, "r", encoding="utf-8") as f:
        content = json.load(f)
    envelope = ErrorEnvelope.model_validate(content)
    assert envelope.detail.code == "FORBIDDEN"

    exc = ForbiddenException()
    assert exc.to_envelope().detail.code == envelope.detail.code


def test_api_contract_routes(client: TestClient):
    """测试 API 路由的契约状态码与响应体包装。"""
    # 测试未认证 401
    resp_401 = client.get("/api/asset-registry/projects", headers={"authorization": "invalid"})
    assert resp_401.status_code == 401
    assert resp_401.json()["detail"]["code"] == "UNAUTHORIZED"

    # 测试智能画布 202
    resp_202 = client.post(
        "/api/canvases/cv-0001/tasks",
        json={"entry_nodes": ["nd-0001"], "run_mode": "single"},
        headers={"Authorization": "Bearer cleanroom-test"},
    )
    assert resp_202.status_code == 202
    assert resp_202.json()["state"] == "accepted"
    assert "job_id" in resp_202.json()


# ---------------------------------------------------------------------------
# Phase 10A：素材库黄金夹具（docs/contracts/ASSET-LIBRARY-INTERFACE-CATALOG.yaml）
# ---------------------------------------------------------------------------


def test_fixture_asset_library_empty(fixtures_dir: Path):
    """验证空素材库夹具严格匹配契约：空数组 + null，不得伪造素材。"""
    content = json.loads((fixtures_dir / "asset-library-empty.json").read_text(encoding="utf-8"))
    res = AssetLibraryResponse.model_validate(content)
    assert res.library.version == 1
    assert res.library.active_library_id is None
    assert res.library.libraries == []


def test_fixture_asset_library_with_library_category(fixtures_dir: Path):
    """验证非空素材库夹具的库-分类-条目三层稳定 ID 结构。"""
    content = json.loads(
        (fixtures_dir / "asset-library-with-library-category.json").read_text(encoding="utf-8")
    )
    res = AssetLibraryResponse.model_validate(content)
    assert res.library.active_library_id == "library_default"
    library = res.library.libraries[0]
    assert library.library_id == "library_default"
    assert library.version == 2
    category = library.categories[0]
    assert category.category_id == "category_image"
    assert category.library_id == "library_default"
    assert category.type is CategoryType.IMAGE
    assert category.items == []


def test_fixture_asset_library_create_requests(fixtures_dir: Path):
    """验证创建素材库 / 创建分类请求夹具可被契约模型接受。"""
    lib_req = AssetLibraryCreateRequest.model_validate(
        json.loads((fixtures_dir / "asset-library-create-library-request.json").read_text(encoding="utf-8"))
    )
    assert lib_req.name == "角色参考库"
    assert lib_req.expected_version is None

    cat_req = CategoryCreateRequest.model_validate(
        json.loads((fixtures_dir / "asset-library-create-category-request.json").read_text(encoding="utf-8"))
    )
    assert cat_req.library_id == "library_default"
    assert cat_req.type is CategoryType.IMAGE


def test_fixture_asset_library_conflict_409(fixtures_dir: Path):
    """验证素材库 CAS 冲突夹具与异常构造逐字一致。"""
    content = json.loads((fixtures_dir / "asset-library-conflict-409.json").read_text(encoding="utf-8"))
    envelope = ErrorEnvelope.model_validate(content)
    assert envelope.detail.code == "VERSION_CONFLICT"
    assert envelope.detail.expected_version == 1
    assert envelope.detail.current_version == 2

    exc = VersionConflictException(expected_version=1, current_version=2, message="素材库版本冲突，请重新读取后重试")
    assert exc.to_envelope().detail.code == envelope.detail.code


def test_fixture_asset_library_duplicate_409(fixtures_dir: Path):
    """验证素材库重名冲突夹具错误码；该码由 CleanroomException 生成，不出现在封闭 ErrorDetail 白名单之外。"""
    content = json.loads((fixtures_dir / "asset-library-duplicate-409.json").read_text(encoding="utf-8"))
    assert content["detail"]["code"] == "DUPLICATE_LIBRARY_NAME"
    exc = CleanroomException(status_code=409, code="DUPLICATE_LIBRARY_NAME", message="素材库名称已存在：默认资产库")
    assert exc.to_envelope().detail.code == content["detail"]["code"]


# ---------------------------------------------------------------------------
# Phase 10B：观测黄金夹具（docs/contracts/OBSERVABILITY-INTERFACE-CATALOG.yaml）
# ---------------------------------------------------------------------------


def test_fixture_observability_empty_not_integrated(fixtures_dir: Path):
    """验证无数据时的诚实空响应夹具：空数组 + not_integrated，禁止伪造遥测/波形。"""
    content = json.loads(
        (fixtures_dir / "observability-empty-not-integrated.json").read_text(encoding="utf-8")
    )
    assert content["series"]["data_status"] == "not_integrated"
    assert content["series"]["series"] == {}
    assert content["series"]["metrics"] == []
    for key in ("events", "tasks"):
        assert content[key]["items"] == [], f"{key} 必须为空数组"
        assert content[key]["total"] == 0, f"{key} 计数必须为 0"
        assert content[key]["has_more"] is False
    for key in ("sources", "asset_volumes"):
        assert content[key]["data_status"] == "not_integrated", key
        assert content[key]["items"] == [], key
        assert content[key]["data_gaps"], f"{key} 必须说明未接入原因"


def test_fixture_observability_health_truthful(fixtures_dir: Path):
    """验证健康检查如实状态夹具：未接入组件必须为 not_integrated，不得整体恒为 ok。"""
    content = json.loads(
        (fixtures_dir / "observability-health-truthful.json").read_text(encoding="utf-8")
    )
    statuses = {check["name"]: check["status"] for check in content["checks"]}
    assert statuses["projects"] == "ok"
    assert statuses["canvas"] == "ok"
    assert statuses["asset_library"] == "ok"
    for name in ("hardware_telemetry", "metrics_series", "source_registry", "asset_volume_index"):
        assert statuses[name] == "not_integrated", f"{name} 必须为 not_integrated"
    assert content["status"] != "ok", "存在未接入组件时整体状态不得为 ok"
    assert content["data_status"] == "degraded"
    assert content["data_gaps"]


# ---------------------------------------------------------------------------
# Phase 10C：提示词库黄金夹具（docs/contracts/PROMPT-LIBRARY-INTERFACE-CATALOG.yaml）
# ---------------------------------------------------------------------------


def test_fixture_prompt_library_empty(fixtures_dir: Path):
    """验证提示词库空库夹具：libraries 必须为空数组，禁止伪造提示词内容。"""
    content = json.loads(
        (fixtures_dir / "prompt-library-empty.json").read_text(encoding="utf-8")
    )
    snapshot = PromptLibrarySnapshot.model_validate(content["library"])
    assert snapshot.version == 1
    assert snapshot.active_library_id is None
    assert snapshot.libraries == []


def test_fixture_prompt_library_with_empty_category(fixtures_dir: Path):
    """验证非空提示词库夹具的库-分类两层稳定 ID 结构；分类仅为空结构，不含提示词文本。"""
    raw = (fixtures_dir / "prompt-library-with-empty-category.json").read_text(encoding="utf-8")
    content = json.loads(raw)
    snapshot = PromptLibrarySnapshot.model_validate(content["library"])
    assert snapshot.active_library_id == "plib_default"
    library = snapshot.libraries[0]
    assert library.library_id == "plib_default"
    assert library.version == 2
    category = library.categories[0]
    assert category.category_id == "pcat_default"
    assert category.library_id == "plib_default"
    # 零伪造：夹具不得出现任何提示词文本字段或条目集合
    for marker in ("items", "positive", "negative", "scene"):
        assert marker not in raw, f"提示词库夹具不得包含 {marker} 字段"


def test_fixture_prompt_library_create_request(fixtures_dir: Path):
    """验证创建提示词库请求夹具可被契约模型接受。"""
    payload = json.loads(
        (fixtures_dir / "prompt-library-create-request.json").read_text(encoding="utf-8")
    )
    req = PromptLibraryCreateRequest.model_validate(payload)
    assert req.name == "角色提示词库"
    assert req.expected_version is None


def test_fixture_prompt_library_conflict_409(fixtures_dir: Path):
    """验证提示词库 CAS 冲突夹具与异常构造逐字一致。"""
    content = json.loads(
        (fixtures_dir / "prompt-library-conflict-409.json").read_text(encoding="utf-8")
    )
    envelope = ErrorEnvelope.model_validate(content)
    assert envelope.detail.code == "VERSION_CONFLICT"
    assert envelope.detail.expected_version == 1
    assert envelope.detail.current_version == 2

    exc = VersionConflictException(
        expected_version=1,
        current_version=2,
        message="提示词库 plib_default 版本冲突，请重新读取后重试",
    )
    assert exc.to_envelope().detail.code == envelope.detail.code


def test_fixture_prompt_library_not_empty_409(fixtures_dir: Path):
    """验证非空库删除冲突夹具的错误码；该码由 CleanroomException 生成。"""
    content = json.loads(
        (fixtures_dir / "prompt-library-not-empty-409.json").read_text(encoding="utf-8")
    )
    assert content["detail"]["code"] == "LIBRARY_NOT_EMPTY"
    exc = CleanroomException(
        status_code=409,
        code="LIBRARY_NOT_EMPTY",
        message="提示词库 plib_default 仍包含 1 个分类，请先清空后再删除",
    )
    assert exc.to_envelope().detail.code == content["detail"]["code"]


# ---------------------------------------------------------------------------
# Phase 10D：设置页夹具（docs/contracts/SETTINGS-INTERFACE-CATALOG.yaml）
# ---------------------------------------------------------------------------


def test_fixture_settings_providers_empty(fixtures_dir: Path):
    """验证 providers 空列表夹具：不得预置任何厂商条目。"""
    content = json.loads((fixtures_dir / "settings-providers-empty.json").read_text(encoding="utf-8"))
    snapshot = ProviderSnapshot.model_validate(content)
    assert snapshot.providers == []
    assert snapshot.configured is False
    assert snapshot.data_status == "not_configured"


def test_fixture_settings_storage_unconfigured(fixtures_dir: Path):
    """验证存储设置未配置夹具：不得伪造根目录。"""
    content = json.loads((fixtures_dir / "settings-storage-unconfigured.json").read_text(encoding="utf-8"))
    snapshot = StorageSettingsSnapshot.model_validate(content)
    assert snapshot.configured is False
    assert snapshot.dirs == {}
    assert snapshot.local_libraries == []


def test_fixture_settings_storage_conflict_409(fixtures_dir: Path):
    """验证存储设置 CAS 冲突夹具错误码。"""
    content = json.loads((fixtures_dir / "settings-storage-conflict-409.json").read_text(encoding="utf-8"))
    envelope = ErrorEnvelope.model_validate(content)
    assert envelope.detail.code == "VERSION_CONFLICT"
    assert envelope.detail.expected_version == 1
    assert envelope.detail.current_version == 2


def test_fixture_settings_probe_not_integrated(fixtures_dir: Path):
    """验证探测端点未接入夹具错误码。"""
    content = json.loads(
        (fixtures_dir / "settings-provider-probe-not-integrated.json").read_text(encoding="utf-8")
    )
    assert content["detail"]["code"] == "PROVIDER_PROBE_NOT_INTEGRATED"
    exc = CleanroomException(
        status_code=503,
        code="PROVIDER_PROBE_NOT_INTEGRATED",
        message=content["detail"]["message"],
    )
    assert exc.to_envelope().detail.code == content["detail"]["code"]


def test_fixture_settings_structure_conflict_409(fixtures_dir: Path):
    """验证结构 CAS 冲突夹具错误码。"""
    content = json.loads((fixtures_dir / "settings-structure-conflict-409.json").read_text(encoding="utf-8"))
    envelope = ErrorEnvelope.model_validate(content)
    assert envelope.detail.code == "STRUCTURE_VERSION_CONFLICT"
    assert envelope.detail.expected_version == 1
    assert envelope.detail.current_version == 2


# ---------------------------------------------------------------------------
# Phase 10E：画布闭环夹具（docs/contracts/CANVAS-CLOSURE-INTERFACE-CATALOG.yaml）
# ---------------------------------------------------------------------------


def test_fixture_canvas_closure_trash_empty(fixtures_dir: Path):
    """验证回收站空列表夹具：空集合必须为空，禁止伪造演示条目。"""
    content = json.loads(
        (fixtures_dir / "canvas-closure-trash-empty.json").read_text(encoding="utf-8")
    )
    snapshot = CanvasTrashResponse.model_validate(content)
    assert snapshot.canvases == []
    assert snapshot.data_status == "ok"
    assert snapshot.data_gaps == []


def test_fixture_canvas_closure_cas_conflict_409(fixtures_dir: Path):
    """验证画布闭环 CAS 冲突夹具：画布版本冲突错误包。"""
    content = json.loads(
        (fixtures_dir / "canvas-closure-cas-conflict-409.json").read_text(encoding="utf-8")
    )
    envelope = ErrorEnvelope.model_validate(content)
    assert envelope.detail.code == "CANVAS_VERSION_CONFLICT"
    assert envelope.detail.expected_version == 1
    assert envelope.detail.current_version == 2


def test_fixture_canvas_closure_asset_index_empty(fixtures_dir: Path):
    """验证素材索引空快照夹具：三类（smart / classic / reference）计数均为 0，禁止伪造素材条目。"""
    content = json.loads(
        (fixtures_dir / "canvas-closure-asset-index-empty.json").read_text(encoding="utf-8")
    )
    snapshot = CanvasAssetIndexResponse.model_validate(content)
    assert snapshot.canvases == []
    assert snapshot.items == []
    assert snapshot.data_status == "not_integrated"
    assert "canvas_asset_index_source_not_integrated" in snapshot.data_gaps
    assert all(category["count"] == 0 for category in snapshot.categories)


def test_fixture_canvas_closure_shared_folders_empty(fixtures_dir: Path):
    """验证共享文件夹空列表夹具：folders 为空、revision 稳定。"""
    content = json.loads(
        (fixtures_dir / "canvas-closure-shared-folders-empty.json").read_text(encoding="utf-8")
    )
    snapshot = SharedFolderListResponse.model_validate(content)
    assert snapshot.folders == []
    assert snapshot.revision >= 1
    assert snapshot.data_status == "ok"


def test_fixture_canvas_closure_video_tasks_empty(fixtures_dir: Path):
    """验证视频任务空列表夹具：video_tasks 为空，禁止伪造任务与进度。"""
    content = json.loads(
        (fixtures_dir / "canvas-closure-video-tasks-empty.json").read_text(encoding="utf-8")
    )
    snapshot = VideoTaskListResponse.model_validate(content)
    assert snapshot.video_tasks == []
    assert snapshot.data_status == "not_integrated"
    assert "video_renderer_source_not_integrated" in snapshot.data_gaps


def test_fixture_canvas_closure_video_task_not_integrated(fixtures_dir: Path):
    """验证视频任务创建 fail-closed 夹具：503 且不携带任何伪造任务字段。"""
    content = json.loads(
        (fixtures_dir / "canvas-closure-video-task-not-integrated.json").read_text(encoding="utf-8")
    )
    assert content["detail"]["code"] == "VIDEO_RENDERER_NOT_INTEGRATED"
    assert set(content["detail"].keys()) == {"code", "message"}
    for forbidden in ("task_id", "progress", "eta", "url"):
        assert forbidden not in content["detail"]
    exc = CleanroomException(
        status_code=503,
        code="VIDEO_RENDERER_NOT_INTEGRATED",
        message=content["detail"]["message"],
    )
    assert exc.to_envelope().detail.code == content["detail"]["code"]
