# C9 R2 收口共享契约

- **契约标识**：`C9-R2-CLOSURE-001`
- **状态**：`PROPOSED / IMPLEMENTATION INPUT`
- **任务**：QIN-185（C9-T1）
- **适用范围**：R2 项目、画布/镜头、任务、资产/版本、审阅和交付的收口、回收与恢复；覆盖 C9-AC1、C9-AC3、C9-AC4。
- **依据**：`.local/cuts/C9-2026-10-17.md`、`C9-TASKS.md`、`docs/contracts/R2-REVIEW-DELIVERY-CONTRACT.md`。
- **证据要求**：本文档以提交 SHA 和可复跑的 `fixture_id` 作为文档证据。真实 Provider、真实浏览器和跨账户证据由后续实现任务提供，本文档不单独宣称这些验收已通过。

## 1. 目的与边界

本契约为 C9-T2～T5 提供同一套 R2 收口字段和状态语义：页面与后端必须使用同一个阻塞描述、同一个 `next_action`、同一组稳定引用以及同一套回收恢复结果。收口是一个可回读的事实状态，不是“按钮已点击”或内存中的成功标记。

以下内容由 C8 契约作为唯一来源，本文件只引用其语义，不重新定义同名字段：

- 稳定领域 ID、`version_id`/`version`、`expected_version`；
- `review_id`、`decision_id`、`delivery_id` 及审阅/交付关系；
- `next_action` 的既有审阅值、标准错误外层和 HTTP 语义；
- 审阅版本绑定、批准失效和交付不可改绑规则。

本任务只新增本文件。接口实现、回收恢复、真实 Provider 调用、浏览器验收和准入元数据登记由对应任务完成；不得把本文档当成运行成功证据。批量制作、配音、合成、R3/R4 和外部分享不属于本契约。

## 2. 字段所有权与唯一命名

### 2.1 复用 C8 字段

| 字段 | 所有权 | C9 约束 |
| --- | --- | --- |
| `project_id`、`entity_id`、`episode_id`、`scene_id`、`shot_id`、`canvas_id` | C8 稳定标识 | 只引用真实对象；不得用标题、数组位置或 URL 替代 |
| `job_id`、`asset_id`、`version_id`、`version` | C8 生成/资产标识 | 未确认的远端任务或资产不得伪造 ID |
| `review_id`、`decision_id`、`delivery_id`、`artifact_id` | C8 审阅/交付标识 | 保留原记录；恢复不得新建同一事实的第二条记录 |
| `expected_version` | C8 CAS 字段 | 所有收口写操作都校验；冲突返回 409 `VERSION_CONFLICT` |
| `next_action` | C8 用户动作字段 | C9 继续使用此字段；不得另造 `next_step`、`action` 或 `closure_next_action` |

### 2.2 C9 新增字段

C9 只新增下面三个收口字段。它们与 C8 的审阅、交付字段并列出现，不替代或复制后者。

| 字段 | 类型 | 语义和约束 |
| --- | --- | --- |
| `closure_status` | 枚举 | 当前收口投影：`open`、`blocked`、`recovery_required`、`partially_recovered`、`closed` 或 `unknown` |
| `blocker` | 对象或 `null` | 非 `closed` 状态的主要阻塞事实；只能有一个主对象，其他关联由稳定 ID 回读，不增加平行阻塞字段 |
| `recovery` | 对象或 `null` | 删除/回收/恢复的事实快照；没有发生回收操作时为 `null` |

`blocker` 的固定形状如下。`subject` 只放适用的 C8 稳定 ID（例如 `asset_id`、`canvas_id` 或 `job_id`），不得使用通用 `id` 字段：

```json
{
  "code": "VERSION_CONFLICT",
  "scope": "asset",
  "subject": {
    "asset_id": "asset-0001",
    "version_id": "asset-0001-v002",
    "version": 2
  },
  "message": "当前资产版本已变化，请重新读取后再提交。",
  "retryable": false,
  "observed_at": "2026-10-17T10:00:00Z"
}
```

