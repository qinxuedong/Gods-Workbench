# R1 文本与媒体最小生产闭环及制片/分镜消费契约

- **契约标识**：`R1-TEXT-MEDIA-LOOP-CONTRACT`
- **状态**：CURRENT / VERIFIED
- **依据与阶段**：Roadmap R1（两条最小真实生产闭环）；关联用户故事 US-01、US-03、US-04、US-05、US-07、US-17A、US-20A；承接 C7 切片（C7-AC1、C7-AC2、C7-AC3），解除制片页（QIN-180）与分镜页（QIN-181）对前置实现的隐性依赖。
- **核心定位**：规范 Gods' Workbench 在 R1 阶段已打通的文本最小生产闭环与媒体最小生产闭环接口协议、数据结构与持久化不变量，并明确制片工作台与分镜矩阵消费真实项目、产物与媒体资产的契约标准与退化空态规范。

---

## 1. 核心不变量与全局约束

1. **全局长 ID 规范**：
   - 全链路严禁使用缩写别名或自增短序号。统一采用稳定 ASCII 字符串长 ID：
     - 项目：`project_id`
     - 画布：`canvas_id`
     - 任务：`job_id`
     - 资产：`asset_id`
     - 文本运行实例：`run_id`
     - 文本产物：`artifact_id`
     - 剧集：`episode_id`
     - 场次：`scene_id`
     - 镜头：`shot_id`
   - 物理文件落盘一律采用安全 ID 或确定性哈希，严禁包含空格、中文或临时施工标记（如 `tmp`, `draft`, `v2`）。

2. **CAS 乐观并发控制**：
   - 涉及项目元数据、资产状态、画布拓扑等一切写操作，必须携带 `expected_version` 参数；
   - 版本不匹配时必须返回 HTTP 409，统一错误包格式：
     ```json
     {
       "detail": {
         "code": "VERSION_CONFLICT",
         "message": "当前版本与预期版本不一致，已拒绝静默覆盖"
       }
     }
     ```

3. **统一认证与授权包**：
   - HTTP 401：未提供或会话凭证失效，统一错误码 `UNAUTHENTICATED`；
   - HTTP 403：当前主体对指定项目或资源无读/写权限，统一错误码 `FORBIDDEN`；
   - HTTP 404：目标资源不存在或非本主体可见，统一错误码 `PROJECT_NOT_FOUND` / `NOT_FOUND`。

4. **零伪造与真实生产准则**：
   - 严禁在代码中保留演示数据、写死占位符或伪造成功；
   - 无法确认远端执行状态时，必须明确返回终态 `outcome_unknown` 与错误码 `OUTCOME_UNKNOWN_NO_REMOTE_ID`，交由人工确认与放弃，禁止静默重试。

---

## 2. 文本最小生产闭环契约 (C7-AC1)

文本链覆盖“项目绑定 → 六阶段文学 Agent (`script-03`) → 候选审核与交互澄清 → 精确版本文本产物 → 项目关联与导出”。

### 2.1 Agent 服务就绪探测
- **接口**：`GET /api/agent/config`
- **认证**：Session / Local Account
- **响应格式**：
  ```json
  {
    "ready": true,
    "default_provider_id": "provider-fake",
    "profiles": [
      {
        "provider_id": "provider-fake",
        "model": "fake-text-model",
        "description": "六阶段文学创作模型"
      }
    ]
  }
  ```
- **前端适配规则**：
  - 当 `ready === true` 且 `profiles.length > 0` 时，`script-03` Agent 状态标注为 `configured`，标题为 `六阶段文学创作与版本产物`；
  - 当不可用或未配置时，状态标注为 `not_integrated`。

### 2.2 文本运行实例与项目绑定
- **发起运行接口**：`POST /api/agent/runs`
- **请求体**：
  ```json
  {
    "project_id": "proj_1234567890ab",
    "agent_id": "script-03",
    "prompt": "创作一段高潮剧情分镜文学",
    "stage": "outline"
  }
  ```
- **响应体**：
  ```json
  {
    "run_id": "run_01928374abcd",
    "project_id": "proj_1234567890ab",
    "status": "awaiting_approval",
    "current_stage": "outline",
    "created_at": 1729135000000
  }
  ```

### 2.3 澄清与候选审核
- **澄清交互**：`POST /api/agent/runs/{run_id}/clarifications`，提交回答推进至下一阶段；
- **候选审核**：`POST /api/agent/runs/{run_id}/approvals`
- **请求体**：
  ```json
  {
    "action": "approve",
    "selected_candidate_index": 0,
    "notes": "采纳候选一并固化版本"
  }
  ```

