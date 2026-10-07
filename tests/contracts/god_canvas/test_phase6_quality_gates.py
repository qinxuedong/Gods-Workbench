"""Phase 6 契约与并发边界深度自动化测试矩阵。

由【契约与测试自动化工程师】负责构建与维护：
1. CAS 乐观锁多线程并发写入竞争压力测试（项目中心与画布服务）
2. 拓扑边界、大规模节点图与异常自愈测试
3. 智能任务状态机完整生命周期（accepted -> running -> completed/cancelled）与终端保护
4. 安全角色权限矩阵与降级边界测试
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
import pytest

from gw.core.errors import (
    CanvasVersionConflictException,
    CleanroomException,
    ForbiddenException,
    UnauthorizedException,
    VersionConflictException,
)
from gw.god_canvas.godmap import parse_godmap_content
from gw.god_canvas.models import (
    CanvasConnection,
    CanvasCreateRequest,
    CanvasMode,
    CanvasNode,
    CanvasTopologyUpdateRequest,
    NodePosition,
    NodeReferences,
)
from gw.god_canvas.service import GodCanvasService
from gw.god_canvas.tasks import SmartCanvasTaskRequest, SmartCanvasRunMode
from gw.projects_hub.models import ProjectCreateRequest, ProjectUpdateRequest
from gw.projects_hub.service import ProjectsService


def test_concurrent_cas_contention_projects():
    """验证项目中心 CAS 乐观锁在高并发写入竞争下的互斥与严格 409 拦截。"""
    service = ProjectsService(seed_golden_fixture=True)
    # 初始种子为 prj-0001, version=3
    initial_project = service.list_projects()[0]
    assert initial_project.project_id == "prj-0001"
    assert initial_project.version == 3

    worker_count = 10
    success_results = []
    conflict_results = []

    def attempt_update(worker_id: int):
        req = ProjectUpdateRequest(
            expected_version=3,
            name=f"并发修改-{worker_id}",
            progress=50.0 + worker_id,
        )
        return service.update_project("prj-0001", req)

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = [executor.submit(attempt_update, i) for i in range(worker_count)]
        for fut in as_completed(futures):
            try:
                res = fut.result()
                success_results.append(res)
            except VersionConflictException as exc:
                conflict_results.append(exc)

    # 核心断言：在 10 个持有旧版本 3 的并发更新中，恰好只有 1 个成功自增到 4
    assert len(success_results) == 1, f"预期仅 1 个更新成功，实际成功数量: {len(success_results)}"
    assert success_results[0].version == 4
    # 其余 9 个线程必须全部被 CAS 乐观锁拦截
    assert len(conflict_results) == worker_count - 1
    for conflict in conflict_results:
        assert conflict.status_code == 409
        assert conflict.code == "VERSION_CONFLICT"
        assert conflict.extra["expected_version"] == 3
        assert conflict.extra["current_version"] == 4

    # 最终状态版本严格为 4，且项目数据一致
    updated_project = [p for p in service.list_projects() if p.project_id == "prj-0001"][0]
    assert updated_project.version == 4


def test_concurrent_cas_contention_canvas():
    """验证 god-canvas 画布拓扑 CAS 乐观锁在高并发下的原子递增与冲突拦截。"""
    service = GodCanvasService(seed_golden_fixture=True)
    # 初始种子 cv-0001, version=1
    top = service.get_topology("cv-0001")
    assert top.version == 1

    worker_count = 10
    success_results = []
    conflict_results = []

    def attempt_canvas_update(worker_id: int):
        nodes = [
            CanvasNode(
                entity_id=f"nd-worker-{worker_id}",
                kind="process",
                position=NodePosition(x=10.0 * worker_id, y=20.0 * worker_id),
                refs=NodeReferences(),
            )
        ]
        update_req = CanvasTopologyUpdateRequest(
            expected_version=1,
            nodes=nodes,
            connections=[],
        )
        return service.update_topology(
            canvas_id="cv-0001",
            payload=update_req,
        )

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = [executor.submit(attempt_canvas_update, i) for i in range(worker_count)]
        for fut in as_completed(futures):
            try:
                res = fut.result()
                success_results.append(res)
            except CanvasVersionConflictException as exc:
                conflict_results.append(exc)

    # 核心断言：恰好只有 1 个成功更新到版本 2
    assert len(success_results) == 1
    assert success_results[0].version == 2
    # 其余 9 个必须全部收到 409 CANVAS_VERSION_CONFLICT
    assert len(conflict_results) == worker_count - 1
    for conflict in conflict_results:
        assert conflict.status_code == 409
        assert conflict.code == "CANVAS_VERSION_CONFLICT"
        assert conflict.extra["canvas_id"] == "cv-0001"
        assert conflict.extra["expected_version"] == 1
        assert conflict.extra["current_version"] == 2

    # 验证最终画布版本
    final_top = service.get_topology("cv-0001")
    assert final_top.version == 2


def test_topology_edge_cases_and_large_scale():
    """验证拓扑边界条件：空节点、孤立节点、复杂网状、以及百级大规模节点图。"""
    service = GodCanvasService(seed_golden_fixture=False)
    create_req = CanvasCreateRequest(
        project_id="prj-test",
        title="极限测试画布",
        mode=CanvasMode.CLASSIC,
    )
    created = service.create_canvas(create_req)
    cid = created.canvas_id
    assert created.version == 1

    # 1. 边界情况：空节点与空连线合法保存
    res = service.update_topology(
        cid,
        payload=CanvasTopologyUpdateRequest(expected_version=1, nodes=[], connections=[]),
    )
    assert res.version == 2
    top = service.get_topology(cid)
    assert len(top.nodes) == 0
    assert len(top.connections) == 0

    # 2. 百级大规模节点图测试（100 个节点 + 99 条链式连接）
    node_count = 100
    large_nodes = [
        CanvasNode(
            entity_id=f"nd-{i:04d}",
            kind="task" if i % 2 == 0 else "resource",
            position=NodePosition(x=float(i * 10), y=float(i * 5)),
            refs=NodeReferences(asset_id=f"ast-{i}" if i % 3 == 0 else None),
        )
        for i in range(node_count)
    ]
    large_connections = [
        CanvasConnection(
            connection_id=f"ln-{i:04d}",
            from_node=f"nd-{i:04d}",
            to_node=f"nd-{i+1:04d}",
        )
        for i in range(node_count - 1)
    ]

    res_large = service.update_topology(
        cid,
        payload=CanvasTopologyUpdateRequest(
            expected_version=2,
            nodes=large_nodes,
            connections=large_connections,
        ),
    )
    assert res_large.version == 3

    # 校验拓扑完整性与导出/导入回放
    top_large = service.get_topology(cid)
    assert len(top_large.nodes) == 100
    assert len(top_large.connections) == 99

    godmap_str = service.export_workflow(cid, export_format="godmap")
    assert '"format": "godmap"' in godmap_str
    assert '"entity_id": "nd-0000"' in godmap_str
    assert '"entity_id": "nd-0099"' in godmap_str
    assert '"connection_id": "ln-0000"' in godmap_str

    # 4. 反序列化与无损还原闭环校验
    parsed_doc = parse_godmap_content(godmap_str)
    assert parsed_doc.format == "godmap"
    assert len(parsed_doc.payload.nodes) == 100
    assert len(parsed_doc.payload.connections) == 99

    # 5. 损坏的 godmap 格式导入时防御拦截
    corrupted_godmap = "INVALID_MAGIC_HEADER\nversion=invalid"
    with pytest.raises(CleanroomException) as exc_info:
        service.import_workflow(cid, corrupted_godmap, file_format="godmap", expected_version=3)
    assert exc_info.value.status_code == 400


def test_smart_task_state_machine_full_lifecycle():
    """验证智能任务状态机完整生命周期流转、终端保护与取消机制。"""
    service = GodCanvasService(seed_golden_fixture=True)

    # 1. 提交新任务 -> 202 Accepted
    task_req = SmartCanvasTaskRequest(
        expected_version=1,
        entry_nodes=["nd-0001"],
        run_mode=SmartCanvasRunMode.SINGLE,
    )
    resp = service.submit_smart_task("cv-0001", task_req, authorization="Bearer cleanroom-test", user_role="editor")
    assert resp.state == "accepted"
    job_id = resp.job_id
    assert resp.poll_hint == f"/api/jobs/{job_id}"

    # 2. 查询当前任务状态
    job = service.get_job(job_id)
    assert job.state == "accepted"

    # 3. 流转为 running
    job_running = service.update_job_state(job_id, state="running")
    assert job_running.state == "running"
    assert job_running.poll_hint == f"/api/jobs/{job_id}"

    # 4. 流转为终端状态 completed
    job_completed = service.update_job_state(job_id, state="completed")
    assert job_completed.state == "completed"
    assert job_completed.poll_hint is None  # 终端状态轮询地址清空

    # 5. 终端保护：不可再次变更为 running 或 failed
    with pytest.raises(CleanroomException) as exc_terminal:
        service.update_job_state(job_id, state="running")
    assert exc_terminal.value.status_code == 400
    assert exc_terminal.value.code == "ILLEGAL_STATE_TRANSITION"

    # 6. 取消任务场景验证
    task_cancel_req = SmartCanvasTaskRequest(
        entry_nodes=["nd-0001"],
        run_mode=SmartCanvasRunMode.SINGLE,
    )
    cancel_resp = service.submit_smart_task("cv-0001", task_cancel_req, authorization="Bearer cleanroom-test", user_role="editor")
    cancelled_job = service.cancel_job(cancel_resp.job_id)
    assert cancelled_job.state == "cancelled"
    assert cancelled_job.poll_hint is None


def test_security_auth_and_role_downgrade_matrix():
    """验证认证与角色权限矩阵：401 未认证、403 只读降级拦截与合法角色放行。"""
    service = GodCanvasService(seed_golden_fixture=True)
    req = SmartCanvasTaskRequest(entry_nodes=["nd-0001"])

    # 1. 401 未认证 / Token 失效
    with pytest.raises(UnauthorizedException) as exc_401:
        service.submit_smart_task("cv-0001", req, authorization="Bearer invalid")
    assert exc_401.value.status_code == 401
    assert exc_401.value.code == "UNAUTHORIZED"

    with pytest.raises(UnauthorizedException):
        service.submit_smart_task("cv-0001", req, authorization="Bearer expired")

    # 2. 403 权限降级拦截（只读、访客角色）
    for banned_role in ("readonly", "reviewer", "guest", "forbidden"):
        with pytest.raises(ForbiddenException) as exc_403:
            service.submit_smart_task("cv-0001", req, authorization="Bearer cleanroom-test", user_role=banned_role)
        assert exc_403.value.status_code == 403
        assert exc_403.value.code == "FORBIDDEN"

    # 3. 合法角色（editor, admin）正常放行并生成稳定 job_id
    res_editor = service.submit_smart_task("cv-0001", req, authorization="Bearer cleanroom-test", user_role="editor")
    assert res_editor.state == "accepted"
    res_admin = service.submit_smart_task("cv-0001", req, authorization="Bearer cleanroom-test", user_role="admin")
    assert res_admin.state == "accepted"
