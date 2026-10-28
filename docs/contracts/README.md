# Gods-Workbench 接口契约目录

- **状态**：CURRENT / VERIFIED
- **适用范围**：本目录存放经过审核的接口定义、数据形状、错误语义与兼容性约束。全部契约均由 `tests/contracts/` 自动化测试进行端到端闭环验证。

## 1. 契约规范与边界清单

- [`AUDIT-OUTBOX-LOCAL-SINK-CONTRACT.md`](AUDIT-OUTBOX-LOCAL-SINK-CONTRACT.md)：本地审计 Outbox 与持久收据契约（治理与审计流转）。
- [`LOCAL-ACCOUNT-AUTH-2026-09-26.md`](LOCAL-ACCOUNT-AUTH-2026-09-26.md)：本地 SQLite 账户模式登录、会话轮换、限速与防提权契约。
- [`OUTPUT-ACCESS-CONTRACT.md`](OUTPUT-ACCESS-CONTRACT.md)：通用产物安全下载与物理删除边界契约（重解析点与逃逸防护）。
- [`PROJECT-PERSISTENCE-GATES-CONTRACT.md`](PROJECT-PERSISTENCE-GATES-CONTRACT.md)：项目中心生命周期、单调预留 ID 与阶段门（Gates）实施契约。
- [`PROVIDER-PROTOCOL-BOUNDARIES.md`](PROVIDER-PROTOCOL-BOUNDARIES.md)：外部 Provider 适配器边界、吞吐度量与异步凭据隔离契约。
- [`REGISTRY-INDEX-JOBS-CONTRACT.md`](REGISTRY-INDEX-JOBS-CONTRACT.md)：后台素材索引异步任务、有界执行器与状态机契约。
- [`TEXT-PDF-EXPORT-CONTRACT.md`](TEXT-PDF-EXPORT-CONTRACT.md)：标准文本与清单 PDF1.7 纯自研导出排版契约。

## 2. 接口目录清单 (YAML Interface Catalogs)

包含 `AUTH-INTERFACE-CATALOG.yaml`、`PROJECTS-HUB-INTERFACE-CATALOG.yaml`、`CANVAS-INTERFACE-CATALOG.yaml`、`AGENT-WORKBENCH-INTERFACE-CATALOG.yaml`、`ASSET-LIBRARY-INTERFACE-CATALOG.yaml`、`ASSET-REGISTRY-INTERFACE-CATALOG.yaml`、`VIDEO-TASKS-INTERFACE-CATALOG.yaml`、`PROMPT-LIBRARY-INTERFACE-CATALOG.yaml`、`TEAM-MESSAGES-INTERFACE-CATALOG.yaml`、`SETTINGS-INTERFACE-CATALOG.yaml` 等机器可读接口目录。

Agent-host 目录定义隔离 Agent 到 Workbench 的 HTTP 适配及共享任务查询分支；宿主负责人已签认采用选项 B（扩展宿主 TaskStatus），将 `waiting_review`、`paused` 纳入顶层 7 态任务模型并实现 1:1 精确投影。共享 `/api/jobs/{job_id}` 路径的通用任务契约仍由画布目录所有。

## 3. 门禁要求

1. 核心 ID 统一为 `project_id`、`canvas_id`、`entity_id`、`job_id`、`asset_id`。
2. 错误响应统一包装为 `{"detail": {"code": "...", "message": "..."}}`；未认证 401，权限不足默认 403，明确登记的资源隐藏端点可将未知与越权统一为 404，CAS 版本冲突 409。
3. 所有契约文件必须在 `tests/hygiene/fixtures/cleanroom_admission_metadata.json` 中登记准入。