### 2.4 精确版本文本产物与项目查询
- **项目运行列表接口**：`GET /api/projects/{project_id}/runs`
- **权限校验**：校验当前主体对 `project_id` 的读权限（通过 `project_membership` 或 `owner_key` 鉴权）；跨主体访问返回 403 `FORBIDDEN`。
- **文本产物数据结构 (Text Artifact)**：
  ```json
  {
    "artifact_id": "art_txt_9876543210ef",
    "project_id": "proj_1234567890ab",
    "run_id": "run_01928374abcd",
    "stage": "storyboard_script",
    "version": 1,
    "title": "第 1 场 追逐戏 分镜台本文学稿",
    "content_markdown": "# 第 1 场\n\n[镜头 1] 街道全景。霓虹倒影在雨水洼中。\n[镜头 2] 黑色机车疾驰入画。",
    "created_at": 1729135200000
  }
  ```
- **项目上下文导航**：
  - 前端控制台渲染 `#agentProjectContextBar`，标明当前项目名称与 `project_id`，并提供返回入口：
    `<a id="agentBackToProjectLink" href="/static/pages/projects.html?project_id=...">回到项目</a>`。

---

## 3. 媒体最小生产闭环契约 (C7-AC2)

媒体链覆盖“受控工作流参数 → ComfyUI 或 RunningHub 执行 → 持久任务跟踪 → 统一资产登记 → 来源上下文回跳”。

### 3.1 任务提交与受控参数映射
- **接口**：`POST /api/god_workflow/tasks`
- **请求体**：
  ```json
  {
    "workflow_id": "wf_master_portrait_v1",
    "target": "comfy",
    "node_info_list": [
      {
        "node_id": "6",
        "field_name": "text",
        "value": "masterpiece, cinematic lighting, neon alley"
      },
      {
        "node_id": "3",
        "field_name": "seed",
        "value": 42
      }
    ],
    "source_context": {
      "project_id": "proj_1234567890ab",
      "canvas_id": "cvs_876543210fed",
      "shot_id": "shot_001"
    }
  }
  ```
- **响应**：`202 Accepted`
  ```json
  {
    "job_id": "job_media_20261017_0001",
    "status": "queued",
    "poll_hint": "/api/god_workflow/tasks/job_media_20261017_0001"
  }
  ```
- **跨租户防护**：若请求体指定 `project_id` 或 `canvas_id`，必须在任务创建前校验当前主体归属，非本项目资源返回 403 或 404，禁止越权污染。

### 3.2 任务轮询与终态枚举
- **接口**：`GET /api/god_workflow/tasks/{job_id}`
- **任务状态枚举**：
  - `queued`：排队中
  - `running`：执行中
  - `completed`：执行成功并交付产物
  - `failed`：执行失败
  - `cancelled`：已主动取消
  - `outcome_unknown`：远端请求结果未知（网络断连或无远端任务 ID），停止自动轮询，待人工确认
- **终态响应体示例**：
  ```json
  {
    "task_id": "job_media_20261017_0001",
    "status": "completed",
    "target": "comfy",
    "source_context": {
      "project_id": "proj_1234567890ab",
      "canvas_id": "cvs_876543210fed",
      "shot_id": "shot_001"
    },
    "outputs": [
      {
        "output_id": "out_001",
        "asset_id": "ast_unified_media_20261017_01",
        "url": "/api/assets/ast_unified_media_20261017_01/content",
        "media_type": "image",
        "registered": true,
        "filename": "render_v001.png"
      }
    ],
    "created_at": 1729136000000,
    "completed_at": 1729136015000
  }
  ```

### 3.3 统一资产中枢自动入库与回跳契约
- **入库原则**：
  - 工作流产生的每一份有效交付物（图像/视频）由服务端自动在统一资产库登记（Asset Registry & Asset Library），分配唯一 `asset_id`；
  - 产物元数据持久化关联 `job_id`、`project_id`、`canvas_id`，无需用户手工复制 ID 或按文件名回连。
- **前端输出渲染约定**：
  - 产物渲染包含统一资产徽标与资产详情抽屉跳转入口：
    ```html
    <span class="wf-task-asset-badge" data-asset-id="ast_unified_media_20261017_01">统一资产: ast_unified_media_20261017_01</span>
    <a class="mini-link-btn" data-action="view-asset" href="/static/pages/assets.html?asset_id=ast_unified_media_20261017_01" target="_blank">查看资产详情</a>
    ```
  - 任务面板底部渲染来源回跳区：
    ```html
    <div class="wf-source-provenance" data-source-provenance>
      <a class="mini-link-btn" data-action="return-to-source-project" href="/static/pages/projects.html?project_id=proj_1234567890ab">返回来源项目</a>
      <a class="mini-link-btn" data-action="return-to-source-canvas" href="/static/pages/canvas.html?canvas_id=cvs_876543210fed&project_id=proj_1234567890ab">返回来源画布</a>
    </div>
    ```

---

