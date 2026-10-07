# -*- coding: utf-8 -*-
"""Phase 9 前端显式降级与分享页缺参提示守卫（纯 Python，不依赖浏览器）。

覆盖用户 2026-09-21 裁决：
1. `asset-share.html` 无令牌直开时给出明确缺参提示，而不是静默 404；
2. 前端统一「无后端时显式降级」：共享 transport 与 `workspace-common.js`
   在路由不存在时抛出带 `code=NOT_INTEGRATED` 的显式错误，
   但**不得**把含标准错误包的真实业务 404（如 CANVAS_NOT_FOUND）误判为未接入。

本文件为**静态源码守卫**（接线存在性）；**运行时行为**（含 401/403/404/409/501/503 分支）由
`tests/contracts/observability/test_phase9_degradation_runtime.py` 用 Node 真实执行 JS 覆盖。

证据边界：静态守卫与 Node 行为守卫都**不等于**真实浏览器 E2E，
也不代表后端已实现相应端点。
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
STATIC_JS = REPO_ROOT / "web" / "js"
TRANSPORT_JS = STATIC_JS / "core" / "http-transport.js"
WORKSPACE_JS = STATIC_JS / "core" / "workspace-common.js"
SHARE_JS = STATIC_JS / "modules/asset-share.js"
SHARE_HTML = REPO_ROOT / "web" / "asset-share.html"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_asset_share_direct_open_shows_missing_token_hint():
    """无令牌直开必须给出明确缺参提示。"""
    js = _read(SHARE_JS)
    assert "isDirectOpen" in js, "asset-share.js 应识别无令牌直开"
    assert re.search(r"缺少分享令牌", js), "应给出中文缺参提示文案"
    match = re.search(r"async function load\([^\n]*\)\s*\{", js)
    assert match is not None, "未找到带生命周期上下文参数的 load()"
    region = js[match.end() : match.end() + 600]
    token_guard = region.find("if(!token)")
    request_call = region.find("getPublicShare(token")
    assert token_guard != -1, "load() 应保留缺少 token 的拦截"
    assert request_call != -1 and token_guard < request_call, "无 token 分支必须先于元信息请求，不得发起公开分享请求"
    assert "缺少分享令牌" in region, "无 token 分支必须显示中文缺参提示"


def test_asset_share_html_keeps_loading_placeholder():
    """HTML 仍保留可被替换的加载占位，避免空白页。"""
    html = _read(SHARE_HTML)
    assert 'id="shareApp"' in html
    assert "aria-busy" in html


def test_degradation_maps_frozen_auth_permission_and_conflict_semantics():
    """经典脚本必须声明冻结的 401/403/409 语义映射，不得回落为通用请求失败。"""
    js = _read(DEGRADATION_JS)
    assert "UNAUTHORIZED_MESSAGE" in js
    assert "FORBIDDEN_MESSAGE" in js
    assert "CONFLICT_MESSAGE" in js
    assert "if (code === 401) return 'unauthorized';" in js
    assert "if (code === 403) return 'forbidden';" in js
    assert "if (code === 409) return 'conflict';" in js
    assert "isUnauthorized" in js
    assert "isForbidden" in js
    assert "isConflict" in js
    assert "error.readOnly = kind === 'forbidden';" in js
    assert "error.refreshRequired = kind === 'conflict';" in js


def test_shared_transport_defines_not_integrated_semantics():
    """共享 transport 必须定义未接入判定与显式错误码。"""
    js = _read(TRANSPORT_JS)
    assert "NOT_INTEGRATED_MESSAGE" in js
    assert "createNotIntegratedError" in js
    assert "isNotIntegratedResponse" in js
    assert "NOT_INTEGRATED" in js
    # 必须区分标准错误包，避免把真实业务 404 误判
    assert "typeof detail === 'object'" in js, "必须按对象 detail 判定路由存在"
    assert "not found" in js, "必须识别 FastAPI 默认 404 文案"
    # 503 属可恢复的服务不可用，必须与「未纳入当前切片」分离
    assert "NOT_INTEGRATED_STATUSES = new Set([404, 501])" in js, "503 不得列入未接入集合"
    assert "SERVICE_UNAVAILABLE" in js, "必须单独定义服务暂时不可用语义"


def test_transport_wraps_both_transport_factories():
    """普通与惰性 transport 都必须经过未接入判定。"""
    js = _read(TRANSPORT_JS)
    occurrences = js.count("await isNotIntegratedResponse(response)")
    assert occurrences >= 2, f"两个 transport 工厂都应接入判定，实际命中 {occurrences}"


def test_workspace_common_exposes_not_integrated():
    """传统脚本页面的 api() 与 window.Workspace 必须暴露未接入语义。"""
    js = _read(WORKSPACE_JS)
    assert "NOT_INTEGRATED" in js
    assert "isNotIntegrated" in js
    assert "NOT_INTEGRATED_MESSAGE" in js
    assert "reason" not in js or "getPublicShare" not in js
    workspace_export = js[js.find("window.Workspace = {") :]
    assert "NOT_INTEGRATED_MESSAGE" in workspace_export, "window.Workspace 应暴露未接入文案"


def test_no_silent_degradation_without_marker():
    """显式降级必须携带结构化标记，不允许静默吞掉未接入错误。"""
    transport = _read(TRANSPORT_JS)
    assert "unavailable = true" in transport, "错误必须带 unavailable 标记"
    workspace = _read(WORKSPACE_JS)
    assert "unavailable = true" in workspace, "workspace api() 同样必须带结构化标记"


def test_workspace_common_separates_503_from_not_integrated():
    """503 必须在 workspace-common.js 中与「未接入」分离。"""
    js = _read(WORKSPACE_JS)
    assert "[404, 501]" in js, "503 不得列入未接入集合"
    assert "SERVICE_UNAVAILABLE" in js, "必须定义服务暂时不可用语义"
    assert "serviceUnavailableError" in js


def test_workspace_common_renders_object_detail_readably():
    """对象型 detail 不得渲染成 [object Object]。"""
    js = _read(WORKSPACE_JS)
    assert "detailValue" in js and "detailValue.message" in js

CONTROLLERS_DIR = REPO_ROOT / "web" / "js" / "controllers"
PRODUCTION_JS = CONTROLLERS_DIR / "production-controller.js"
STORYBOARD_JS = CONTROLLERS_DIR / "storyboard-controller.js"
WORKSHOP_HTML = REPO_ROOT / "web" / "pages" / "workshop.html"


def test_production_controller_has_no_fake_ready_status():
    """制片控制器不得把未知/降级状态伪装成绿色「就绪」。"""
    js = _read(PRODUCTION_JS)
    assert "status: 'ready'" not in js, "state.shotList 不得保留伪造的 ready 状态"
    assert "sh.status = 'ready'" not in js, "取消选中不得回写伪造的 ready"
    assert "not_integrated" in js, "必须使用显式未接入状态"
    assert 'data-gw-degradation="not_integrated"' in js, "降级态必须带结构化标记"
    assert "未接入" in js, "必须向用户明说未接入"


def test_production_controller_generate_is_explicitly_not_integrated():
    """触发渲染不得静默假装完成；必须显式告知未接入。"""
    js = _read(PRODUCTION_JS)
    anchor = js.find("function triggerGenerate()")
    assert anchor != -1, "未找到 triggerGenerate()"
    region = js[anchor : anchor + 500]
    assert "未接入" in region, "triggerGenerate 必须显式提示未接入，不得伪装生成完成"


def test_production_controller_demo_catalog_is_labeled():
    """内置示例目录必须显式标注为未接入后端，且不得残留旧名。"""
    js = _read(PRODUCTION_JS)
    assert "demoProjectCatalog" in js, "内置示例目录必须改名以提示非真实数据"
    assert "projectCatalog" not in js.replace("demoProjectCatalog", ""), "不得残留内部名 projectCatalog"


def test_storyboard_controller_has_no_fake_done_status():
    """分镜控制器不得把未实现的渲染端点结果标为 done。"""
    js = _read(STORYBOARD_JS)
    assert "not_integrated" in js
    assert "未接入" in js


def test_workshop_demo_catalog_not_merged_into_projects():
    """工坊页内置示例目录不得并入真实 projects 状态。"""
    html = _read(WORKSHOP_HTML)
    assert "demoProjectCatalog" in html
    assert "projectsDegradation" in html and "episodesDegradation" in html

# ---------------------------------------------------------------------------
# Phase 9E：静态页残留「不可验证运行态断言」清零守卫（2026-09-21 追加）
# ---------------------------------------------------------------------------
PAGES_DIR = REPO_ROOT / "web" / "pages"


def _is_comment_line(line: str) -> bool:
    """判断该行是否只是注释/文档串行（不参与运行时行为）。"""
    stripped = line.strip()
    if not stripped:
        return False
    return stripped.startswith(("//", "*", "/*", "<!--", "#"))


def _is_identifier_only_random(line: str) -> bool:
    """判断该行的 Math.random 是否只用于生成标识符 / nonce（非遥测读数）。"""
    if not re.search(r"(id|key|nonce|uuid|token|client_id|group|note|library|layer|aura)\s*[:=]", line, re.I):
        return False
    # 若同一行还写了百分比/负载字段，则不能按标识符豁免。
    return not re.search(r"(cpu|ram|gpu|vram|load|usage|temperat|percent|jitter)\s*[:=]", line, re.I)
CONTROLLERS_DIR = STATIC_JS / "controllers"
INDEX_HTML = PAGES_DIR / "index.html"
PROJECTS_HTML = PAGES_DIR / "projects.html"
PRODUCTION_HTML = PAGES_DIR / "production.html"
STORYBOARD_HTML = PAGES_DIR / "storyboard.html"
AGENTS_HTML = PAGES_DIR / "agents.html"
AGENTS_JS = CONTROLLERS_DIR / "agents-controller.js"
STORYBOARD_JS = CONTROLLERS_DIR / "storyboard-controller.js"
COLLAB_HTML = PAGES_DIR / "collab.html"
SETTINGS_HTML = PAGES_DIR / "settings.html"

#: 无后端时**不得**出现在页面上的伪造运行态断言（不可验证的数值/在线/进度/设备名）
FAKE_RUNTIME_CLAIMS = [
    "3 运行中",
    "生成中 85%",
    "采样中 64%",
    "渲染中 42%",
    "执行中 72%",
    "排队中 #01",
    "ETA 45s",
    "22.4G",
    "18.2G",
    "42.0G",
    "32.4 fps",
    "84 tokens/s",
    "4 在线",
    "5 已生成",
    "AI-FLUX MASTER ENGINE",
    "24-BUS",
]


def test_workbench_static_pages_have_no_fake_runtime_claims():
    """全 Workbench 静态页不得残留伪造运行态断言（含任务中心/GPU/智能体/分镜）。"""
    offenders = {}
    for path in sorted(list(PAGES_DIR.glob("*.html")) + list(CONTROLLERS_DIR.glob("*.js"))):
        text = path.read_text(encoding="utf-8")
        hits = [c for c in FAKE_RUNTIME_CLAIMS if c in text]
        if hits:
            offenders[path.name] = hits
    assert not offenders, f"仍有伪造运行态断言: {offenders}"


def test_agents_controller_has_no_fake_online_status():
    """智能体控制器不得把内置示例目录渲染成 online / idle。"""
    js = _read(AGENTS_JS)
    assert "status: 'online'" not in js, "不得残留伪造 online 状态"
    assert "status: 'idle'" not in js, "不得残留伪造 idle 状态"
    assert "not_integrated" in js, "必须使用显式未接入状态"


def test_agents_controller_test_prompt_not_faked_as_completed():
    """测试指令不得在 400ms 后伪造「已完成」。"""
    js = _read(AGENTS_JS)
    assert "测试指令已完成" not in js, "不得伪造测试指令已完成"
    assert "result: 'local-test'" not in js, "不得把本地桩结果标记为真实执行"
    assert "未接入" in js, "必须向用户明说未接入"


def test_storyboard_controller_has_no_fake_generating_progress():
    """分镜控制器不得伪造「渲染中 68%」这类未实现端点的进度。"""
    js = _read(STORYBOARD_JS)
    assert "渲染中 68%" not in js, "不得伪造渲染进度百分比"
    assert "status: 'generating'" not in js, "不得把未实现渲染标为 generating"
    assert "未接入" in js


def test_workbench_task_center_is_driven_by_real_endpoint():
    """Phase 12：任务中心已真实接入 GET /api/observability/tasks。

    本用例由旧「整体未接入」守卫改写而来（Phase 12 门禁第 6 条：原 *_NOT_INTEGRATED
    断言必须改为断言新真实语义）。要求：
    1. 三页存在由共享模块驱动的真实容器，且不再保留静态示例任务；
    2. 共享模块只消费 /api/observability/tasks，且不含任何随机数或伪造百分比；
    3. 后端不可达时仍必须走显式降级（不得静默伪造队列）。
    """
    for path in (INDEX_HTML, PROJECTS_HTML, PRODUCTION_HTML):
        html = _read(path)
        assert "/static/js/core/task-queue.js" in html, f"{path.name} 必须引入任务队列共享模块"
        assert "workbenchTaskCenterDrawerList" in html, f"{path.name} 必须有真实队列容器"
    index = _read(INDEX_HTML)
    assert 'id="activeTasksCountBadge"' in index, "首页必须有活跃任务计数位"
    assert "暂无真实任务" not in index, "首页不得残留静态示例任务卡片"
    assert "workbenchActiveTasksContainer" in index, "首页必须有真实活跃任务容器"

    queue_js = _read(STATIC_JS / "core/task-queue.js")
    assert "/api/observability/tasks" in queue_js, "共享模块必须消费真实任务端点"
    assert "Math.random" not in queue_js, "任务渲染不得使用随机数"
    assert "job_id" in queue_js and "poll_hint" in queue_js, "必须消费契约字段"
    assert "data-gw-degradation" in queue_js, "降级路径必须带结构化标记"
    assert "未接入" in queue_js, "端点缺失时必须明说未接入"


def test_workbench_gpu_telemetry_not_faked():
    """GPU 集群卡不得伪造设备型号/显存/利用率。

    Phase 12 收口：后端已真实接入 nvidia-smi GPU 遥测
    （/api/observability/health 的 gpu_telemetry 检查项），
    原「静态未接入」断言按门禁要求改为断言新真实语义：
    设备卡必须由真实 devices 数组渲染，静态不得残留任何伪造型号/占用。
    """
    html = _read(PROJECTS_HTML)
    assert "22.4G" not in html and "18.2G" not in html, "不得伪造显存占用"
    assert "IDLE</span>" not in html, "不得伪造 GPU 空闲/占用状态"
    assert 'id="gpuDeviceGrid"' in html, "GPU 设备卡必须由真实 devices 渲染"
    assert "4090 #1" not in html and "A100 #1" not in html, "不得静态写死伪造型号"
    assert 'id="gpuClusterStatus"' in html, "必须有可被真实 gpu_telemetry 驱动的集群状态位"
    js = _read(TELEMETRY_JS)
    assert "gpu_telemetry" in js, "必须消费真实 GPU 检查项"
    assert "applyGpuTelemetry" in js, "必须存在真实 GPU 写入方法"
    assert "nvidia-smi" in js, "GPU 读数来源必须是 nvidia-smi 真实读数"


def test_workbench_collab_and_settings_no_fake_latency_claim():
    """协同页/设置页不得断言 0ms 本地时延。"""
    for path in (COLLAB_HTML, SETTINGS_HTML):
        html = _read(path)
        assert "0ms" not in html, f"{path.name} 不得伪造 0ms 时延断言"
    assert "未接入" in _read(COLLAB_HTML)


# ---------------------------------------------------------------------------
# Phase 9E-2 / 9E-3：随机伪造遥测、虚假在线胶囊、episode-pipeline 伪造运行态
# ---------------------------------------------------------------------------
TELEMETRY_JS = STATIC_JS / "core" / "hardware-telemetry.js"
EPISODE_JS = STATIC_JS / "modules/episode-pipeline.js"
EPISODE_HTML = REPO_ROOT / "web" / "embeds" / "episode-pipeline.html"
WORKBENCH_PAGES = [
    "index.html", "projects.html", "workshop.html", "production.html",
    "storyboard.html", "agents.html", "assets.html", "collab.html", "settings.html",
]


def test_telemetry_has_no_random_fake_vu_jitter():
    """VU 表不得用 Math.random 伪造 CPU/RAM 负载；无端点时必须显式「未接入」。"""
    js = _read(TELEMETRY_JS)
    assert "Math.random" not in js, "硬件遥测模块不得用随机数伪造负载数值"
    assert "integrated" in js, "必须存在真实遥测接线开关"
    assert "data-gw-degradation" in js, "必须在读数上打显式降级标记"


def test_workbench_pages_have_no_static_online_green_led():
    """9 个 Workbench 页顶栏不得保留静态绿色「Online」断言。"""
    offenders = []
    for name in WORKBENCH_PAGES:
        text = (PAGES_DIR / name).read_text(encoding="utf-8")
        assert ">Online<" not in text, f"{name} 不得保留静态 Online 文案"
        assert 'bg-emerald-400 shadow-[0_0_6px_#34d399]' not in text, (
            f"{name} 不得保留静态绿色就绪灯"
        )
        assert "uv-hub-percent" in text
        offenders.append(name)
    assert len(offenders) == 9


def test_workbench_online_capsule_is_driven_by_real_health_probe():
    """Phase 12：顶栏在线胶囊已真实接入 /api/observability/health。

    旧口径「本切片无任何真实在线状态探针」已不成立（后端 health 探针可用），
    本用例断言新真实语义：9 页共用同一 ID，由共享模块按后端真实字段判定，
    且绝不在未接入/异常时渲染绿色在线灯。
    """
    for name in WORKBENCH_PAGES:
        text = (PAGES_DIR / name).read_text(encoding="utf-8")
        assert 'id="gwOnlineStatus"' in text, f"{name} 在线胶囊必须可被真实探针驱动"
        assert "无任何真实在线状态探针" not in text, f"{name} 不得再断言无在线探针"

    js = _read(TELEMETRY_JS)
    assert "/api/observability/health" in js, "在线胶囊必须消费真实 health 探针"
    assert "syncOnlineStatus" in js, "必须存在真实在线状态同步方法"
    assert "data-gw-degradation" in js, "降级态必须带结构化标记"
    assert "Math.random" not in js, "在线状态不得由随机数决定"


def test_workbench_pages_vu_reading_is_explicitly_not_integrated():
    """CPU/RAM 读数初值不得是具体百分比，必须写明「未接入」。"""
    import re

    pattern = re.compile(
        r'<span class="uv-hub-percent [^"]*" id="(?:cpuValText|ramValText)"[^>]*>([^<]*)</span>'
    )
    for name in WORKBENCH_PAGES:
        text = (PAGES_DIR / name).read_text(encoding="utf-8")
        values = pattern.findall(text)
        assert len(values) == 2, f"{name} 应有 CPU/RAM 两个读数"
        for value in values:
            assert value.strip() in {"未接入", "未测量", "—"}, (
                f"{name} 读数不得为具体百分比，实际 {value!r}"
            )


def test_episode_pipeline_has_no_fake_hardware_or_model_claims():
    """剧本流水线不得伪造算力/显存/随机种子/模型就绪或默认厂商模型名。"""
    import re

    js = _read(EPISODE_JS)
    assert not re.search(r"\d+(?:\.\d+)?\s*TFLOPS", js), "不得伪造算力数值"
    assert not re.search(r"\d+(?:\.\d+)?\s*/\s*\d+\s*GB", js), "不得伪造显存占用"
    assert not re.search(r"SEED:\s*\d+", js), "不得伪造随机种子"
    assert "owai / gpt-5.6-luna" not in js, "不得回退到具体厂商/模型名"
    assert "AIZZZ-gpt-image2" not in js, "不得回退到具体图片模型名"
    assert "未配置模型" in js, "无 providers 时必须显式说明未配置模型"
    assert js.count("未接入") >= 3, "必须在硬件与模型状态上使用显式未接入"
    assert "data-gw-degradation" in js


# ---------------------------------------------------------------------------
# Phase 9F：顶栏拟物推子不得残留具体读数（裁定 3 收尾，2026-09-21 追加）
#
# 真实缺陷：7 个主页面 + workbench-shell.js 的顶栏推子曾写死 width:78% / left:78% 与
# 「14.8G / 12.2G / 18.4G」等具体读数，渲染后被读成"真实算力/显存遥测"。
# 修复：推子一律 0%，读数改为「未接入」并带 data-gw-degradation="not_integrated"。
# 本节为静态守卫（源码层），不等于浏览器渲染 E2E。
# ---------------------------------------------------------------------------

TOP_BAR_FADER_FILES = [
    "index.html", "projects.html", "production.html", "storyboard.html",
    "agents.html", "assets.html", "collab.html",
]

#: 修复前的具体读数；这些字符串不得再出现在顶栏推子文件里。
STALE_FADER_READOUTS = ["78%", "82%", "75%", "92%", "68%", "88%", "14.8G", "18.4G", "12.2G"]


def _topbar_fader_blocks(text: str):
    """截取**顶栏**推子块：只取带 `data-gw-degradation` 读数标记的推子。

    页面里还有另一类**真实可调参数**推子（如温度 70%、CFG 65%、LoRA 权重 85%），
    它们是交互输入而非遥测断言，**必须保留**，因此不能用全页匹配把它们误判为伪造读数。
    """
    blocks = []
    for match in re.finditer(r"hw-fader-track-horizontal", text):
        block = text[max(0, match.start() - 400) : match.start() + 1200]
        if 'data-gw-degradation="not_integrated"' in block or "未接入" in block:
            blocks.append(block)
    return blocks


def test_workbench_topbar_faders_have_no_stale_readouts():
    """7 个主页面的**顶栏推子块**不得残留具体读数（% / G）。"""
    offenders = {}
    for name in TOP_BAR_FADER_FILES:
        text = _read(PAGES_DIR / name)
        blocks = _topbar_fader_blocks(text)
        assert blocks, f"{name} 未找到顶栏推子块"
        for block in blocks:
            hits = [token for token in STALE_FADER_READOUTS if token in block]
            if hits:
                offenders.setdefault(name, []).append(hits)
    assert not offenders, f"顶栏推子仍残留具体读数: {offenders}"


def test_workbench_shell_injected_faders_have_no_stale_readouts():
    """workbench-shell.js 注入的顶栏不得残留具体读数或非零推子位移。

    本轮修正口径：注入推子**不得**永久写死「未接入」，因为它旁边的真实 GPU 遥测已接入；
    必须改为可被真实读数驱动的钩子（初值「读取中…」），否则就是自相矛盾的假降级。
    """
    js = _read(STATIC_JS / "core" / "workbench-shell.js")
    for token in STALE_FADER_READOUTS:
        assert token not in js, f"workbench-shell.js 不得残留具体读数 {token}"
    assert "width:78%" not in js.replace(" ", ""), "注入推子宽度必须为 0%"
    assert "data-gw-gpu-util" in js, "注入的 FLUX 推子必须提供可被真实 GPU 读数驱动的钩子"
    assert "data-gw-gpu-vram" in js, "注入的 VRAM 推子必须提供可被真实 GPU 读数驱动的钩子"
    assert "读取中" in js, "注入推子初值应为读取中"
    # 只有推子读数位不得写死未接入；头像「认证状态未接入」等其它语义不在此范围。
    for hook in ("data-gw-gpu-util", "data-gw-gpu-vram"):
        begin = js.find(hook)
        assert begin != -1, hook
        region = js[begin : begin + 400]
        assert "未接入" not in region, f"{hook} 推子不得永久写死未接入（真实 GPU 遥测已可驱动）"


def test_workbench_shell_injected_faders_are_driven_by_real_gpu_telemetry():
    """注入推子的钩子必须真的被 applyGpuTelemetry 消费，且写入真实百分比。"""
    telemetry = _read(STATIC_JS / "core" / "hardware-telemetry.js")
    assert "data-gw-' + key + '" in telemetry or "data-gw-gpu-util" in telemetry, \
        "必须按钩子选择器写入真实读数"
    assert "data-gw-gpu-util" in telemetry, "FLUX 推子钩子必须被真实遥测消费"
    assert "data-gw-gpu-vram" in telemetry, "VRAM 推子钩子必须被真实遥测消费"
    assert "lastGpuCheck" in telemetry, "必须缓存最近一次真实读数供后注入推子回放"
    shell = _read(STATIC_JS / "core" / "workbench-shell.js")
    assert "lastGpuCheck" in shell, "注入推子后必须立即回放最近真实读数，不得停在读取中"


def test_workbench_topbar_fader_readouts_are_marked_not_integrated():
    """推子块必须给出「未接入」文案 + 结构化降级标记，且位移一律 0% 起手。"""
    for name in TOP_BAR_FADER_FILES:
        text = _read(PAGES_DIR / name)
        assert "未接入" in text, f"{name} 推子读数必须明说未接入"
        assert 'data-gw-degradation="not_integrated"' in text, f"{name} 推子必须带降级标记"
        for block in _topbar_fader_blocks(text):
            for match in re.finditer(r"(?:width|left)\s*:\s*(\d+)%", block):
                assert match.group(1) == "0", (
                    f"{name} 顶栏推子必须以 0% 起手，实际 {match.group(0)}"
                )


# ---------------------------------------------------------------------------
# R6-9：asset-review 授权门禁不得 fail-open
#
# 真实缺陷：后端 /api/asset-auth/status 契约里**没有** auth_required 字段，
# 旧 can() 用 `!state.auth?.auth_required || ...` → `!undefined === true`，
# 未认证访客被判为拥有 admin/editor/reviewer 全部权限。
# 本节为静态守卫；行为级对照由 %TEMP% 独立复算脚本给出（7 组场景 0 分歧）。
# ---------------------------------------------------------------------------

ASSET_REVIEW_JS = STATIC_JS / "modules/asset-review.js"


def test_asset_review_can_is_fail_closed():
    """can() 必须先要求已认证且有 principal，再比较角色等级。"""
    js = _read(ASSET_REVIEW_JS)
    code = "\n".join(
        line for line in js.splitlines() if not line.strip().startswith(("//", "*", "/*"))
    )
    assert "state.auth?.auth_required" not in code, "can() 不得再依赖后端不存在的 auth_required"
    anchor = code.find("const can =")
    assert anchor != -1, "未找到 can()"
    region = code[anchor : anchor + 320]
    assert "state.auth?.authenticated" in region, "can() 必须要求 authenticated"
    assert "state.auth?.principal" in region, "can() 必须要求 principal"
    assert "roleLevel" in region, "can() 必须做角色等级比较"


def test_asset_review_login_failure_is_explicitly_degraded():
    """认证服务不可达时必须显式降级，不得写入宽松默认值。"""
    js = _read(ASSET_REVIEW_JS)
    assert "degraded:true" in js.replace(" ", ""), "必须写入 degraded 标记"
    assert "认证服务未接入或不可达" in js, "必须给出中文原因文案"
    code = "\n".join(
        line for line in js.splitlines() if not line.strip().startswith(("//", "*", "/*"))
    )
    assert "needs_setup" not in code, "不得残留后端不存在的 needs_setup 字段"
    assert "bootstrap(" not in code, "不得残留旧 bootstrap 登录分支"
    assert "needsLogin" in code, "must expose needsLogin()"
# ---------------------------------------------------------------------------
# Phase 9F-2：侧栏「项目树进度」等同类假运行态读数清零（2026-09-21 追加）
#
# 与顶栏推子同源缺陷：项目树节点后挂 100% / 85% / 75% / 20% 及
# 「online」胶囊、「4节点」、「VRAM CAP 85%」等不可验证读数，渲染后被读成真实进度/容量。
# 修复：一律改为「未接入 / 未验证」+ data-gw-degradation="not_integrated"。
# 注意：production.html 的 LoRA(85%)/roughness(18%)/CFG(65%) 与温度(70%) 是真实交互输入，保留。
# ---------------------------------------------------------------------------

#: 侧栏项目树 / 场景树的伪造进度读数（形如 >NN%<）
TREE_PROGRESS_RE = re.compile(r">(?:100|85|82|78|75|68|58|92|88|64|42|72|20)%<")


def test_workbench_side_tree_has_no_fake_progress_readouts():
    """侧栏项目树 / 场景树不得残留伪造进度百分比。"""
    offenders = {}
    for name in ("index.html", "projects.html", "production.html", "storyboard.html"):
        text = _read(PAGES_DIR / name)
        hits = [m.group(0) for m in TREE_PROGRESS_RE.finditer(text)]
        if hits:
            offenders[name] = hits
    assert not offenders, f"侧栏树仍残留伪造进度读数: {offenders}"


def test_workbench_pages_have_no_fake_online_badge_or_node_count():
    """不得残留 `online` 状态胶囊 / `4节点` / `VRAM CAP NN%` 这类不可验证断言。"""
    offenders = {}
    for name in ("index.html", "projects.html", "production.html", "storyboard.html", "agents.html"):
        text = _read(PAGES_DIR / name)
        hits = []
        if "<span>online</span>" in text:
            hits.append("online")
        if re.search(r">\d+节点<", text):
            hits.append("节点数")
        if re.search(r"VRAM CAP \d+%", text):
            hits.append("VRAM CAP")
        if hits:
            offenders[name] = hits
    assert not offenders, f"仍残留不可验证运行态断言: {offenders}"


def test_workbench_side_tree_degradation_replacements_are_marked():
    """被替换的侧栏读数必须带结构化降级标记，不得改成另一个数值。"""
    for name in ("index.html", "projects.html", "production.html"):
        text = _read(PAGES_DIR / name)
        assert 'data-gw-degradation="not_integrated"' in text, f"{name} 缺少降级标记"
    assert "VRAM 上限未接入" in _read(PAGES_DIR / "projects.html")
    assert "<span>online</span>" not in _read(PAGES_DIR / "index.html")
    assert "<span>未验证</span>" in _read(PAGES_DIR / "index.html")


# ---------------------------------------------------------------------------
# Phase 9G：`X || 默认值` 把真实 0 吞掉（静默伪造）——静态守卫
#
# 真实缺陷（独立复核方 + 主代理各自复算）：
#   `p.progress || 10` / `Number(p.progress) || 60` / `p.progress || ... : 75`
#   在 `progress === 0`（后端新建项目真实值）时返回**非 0 伪造值**，
#   即 `0 || 10 === 10`、`0 || 60 === 60`、`0 || 75 === 75`。
# 这两处控制器因此把「0% 进度」显示成 10% / 60% / 75% —— 与真实值相反。
# 正确写法：`Number.isFinite(x) ? x : null`（或 `??`），绝不用 `||`。
# ---------------------------------------------------------------------------

#: 允许出现 `|| 0`（`0 || 0 === 0`，不改变语义），其余 `|| 数字` 一律视为伪造兜底。
FALSY_FALLBACK_RE = re.compile(
    r"\b(?:progress|scenes|shots)\b[^;\n]{0,40}\|\|\s*(?!0(?:\D|$))\d+"
)
FALSY_FALLBACK_RE2 = re.compile(
    r"Number\(\s*p\.progress\s*\)\s*\|\|\s*(?!0(?:\D|$))\d+"
)
FALSY_FALLBACK_RE3 = re.compile(
    r"p\.progress\s*\|\|\s*(?:\([^)]*\)\s*:\s*)?(?<!0)\d{2}"
)

CONTROLLERS = ["home-controller.js", "projects-controller.js", "production-controller.js"]


def test_controllers_have_no_falsy_numeric_fallback():
    """控制器不得用 `X || 数字` 兜底 progress/scenes/shots（会把真实 0 改写）。"""
    offenders = {}
    for name in CONTROLLERS:
        text = _read(CONTROLLERS_DIR / name)
        hits = []
        for regex in (FALSY_FALLBACK_RE, FALSY_FALLBACK_RE2, FALSY_FALLBACK_RE3):
            hits += [m.group(0) for m in regex.finditer(text)]
        # 去掉纯注释行（本轮修复说明中会引用旧写法）
        code = "\n".join(
            ln for ln in text.splitlines() if not ln.strip().startswith(("//", "*", "/*"))
        )
        real = []
        for hit in hits:
            if hit in code:
                real.append(hit)
        if real:
            offenders[name] = real
    assert not offenders, f"存在 `||` 数值兜底（会把真实 0 吞掉）: {offenders}"


def test_progress_helpers_preserve_zero():
    """进度解析 helper 必须用 Number.isFinite / ??，不得用 ||。"""
    home = _read(CONTROLLERS_DIR / "home-controller.js")
    assert "function projectProgressMeta(" in home, "必须有 projectProgressMeta"
    anchor = home.find("function projectProgressMeta(")
    region = home[anchor : anchor + 700]
    assert "Number.isFinite" in region, "必须用 Number.isFinite 判定，而非真值判定"
    assert "||" not in region.replace("|| null", ""), "helper 内不得用 || 兜底"

    pc = _read(CONTROLLERS_DIR / "projects-controller.js")
    assert "function rawNumber(" in pc, "必须有 rawNumber"
    assert "function progressMeta(" in pc, "必须有 progressMeta"


def test_no_fake_scene_shot_counts():
    """不得把缺失的 scenes/shots 伪造成 24/72。"""
    pc = _read(CONTROLLERS_DIR / "projects-controller.js")
    code = "\n".join(
        ln for ln in pc.splitlines() if not ln.strip().startswith(("//", "*", "/*"))
    )
    assert "p.scenes || 24" not in code, "不得把缺失场次数伪造成 24"
    assert "p.shots || 72" not in code, "不得把缺失镜头数伪造成 72"


# ---------------------------------------------------------------------------
# Phase 9G-2：`progress: 0` fixture 的**行为级**守卫（Node 真实执行）
#
# 静态守卫只能证明没有 `|| 数字` 写法；本用例把两个 helper 从源文件**原样抽出**，
# 在 Node 内用 `progress: 0` 的 fixture 真实调用，断言：
#   1. 真实 0 必须原样返回 0（不得变 10 / 60 / 75）；
#   2. 缺失 / null / 空串 / 非数必须显式 degraded（不得静默给数字）。
# 证据边界：Node 行为验证 ≠ 真实浏览器 E2E。
# ---------------------------------------------------------------------------

_NODE = shutil.which("node")


def _extract_fn(source: str, name: str) -> str:
    """按花括号配平从源码中抽出完整函数定义（含 `function ` 前缀）。"""
    idx = source.find("function " + name + "(")
    assert idx != -1, f"未找到函数 {name}"
    brace = source.index("{", idx)
    depth = 0
    for pos in range(brace, len(source)):
        if source[pos] == "{":
            depth += 1
        elif source[pos] == "}":
            depth -= 1
            if depth == 0:
                return source[idx : pos + 1]
    raise AssertionError(f"函数 {name} 花括号不配平")


@pytest.mark.skipif(_NODE is None, reason="需要 Node 执行行为级断言")
def test_progress_zero_is_preserved_at_runtime(tmp_path):
    home_src = _read(CONTROLLERS_DIR / "home-controller.js")
    pc_src = _read(CONTROLLERS_DIR / "projects-controller.js")

    script = "\n".join(
        [
            _extract_fn(pc_src, "rawNumber"),
            _extract_fn(pc_src, "progressMeta"),
            _extract_fn(home_src, "projectProgressMeta"),
            "",
            "const fixtures = [",
            "  {name: 'zero', project: {progress: 0}},",
            "  {name: 'zeroString', project: {progress: '0'}},",
            "  {name: 'sixtyEight', project: {progress: 68}},",
            "  {name: 'missing', project: {}},",
            "  {name: 'null', project: {progress: null}},",
            "  {name: 'empty', project: {progress: ''}},",
            "  {name: 'nan', project: {progress: 'abc'}},",
            "  {name: 'entityOnly', project: {entity_count: 4, completed_entity_count: 3}},",
            "];",
            "const out = fixtures.map(f => ({",
            "  name: f.name,",
            "  home: projectProgressMeta(f.project),",
            "  pc: progressMeta(f.project),",
            "}));",
            "process.stdout.write(JSON.stringify(out));",
        ]
    )
    path = tmp_path / "probe.mjs"
    path.write_text(script, encoding="utf-8")
    result = subprocess.run(
        [_NODE, str(path)], capture_output=True, cwd=str(tmp_path), timeout=60
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")[:500]
    data = {item["name"]: item for item in json.loads(result.stdout.decode("utf-8"))}

    # 1) 真实 0 必须原样保留为 0（不得变 10 / 60 / 75）
    assert data["zero"]["home"]["value"] == 0, "home: progress=0 被改写"
    assert data["zero"]["home"]["degraded"] is False
    assert data["zero"]["pc"]["value"] == 0, "projects: progress=0 被改写"
    assert data["zero"]["pc"]["degraded"] is False
    assert "0%" in data["zero"]["pc"]["html"], "projects: 渲染结果必须是 0%"
    for banned in ("10%", "60%", "75%"):
        assert banned not in data["zero"]["pc"]["html"], f"渲染结果混入伪造值 {banned}"

    # 字符串 '0' 同样必须解析为 0
    assert data["zeroString"]["home"]["value"] == 0

    # 2) 正常值原样透传
    assert data["sixtyEight"]["home"]["value"] == 68
    assert "68%" in data["sixtyEight"]["pc"]["html"]

    # 3) 缺失 / null / 空串 / 非数：必须显式 degraded，不得静默给数字
    for name in ("missing", "null", "empty", "nan"):
        assert data[name]["home"]["degraded"] is True, f"home: {name} 未显式降级"
        assert data[name]["home"]["value"] == 0, f"home: {name} 降级值应为 0"
        assert data[name]["pc"]["degraded"] is True, f"projects: {name} 未显式降级"
        assert data[name]["pc"]["value"] is None
        assert "未接入" in data[name]["pc"]["html"], f"projects: {name} 应明说未接入"

    # 4) 仅有 entity_count 时按完成比例推算，且不算降级
    assert data["entityOnly"]["home"]["value"] == 75
    assert data["entityOnly"]["home"]["degraded"] is False

# ---------------------------------------------------------------------------
# Phase 9F-3：伪硬件读数 / 随机遥测 / 在线徽标清零守卫（独立复核方第 2/3 次提醒的残留项）
# ---------------------------------------------------------------------------

#: 非 vendor 前端里不得出现的「伪硬件读数」字面量（无任何后端来源）
FAKE_HARDWARE_READOUTS = [
    "1.4TB",
    "4090×4",
    "3.84 TB",
    "/ 10 TB",
]

FRONTEND_SCAN_TARGETS = [
    (PAGES_DIR, "*.html"),
    (REPO_ROOT / "web" / "embeds", "*.html"),
    (CONTROLLERS_DIR, "*.js"),
    (STATIC_JS / "core", "*.js"),
    (STATIC_JS / "modules", "**/*.js"),
]


def _frontend_files():
    """返回非 vendor 前端文件清单（Workbench 页面 + 前端脚本）。"""
    files = []
    for base, pattern in FRONTEND_SCAN_TARGETS:
        assert base.is_dir(), f"前端扫描目录不存在：{base}"
        matched = sorted(base.glob(pattern))
        assert matched, f"前端扫描目标没有文件：{base} / {pattern}"
        files.extend(matched)
    return files


def test_frontend_has_no_fake_hardware_readouts():
    """不得残留 1.4TB / 4090x4 / 3.84 TB / 10 TB 这类无来源的伪硬件读数。

    只扫描**可执行代码行**：注释里说明「这里曾有伪读数、已移除」属正当留痕，
    不应被当成违规。
    """
    offenders = {}
    for path in _frontend_files():
        hits = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if _is_comment_line(line):
                continue
            hits.extend(c for c in FAKE_HARDWARE_READOUTS if c in line)
        if hits:
            offenders[path.name] = sorted(set(hits))
    assert not offenders, f"仍有伪硬件读数: {offenders}"


#: 判定「随机数是否被当成遥测读数」的特征：赋值给百分比/指针/负载字段。
_RANDOM_TELEMETRY_PATTERNS = (
    re.compile(r"Math\.random\(\)[^;]*\*\s*\d+"),
    re.compile(r"(cpu|ram|gpu|vram|load|usage|temperat|percent|jitter|vibration)[A-Za-z_]*\s*=\s*[^;]*Math\.random\(\)", re.I),
    re.compile(r"Math\.random\(\)[^;]*setAttribute\('transform'"),
)


def test_frontend_has_no_random_driven_telemetry():
    """不得把 Math.random 当作遥测数据源。

    合法用途（随机 ID / nonce / client_id / 幂等键 / 抖动动画）不在禁用范围；
    禁止的是「随机数 → 百分比/负载/指针读数」这类伪造运行态的模式。
    """
    offenders = []
    for path in _frontend_files():
        for line in path.read_text(encoding="utf-8").splitlines():
            if _is_comment_line(line):
                continue
            if not any(pattern.search(line) for pattern in _RANDOM_TELEMETRY_PATTERNS):
                continue
            if _is_identifier_only_random(line):
                continue
            offenders.append(f"{path.name}: {line.strip()[:110]}")
    assert not offenders, f"仍存在 Math.random 驱动的伪遥测: {offenders}"


def test_static_fader_bars_are_zero_width_only():
    """静态 HTML 的 hw-fader-glow-bar 只有两种情况合法：

    - 宽度为 0%（未接入，或交由 JS 绑定真实数值）；
    - 显式标记 ``data-gw-control="user-input"``（真实用户交互参数初值，
      如 production.html 的 LoRA 权重 / Roughness / CFG，属用户输入而非遥测）。
    """
    offenders = {}
    for path in sorted(PAGES_DIR.glob("*.html")):
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(
            r'<div\s+class="hw-fader-glow-bar[^"]*"[^>]*?style="width:\s*([^;"]+)[^"]*"([^>]*?)>',
            text,
        ):
            value = match.group(1).strip()
            rest = match.group(2)
            if value in ("0%", "0"):
                continue
            if 'data-gw-control="user-input"' in rest:
                continue
            offenders.setdefault(path.name, []).append(value)
    assert not offenders, f"静态推子条残留非零写死宽度: {offenders}"


def test_asset_pool_size_is_marked_not_integrated():
    """资产池容量没有真实后端字段：必须显式标记未接入，不得显示编造的分母。"""
    index = _read(INDEX_HTML)
    assert 'id="statAssetPoolSize"' in index
    assert 'data-gw-degradation="not_integrated"' in index
    assert "3.84" not in index, "不得残留编造的容量读数"


def test_nodes_count_zero_is_preserved():
    """画布节点数为真实 0 时不得被 falsy 回退成 12（R6-12 同类缺陷）。"""
    js = _read(CONTROLLERS_DIR / "projects-controller.js")
    assert "nodes_count ||" not in js, "不得用 `|| 数字` 吞掉真实 0（节点规模）"
    assert "rawNumber(c.nodes_count)" in js, "节点规模必须走 Number.isFinite 口径"


def test_telemetry_online_badges_require_real_status_fields():
    """团队/席位在线徽标必须由真实字段驱动，字段缺失时显示未接入。"""
    js = _read(TELEMETRY_JS)
    assert "statusChip" in js, "在线徽标必须走统一状态判定函数"
    assert "statusChip(team.status)" in js, "团队徽标必须读取真实 status 字段"
    assert "statusChip(u.online)" in js, "席位徽标必须读取真实 online 字段"
    assert ">ONLINE</span>" not in js, "不得无条件渲染 ONLINE"
    assert ">ACTIVE</span>" not in js, "不得无条件渲染 ACTIVE"

# ---------------------------------------------------------------------------
# legacy_page_degradation_guards: 5 个 legacy/v2 页面统一显式降级（2026-09-22 追加）
# ---------------------------------------------------------------------------
DEGRADATION_JS = STATIC_JS / "core" / "degradation.js"
API_SETTINGS_JS = STATIC_JS / "modules/api-settings.js"
LEGACY_SETTINGS_JS = STATIC_JS / "controllers/settings.js"
GOVERNANCE_JS = STATIC_JS / "modules/governance.js"
TASK_CENTER_JS = STATIC_JS / "modules/task-center.js"
CANVAS_LIST_JS = STATIC_JS / "modules/canvas-list.js"

STATIC_DIR = REPO_ROOT / "web"
DEGRADATION_PAGE_BINDINGS = [
    (STATIC_DIR / "embeds" / "api-settings.html", "/static/js/modules/api-settings.js"),
    (STATIC_DIR / "embeds" / "governance.html", "/static/js/modules/governance.js"),
    (STATIC_DIR / "embeds" / "canvas-list.html", "/static/js/modules/canvas-list.js"),
    (STATIC_DIR / "embeds" / "task-center.html", "/static/js/modules/task-center.js"),
    (STATIC_DIR / "pages" / "settings.html", "/static/js/controllers/settings.js"),
]


def test_degradation_module_is_a_shared_single_source():
    """显式降级语义必须由单一共享模块导出。"""
    js = _read(DEGRADATION_JS)
    assert "window.GWDegradation" in js, "degradation.js 应导出 window.GWDegradation"
    for symbol in ("statusKind", "isNotIntegrated", "isServiceUnavailable", "NOT_INTEGRATED_MESSAGE"):
        assert symbol in js, "degradation.js 缺少 " + symbol
    assert "404" in js and "501" in js and "503" in js, "必须区分 404/501 与 503"


def test_api_settings_delegates_to_shared_degradation():
    """设置页必须委派共享判定，不得自建第二套语义。"""
    js = _read(API_SETTINGS_JS)
    assert "NOT_INTEGRATED_MESSAGE" in js
    assert "degradationLabel" in js, "应提供 degradationLabel 显式文案"
    assert "window.GWDegradation" in js, "应优先委派共享模块"
    assert "statusKind" in js, "状态分类必须委派 GWDegradation.statusKind"
    assert js.count("degradationLabel(") >= 5, "至少 5 处 catch 必须走显式降级文案"


def test_settings_page_has_no_silent_catch():
    """settings.js 不得静默吞掉未接入错误。"""
    js = _read(LEGACY_SETTINGS_JS)
    assert ".catch(() => {})" not in js, "不得保留静默 catch"
    assert "degradationText" in js
    assert "window.GWDegradation" in js
    assert "dataset.gwDegradation" in js, "降级必须带结构化标记"
    assert "value, error" in js, "Promise.all 必须逐项保留错误"


def test_governance_uses_explicit_degradation_message():
    """governance.js 不得把服务不可用退回泛泛失败。"""
    js = _read(GOVERNANCE_JS)
    assert "governanceDegradationMessage" in js
    assert js.count("governanceDegradationMessage(") >= 2, "定义与调用都必须存在"
    assert "NOT_INTEGRATED" in js and "SERVICE_UNAVAILABLE" in js


def test_task_center_tracks_load_error_kind():
    """task-center.js 必须标记降级类型。"""
    js = _read(TASK_CENTER_JS)
    assert "loadErrorKind" in js
    assert "taskCenterDegradationNotice" in js
    assert "data-gw-degradation" in js, "提示条必须带降级标记"


def test_canvas_list_badges_are_explicitly_degraded():
    """canvas-list.js 废弃桶/归档计数不得静默失败。"""
    js = _read(CANVAS_LIST_JS)
    assert "canvasListDegradationLabel" in js
    assert js.count("dataset.gwDegradation") >= 2, "两个徽标都必须标记"
    assert "未接入" in js, "徽标失败时应明说未接入"


def test_legacy_pages_load_degradation_before_page_scripts():
    """5 个页面必须在页面自身脚本之前引用 degradation.js。"""
    missing = []
    for html_path, page_script in DEGRADATION_PAGE_BINDINGS:
        html = _read(html_path)
        deco = html.find("/static/js/core/degradation.js")
        page = html.find(page_script)
        if deco == -1 or page == -1 or deco > page:
            missing.append(html_path.name + " (deco=" + str(deco) + ", page=" + str(page) + ")")
    assert not missing, "degradation.js 接线順序不正确: " + str(missing)


# ---------------------------------------------------------------------------
# Phase 9T：三块大功能面的**分项**显式降级（用户 2026-09-21 裁决第 2/3 项）
#
# 真实缺陷（主代理 2026-09-22 实测）：task-center 的 Promise.allSettled 分支只把
# 「XX 数据暂不可用」推入 state.degraded，未读取 error.code，因此 `/api/observability/*`
# 全部 404（未纳入当前切片）时页面显示的是「总览数据暂不可用」——会被读成
# 「后端存在但暂时取不到」，违反「明说未接入」的口径。
#
# 证据边界：本节为**静态源码守卫**，只断言源码形态，不执行浏览器；
# 运行时行为由主代理在真实 Chrome + 真实 HTTP 下单独实测（证据路径见
# docs/governance/TASK-NOTES-2026-09-18.md §21.26 / §21.27）。不等于生产验收。
# ---------------------------------------------------------------------------

TASK_CENTER_I18N = STATIC_JS / "modules/i18n" / "task-center.js"


def test_task_center_per_item_failure_is_explicitly_degraded():
    """task-center 的每条 allSettled 失败必须区分未接入 / 服务不可用。"""
    js = _read(TASK_CENTER_JS)
    assert "degradeKind" in js, "必须提供分项降级分类函数"
    assert "degradeLabel" in js, "必须提供分项降级文案函数"
    assert "NOT_INTEGRATED_LABEL" in js, "必须复用「未纳入当前切片」文案"
    assert "未纳入当前切片" in js
    # allSettled 失败分支（failed 处理器）内的每一条 push 都必须经 degradeLabel 分类
    marker = "const failed = (result, label) => {"
    start = js.find(marker)
    assert start != -1, "未找到 failed 处理器（allSettled 失败分支）"
    end = js.find("};", start)
    assert end != -1, "未找到 failed 处理器的闭合"
    failed_body = js[start:end]
    assert "degradeLabel(result.reason, label)" in failed_body, "失败分支必须经分类器"
    assert "state.degraded.push(label)" not in failed_body, "不得保留未分类的裸 push"
    assert js.count("degradeKind(result.reason)") >= 1
    # 分项降级（审核发现 D2）：每条降级记录必须携带**自身** kind，
    # 且渲染时必须逐条取 item.kind；不得用全屏汇总变量给所有条目打同一标记
    # （否则一页混装 404 与 503 时，503 条目会被误标为 not_integrated）。
    assert "function pushDegraded" in js, "必须提供携带自身 kind 的降级登记器"
    assert "state.degraded.push({ text: String(text), kind: normalized })" in js, (
        "降级条目必须以 {text, kind} 结构登记，才能逐条区分"
    )
    assert "const kind = (item && item.kind) || fallbackKind;" in js, (
        "渲染时必须逐条取 item.kind，不得用页面级汇总标记"
    )
    assert 'data-gw-degradation="${esc(kind)}"' in js, "分项降级必须带逐条结构化标记"


def test_task_center_service_unavailable_label_is_translated():
    """新增的服务不可用文案必须有 i18n 键，不得漏键回落成裸 key。"""
    js = _read(TASK_CENTER_JS)
    i18n = _read(TASK_CENTER_I18N)
    import re
    keys = sorted(set(re.findall(r"tr\('([^']+)'\)", js)))
    missing = [key for key in keys if ('"' + key + '"') not in i18n]
    assert not missing, "task-center.js 使用的 i18n 键缺失: " + str(missing)


def test_episode_pipeline_has_no_silent_catch_fallback():
    """episode-pipeline 不得用 `.catch(() => fallback)` 静默吞掉未接入错误。"""
    js = _read(EPISODE_JS)
    assert ".catch(() => ({ projects: DEMO_PROJECTS_FALLBACK }))" not in js, "项目回落不得静默"
    assert ".catch(() => ({ providers: [] }))" not in js, "providers 回落不得静默"
    assert "loadWithExplicitFallback" in js, "必须经由显式降级包装器回落"
    assert "recordDegradation" in js
    assert "data-gw-degradation" in js
    assert "未纳入当前切片" in js, "必须明说未接入"


def test_episode_pipeline_single_project_lookup_is_not_silent():
    """审核发现 D3：单项目查询原本是两级静默 .catch，必须接入显式降级。"""
    js = _read(EPISODE_JS)
    assert ".catch(() => api(" not in js, "不得保留静默链式 .catch 回落"
    marker = "if (state.projectId"
    start = js.find(marker)
    assert start != -1, "未找到单项目查询分支"
    end = js.find("const pObj =", start)
    assert end != -1, "未找到单项目查询分支闭合"
    body = js[start:end]
    assert "recordDegradation(" in body, "单项目查询失败必须登记显式降级"


def test_episode_pipeline_load_pipelines_is_not_silent():
    """审核发现 D5：主数据路径 loadPipelines() 原本 try/catch + console.warn 静默吞错。"""
    js = _read(EPISODE_JS)
    start = js.find("async function loadPipelines()")
    assert start != -1, "未找到 loadPipelines()"
    end = js.find("const activeIds", start)
    assert end != -1, "未找到 loadPipelines() 主体闭合"
    body = js[start:end]
    assert "loadWithExplicitFallback(" in body, "loadPipelines() 必须经显式降级包装器"
    assert "recordDegradation(" in body or "loadWithExplicitFallback(" in body
    assert "console.warn('获取项目流水线列表失败:'" not in body, (
        "不得保留 console.warn 式静默降级"
    )
    # 所有 api(...) 调用点必须有显式降级路径：统计 try/catch 静默吞错的残留
    assert "incoming = [];" not in body, "失败后不得静默置空而不登记降级"


def test_episode_pipeline_degradation_state_is_reset_on_reload():
    """审核发现 D1：load() 必须重置降级清单，否则端点接入后陈旧横幅仍报「未接入」。"""
    js = _read(EPISODE_JS)
    assert "function resetDegradations()" in js, "必须提供降级状态重置函数"
    reset_start = js.find("function resetDegradations()")
    reset_body = js[reset_start: js.find("async function loadWithExplicitFallback", reset_start)]
    assert "episodeDegradations.length = 0" in reset_body, "必须清空降级清单"
    assert "host.hidden = true" in reset_body, "必须隐藏降级横幅"
    load_start = js.find("async function load() {")
    assert load_start != -1, "未找到 load()"
    load_head = js[load_start: js.find("try {", load_start)]
    assert "resetDegradations()" in load_head, "load() 开始时必须重置降级状态"


def test_episode_pipeline_host_marker_is_deterministic():
    """审核发现 D2 同类：宿主元素标记不得「最后一次写入获胜」，必须按优先级确定。"""
    js = _read(EPISODE_JS)
    assert "host.dataset.gwDegradation = kind;" not in js, (
        "不得用最后一次写入覆盖宿主标记（结果随调用顺序变化）"
    )
    assert "episodeDegradations.some(i => i.kind === 'not_integrated')" in js


def test_episode_pipeline_degradation_host_is_outside_render_container():
    """降级容器必须**不是** #episodePipeline 的后代，否则会被 render() 的 innerHTML 重写抹掉。

    实测（主代理 2026-09-22，真实 Chrome + 真实 HTTP）：容器放在 #episodePipeline 内时
    `render()` 执行后 `#episodeDegradation` 从 DOM 消失，降级文案不可见。本守卫按标签配对
    判定父子关系，而非仅比较下标（下标比较对「放在容器内部」同样成立，是恒真守卫）。
    """
    html = _read(EPISODE_HTML)
    assert 'id="episodeDegradation"' in html
    start = html.find('<section id="episodePipeline"')
    assert start != -1, "未找到 #episodePipeline"
    end = html.find("</section>", start)
    assert end != -1, "未找到 #episodePipeline 的闭合标签"
    inside = html[start:end]
    assert 'id="episodeDegradation"' not in inside, (
        "降级容器不得位于 #episodePipeline 内部（render() 会整体重写该容器）"
    )
    assert html.find('id="episodeDegradation"') > end, "降级容器应作为稳定兄弟节点位于其后"


def test_episode_pipeline_degradation_banner_has_style():
    """降级横幅必须有可见样式，不得依赖 Tailwind 工具类（该页 Tailwind 为 CDN）。"""
    css = _read(STATIC_JS.parent / "css" / "pages" / "episode-pipeline.css")
    assert ".episode-degradation" in css
    assert 'data-gw-degradation="not_integrated"' in css


# ---------------------------------------------------------------------------
# Phase 9T：Tailwind 自托管快照守卫（用户 2026-09-22 裁决 O4）
#
# HTML 不得继续加载外部 Tailwind CDN；本地快照必须存在并与 MANIFEST 记录一致。
# 该自有 runtime 基于 Tailwind 3.4.17 和固定插件构建；15 个实际运行包的来源/通知见 vendor/LICENSES.md §5.2，页面不加载外部 CDN。
# ---------------------------------------------------------------------------

LOCAL_TAILWIND_SCRIPT = STATIC_DIR / "vendor" / "js" / "tailwindcss-cdn.js"
TAILWIND_MANIFEST = STATIC_DIR / "vendor" / "MANIFEST.md"
LOCAL_TAILWIND_SHA256 = "D11003B2F6FBE0CA79340ECD3349DFFE61E11A3FEB224B3FDFA3D24900F70E83"


def test_tailwind_is_self_hosted():
    """所有 HTML 页面必须引用本地 Tailwind 快照，不得引用外部 CDN。"""
    assert LOCAL_TAILWIND_SCRIPT.is_file(), "缺少本地 Tailwind 自托管快照"
    offenders = []
    for path in STATIC_DIR.rglob("*.html"):
        text = _read(path)
        if "https://cdn.tailwindcss.com" in text:
            offenders.append(path.relative_to(REPO_ROOT).as_posix())
    assert not offenders, "HTML 仍包含外部 Tailwind CDN 引用: " + str(offenders)
    refs = [
        path.relative_to(REPO_ROOT).as_posix()
        for path in STATIC_DIR.rglob("*.html")
        if "/static/vendor/js/tailwindcss-cdn.js" in _read(path)
    ]
    assert refs, "没有 HTML 页面引用本地 Tailwind 快照"


def test_tailwind_self_hosted_snapshot_hash_is_pinned():
    """本地 Tailwind 快照哈希必须与供应链清单保持一致。"""
    import hashlib

    digest = hashlib.sha256(LOCAL_TAILWIND_SCRIPT.read_bytes()).hexdigest().upper()
    assert digest == LOCAL_TAILWIND_SHA256
    manifest = _read(TAILWIND_MANIFEST)
    row = next(
        (line for line in manifest.splitlines() if "`js/tailwindcss-cdn.js`" in line),
        "",
    )
    manifest_hashes = re.findall(r"\b[A-Fa-f0-9]{64}\b", row)
    assert manifest_hashes, "MANIFEST.md 缺少 Tailwind 快照 SHA-256"
    assert digest == manifest_hashes[0].upper()


# ---------------------------------------------------------------------------
# Phase 12 收口：顶栏状态位 / 阶段汇总位 / 渲染总线 / 立项状态位「真实接线」守卫
#
# 背景：这些位置原先静态写死「未接入」（假缺口），Phase 12 已改为由真实端点驱动。
# 本组用例锁死新语义：初值只能是「读取中…」，真实读数/降级文案必须由共享模块
# 依据后端应答写入；同时不得回退成静态绿色在线灯或写死读数。
# 证据边界：静态守卫 != 真实浏览器 E2E（真机验证另见 wire_e2e / hdr_e2e）。
# ---------------------------------------------------------------------------
TELEMETRY_JS = STATIC_JS / "core" / "hardware-telemetry.js"


def test_workbench_pages_all_load_hardware_telemetry_module():
    """9 个 Workbench 页都必须引入共享遥测模块，否则 CPU/RAM/在线/阶段位无法真实接线。"""
    for name in WORKBENCH_PAGES:
        text = (PAGES_DIR / name).read_text(encoding="utf-8")
        assert "/static/js/core/hardware-telemetry.js" in text, f"{name} 必须引入共享遥测模块"
    js = _read(TELEMETRY_JS)
    assert "applyHeaderTelemetryLabels" in js, "遥测模块必须实现顶栏状态位同步方法"
    assert "data-gw-header-prefix" in js, "顶栏状态位必须按前缀真实改写"


def test_workbench_header_prefixes_are_not_static_not_integrated():
    """顶栏标题状态位不得静态写死「未接入」，初值只能是「读取中…」。"""
    prefixed = []
    for name in WORKBENCH_PAGES:
        text = (PAGES_DIR / name).read_text(encoding="utf-8")
        for match in re.finditer(r'data-gw-header-prefix="[^"]*"[^>]*>([^<]*)<', text):
            prefixed.append(name)
            body = match.group(1)
            assert "未接入" not in body, f"{name} 顶栏状态位不得静态写死未接入：{body!r}"
            assert "读取中" in body, f"{name} 顶栏状态位初值应为读取中：{body!r}"
    assert prefixed, "至少应有页面提供可被真实驱动的顶栏状态位"


def test_workbench_production_stage_readout_is_driven_by_real_endpoint():
    """制片页 STAGE 汇总位必须由真实 /api/episode-pipelines 阶段计数驱动。"""
    html = _read(PRODUCTION_HTML)
    assert 'id="prodStageReadout"' in html, "制片页必须有真实阶段汇总位"
    assert "未接入</span>" not in html.split('id="prodStageReadout"')[1][:160], (
        "阶段汇总位不得静态写死未接入"
    )
    js = _read(TELEMETRY_JS)
    assert "syncStageProgress" in js and "/api/episode-pipelines" in js, "必须消费真实阶段端点"
    assert "['prodStageReadout', '*']" in js, "汇总位必须注册到真实阶段探针"
    assert "__summary" in js and "已完成" in js, "汇总位必须由后端阶段状态聚合，不得伪造百分比"


def test_workbench_render_bus_status_is_driven_by_real_queue():
    """首页渲染总线状态位必须由真实任务队列驱动，不得静态写死未接入。"""
    html = _read(INDEX_HTML)
    assert 'id="renderBusStatus"' in html and 'id="renderBusLed"' in html, "首页必须有渲染总线状态位与灯"
    assert "渲染总线 · 未接入" not in html, "渲染总线不得静态写死未接入"
    js = _read(STATIC_JS / "core/task-queue.js")
    assert "renderBusStatus" in js, "共享任务队列必须改写渲染总线状态位"
    assert "state.loaded" in js and "进行中" in js, "总线必须按真实队列结果写入进行中/总数"
    assert "data-gw-degradation" in js, "队列失败路径必须带结构化降级标记"


def test_workbench_project_dispatch_status_is_driven_by_real_create():
    """立项状态位不得静态谎称已接入；只能按真实创建结果写入。"""
    html = _read(PROJECTS_HTML)
    assert 'id="projectDispatchStatus"' in html and 'id="projectDispatchLed"' in html, "立项页必须有调度状态位"
    segment = html.split('id="projectDispatchStatus"')[1][:200]
    assert "已接入" not in segment, "状态位初始不得断言已接入"
    assert "待命" in segment or "读取中" in segment, "状态位初值应为待命/读取中"
    js = _read(CONTROLLERS_DIR / "projects-controller.js")
    assert "function setDispatchStatus" in js, "必须由控制器按真实结果写状态位"
    assert "/api/asset-registry/projects" in js, "必须消费真实立项端点"
    create_anchor = js.find("const res = await fetch('/api/asset-registry/projects'")
    assert create_anchor != -1, "必须存在真实立项请求"
    region = js[create_anchor : create_anchor + 4000]
    assert "setDispatchStatus('ok'" in region, "创建成功后才能写已接入"
    assert "404" in region and "501" in region, "端点缺失必须走显式降级分支"


def test_workbench_project_counts_do_not_swallow_missing_backend():
    """T26 子项 4：项目计数不得用 .catch(() => null) 把后端缺失吞成普通空值。"""
    js = _read(CONTROLLERS_DIR / "projects-controller.js")
    start = js.find("async function fetchCounts()")
    assert start != -1, "未找到 fetchCounts()"
    body = js[start: js.find("function updateCountBadges", start)]
    assert ".catch(() => null)" not in body, "计数请求失败必须进入显式降级，不得静默变成 null"
    assert "classifyFetchFailure" in body, "非 2xx 必须按共享口径分类"
    assert "countsDegradation" in body, "失败必须留下结构化降级状态"


def test_workbench_home_asset_overview_distinguishes_empty_from_failure():
    """真实空集合必须给中性空态，不得把 200+空 渲染成「未接入（HTTP 0）」。"""
    js = _read(CONTROLLERS_DIR / "home-controller.js")
    assert "assetOverviewDegradation" in js, "必须有显式降级状态"
    assert "后端已接入并如实返回空集合" in js, "必须区分真实空集合与未接入"
    assert "(HTTP 0)" not in js, "不得把成功空集合伪造成 HTTP 0 未接入"


def test_workbench_agents_metrics_are_driven_by_real_chat_usage_and_config_only_load():
    """配置读取不触发生成；用户动作后的吞吐只取服务端 Chat metrics。"""
    html = (PAGES_DIR / "agents.html").read_text(encoding="utf-8")
    assert 'id="agentLatencyReadout"' in html and 'id="agentThroughputReadout"' in html
    assert 'id="agentProviderSelect"' in html and 'id="agentModelSelect"' in html
    assert "可能产生费用" in html and "不会自动重试" in html
    js = _read(CONTROLLERS_DIR / "agents-controller.js")
    assert "loadChatConfiguration" in js and "'/api/chat/config'" in js
    assert "applyAgentMetrics" in js and "tokens_per_second" in js
    assert "usage_source" in js and "unavailable_reason" in js
    assert "performance.now()" not in js, "吞吐/服务端耗时必须来自同一次后端请求metrics"
    assert "probeAgentProvider" not in js and "body: JSON.stringify({message: 'ping'" not in js
    assert "window.confirm(" in js and "requestInFlight" in js, "计费提示与重复提交保护必须真实实现"
    assert "configuration_changed" in js and "clearAgentMetrics" in js, "切换Provider/模型必须清除陈旧读数"

def test_workbench_pages_have_no_static_connected_assertion():
    """Workbench 静态页的 HTML 文本节点不得写死「已接入」断言（必须由真实接口结果驱动）。

    独立审核（GPT-6-Astra high）在 index.html 发现静态
    「AURA 核心神经元已接入制片总线」——未认证/接口失败时仍显示「已接入」，属伪造断言。
    本用例只检查 **HTML 文本节点**（先剥掉 <script>/<style> 块），避免把 JS 里的运行期
    状态映射误判为静态断言。
    """
    offenders = {}
    for path in sorted(PAGES_DIR.glob("*.html")):
        text = path.read_text(encoding="utf-8")
        # 先剥离脚本/样式块，只保留真正的 HTML 标记文本。
        stripped = re.sub(r"<script\b.*?</script>", "", text, flags=re.S | re.I)
        stripped = re.sub(r"<style\b.*?</style>", "", stripped, flags=re.S | re.I)
        hits = re.findall(r">([^<>{}]*已接入[^<>{}]*)<", stripped)
        hits = [h.strip() for h in hits if h.strip()]
        if hits:
            offenders[path.name] = hits
    assert not offenders, f"Workbench 静态 HTML 文本节点仍存在写死「已接入」断言: {offenders}"


def test_workbench_aura_bus_status_is_driven_by_read_only_chat_configuration():
    """首页总线只消费配置状态 GET；初始化不得发送可能计费的 POST。"""
    html = _read(INDEX_HTML)
    assert 'id="auraBusStatus"' in html, "首页必须有 AURA 配置状态位"
    assert "AURA 智能体总线 · 读取中…" in html, "状态位初值必须是读取中"
    assert "home-controller.js" in html, "首页必须引入控制器"
    js = _read(CONTROLLERS_DIR / "home-controller.js")
    assert "syncAuraBusStatus" in js, "必须实现 AURA 配置状态同步"
    anchor = js.find("async function syncAuraBusStatus()")
    end = js.find("function init()", anchor)
    probe = js[anchor:end]
    assert "'/api/chat/config'" in probe and "method: 'GET'" in probe, "初始化只读Provider配置摘要"
    assert "method: 'POST'" not in probe, "首页加载不得发送可能计费的模型请求"
    assert "auraBusStatus" in js, "必须写入 AURA 状态位"
    assert "data-gw-degradation" in probe, "不可用路径必须带结构化降级标记"

def test_workbench_project_card_compute_and_asset_cells_are_real():
    """项目卡「算力集群 / 资产规模」必须由真实端点驱动，不得静态写死未接入。"""
    js = _read(CONTROLLERS_DIR / "projects-controller.js")
    assert "data-gw-gpu-cluster" in js, "算力集群格必须可被真实 GPU 遥测驱动"
    assert "data-gw-asset-scale" in js, "资产规模格必须可被真实素材端点驱动"
    assert "syncAssetScale" in js, "必须实现真实资产规模同步"
    assert "/api/asset-registry/assets" in js, "必须消费真实素材登记端点"
    assert "total" in js, "必须消费后端返回的真实 total"


def test_workbench_project_card_readouts_run_after_render():
    """真实读数的写入点必须在 render() 之后。

    回归背景：`syncAssetScale()` 原先只在 `init()` 里调用一次，早于 `load() -> render()`
    重建卡片 innerHTML；浏览器实测「资产规模」格永远停留在静态初值「未接入」（假降级）。
    """
    js = _read(CONTROLLERS_DIR / "projects-controller.js")
    render_anchor = js.find("function render()")
    assert render_anchor != -1, "必须存在 render()"
    render_body = js[render_anchor:js.find("function selectProject", render_anchor)]
    assert "syncAssetScale()" in render_body, "render() 重建卡片后必须重跑资产规模真实读数"
    # 且不得把调用点只留在 init()：init() 早于 render()，会命中 0 个节点。
    init_anchor = js.find("function init()")
    init_body = js[init_anchor:js.find("if (document.readyState", init_anchor)]
    assert "syncAssetScale()" not in init_body, "初始化阶段读取会命中 0 个节点，必须在 render() 之后"
