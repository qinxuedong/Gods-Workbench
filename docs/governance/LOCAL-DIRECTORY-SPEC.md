# Gods-Workbench 本地文件夹全流程规范标准 (LDS-2026)

- **规范代号**：`LDS-2026`（Gods-Workbench Local Directory Specification）
- **状态**：CURRENT / MANDATORY
- **关联上位规范**：[`docs/governance/GLOBAL-NAMING-SPEC.md`](GLOBAL-NAMING-SPEC.md) (GNS-2026)、[`docs/governance/PROJECT-GOVERNANCE.md`](PROJECT-GOVERNANCE.md)、`AGENTS.md`
- **适用范围**：本地项目工作区创建、多模态创作管线组织、DCC/NLE 专业软件协同、系统运行时存储以及 Agent 本地治理
- **约束力**：**全仓库最高级别工程规范之一**。所有智能代理（Agent）、开发人员及自动化工具在本地执行文件夹创建、重命名、移动或删除时必须严格遵守。

---

## 1. 核心原则与第一性原理

### 1.1 双层解耦映射模型 (Dual-Layer Decoupling)
在本地优先（Local-First）且深度融合 AI 的音视频创作工作台中，文件夹体系必须兼顾**底层文件系统的绝对安全性**与**创作者界面的直观可用性**：

1. **物理存储层（底层文件系统）**：
   - **100% 纯 ASCII 字符集**：严禁中文字符、全角标点、Emoji 或空格，杜绝跨平台（Windows / Linux / macOS）UTF-8 与 GBK 编码转换导致的乱码、损坏或路径丢失；
   - **确定性与自解释性**：采用结构化数字序号前缀与语义英文单词组合（如 `01_scripts`、`02_assets`），确保任何操作系统终端和文件管理器按创作阶段正序排列；
   - **无特殊与保留字符**：严禁使用 Windows 保留设备名（`CON`, `PRN`, `AUX`, `NUL` 等）及非法字符（`\ / : * ? " < > |`）。

2. **业务语义层（前端展示与成片导出）**：
   - **UI 映射呈现**：在前端项目看板、资源树形控件、面包屑导航中，通过内置国际化映射表将纯 ASCII 目录名呈现为清晰易懂的中文阶段名称（如 `01 剧本与企划`、`02 资产与设定`）；
   - **工业化导出命名**：对外发布、分发交付与 NLE 导出时，严格依照 GNS-2026 的分集、场次、镜头与三位补零版本号公式动态拼接自解释语义文件名。

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    LDS-2026 双层解耦映射模型                            │
└─────────────────────────────────────────────────────────────────────────┘
                                     │
           ┌─────────────────────────┴─────────────────────────┐
           ▼                                                   ▼
【物理存储层：底层文件系统】                             【业务语义层：UI 与导出展示】
 · 物理路径：data/workspaces/{project_id}/            · 项目中心目录树、资产抽屉面包屑
 · 目录结构：01_scripts/, 02_assets/, ...             · 映射呈现：01 剧本与企划、02 资产与设定
 · 特性：100% 纯 ASCII / 序号前缀 / 跨平台安全         · 导出格式：EP01_SC01_SH001_v001_master.mp4
