# Gods-Workbench 测试体系说明

- **状态**：CURRENT / VERIFIED
- **适用范围**：`tests/` 目录下全部单元测试、契约测试、运行时隔离测试、卫生门禁及端到端浏览器测试。

## 1. 测试架构概览

本项目采用严格契约驱动与洁净室验证机制，测试套件涵盖千余项自动化测试用例，按领域划分为以下主要模块：

```
tests/
├── contracts/             # 8 大契约测试域（严格依据 docs/contracts/ 契约）
│   ├── assets/            # 素材库、注册表、审查、交付、PDF 导出、分享闭环
│   ├── auth/              # 本地账户、认证会话、限速防爆破、CLI 边界
│   ├── canvas/            # 神工画布拓扑、CAS 版本并发冲突、任务挂载
│   ├── observability/     # 可观测性、健康度、吞吐与单调时钟指标
│   ├── projects/          # 项目中心、阶段门（Gates）持久化、生命周期
│   ├── prompts/           # 提示词资产库、分类与条目生命周期
│   ├── settings/          # 系统配置、Provider 探针与存储策略
│   └── video/             # 视频任务调度、异步轮询与媒体元数据
├── hygiene/               # 洁净室防污染与结构卫生门禁
│   ├── test_cleanroom_hygiene.py        # 契约/夹具/文档准入元数据一致性
│   ├── test_structure_guardrails.py     # 根目录与路径白名单、无循环依赖
│   └── test_public_resource_integrity.py# 静态资源完整性与白名单字体
├── runtime/               # 三态运行环境隔离测试（Dev/Prod/Test 物理隔离）
├── smoke/ & e2e/          # 浏览器端真实交互冒烟与端到端验收测试
└── frontend/              # 前端现代 JavaScript 纯模块测试
```

## 2. 核心门禁与数据不变量

1. **三态绝对隔离**：测试态每例均使用独立的临时沙盒路径（`tmp_path`），在应用导入前完成环境变量隔离；绝对禁止读取或写入真实用户生产数据。
2. **CAS 乐观并发控制**：所有涉及状态与生命周期变更的测试均严格断言 `expected_version`，并发冲突返回 409（`VERSION_CONFLICT` / `CANVAS_VERSION_CONFLICT`）。
3. **统一错误语义**：未认证统一返回 401，权限不足返回 403，错误外层统一为 `{"detail": {"code": "...", "message": "..."}}`。
4. **洁净室准入约束**：所有引入 `docs/` 的文件必须在 `tests/hygiene/fixtures/cleanroom_admission_metadata.json` 登记准入。

## 3. 常用测试执行命令

```powershell
# 运行全量测试套件
pytest -v

# 仅运行卫生与结构安全门禁
pytest -v tests/hygiene/

# 运行特定契约域测试（例如项目中心）
pytest -v tests/contracts/projects/

# 运行三态路径与运行时隔离测试
pytest -v tests/runtime/
```

