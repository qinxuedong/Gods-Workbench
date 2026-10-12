# Gods-Workbench 全局命名系统规范指南 (GNS-2026)

- **规范代号**：`GNS-2026`（Gods-Workbench Global Naming Standard）
- **状态**：CURRENT / MANDATORY
- **适用范围**：全仓库代码、文件、目录、API 路由、数据库、多模态产物、分集与分镜、版本控制与日常开发
- **约束力**：**全仓库最高级别工程约束之一**。所有智能代理（Agent）、开发人员及自动化脚本必须严格遵守，不得随意违背、降级或创建平行规则。
- **核心理念**：简洁至上（KISS）、第一性原理分析、双层解耦（存储与语义分离）、零非 ASCII 安全落盘、防熵增硬门禁。

---

## 1. 核心定位与第一性原理

### 1.1 双层解耦映射模型 (Dual-Layer Decoupling)
在本地优先、AI 赋能的音视频创作平台中，资源产物面临双重诉求：
1. **底层文件系统安全性**：需要防路径遍历、防字符集乱码、防并发覆盖、防多操作系统冲突，要求**物理文件名唯一、紧凑、纯 ASCII、确定性**；
2. **创作者与工业化协同**：创作者、导演、剪辑师和专业 NLE 软件（Premiere、DaVinci Resolve 等）需要从文件名直观获知**所属项目、分集、场次、镜头、版本与工种**。

因此，GNS 建立“存储层物理命名”与“业务语义/导出命名”的双层解耦映射架构：

```
┌─────────────────────────────────────────────────────────────────────────┐
│                     GNS-2026 双层解耦映射模型                             │
└─────────────────────────────────────────────────────────────────────────┘
                                     │
           ┌─────────────────────────┴─────────────────────────┐
           ▼                                                   ▼
【底层：存储持久化命名 (Storage Name)】            【表层：业务语义与导出命名 (Semantic/Export Name)】
 · 物理路径：GW_DATA_DIR / GW_VIDEO_DATA_DIR        · 资产看板展示名 / NLE 导出文件名
 · 格式：<asset_id>[_<variant>].<ext>              · 格式：[PROJ_][EPxx_][SCxx_][SHxxx_]<Name>_<vxxx>[_<mod>].<ext>
 · 特性：单调唯一 / UUIDv5 / 防冲突 / 纯 ASCII       · 特性：高度自解释 / 工业化分镜 / 3位补齐版本
           ▲                                                   ▲
           └────────────────── 统一元数据中枢 ─────────────────┘
                      (asset_id, project_id, job_id)
```

### 1.2 跨平台工程铁律（零容忍红线）
1. **100% 纯 ASCII 字符集**：所有提交至 Git（以及 `.local/` 持久化）的文件名、目录名**严禁包含任何中文字符、全角标点、Emoji 或空格**（即使是中文技术文档，文件名也必须使用纯英文缩写或拼音，如 `USER-STORIES.md`、`TYPOGRAPHY-SYSTEM-SPEC.md`，严禁 `USER-短剧.md`，避免跨平台 UTF-8/GBK 乱码损坏）；
2. **严禁包含 Windows 保留设备名**：禁止使用 `CON`, `PRN`, `AUX`, `NUL`, `COM1`~`COM9`, `LPT1`~`LPT9` 及其带扩展名变体；
3. **严禁路径遍历与非法字符**：禁止包含 `..`、双斜杠、冒号、星号、问号、引号、尖括号、竖线及尾部点或空格；
4. **严禁临时施工标记（已有硬门禁）**：
   - 严禁包含：`tmp`, `temp`, `backup`, `draft`, `drafts`, `handoff` 路径段；
   - 严禁包含：`phase[0-9]+`（阶段标记）、`_b[0-9]+`（批次后缀）、独立的 `v2/v3` 标记；
5. **严禁未审二进制入库**：除白名单内的 3 个思源黑体字体（Bold/Medium/Normal.otf）外，所有媒体文件绝不进入 Git 仓库，全部隔离在运行时数据根中。

---

## 2. 项目与文件系统命名规范