```

---

## 二、 创作业务层：项目全流程生命周期工作区（Project Workspace）

项目工作区物理落盘于当前数据根目录下的 `data/workspaces/{project_id}/`，按受控相对路径隔离。项目创建时依据项目类型原子化分配模板。

### 2.1 电影 / 短片项目模板 (`film`)
适用场景：单部微电影、广告片、短片、概念片等单体创作项目。

#### 物理结构与 UI 映射标准：

| 物理相对路径 (纯 ASCII) | 前端 UI 映射名称 | 创作阶段职责与标准产物契约 (GNS-2026) |
| :--- | :--- | :--- |
| `01_scripts/` | `01 剧本与企划` | 承载文本策划产物。<br>• 提纲与简报：`brief_v001.md`<br>• 故事梗概与人物：`outline_v001.md` / `characters_v001.json`<br>• 完整文学剧本：`script_v001.md`<br>• 分场剧本：`breakdown_v001.md` |
| `02_assets/` | `02 资产与设定` | 承载视觉与听觉参考资产库。<br>• 角色概念图：`CHR_<name>_turnaround_v001.png`<br>• 场景概念图：`ENV_<scene>_concept_v001.png`<br>• 道具设定与参考垫图 |
| `03_storyboards/` | `03 分镜与节拍` | 承载镜头节奏与画面规划。<br>• 文字分镜表：`storyboard_text_v001.csv`<br>• 分镜关键帧：`SCxx_SHxxx_keyframe_v001.png`<br>• 动态预演视频 (Animatic) |
| `04_generations/` | `04 生成与制作` | 承载 AI 模型直出与工作流运行产物。<br>• 工作流配置：`workflow_<name>_v001_api.json`<br>• 镜头生成切片：`SCxx_SHxxx_v001_raw.mp4`<br>• 提示词工程表 |
| `05_post/` | `05 剪辑与后期` | 承载音视频合成与后期工程。<br>• 配音台词：`SCxx_SHxxx_CHR_<name>_vo_v001.mp3`<br>• 音效：`SFX_<action>_v001.wav`<br>• 背景音乐：`BGM_<theme>_v001.mp3`<br>• 剪辑工程交换文件：`timeline_v001.xml` / `timeline_v001.fcpxml` |
| `06_delivery/` | `06 交付与审阅` | 承载最终交付成品与审阅沉淀。<br>• 终审母版：`assembly_v001_master.mp4`<br>• 交付清单：`delivery_check_v001.json`<br>• 审阅摘要报告：`delivery_<id>.pdf` |

#### 典型物理目录树：
```text
data/workspaces/{project_id}/
├── 01_scripts/
│   ├── brief_v001.md
│   ├── outline_v001.md
│   ├── script_v001.md
│   └── breakdown_v001.md
├── 02_assets/
│   ├── CHR_hero_turnaround_v001.png
│   └── ENV_station_concept_v001.png
├── 03_storyboards/
│   ├── storyboard_text_v001.csv
│   └── SC01_SH001_keyframe_v001.png
├── 04_generations/
│   ├── workflow_txt2video_v001_api.json
│   └── SC01_SH001_v001_raw.mp4
├── 05_post/
│   ├── SC01_SH001_CHR_hero_vo_v001.mp3
│   ├── BGM_theme_v001.mp3
│   └── timeline_v001.xml
└── 06_delivery/
    ├── assembly_v001_master.mp4
    └── delivery_check_v001.json
```

---

### 2.2 连续剧集项目模板 (`series`)
适用场景：网络短剧、分集漫剧、系列季播短片。

分集编号统一执行 GNS-2026 标准：
- 单季标准短剧（$< 100$ 集）：`EP01` ~ `EP99`；
- 超长篇单季剧集（$\ge 100$ 集）：`EP001` ~ `EP120`（三位补零，保障排序）；
- 多季大型剧集：`S01EP01` 或 `S01E01`。

#### 物理结构与层级规范：
```text
data/workspaces/{project_id}/
├── 00_global_assets/            # [UI: 全局共享资产] 跨集复用角色、场景、主题音乐
│   ├── characters/              # 全局主角/配角 LoRA 与概念原画
│   ├── environments/            # 固定主场景空间概念
│   └── audio/                   # 全剧主题曲、片头片尾曲、统一转场音效
├── 01_series_plan/              # [UI: 全季策划]
│   ├── bible_v001.md            # 世界观总设定集 (Series Bible)
│   ├── synopsis_v001.md         # 分集梗概总览表
│   └── cast_index_v001.json     # 演职员与角色对应表
└── 02_episodes/                 # [UI: 分集制作] 各集完全受控隔离
    ├── EP01/                    # 第 1 集独立流水线
    │   ├── 01_scripts/          # EP01_script_v001.md
    │   ├── 02_assets/           # 本集特有资产 (如客串角色、突发道具)
    │   ├── 03_storyboards/      # EP01_storyboard_text_v001.csv
    │   ├── 04_generations/      # EP01_SC01_SH001_v001_raw.mp4
    │   ├── 05_post/             # EP01_timeline_v001.xml
    │   └── 06_delivery/         # EP01_assembly_v001_master.mp4
    ├── EP02/
    │   └── ...                  # 内部结构同上
    └── EP03/
        └── ...
