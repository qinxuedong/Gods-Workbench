# C8 R2 审阅与交付关联契约

- **契约标识**：`C8-R2-REVIEW-DELIVERY-001`
- **状态**：`PROPOSED / IMPLEMENTATION INPUT`
- **任务**：QIN-173（C8-T1）
- **适用范围**：C8 R2 单项目、单镜头、单版本闭环；覆盖 C8-AC2、C8-AC3、C8-AC4。
- **依据**：`.local/cuts/C8-2026-10-17.md`、`C8-TASKS.md`、`docs/contracts/ASSET-REVIEW-INTERFACE-CATALOG.yaml`。
- **证据要求**：契约变更以提交 SHA 和可复跑的 `fixture_id` 证明；真实 ComfyUI/RunningHub 与浏览器证据由生成和后端任务补齐。本文件不把文档断言当成真实运行成功。

## 1. 边界

本契约规定一个真实镜头版本从生成结果进入审阅、记录意见、批准或要求修改，再形成交付记录时必须保持的字段、引用、状态和错误语义。它只服务一个 `shot_id` 的一个 `version_id`，不扩展到批量镜头、配音、合成、R3/R4 或外部分享。

本任务只新增本契约文件。接口实现、真实远端调用、回收恢复、浏览器验收和 `tests/hygiene/fixtures/cleanroom_admission_metadata.json` 的准入登记由对应任务负责；不得以本文件替代这些验收。

## 2. 术语和稳定标识

### 2.1 领域标识

所有对象使用长、稳定、ASCII 安全的字段名和值。对象之间只通过稳定 ID 关联，不把标题、数组位置、时间戳或媒体 URL 当作主键。

| 字段 | 含义 | 约束 |
| --- | --- | --- |
| `project_id` | 项目 | 必填，项目权限边界 |
| `entity_id` | 统一实体引用 | 有实体层时必填，必须能回读到实体 |
| `episode_id`、`scene_id`、`shot_id` | 分集、场景、镜头 | C8 单镜头至少有 `shot_id`；分集或场景存在时一并保存 |
| `canvas_id` | 画布来源 | 有画布来源时保存，不能为审阅另造别名 |
| `job_id` | 真实生成任务 | 由生成任务产生；未知或没有远端 ID 时不得伪造成功 |
| `asset_id` | 统一资产 | 必须与资产注册表中的同一资产一致 |
| `version_id` | 不可变资产版本 | 每次版本变化生成新的稳定 ID；审阅和交付必须精确绑定 |
| `version` | 资产内单调版本号 | 与 `version_id` 同时保存，用于展示和 CAS 诊断 |
| `review_id` | 审阅记录 | 贯穿意见、决定和交付引用；同一审阅不可换绑版本 |
| `decision_id` | 审阅决定 | 每次批准或要求修改的不可变记录 |
| `delivery_id` | 交付记录 | 只对应一个已批准版本，刷新或重启后仍可回读 |
| `artifact_id` | 文本、设置和媒体等产物 | 只引用真实已登记产物，不内嵌伪造结果 |

不得新增短别名（例如 `id`、`job`、`asset`）替代上述字段，也不得为同一关系建立第二套 `session`、`review` 或 `delivery` 存储。当前资产审阅 API 使用的 `session_id=rs-NNNN` 是兼容层标识；在 C8 领域响应中它映射为同一条 `review_id`，不能再生成另一条审阅记录。

### 2.2 版本绑定

`version_id` 是版本的不可变身份，`version` 是该资产的单调显示号。二者必须成对出现，并且与 `asset_id` 一致。审阅评论、决定和交付记录都必须复制这三个字段，而不是只保存一个当前资产指针。

版本变更的结果是新的 `version_id` 和新的审阅目标。旧版本的批准不得沿用；旧决定和旧交付只能作为历史记录读取，并标为 `superseded` 或 `approval_invalidated`。任何缺少版本绑定的历史记录都不能直接成为 C8 交付依据。

## 3. 关联模型

### 3.1 审阅目标

审阅目标是一个镜头的一个确切版本，建议响应形状如下：

```json
{
  "review_id": "rs-0001",
  "project_id": "project-0001",
  "entity_id": "entity-0001",
  "episode_id": "EP01",
  "scene_id": "SC01",
  "shot_id": "SC01_SH001",
  "canvas_id": "canvas-0001",
  "job_id": "job-0001",
  "asset_id": "asset-0001",
  "version_id": "asset-0001-v001",
  "version": 1,
  "media_type": "image",
  "source": {
    "provider": "comfyui",
    "source_url": "http://local-comfyui.example/output/asset-0001-v001",
    "workflow_artifact_id": "artifact-workflow-0001"
  },
  "review_status": "pending_review",
  "next_action": "review",
  "expected_version": 1
}
```

