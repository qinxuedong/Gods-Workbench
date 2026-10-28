# Gods-Workbench 黄金夹具规范与清单

- **状态**：CURRENT / FROZEN
- **适用范围**：`docs/fixtures/` 目录下全部基线数据与测试夹具
- **目标**：为系统行为规范与接口契约提供确定性、最小化、不可变的黄金验证输入与预期输出。

## 1. 夹具资产概览

当前目录共包含 54 项标准 JSON 与 `.godmap` 黄金夹具，由索引清单统一编目：

- **核心清单**：`GOLDEN-FIXTURE-MANIFEST.json`（定义各模块夹具的 ID、用途、校验哈希与关联契约）。
- **主要分域夹具**：
  - **项目中心**：`projects-hub-create-request.json`、`projects-hub-list-active.json`、`projects-hub-update-conflict-409.json` 等。
  - **神工画布**：`canvas-workflow-minimal.godmap`、`canvas-workflow-minimal.json`、`canvas-save-conflict-409.json`、`canvas-task-accepted-202.json` 等。
  - **素材注册表与库**：`asset-library-empty.json`、`asset-library-conflict-409.json`、`asset-library-with-library-category.json` 等。
  - **提示词资产库**：`prompt-library-empty.json`、`prompt-library-conflict-409.json`、`prompt-library-with-empty-category.json` 等。
  - **系统配置与观测**：`observability-health-truthful.json`、`settings-providers-empty.json`、`settings-storage-conflict-409.json` 等。
  - **准入与边界夹具**：`phase11-b1-boundary.json` 至 `phase11-b9-boundary.json` 系列。

## 2. 洁净室门禁与安全约束

1. **零二进制原则**：夹具仅允许纯文本 JSON 或结构化文本；绝对禁止包含图片、音频、视频、压缩包或未授权字体。
2. **脱敏与无害化**：所有夹具数据均为确定性模拟载荷，不包含任何真实用户凭据、敏感密钥或实际生产内容。
3. **不可变基线**：所有夹具文件必须在 `tests/hygiene/fixtures/cleanroom_admission_metadata.json` 的 `accepted_roots` 中完整登记，未经准入审核禁止擅自增删或改写。