```

---

### 2.3 极简通用模板 (`default`)
适用场景：轻量化灵感记录、单一工作流测试、敏捷创作验证。

```text
data/workspaces/{project_id}/
├── 01_scripts/                  # 脚本、提示词与文本
├── 02_references/               # 垫图、参考原图与视频样本
├── 03_generations/              # 试产视频与生图切片
└── 04_exports/                  # 导出文件与成片
```

---

## 三、 专业软件协同层：DCC / NLE 桥接目录 (`GW-source`)

当创作者在神工坊中唤起本地专业后期与剪辑软件（Adobe Premiere Pro、Blackmagic DaVinci Resolve、Adobe After Effects、Apple Final Cut Pro 等）或执行工程回套时，神工坊在工作区根目录下挂载纯 ASCII 的 `GW-source/` 目录供专业软件直读与 Relink：

```text
GW-source/
├── video/                      # 剪辑源视频与代理视频
│   ├── raw/                    # 模型直出原始高码率视频 (*_raw.mp4)
│   └── proxy/                  # 轻量化剪辑代理视频 (*_proxy.mp4, 720p/24fps)
├── pic/                        # 平面视觉素材
│   ├── textures/               # 材质贴图、背景定帧原图
│   └── frames/                 # 分镜抽帧关键画面与序列帧
├── music/                      # 音频分轨素材
│   ├── vo/                     # 角色对白干声 (*_vo_*.mp3)
│   ├── sfx/                    # 场景音效与动作拟音 (SFX_*.wav)
│   └── bgm/                    # 完整背景音乐与情绪铺垫 (BGM_*.mp3)
└── other/                      # 工程交换文件与辅助数据
    ├── timeline/               # FCPXML / CMX3600 EDL 剪辑工程交换文件
    └── subtitles/              # 逐镜时间轴字幕文件 (*.srt / *.vtt)
```

---

## 四、 系统运行态：三态存储物理隔离与目录结构

为防止测试数据污染生产、研发数据外溢，系统严格执行**三态物理隔离**架构。所有本地落盘路径在系统初始化时确定，严禁任何业务代码使用相对路径跳转：

### 4.1 三态根路径规范

| 运行环境 | 本地物理存储根目录 | 默认端口 | 安全隔离准则 |
| :--- | :--- | :--- | :--- |
| **测试态 (Test)** | pytest 各测试用例独占的 `tmp_path` | 动态随机 | • 严禁触碰任何真实沙盒或生产路径；<br>• 用例执行完毕后由系统自动销毁清理。 |
| **开发沙盒态 (Dev)** | 仓库根目录下 `.local/runtime-dev/` | `2077` | • 研发人员与智能代理本地联调专用；<br>• 物理存储严禁越出 `.local/` 沙盒边界。 |
| **生产运行态 (Prod)** | 用户本机操作系统目录 `%LOCALAPPDATA%\GodsWorkbench\` | `2077` | • 面向真实用户的单机客户端本地持久化；<br>• 绝对留存用户本地磁盘，绝不进入源码仓库。 |

### 4.2 统一物理子目录标准
开发沙盒态与生产运行态保持完全同构的子目录组织：

```text
[运行存储根目录]/
├── data/                       # 核心元数据与非视频产物
│   ├── projects.json           # 原子单进程项目核心元数据注册表
│   ├── workspaces/             # 【项目工作区物理根】各 project_id 的受控工作区
│   ├── ai_uploads/             # 创作垫图与初始参考文件输入
│   ├── local_assets/           # 统一资产中枢物理实体 (如 images/<asset_id>.png)
│   └── media_output/           # 派生衍生品 (thumbnails/, sprites/, waveforms/, pdf/)
│
├── auth/                       # 认证与账户数据
│   └── auth.sqlite3            # SQLite 用户账户与权限凭据数据库
│
└── video/                      # 【视频专有数据根，受项目鉴权保护】
    ├── artifacts/              # 正式渲染成片: {job_id}.mp4
    ├── proxies/                # 低码率转码代理: {job_id}.mp4
    └── work/                   # 临时渲染写入工作区: {job_id}.part.mp4