### 2.1 顶级根目录白名单
根目录只允许 `tests/hygiene/test_structure_guardrails.py` 严格登记的顶级项目：
- 核心目录：`.github/`, `.local/`, `gw/`, `web/`, `docs/`, `scripts/`, `tests/`, `tools/`；
- 根级文件：`.gitattributes`, `.gitignore`, `AGENTS.md`, `LICENSE`, `THIRD_PARTY_NOTICES.md`, `Changelog.md`, `README.md`, `README_EN.md`, `requirements.txt`, `requirements-dev.txt`, `requirements.lock`, `requirements.lock.hashes`, `start.bat`, `run.py`。
- **任何新增顶级目录必须通过治理批准并在门禁中显式登记**。

### 2.2 后端 Python 体系 (`gw/`)
- **领域包与目录**：全小写下划线 `snake_case`（如 `gw/god_canvas/`, `gw/god_workflow/`, `gw/projects_hub/`, `gw/video_tasks/`）；
- **模块文件**：全小写下划线 `snake_case.py`（如 `models.py`, `service.py`, `credentials.py`, `runtime_paths.py`）；
- **路由文件**：单一领域由单一路由承载，命名为 `routes_<domain>.py`（位于 `gw/api/`）或 `routes.py`（位于领域包内）。严禁为同一业务领域新增平行路由文件；
- **分层职能后缀**：
  - 数据模型：`models.py` / `schemas.py`
  - 业务编排：`service.py`
  - 数据仓储与持久化：`repository.py` / `store.py` / `registry.py`
  - 核心错误与异常：`errors.py`

### 2.3 前端 Web 体系 (`web/`)
- **HTML 页面与嵌入件**：全小写短横线 `kebab-case.html`：
  - 主页面：`web/pages/{projects,agents,assets,workshop,storyboard,settings,workflow-workbench}.html`
  - 嵌入件：`web/embeds/{api-settings,asset-manager,canvas-list,episode-pipeline,task-center,governance}.html`
- **样式表**：全小写短横线 `kebab-case.css`（如 `web/css/base/hardware-tokens.css`, `web/css/pages/workflow-workbench.css`）；
- **JavaScript 核心与模块**：全小写短横线 `kebab-case.js`（如 `workbench-shell.js`, `floating-dismissal.js`）；
- **控制器统一命名**：`web/js/controllers/` 下所有页面控制器文件**必须以 `-controller.js` 结尾**（如 `projects-controller.js`, `settings-controller.js`, `agents-controller.js`）。

### 2.4 文档与夹具体系 (`docs/`, `.local/docs/`)
- **规范、契约与指南文档**：全大写短横线 `UPPER-KEBAB-CASE.md` 或 `.yaml`（如 `GLOBAL-NAMING-SPEC.md`, `DESIGN-SYSTEM-GUIDE.md`, `BUTTON-SYSTEM-SPEC.md`, `AUTH-INTERFACE-CATALOG.yaml`）；
- **测试夹具与数据样本 (`docs/fixtures/`)**：全小写短横线 `kebab-case.json`（如 `asset-library-empty.json`，唯一例外为全大写清单 `GOLDEN-FIXTURE-MANIFEST.json`）；
- **本地用户文档与备忘 (`.local/docs/`)**：同样使用纯 ASCII 的 `UPPER-KEBAB-CASE.md`（如 `USER-STORIES.md`, `USER-REQUIREMENTS.md`, `ROADMAP.md`）。

### 2.5 自动化测试体系 (`tests/`)
- **契约测试组织**：按业务领域严格划分目录（`tests/contracts/{assets,auth,god_canvas,observability,projects,prompts,settings,video_tasks,agent}/`）；
- **测试文件名**：`test_<domain>_<feature>.py`（如 `test_rh_workflow_contract.py`, `test_video_tasks.py`）；
- **门禁拦截**：严禁在测试文件名中包含 `phase`、`batch`、`handoff`、`v2` 等已废弃字眼。

### 2.6 Agent 任务单与记录 (`.local/agent_tasks/`)
- **活跃任务单文件**：`TASK-YYYYMMDD-<slug>.md`（如 `TASK-20261012-naming-governance.md`）；
- **阶段任务目录**：`active/<slug>-YYYYMMDD/`，其内部主控单为 `TASK.md`，交接单为 `HANDOFF.md`。