`code` 必须可定位原因，至少支持 `MISSING_CONTEXT`、`PERMISSION_DENIED`、`VERSION_CONFLICT`、`REFERENCE_NOT_FOUND`、`APPROVAL_INVALIDATED`、`PROVIDER_PENDING`、`PROVIDER_FAILED`、`PROVIDER_UNKNOWN`、`RECOVERY_PARTIAL` 和 `PERSISTENCE_FAILED`。`scope` 取 `project`、`canvas`、`shot`、`asset`、`job`、`review` 或 `delivery`。`retryable` 只表示同一事实是否允许按 `next_action` 重试，不表示可以重复远端副作用。

`recovery` 的固定形状如下：

```json
{
  "operation": "restore",
  "recycle_entry_id": "u_rcy_0001",
  "state": "partially_recovered",
  "preserved_subjects": [
    {"canvas_id": "canvas-0001"},
    {"asset_id": "asset-0001", "version_id": "asset-0001-v001"}
  ],
  "failed_scopes": ["delivery"],
  "idempotency_key": "restore:u_rcy_0001",
  "occurred_at": "2026-10-17T10:05:00Z"
}
```

`recycle_entry_id` 使用现有回收条目标识（例如资产 `rcy_NNNN` 或统一画布回收条目 `u_rcy_NNNN`），不得为同一条目再生成别名。`preserved_subjects` 只能列真实稳定 ID；`failed_scopes` 为空表示没有已知部分失败。重复恢复必须使用同一个 `idempotency_key` 返回同一事实，不能创建第二份资产、版本、审阅或交付。

### 2.3 `next_action` 唯一动作字段

`next_action` 仍是 C8 定义的唯一用户动作字段。C9 允许在其既有值之外使用下列收口动作；实现不得把这些动作写入 `next_step` 或其他别名：

| `next_action` | 使用条件 | 完成后的预期状态 |
| --- | --- | --- |
| `review` | 当前版本需要重新审阅，或旧批准已失效 | 新的精确版本审阅记录 |
| `regenerate_version` | 审阅要求修改且必须产生新版本 | 新 `version_id`，旧批准不再可用 |
| `restore_reference` | 稳定引用缺失但回收条目仍可恢复 | `recovery.state=completed` 或明确部分失败 |
| `retry_recovery` | 回收/恢复部分失败且可安全重试 | 同一 `recycle_entry_id` 的幂等结果 |
| `wait_for_provider` | 真实 Provider 仍在等待 | 保持等待，不登记成功资产 |
| `reconcile_unknown_job` | Provider 返回未知结果且没有可确认远端 ID | 先核对事实，禁止盲目重试副作用 |
| `reread_current_version` | CAS 或批准绑定过期 | 读取当前版本后重新提交 |
| `resolve_permission` | 当前主体没有资源权限 | 权限修复后重新读取，不泄露资源存在性 |
| `inspect_persisted_state` | 落盘或恢复结果无法确认 | 先核对持久化事实，不显示成功 |

`closure_status=closed` 表示本轮收口没有待处理动作；其余状态必须同时返回 `blocker` 和可执行的 `next_action`。未知、失败和部分恢复不能只显示通用“失败”，也不能伪造 `closed`。

## 3. 收口响应最小形状

收口字段作为现有项目、画布、审阅、资产或交付响应的附加投影返回，不创建新的平行资源或路由。示例只展示 C9 增量字段，C8 的完整版本与审阅/交付字段仍按原契约返回：

```json
{
  "closure_status": "blocked",
  "blocker": {
    "code": "APPROVAL_INVALIDATED",
    "scope": "delivery",
    "subject": {
      "delivery_id": "dlv-0001",
      "asset_id": "asset-0001",
      "version_id": "asset-0001-v001"
    },
    "message": "资产已有新版本，原批准不能用于交付。",
    "retryable": false,
    "observed_at": "2026-10-17T10:10:00Z"
  },
  "recovery": null,
  "next_action": "review"
}
```