```

---

## 五、 智能代理（Agent）与工程治理本地目录规范

所有由 Agent 规划、拆解或本地扩展的文件，严格限制在本地私有目录中，**受 `.gitignore` 保护，永不上云**：

```text
.local/
├── agent_tasks/                # 【任务计划落盘硬约束】禁止仅留聊天或内存
│   ├── active/                 # 当前执行中的任务（单任务单或专项阶段目录）
│   │   ├── TASK-YYYYMMDD-<slug>.md   # 独立任务单
│   │   └── <slug>-YYYYMMDD/          # 阶段性复合任务目录
│   │       ├── TASK.md               # 阶段主控单（目标、范围、步骤、验收证据）
│   │       ├── README.md             # 导航与子任务索引
│   │       └── HANDOFF.md            # 阶段交接单与检查点
│   └── archive/                # 已验收完成的历史任务归档（按阶段封存）
│
├── docs/                       # 本地私有业务与技术文档（文件名必须 100% 纯 ASCII）
│   ├── user_doc/               # 用户需求 (USER-REQUIREMENTS.md)、用户故事 (USER-STORIES.md)
│   ├── notes/                  # 研发路线 (ROADMAP.md)、AI 管线备忘
│   └── research/               # 技术调研、选型分析与防坑账本
│
└── runtime-dev/                # 开发沙盒态物理运行数据根（见第四节）

.agents/
└── skills/                     # 专属本地技能
    └── gods-workbench/         # 神工坊核心技能与低 Token 路由 references
        ├── SKILL.md            # 技能总则
        └── references/         # 各领域精准开发指南 (纯小写短横线 *.md)
```

---

## 六、 本地文件夹创建与更改的安全操作标准（执行铁律）

所有后端服务代码、命令行脚本及自动化 Agent，在执行本地文件与目录的创建、重命名、移动或删除时，必须强制遵守以下 5 条安全红线：

### 6.1 零非 ASCII 与严格命名校验
物理创建的文件与目录名必须通过以下正则表达式校验：
$$\text{Regex: } \wedge[a-zA-Z0-9\_\-\.]+\$$$
- **严禁使用中文字符、全角标点、Emoji 或空格**；
- **严禁使用 Windows 保留设备名称**（`CON`, `PRN`, `AUX`, `NUL`, `COM1`~`COM9`, `LPT1`~`LPT9` 及其带扩展名变体）；
- **严禁临时施工标记**：路径名严禁出现 `tmp`, `temp`, `backup`, `draft`, `drafts`, `handoff`, `phase[0-9]+`, `_b[0-9]+`。

### 6.2 路径穿越严格防御（Path Traversal Guard）
所有对工作区目录与文件的操作，必须在解析真实路径后进行边界断言：
```python
resolved_target = (base_dir / relative_path).resolve()
if not resolved_target.is_relative_to(base_dir.resolve()):
    raise CleanroomException(400, "PATH_TRAVERSAL_FORBIDDEN", "禁止越出受控工作区目录")
```
严禁允许任何包含 `..`、根路径绝对跳转或 Windows 驱动器盘符跳转的相对路径输入。

### 6.3 跨进程 / 跨线程文件锁
在执行工作区初始化、多级子目录批量生成或跨目录归档移动时，必须持有基于目标根路径的并发文件锁（基于 `threading.RLock` 或进程级锁），防止并发调用产生目录碎片或竞态覆盖。

### 6.4 CAS 乐观并发控制 (Compare-And-Swap)
对工作区目录元数据、结构定义以及项目的更新与删除操作，请求必须携带 `expected_version`：
- 当前版本不匹配时，明确返回 HTTP 409 与标准错误包：
  ```json
  {
    "detail": {
      "code": "VERSION_CONFLICT",
      "message": "工作区版本已发生变化，请刷新后重试"
    }
  }
  ```
- **绝对禁止静默覆盖或强行覆写**。

### 6.5 防破坏与非破坏性清理原则
1. **清理边界锁定**：命令行 `--reset` 选项必须显式提供清除目标，且**仅允许**清理开发沙盒 `.local/runtime-dev/`，绝对禁止删除用户生产目录（`%LOCALAPPDATA%`）或 Git 代码仓库；
2. **数据迁移原则**：版本迭代或旧数据迁移时，只允许采用“读源复制到新目录”，严禁直接在源目录破坏性重命名；若迁移过程失败，原源数据必须完整保留，且不得写入成功回执；
3. **写入原子性提交**：渲染或下载中间态必须写入 `.part` 临时后缀（如 `{job_id}.part.mp4`），完全写入并通过校验后，方可通过原子重命名操作提交为正式产物。