---

## 3. 代码符号与变量命名规范

### 3.1 核心数据实体与不变量 (Contract Invariants)
- **稳定长 ID 强约束**：核心业务实体必须使用统一长 ID，**严禁自行缩写为短别名**（如 `id`, `pid`, `cid`）：
  - 项目：`project_id`
  - 画布：`canvas_id`
  - 画布实体/节点：`entity_id`
  - 异步任务：`job_id`
  - 资产中枢：`asset_id`
- **扩展业务实体 ID**：统一遵循 `<entity>_id` 格式：
  - 分集：`episode_id`
  - 工作流：`workflow_id`
  - 生产流水线：`pipeline_id`
  - 用户主体：`user_id` / `actor_key`
  - 会话：`session_id`
- **CAS 乐观并发控制字段**：
  - 更新请求入参：`expected_version: int`
  - 实体当前版本：`version: int`
  - 冲突错误码：HTTP 409 + `VERSION_CONFLICT` 或 `CANVAS_VERSION_CONFLICT`；
- **时间戳字段**：统一以 `_at` 结尾（如 `created_at`, `updated_at`, `deleted_at`, `start_at`, `due_at`, `archived_at`），存储使用毫秒级时间戳或标准 ISO-8601 字符串；
- **标准错误响应结构**：
  ```json
  {
    "detail": {
      "code": "UPPER_SNAKE_CASE_CODE",
      "message": "面向用户的清晰中文解释"
    }
  }
  ```

### 3.2 后端 Python 符号规范
- **类名 (Classes)**：`PascalCase`（如 `ProjectRepository`, `WorkflowDocument`, `AuthContext`）；
- **异常类 (Exceptions)**：`PascalCase` + `Exception`/`Error`（如 `CleanroomException`, `VersionConflictException`）；
- **函数与方法**：`snake_case`（如 `get_project_by_id()`, `verify_cas_version()`）；
- **模块私有成员**：单下划线前缀 `_snake_case`（如 `_parse_payload()`, `_read_context()`）；
- **常量与枚举项**：`UPPER_SNAKE_CASE`（如 `DEFAULT_PORT = 2077`, `AUTH_MODE_LOCAL_ACCOUNT`）；
- **布尔指示变量**：`is_*`, `has_*`, `can_*`, `*_enabled`（如 `is_archived`, `has_permission`, `integration_enabled`）。

### 3.3 前端 JavaScript 符号规范
- **类与构造函数**：`PascalCase`（如 `WorkbenchShell`, `HardwareTelemetry`）；
- **函数、方法与变量**：`camelCase`（如 `fetchProjectList()`, `handleCasConflict()`）；
- **全局冻结常量**：`UPPER_SNAKE_CASE`（如 `DEFAULT_POLL_INTERVAL_MS = 2000`）；
- **DOM 自定义数据属性**：必须携带 `data-gw-` 命名空间（如 `data-gw-project-id="proj_123"`, `data-gw-status="active"`）；
- **自定义 DOM 事件**：统一使用冒号命名空间 `gw:<domain>:<action>`（如 `gw:asset:selected`, `gw:modal:dismissed`）；
- **全局单例挂载**：挂载在 `window.GW` 命名空间下（如 `window.GW.Telemetry`），禁止随意污染全局作用域。

### 3.4 前端 CSS 与黑金硬件设计系统 Token
- **CSS 变量 (Custom Properties)**：
  - 统一标准格式：`--gw-<category>-<variant>`
  - 字体：`--gw-font-display`, `--gw-font-body`, `--gw-font-mono`
  - 字阶：`--gw-type-headline-md`, `--gw-type-body-sm`, `--gw-type-button-md`
  - 颜色材质：`--gw-color-gold-primary`, `--gw-color-chassis-bg`, `--gw-text`
  - 间距与行高：`--gw-space-outer`, `--gw-leading-headline-md`
  - **收敛规则**：存量裸变量（如 `--gold-primary`）仅在 `hardware-tokens.css` 作为向下兼容别名，所有新代码统一只写 `--gw-*`；