刷新、重启和重新读取必须返回相同的稳定 ID、`closure_status`、阻塞原因和回收条目状态。页面只能渲染该投影，不能根据按钮点击或本地数组自行推导成功。

## 4. 状态和流转

| 当前状态 | 允许的结果 | 约束 |
| --- | --- | --- |
| `open` | `closed`、`blocked`、`recovery_required` | 有待处理事实时必须给出 `blocker` 和 `next_action` |
| `blocked` | `open`、`recovery_required`、`unknown` | 阻塞原因改变时替换同一主 `blocker`，不得静默清空 |
| `recovery_required` | `partially_recovered`、`closed`、`unknown` | 必须带 `recovery` 和 `recycle_entry_id` |
| `partially_recovered` | `recovery_required`、`closed`、`unknown` | 保留已恢复引用和失败域；重试不得换条目 |
| `unknown` | `blocked`、`open` | 必须先核对外部或持久化事实，禁止直接宣称成功 |
| `closed` | 新版本或新操作导致的 `open`/`blocked` | 关闭是当前快照，不冻结后续合法版本；历史记录仍可回读 |

任何状态变化都以对象或集合的 `expected_version` 做 CAS。版本冲突、权限失败和不可确认的远端结果必须在同一次响应中给出标准错误或上述收口投影，不能先返回 200 成功再异步改成失败。

## 5. R2 回收与恢复覆盖域

回收是关联快照，恢复是按稳定 ID 的可重复操作。每个域都要保留引用、版本和权限边界；子域部分失败时保留回收条目，以便安全重试。

| 覆盖域 | 必保留的稳定引用 | 回收要求 | 恢复要求 |
| --- | --- | --- | --- |
| 项目 | `project_id` | 保存项目状态和可见性快照；按项目 CAS 删除/回收 | 恢复同一 `project_id`，权限检查先于版本探测 |
| 画布 | `canvas_id`、`project_id`、画布版本 | 保存拓扑、节点和项目关系；使用 `expected_version` | 恢复同一画布和拓扑版本；冲突返回 `VERSION_CONFLICT` |
| 场景/镜头 | `episode_id`、`scene_id`、`shot_id`、`canvas_id` | 保留顺序和到任务/资产/审阅的引用 | 不创建临时镜头；引用缺失时 `REFERENCE_NOT_FOUND` + `restore_reference` |
| 资产/版本 | `asset_id`、`version_id`、`version`、回收条目 | 资产和不可变版本一起记录；不得把当前指针当版本快照 | 原 ID 原版本幂等恢复；新版本不得覆盖旧版本 |
| 任务 | `job_id`、来源和可确认状态 | 保存真实任务状态；未知任务不得伪造成功或自动重发 | 只恢复关联和可验证状态；未知结果走 `reconcile_unknown_job` |
| 审阅/交付 | `review_id`、`decision_id`、`delivery_id`、版本绑定 | 历史记录不可改绑；回收时保留审阅/交付快照 | 只能恢复同一引用；旧批准失效时必须重新审阅，不能自动交付 |

### 5.1 幂等、部分失败与回读

1. 删除、回收、恢复都使用稳定的 `recycle_entry_id` 和 `idempotency_key`；同一请求重放返回同一事实。
2. 恢复前先校验权限、回收条目存在性和 `expected_version`，再按依赖顺序恢复项目/画布/镜头、资产版本、任务关联、审阅/交付引用。
3. 任一子域失败时返回 `closure_status=partially_recovered`、已恢复稳定引用、`failed_scopes` 和 `next_action=retry_recovery`；不删除回收条目，不把整体响应标成 `closed`。
4. 回收条目不存在、已被其他主体占用或版本已变化时，保留标准错误外层和可定位 `blocker`；不得用新的临时 ID 绕过冲突。
5. 恢复完成后刷新或重启读取仍能沿 `project_id → canvas_id/shot_id → job_id → asset_id → version_id → review_id → decision_id → delivery_id` 回到同一组来源。

