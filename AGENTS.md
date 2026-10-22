# AGENTS.md — Jules 与编码 Agent 工作约定

本文档为 Jules 等 AI 编码 Agent 在 **神工坊 (Gods-Workbench)** 仓库中的工作约定与行为准则，涵盖环境配置、测试命令、硬性规则与红线、任务状态管理及 PR / Commit 提交约定。所有 Agent 在执行开发与维护任务时必须严格遵守。

---

## 1. 环境与配置约定 (Environment & Setup)

1. **Python 运行环境**
   - 项目要求 Python 3.11+ (推荐 Python 3.11 / 3.12)。
   - 依赖安装方式：
     ```bash
     python -m pip install -r requirements.txt -r requirements-dev.txt
     ```
2. **服务端口与运行模式**
   - 单进程 Web 服务统一固定默认监听端口为 **2077**。
   - 启动命令：
     - 开发沙盒模式：`python run.py`
     - 生产模式：`python run.py --prod`
3. **数据存储三态物理隔离 (Three-Tier Isolation)**
   - **开发沙盒态 (Dev)**：所有调试及临时数据只能落盘在仓库根目录下的 `.local/runtime-dev/`，该目录已被 `.gitignore` 忽略，不得提交至 Git（仅允许 `.local/README.md`）。
   - **生产态 (Prod)**：用户数据落盘于操作系统用户目录 `%LOCALAPPDATA%\GodsWorkbench\`，与 Git 源码树彻底解耦。
   - **测试态 (Test)**：自动化测试必须使用独立的临时隔离目录（`tmp_path`），测试结束后即刻清理，绝不污染开发沙盒或生产环境。

---

## 2. 测试命令 (Test Commands)

1. **全量与定向测试**
   - 运行全量测试套件：
     ```bash
     python -m pytest tests/
     ```
   - 运行定向契约/功能测试（例如）：
     ```bash
     python -m pytest tests/contracts/projects/
     ```
2. **洁净室卫生与结构门禁**
   - 在完成任何代码变更前/后，必须运行全套卫生与防污染测试：
     ```bash
     python -m pytest tests/hygiene/
     ```
3. **代码风格与静态检查**
   - 运行 Ruff 检查：
     ```bash
     ruff check .
     ```

---

## 3. 硬性规则与红线 (Hard Rules & Guardrails)

1. **洁净室零二进制受限资产红线 (Zero Binary Policy)**
   - 严禁向仓库提交未经许可的二进制文件、图片（`.png`, `.jpg`, `.webp` 等）、字体（`.ttf`, `.woff` 等）、压缩包（`.zip`, `.tar`, `.gz` 等）或可执行二进制文件（`.exe`, `.dll`, `.so` 等）。
   - 仅白名单精确路径（如 `start.bat` 及指定思源黑体）允许存在。所有新增素材/产物必须通过运行时生成或本地沙盒承载。
2. **全局命名与路径规范 (Global Naming Specification - GNS)**
   - **纯 ASCII 与无空格**：仓库工作树内所有文件和目录路径必须为纯 ASCII 字符，且严禁包含空格。
   - **前端控制器命名**：`web/js/controllers/` 下的前端控制器必须遵循 `*-controller.js` 命名规范。
   - **禁入临时路径**：严禁创建或提交包含 `tmp/`, `temp/`, `backup/`, `draft/`, `handoff/` 或 `v2` 独立记号的平行临时路径。
3. **CAS 乐观并发控制 (Compare-And-Swap)**
   - 核心数据写操作（项目编辑、归档、回收站及画布拓扑更新）必须携带并校验 `expected_version`，发生冲突时返回 409 状态码与清晰语义，杜绝静默覆盖或脏写。
4. **数据安全与凭据保护**
   - 严禁硬编码任何真实 API Key、Token、私钥、Cookie 或敏感凭据。测试与示例仅允许使用占位符或脱敏摘要。
5. **代码洁净度与协议隔离**
   - 严禁复制旧仓源码或泄漏旧仓私有实现。未准入协议（如 `PluginProtocol`）严禁在 `gw/` 实现代码中隐式呈现。

---

## 4. 任务状态与生命周期 (Task Lifecycle Management)

1. **三态流程**：任务生命周期明确分为 `Pending` -> `In Progress` -> `Completed`。
2. **每次修改后强制验证**：
   - 每次对文件进行新增、删除或修改操作后，**必须**使用只读工具（如 `read_file`、`list_files` 等）检查修改结果，确认变更按预期生效。
3. **证据闭环**：
   - 任何改动均须在本地环境提供自动化测试运行成功的实际控制台输出作为验收证据，不得凭假设宣称“完成”。

---

## 5. PR 与 Commit 提交约定 (PR & Commit Conventions)

1. **Commit Message 规范**：
   - 格式：`<type>(<scope>): <short description>`
   - 首行不超过 50 个字符，简短清晰地说明变更动机与内容（如 `feat`, `fix`, `docs`, `refactor`, `test`, `chore`）。
   - 必要时在空行后补充详细的上下文和影响面描述。
2. **原子化提交**：
   - 严禁使用 `git add -A` 一键混入无关或未审计的改动；提交内容必须精确针对当前任务目标。
3. **Pre-Commit 校验**：
   - 在提交前必须执行 Pre-Commit 检查并确保所有 `tests/hygiene/` 门禁测试 100% 通过。