`source` 只能描述实际生成路径和已登记产物。无法确认 Provider、任务或资产状态时返回明确的等待、失败或未知状态，不返回看似成功的占位对象。

### 3.2 意见和决定

评论锚定 `review_id + asset_id + version_id`。决定还必须包含 `decision_id`、`decision`、`next_action`、`reviewer_id` 和发生时间：

```json
{
  "review_id": "rs-0001",
  "comment_id": "rc-0001",
  "decision_id": "rd-0001",
  "project_id": "project-0001",
  "shot_id": "SC01_SH001",
  "asset_id": "asset-0001",
  "version_id": "asset-0001-v001",
  "version": 1,
  "comment": "保留构图，调整主体光照。",
  "decision": "changes_requested",
  "review_status": "changes_requested",
  "next_action": "regenerate_version",
  "reviewer_id": "account-0001",
  "expected_version": 1,
  "occurred_at": "2026-10-17T10:00:00Z"
}
```

`decision` 只允许 `approved` 或 `changes_requested`。前端可以显示“拒绝”作为 `changes_requested` 的文案，但持久化值和下一步动作必须明确。评论的 `open`/`resolved` 只表示评论处理状态，不得覆盖审阅决定。

### 3.3 交付记录

交付记录必须是可追溯的事实快照，保存创建时的全部关系：

```json
{
  "delivery_id": "dlv-0001",
  "project_id": "project-0001",
  "entity_id": "entity-0001",
  "episode_id": "EP01",
  "scene_id": "SC01",
  "shot_id": "SC01_SH001",
  "job_id": "job-0001",
  "asset_id": "asset-0001",
  "version_id": "asset-0001-v001",
  "version": 1,
  "review_id": "rs-0001",
  "decision_id": "rd-0002",
  "decision": "approved",
  "text_artifact_id": "artifact-text-0001",
  "settings_artifact_id": "artifact-settings-0001",
  "media_artifact_id": "artifact-media-0001",
  "delivery_status": "delivered",
  "created_at": "2026-10-17T10:05:00Z",
  "updated_at": "2026-10-17T10:05:00Z",
  "expected_version": 1
}
```

交付至少要能沿 `project_id → shot_id → job_id → asset_id → version_id → review_id → decision_id` 回到来源，并保存文本产物、设置产物和媒体产物的 `artifact_id`。没有真实产物或没有明确批准决定时，不能创建 `delivery_id`。交付记录创建后不可改绑到另一版本；修订必须产生新的版本、审阅和交付记录。

## 4. 状态和流转

### 4.1 C8 领域状态

审阅领域用以下投影表达用户可见状态：

```text
pending_review → changes_requested
pending_review → approved
changes_requested → pending_review       (新版本重新进入审阅)
approved → superseded                     (版本变化使批准失效)
```

只有 `approved` 且决定、资产、版本三者完全相等时，才允许创建交付记录。`changes_requested` 必须带 `next_action`，例如 `regenerate_version` 或 `revise_and_resubmit`；不得只显示“失败”而无法定位下一步。

### 4.2 现有资产审阅 API 的兼容映射

不新建路由族或第二套存储。现有 `/api/asset-reviews/sessions/{session_id}` 的兼容字段映射如下：

| 现有字段或状态 | C8 领域含义 | 约束 |
| --- | --- | --- |
| `session_id` | `review_id` | 同一稳定值；不得另建 `review_id` 记录 |
| `open` | `pending_review` | 目标尚未完成决定 |
| `delivered` | `pending_review` 的已提交兼容状态 | 仅表示审阅目标已提交，不能单独证明最终交付 |
| `approved` | `approved` | 必须同时带精确版本和 `decision_id` |
| `rejected` | `changes_requested` | 响应必须带 `next_action` |
| `delivery` | `DeliveryRecord` | 只有批准版本可创建；必须回读完整关联 |

兼容层可以继续输出旧字段以支持现有客户端，但 C8 新代码必须按本契约读取版本、决定和来源字段。兼容状态不得被解释成成功的真实生成或最终交付。

## 5. 并发控制和版本失效

1. 所有创建、修改、批准、要求修改和交付写操作都携带 `expected_version`。
2. 集合级并发使用当前 `revision`，对象级并发使用对象 `version`；两者冲突都返回 409 `VERSION_CONFLICT`，不得静默覆盖。
3. 在检查权限和目标版本后再执行写入。无权请求不得通过版本探测泄露资源存在或版本号。
4. 批准和交付必须原子地验证 `asset_id`、`version_id`、`version`、`review_id` 和 `decision_id`。任一不匹配返回 `VERSION_CONFLICT`，并在 `detail.message` 说明需要重新读取并审阅当前版本。
5. 版本变化后，旧批准和依赖它的未提交交付立即失效；不得以“当前资产”指针自动改写历史记录。
6. 同一 `delivery_id` 的重复提交必须幂等返回同一记录；若请求试图改绑其他版本，返回 `VERSION_CONFLICT`。