## 6. 版本变化与批准失效

C8 的版本绑定规则在 C9 中继续生效：`asset_id`、`version_id`、`version` 必须成对保存，批准和交付只能指向完全相同的版本。C9 对收口状态增加以下明确处理：

| 事实变化 | 旧记录处理 | C9 收口投影 |
| --- | --- | --- |
| 产生新的资产版本 | 旧批准和依赖它的未提交交付标记 `superseded` 或 `approval_invalidated`；历史引用不改写 | `closure_status=blocked`，`blocker.code=APPROVAL_INVALIDATED`，`next_action=review` |
| 恢复原版本且版本未变 | 保留原 `review_id`/`decision_id`/`delivery_id` 引用；恢复操作幂等 | 可回到 `open` 或 `closed`，不得新建同一事实的第二条记录 |
| 恢复时当前版本已变化 | 不覆盖当前对象，不沿用旧批准 | `VERSION_CONFLICT`，`next_action=reread_current_version` |
| 交付请求使用旧批准 | 拒绝创建或改写 `delivery_id` | `INVALID_STATE_TRANSITION` 或 `VERSION_CONFLICT`，下一步为 `review` |
| Provider 失败、等待或未知 | 不登记成功资产，不生成虚构 `job_id`/`asset_id` | 分别使用 `PROVIDER_FAILED`、`PROVIDER_PENDING`、`PROVIDER_UNKNOWN` 及对应动作 |

批准失效不会删除历史审阅或交付记录；它只阻止这些记录继续作为当前收口的成功依据。任何重新批准都必须绑定新的 `decision_id` 和当前精确版本。

## 7. 错误、权限与不可用状态

沿用 C8 的标准错误外层：`{"detail":{"code":"...","message":"..."}}`。C9 不新增平行错误包装；至少保持下列映射：

| HTTP | `code` | 收口处理 |
| --- | --- | --- |
| 401 | `UNAUTHENTICATED` | 不读取或探测资源版本 |
| 403 | `FORBIDDEN` | `blocker.code=PERMISSION_DENIED`，不泄露资源存在性 |
| 404 | `NOT_FOUND`、具体资源未找到码 | `blocker.code=REFERENCE_NOT_FOUND` 或恢复条目不可用 |
| 409 | `VERSION_CONFLICT` | 返回当前可见版本和 `next_action=reread_current_version` |
| 409 | `INVALID_STATE_TRANSITION` | 返回阻塞原因和合法的 `next_action` |
| 503 | 已有 Provider/审阅未接入码 | 保持 `blocked`/`unknown`，不返回成功资产或交付 |

错误和收口投影都必须可在日志或审计记录中用稳定 ID 关联；不得把密钥、Cookie、生产数据或远端凭据写入响应、契约证据或日志。

## 8. C9 验收映射与实施交接

| 验收 | 本契约要求 | 后续证据 |
| --- | --- | --- |
| C9-AC1 | 制片/分镜响应沿用真实项目、镜头、任务和资产 ID，并用唯一 `blocker`/`next_action` 表达未接入、失败和空上下文 | API JSON、浏览器截图、测试日志 |
| C9-AC3 | 项目、画布/镜头、资产、任务和交付关联可按稳定 ID 删除、回收、恢复并回读；CAS、幂等和部分失败可定位 | API JSON、`fixture_id`、测试日志 |
| C9-AC4 | 版本变化使旧批准失效；失败、等待、未知状态没有演示兜底，且下一步可执行 | API JSON、`fixture_id`、测试日志 |

C9-T2 和 C9-T3 只消费第 2～4 节字段，不在页面控制器中发明字段或成功状态。C9-T4 实现第 5 节回收/恢复覆盖和幂等/部分失败。C9-T5 实现第 6～7 节批准失效、阻塞和下一步 API。C9-T6 负责真实浏览器和证据汇总。本任务不修改实现、测试或准入登记文件。