- **CSS 选择器命名**：
  - 硬件组件前缀：`.gw-*` 或 `.hw-*`（如 `.gw-btn`, `.hw-card`, `.gw-modal`）；
  - 交互状态修饰：`.is-active`, `.is-disabled`, `.is-loading`, `.is-collapsed`。

---

## 4. 接口与网络协议命名规范 (RESTful API)

### 4.1 URL 路径规范
- **全小写短横线 (kebab-case)**：路径段一律全小写，多词之间使用短横线连接（如 `/api/asset-library`, `/api/video-tasks`, `/api/prompt-libraries`）。**严禁在 URL 路径中出现下划线或大写字母**；
- **资源集合名词复数**：表示资源集合时统一采用复数名词（如 `/api/projects`, `/api/canvases`, `/api/prompt-libraries`）；
  - 抽象子系统/单例资源允许单一概念名词（如 `/api/settings`, `/api/observability`）；
- **动作置于末尾 (RPC Actions)**：无法用标准 HTTP 谓词映射的特殊控制动作，动作动词一律置于路径末尾（如 `/api/video-tasks/{job_id}/cancel`, `/api/god-workflow/parse`）；
- **历史别名兼容**：`/api/god_workflow` 作为兼容路由保留，新端点与全站标准首选 `/api/god-workflow`。

### 4.2 HTTP 标头 (Headers)
- 遵循标准 `Train-Case`（首字母大写短横线）：
  - 幂等键：`Idempotency-Key`
  - 角色凭据透传：`X-User-Role`
  - 内容类型：`Content-Type`

### 4.3 JSON 载荷字段 (Payloads)
- 请求体与响应体 JSON 键名一律使用小写下划线 `snake_case`（与后端 Pydantic v2 属性完全一致，杜绝驼峰胶水转换）。

---

## 5. 影视多模态产物与分集命名规范 (GNS-Artifacts & Episodes)

### 5.1 剧集与分集命名层次 (Episode Hierarchy)
影视与短剧创作支持多集架构。分集标识依据项目规模采用标准化分层编码：

| 剧集模式 | 分集编码格式 | 示例与范围 | 说明与规范 |
| :--- | :--- | :--- | :--- |
| **单季多集 (Standard Series)** | `EP` + 2位数字补零 | `EP01` ~ `EP99` | 适用于绝大多数 100 集以内的短剧、微短剧、系列短片。 |
| **超长篇单季 (Long-form Series)** | `EP` + 3位数字补零 | `EP001` ~ `EP120` | 当剧集总集数 $\ge 100$ 时，统一采用 3 位补齐，防止文件排序错乱。 |
| **多季多集 (Multi-Season Series)** | `S` + 2位季号 + `E` + 2位集号 | `S01E01`, `S02E15` | 大型跨季剧集标准命名。 |

### 5.2 场次与镜头编码 (Scene & Shot Standards)
- **场次编码 (Scene Tag)**：`SC` + 2位数字补零（如 `SC01`, `SC02`, `SC15`）；
- **镜头编码 (Shot Tag)**：`SH` + 3位数字补零（如 `SH001`, `SH002`, `SH045`）；
- **角色标识 (Character Tag)**：`CHR_<name>`（如 `CHR_lihua`, `CHR_detective`）；
- **场景环境标识 (Environment Tag)**：`ENV_<name>`（如 `ENV_cyberpunk_alley`, `ENV_office`）。

### 5.3 语义与导出文件名拼装公式

对外导出、交付清单、本地分享及用户下载时，资源文件名统一遵循以下标准公式：

$$\text{Filename} = [\text{项目\_}][\text{分集\_}][\text{场次\_}][\text{镜头\_}]\text{主体描述\_}\text{版本号}[\_\text{修饰词}].\text{扩展名}$$

#### 典型层级拼装实例：
1. **项目通用资产**：`PRJ_cyberpunk_CHR_lihua_turnaround_v001.png`
2. **单集整体产物**：`EP01_script_v001.md` / `EP01_assembly_v001_master.mp4`
3. **单集镜头产物**：`EP01_SC02_SH005_hero_v001_raw.mp4`
4. **多季剧集镜头产物**：`S01E03_SC01_SH002_bgm_v001.mp3`