## 6. API 边界

沿用现有资产审阅路由，不新增平行路由：

| 操作 | 路由 | 必须携带或返回 |
| --- | --- | --- |
| 列出审阅 | `GET /api/asset-reviews/sessions` | 按项目和可见性过滤；返回稳定 `review_id/session_id` |
| 创建审阅目标 | `POST /api/asset-reviews/sessions` | `project_id`、`shot_id`、`asset_id`、`version_id`、`version`、来源 |
| 读审阅详情 | `GET /api/asset-reviews/sessions/{session_id}` | `session`、`comments`、`approvals`、`delivery`、`asset` 及完整版本关联 |
| 添加评论 | `POST /api/asset-reviews/sessions/{session_id}/comments` | 精确版本和 `expected_version` |
| 提交决定 | `PUT /api/asset-reviews/sessions/{session_id}/approval` | `decision`、精确版本、`expected_version`；返回 `decision_id` 和 `next_action` |
| 创建交付 | `POST /api/asset-reviews/sessions/{session_id}/delivery` | 只接受已批准的同一版本；返回完整 `DeliveryRecord` |
| 读取导出 | `POST /api/asset-reviews/deliveries/{delivery_id}/export` | 只能导出记录绑定的版本和产物 |

路径参数仍按现有 API 的 `session_id`、`delivery_id` 命名；响应和持久化关联必须遵守本契约的领域字段。不得通过新路径绕过项目权限、版本校验或批准校验。

## 7. 持久化和重启回读

- `review_id`、评论、`decision_id`、`delivery_id`、版本绑定和 `next_action` 必须落盘；不能只存在进程内缓存。
- `GET` 详情在刷新和进程重启后必须返回同一组稳定 ID、同一决定和同一交付关联。
- 交付记录是创建时的快照，资产后续产生新版本不会修改旧记录的 `version_id` 或 `decision_id`。
- 落盘失败返回明确错误，不返回“已交付”的伪成功响应。

## 8. 权限、错误和不可用状态

标准错误外层固定为 `{"detail":{"code":"...","message":"..."}}`：

| HTTP | `code` | 使用条件 |
| --- | --- | --- |
| 401 | `UNAUTHENTICATED` | 没有有效登录主体 |
| 403 | `FORBIDDEN` | 已认证但没有项目或资源权限 |
| 404 | `NOT_FOUND`、`SESSION_NOT_FOUND`、`DELIVERY_NOT_FOUND` | 资源不存在或按隐藏策略不可见 |
| 409 | `VERSION_CONFLICT` | `expected_version`、版本绑定或交付幂等键过期/不匹配 |
| 409 | `INVALID_STATE_TRANSITION` | 当前状态不允许该决定或交付 |
| 503 | `ASSET_REVIEW_NOT_INTEGRATED` | 审阅域未接入真实存储或真实 Provider |

远端任务失败、等待、未知结果和缺少真实来源必须分别暴露状态与 `next_action`。未知结果没有可确认的远端 ID 时，禁止重试不确定的外部副作用、禁止生成虚构 `job_id`/`asset_id`、禁止登记成功资产。

## 9. C8 验收映射

| 验收 | 契约要求 | 后续实测证据 |
| --- | --- | --- |
| C8-AC2 | 详情可读出精确版本、评论、决定、状态和 `next_action`；刷新后引用不变 | 真实浏览器截图、API JSON、`fixture_id` |
| C8-AC3 | 新版本生成新 `version_id`；旧批准失效；交付只引用明确批准版本；过期写入 409 | 后端契约测试、API JSON、`fixture_id` |
| C8-AC4 | 交付记录同时关联项目、文本/设置/媒体产物、镜头、任务、资产、版本、审阅和决定，并可重启回读 | 后端契约测试、持久化回读 API JSON、`fixture_id` |

真实 ComfyUI/RunningHub 运行和实际浏览器跨账户证据属于 C8-T2、C8-T6、C8-T7 的验收范围；本契约不能单独宣称 C8-AC1 或 C8-AC7 通过。

## 10. 实施交接

- C8-T2 提供真实 `job_id`、`asset_id`、`version_id` 和来源回跳；若生成结果为失败、等待或未知，审阅入口必须保留该状态。
- C8-T6 实现本文件的版本绑定、旧批准失效和交付完整回读；变更字段时先更新本契约并让依赖任务同步。
- C8-T5 负责删除、统一回收和恢复，恢复后仍需保留本契约定义的稳定引用。
- 本任务不修改现有接口目录；准入元数据仅按仓库门禁登记本文件，不复制一份近似契约。接口实现或集成任务仍须在同一字段定义下完成真实验收。