## 4. 制片页与分镜页真实上下文消费契约 (C7-AC3)

本节明确解除 QIN-180（制片页真实上下文）与 QIN-181（分镜页真实上下文）的实现边界，确立严格真实数据消费与空态降级规范。

### 4.1 项目上下文解析准则 (Context Resolution)
所有页面控制器必须遵循统一的上下文解析降级链：
1. 优先读取当前页面 URL 参数 `?project_id=...`；
2. 若 URL 未提供，回退读取 `window.localStorage.getItem('workspace_project_id')`；
3. 若均未获取到有效非空字符串，判定为**上下文缺失**，触发标准未接入降级，严禁回退至静态写死样例或伪造虚假项目。

### 4.2 制片页控制器契约 (`production-controller.js` / QIN-180)
1. **项目读取与权限校验**：
   - 调用 `GET /api/projects/{project_id}` 验证项目存在性与访问权限；
   - 权限不足返回 403，项目不存在返回 404；
   - 校验成功后更新页面标题：`#currentProjectDisplayTitle` 呈现真实项目名称。
2. **剧本文本与场次镜头加载**：
   - 读取项目文本产物，映射至剧本与场次目录；
   - 镜头调参推子台与视窗 (`#currentShot`) 必须绑定真实镜头元数据：
     ```javascript
     {
       id: "shot_001",
       name: "镜头 01 · 霓虹追逐",
       imageUrl: "/api/assets/ast_unified_media_20261017_01/content",
       focal: 50,
       prompt: "masterpiece, cinematic lighting, neon alley",
       asset_id: "ast_unified_media_20261017_01"
     }
     ```
3. **退化与空态契约**：
   - 当 `projectId` 缺失或项目接口返回错误时，调用 `renderNotIntegratedState()`：
     - `#currentProjectDisplayTitle` 呈现 `项目数据未接入 · 剧集制片工坊`；
     - 镜头列表与推子台必须渲染带有 `data-gw-degradation="not_integrated"` 的标准中文空态卡片；
     - **严禁**保留或回退至任何写死的演示剧集/镜头。

### 4.3 分镜页控制器契约 (`storyboard-controller.js` / QIN-181)
1. **分镜网格加载**：
   - 调用项目镜头/分镜接口获取真实分镜数组 `state.shots`；
   - 每个分镜项模型：
     ```javascript
     {
       id: "shot_001",
       scene_id: "sc_01",
       name: "镜头 01",
       imageUrl: "/api/assets/ast_unified_media_20261017_01/content",
       asset_id: "ast_unified_media_20261017_01",
       prompt: "masterpiece, cinematic lighting, neon alley",
       status: "ready"
     }
     ```
2. **退化与空态契约**：
   - 当 `state.shots` 为空、未接入或读取异常时，调用 `renderNotIntegratedState()`：
     - `#storyboardTitle` 明确设置为 `分镜数据未接入 (共 0 个分镜)`；
     - `#storyboardGrid` 插入标准化降级节点：
       ```html
       <div class="col-span-full bay-inset p-8 text-center text-slate-500 font-mono gw-type-body-sm rounded-xl" data-gw-degradation="not_integrated">
         <i data-lucide="unlink" class="w-6 h-6 mx-auto mb-1.5 text-slate-600"></i>
         <div>分镜数据未接入</div>
       </div>
       ```
     - 标题分镜数必须如实反映 `0`，禁止宣称有内容。

---

## 5. 验收黄金夹具与断言清单 (Verification Proof)

### 5.1 黄金夹具标识 (Fixture IDs)
- **项目夹具**：`fix_c7_project_closed_loop_01`
- **文本运行夹具**：`fix_c7_agent_text_run_01`
- **媒体任务夹具**：`fix_c7_workflow_task_media_01`
- **统一资产夹具**：`fix_c7_unified_asset_media_01`

### 5.2 核心测试与证明类型 (Proof Types)
1. **C7-AC1（文本闭环）**：
   - Proof: `tests/contracts/agent/test_agent_integration.py`
   - 断言：项目存在性校验、六阶段状态流转、候选审核确认、`artifact_id` 精确绑定、跨租户 403 阻断。
2. **C7-AC2（媒体闭环）**：
   - Proof: `tests/contracts/media_double/test_c7_t2_media_production_loop.py`
   - 断言：受控参数正确下发、持久任务稳定轮询、统一资产入库分配 `asset_id`、来源上下文（`project_id`/`canvas_id`）回跳链接生成。
3. **C7-AC3（制片/分镜消费）**：
   - Proof: `tests/frontend/c6-t9-storyboard-empty-state.test.mjs` 及 QIN-180/181 前端自动化测试
   - 断言：URL 优先上下文解析、真实项目数据绑定、无数据时展示 `data-gw-degradation="not_integrated"` 与 `0 个分镜`、无演示数据兜底。