### 5.4 版本编号标准 (Version Formatting)
- **严格三位补零**：统一采用小写 `v` 加 3 位数字（`v001`, `v002`, `v010`, `v105`）；
- **严禁随意后缀**：严禁出现 `v1`, `v1.2`, `final`, `最新版`, `最终不改版` 等非结构化版本标记。

### 5.5 修饰词封闭标准字典 (Modifier Lexicon)
修饰词置于版本号之后、扩展名之前，必须选自以下**封闭集合**：
- `raw`：模型直出原始生成产物
- `master`：终审通过的高码率成片母版
- `proxy`：低码率剪辑代理文件（如 720p 8-bit）
- `preview`：轻量网页预览文件（如 30fps webm/mp4）
- `thumb`：静态缩略图（通常 256px 或 400px）
- `sprite`：时间线九宫格/多帧雪碧图（Contact Sheet）
- `waveform`：声学波形特征采样数据
- `approved`：编导/制片审阅批准的定稿状态
- `part`：**仅限物理写入中的临时中间态**（写入完毕必须通过原子重命名去掉 `.part`）

### 5.6 多模态产物矩阵

| 模态类别 | 产物细分 | 语义导出文件名规范 | 物理落盘格式 (Storage Name) | 标准格式 |
| :--- | :--- | :--- | :--- | :--- |
| **文本类** | 需求简报 (Brief) | `EP01_brief_v001.md` | `ai_uploads/brief_<id>.md` | Markdown (`.md`) |
| | 大纲与人物 (Outline) | `outline_v001.md` / `characters_v001.json` | `data/agents/outlines/<id>.json` | Markdown / JSON |
| | 完整剧本 (Full Script) | `EP01_script_v001.md` | `data/agents/scripts/<id>.md` | Markdown (`.md`) |
| | 分场剧本 (Breakdown) | `EP01_breakdown_v001.md` | `data/agents/breakdowns/<id>.md` | Markdown (`.md`) |
| | 文字分镜表 (Storyboard) | `EP01_storyboard_text_v001.csv` | `data/agents/storyboards/<id>.csv` | CSV (`.csv`, 带 UTF-8 BOM) |
| | 交付检查 (Delivery Check) | `EP01_delivery_check_v001.json` | `data/agents/delivery/<id>.json` | JSON (`.json`) |
| | 审阅摘要 PDF | `delivery_<delivery_id>.pdf` | `media_output/pdf/<delivery_id>.pdf` | PDF 1.7 (`.pdf`, A4 单列) |
| **图像类** | 角色概念图 (Character) | `CHR_<name>_turnaround_v001.png` | `local_assets/images/<asset_id>.png` | PNG / WEBP |
| | 场景概念图 (Environment) | `ENV_<scene>_concept_v001.png` | `local_assets/images/<asset_id>.png` | PNG / WEBP |
| | 分镜关键帧 (Keyframe) | `EP01_SC01_SH001_keyframe_v001.png` | `media_output/storyboards/{asset_id}/frame_{s}_{ms}.png` | PNG |
| | 视频抽帧 (Extracted Frame) | `frame_s{sec}_ms{ms}.png` | 同上 | PNG |
| | 资产缩略图 (Thumbnail) | `<entity>_thumb.png` | `media_output/thumbnails/{asset_id}.png` | PNG (宽 400px) |
| | 九宫格雪碧图 (Sprite) | `<video_id>_sprite_10x10.jpg` | `media_output/sprites/{asset_id}.jpg` | JPEG |
| **视频类** | 单镜头生成 (Shot Video) | `EP01_SC01_SH001_v001_raw.mp4` | `GW_VIDEO_DATA_DIR/artifacts/{job_id}.mp4` | MP4 (H.264/yuv420p/+faststart) |
| | 剪辑代理 (Proxy) | `EP01_SC01_SH001_v001_proxy.mp4` | `GW_VIDEO_DATA_DIR/proxies/{job_id}.mp4` | MP4 (720p/24fps) |
| | 多镜头成片 (Assembly) | `EP01_assembly_v001_master.mp4` | `GW_VIDEO_DATA_DIR/exports/{export_id}.mp4` | MP4 (1080p+/30fps) |
| | 临时渲染态 (In-progress) | **禁止外部可见** | `GW_VIDEO_DATA_DIR/work/{job_id}.part.mp4` | 临时文件 |
| **音频类** | 角色配音台词 (TTS / VO) | `EP01_SC01_SH001_CHR_<name>_vo_v001.mp3` | `media_output/audio/vo/<asset_id>.mp3` | MP3 / AAC |
| | 环境音效 (SFX) | `SFX_<action>_v001.wav` | `media_output/audio/sfx/<asset_id>.wav` | WAV (48kHz/24-bit) |
| | 背景音乐 (BGM) | `BGM_<theme>_v001.mp3` | `media_output/audio/bgm/<asset_id>.mp3` | MP3 (320kbps) / FLAC |
| | 波形采样特征 (Waveform) | `<asset_id>_waveform.json` | `media_output/waveforms/{asset_id}.json` | JSON |
| **脚本/工程** | 画布拓扑定义 | `<canvas_name>_v001.godmap` | `data/canvases/<canvas_id>.json` | JSON (`.godmap`) |
| | ComfyUI 网页图 | `workflow_<name>_v001_litegraph.json` | `data/workflows/<workflow_id>.json` | JSON |
| | ComfyUI API 图 | `workflow_<name>_v001_api.json` | `data/workflows/<workflow_id>_api.json` | JSON |
| | NLE 剪辑交换工程 | `EP01_timeline_v001.xml` | `media_output/nle/<project_id>.xml` | FCPXML 1.10 / CMX3600 EDL |

---

## 6. 存储层落盘与安全白名单映射

所有物理产物统一落盘在 `runtime_paths.py` 定义的受控数据命名空间内：

```
GW_DATA_DIR/ (生产态或沙盒态数据根)
  ├── ai_uploads/          # AI 参考输入与原图
  ├── registry_uploads/    # 资产注册表与提示词库
  ├── local_assets/        # 创作者导入素材
  └── media_output/        # 平台各管线派生衍生品 (受 OUTPUT-ACCESS-CONTRACT 保护)
        ├── storyboards/   # 分镜抽帧：{asset_id}/frame_{sec}_{ms}.png
        ├── thumbnails/    # 素材缩略图：{asset_id}.png
        ├── sprites/       # 视频雪碧图
        ├── waveforms/     # 音频采样波形 JSON
        └── pdf/           # 交付清单 PDF 文件

GW_VIDEO_DATA_DIR/ (视频专有数据根，受严格项目鉴权保护)
  ├── artifacts/           # 正式渲染/下载视频：{job_id}.mp4
  ├── proxies/             # 预览转码视频：{job_id}.mp4
  └── work/                # 临时渲染工作区：{job_id}.part.mp4 (执行中)
```

### 存储层四大安全准则：
1. **纯 ASCII 物理文件名**：落盘文件一律使用安全的 `asset_id`、`job_id` 或摘要哈希命名，禁止将用户输入的未经转义中文名作为物理文件名；
2. **零绝对路径泄漏**：所有 API 响应与日志记录仅返回相对数据根的安全相对路径；
3. **原子性提交保障**：中间渲染必须使用 `.part` 扩展名，写入成功后通过原子重命名提交；写入失败或取消立即 `unlink` 清理；
4. **强隔离项目鉴权**：`GW_VIDEO_DATA_DIR` 下的视频内容严禁挂载在公共静态路由，必须经过 `/api/video-tasks/{job_id}/content` 项目鉴权检查。

---

## 7. 审计与自动化门禁规则 (Hygiene & Guardrails)

本规范作为全局硬性约束，直接接入仓库质量门禁：
1. **结构门禁校验 (`test_structure_guardrails.py`)**：自动扫描所有提交文件与路径，违规包含 `tmp/phase/handoff/v2/_b*` 标记直接阻断；
2. **同形字与非 ASCII 校验 (`test_cleanroom_hygiene.py`)**：阻断任何非 ASCII 命名、同形字混入或恶意伪装；
3. **契约测试基准**：核心长 ID 契约、CAS `expected_version` 乐观锁、标准错误包格式在 8 大领域契约测试中持续验证。
