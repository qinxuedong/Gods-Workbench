# 神工坊 (Gods-Workbench)

<div align="center">

[![Language](https://img.shields.io/badge/Language-English%20%7C%20%E4%B8%AD%E6%96%87-blue.svg)](#-语言切换--language)
[![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB.svg?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115%2B-009688.svg?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![License](https://img.shields.io/badge/License-Apache%202.0-D22128.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/Platform-Windows-0078D6.svg?logo=windows&logoColor=white)](#-快速上手)
[![Architecture](https://img.shields.io/badge/Design-Local--First-success.svg)](#-数据安全与资产主权)

**面向 AI 创作者与多媒体团队的本地优先、全流程智能创作与数字资产工作台**

[English Version](README_EN.md) | [中文说明](README.md)

</div>

---

> [!IMPORTANT]
> ### 🌟 核心理念与产品哲学
> **元数据管理，素材统一ID。所有的内容都是围绕用户资产展开。保证每一个生成过程和结果都能成为用户可复用长期资产。**

---

## 📖 项目简介

在以生成式 AI 为驱动的多媒体创作时代，散落在聊天窗口、临时文件夹和历史记录中的生成结果往往转瞬即逝——参数丢失、提示词遗忘、模型版本混乱导致优质内容无法二次复用。

**神工坊（Gods-Workbench）** 专为解决这一痛点而生。它是一个**本地优先（Local-First）**、围绕**数字资产全生命周期**构建的高性能创作工作台。神工坊将项目编排、智能画布、分集管线、提示词金库、素材注册表与视频处理深度打通，为创作者打造一体化、确定性、高可控的生产级流水线。

无论单兵作战的数字艺术家，还是追求严谨交付的剧集制作团队，神工坊都确保您的每一次灵感迸发与算力投入，都能完整凝练为具有长久复用价值的数字资产。

---

## 💡 核心亮点

```
                               ┌─────────────────────────────────────────┐
                               │           神工坊 核心资产枢纽            │
                               │        Unified Asset Hub (gw)           │
                               └────────────────────┬────────────────────┘
                                                    │
                 ┌───────────────────┬──────────────┴──────┬───────────────────┐
                 ▼                   ▼                     ▼                   ▼
       ┌──────────────────┐ ┌──────────────────┐ ┌──────────────────┐ ┌──────────────────┐
       │   统一资产 ID    │ │   元数据全链路   │ │   CAS 防冲突并发  │ │   本地优先主权   │
       │ 单调稳定标识体系 │ │ 提示词/参数/溯源 │ │ 乐观锁原子化提交 │ │ 零云端依赖/安全 │
       └──────────────────┘ └──────────────────┘ └──────────────────┘ └──────────────────┘
```

### 1. 统一资产 ID（Unified Asset ID）
- **确定性与不可篡改**：抛弃易失、无序的随机 UUID，采用基于命名空间与单调递增序列的全局确定性资产标识体系（如 `local_NNNN`、`prj-p-{namespace}-{seq}`）。
- **引用拓扑完整性**：无论素材在磁盘中如何重命名、移动目录或跨模块调用，资产 ID 始终保持稳定，彻底消除悬挂引用与孤儿资源，确保跨工程复用的连续性。

### 2. 元数据深度沉淀（Metadata Accumulation）
- **全要素创作快照**：完整记录生成过程中的提示词（Prompts）、正负向权重、模型快照、Seed 种子、采样器配置、前置依赖及演化衍生关系。
- **可复现与可回溯**：告别“盲盒式生成”。每一张渲染图片、每一个生成镜头均携带完整元数据基因，随时一键重放生成环境与复刻创作灵感。

### 3. CAS 版本防冲突机制（Compare-And-Swap）
- **乐观并发控制**：在所有核心写操作中强制贯彻 `expected_version` 校验机制，提供严谨的原子性比对提交保障。
- **数据一致性防护**：在多标签页操作、长耗时任务回填与复杂画布拓扑编辑时，杜绝静默覆盖或脏写（Dirty Write），命中并发版本冲突时返回清晰语义并支持安全合并。

### 4. 资产闭环生产工具链（Closed-Loop Toolchain）
神工坊提供模块化且深度联动的创作矩阵，串联创意到最终交付的全环节：
- **项目中心（Projects Hub）**：支持多工程看板管理、生产排期输入、场次/镜头进度追踪与全生命周期归档流转。
- **神工画布（God Canvas / Smart Canvas）**：节点式可视化交互工作流，支持视口平移缩放、节点智能编组、无损 `.godmap` 与 JSON 拓扑导入导出。
- **分集管线（Episode Pipeline）**：面向剧集叙事的镜头编排与流水线，实现多场次分镜的结构化拆解与批量推进。
- **素材金库与注册表（Asset Library & Registry）**：支持本地海量素材秒级入库、智能标签系统、多维目录树、多模态元数据检索与回收站机制。
- **提示词资产库（Prompt Library）**：结构化沉淀优质 Prompt 模板、关键词词库与组合预设，随时赋能画布节点与生成任务。
- **视频与媒体引擎（Video Tasks & Media Engine）**：本地视频生成任务排队、状态自愈、异步探测与多分辨率规格交付。

### 5. 本地优先与绝对资产主权（Local-First & Sovereignty）
- **数据资产物理归属**：所有工程元数据、素材文件与凭据完全落盘于创作者本地计算机，无需强制联网认证，彻底消除公有云数据泄漏与平台绑架风险。
- **极简原生技术栈**：基于 Python 3.11 + FastAPI 后端与原生现代 Web 前端（Tailwind CSS / Lucide），单进程架构，内存占用低，启动迅捷。

---

## 🚀 快速上手

### 环境要求
- **操作系统**：Windows 10 / 11（推荐，开箱即用支持路径隔离）
- **Python 环境**：Python 3.11+
- **浏览器**：现代 Chromium 内核浏览器（Chrome、Edge、Brave 等）

### 1. 克隆仓库与安装依赖

```powershell
# 1. 克隆本仓库到本地
git clone https://github.com/qinxuedong/Gods-Workbench.git
cd Gods-Workbench

# 2. 安装 Python 依赖（推荐在虚拟环境中执行）
python -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt

# 生产级安全校验（可选：使用严格哈希锁安装）
python -m pip install --require-hashes -r requirements.lock.hashes
```

### 2. 启动运行

#### 方式一：Windows 一键快速启动（推荐）
直接双击运行根目录下的 `start.bat` 脚本：
```cmd
start.bat
```
脚本将自动以生产模式启动单进程服务，并在就绪后唤起默认浏览器打开项目中心。

#### 方式二：命令行生产模式启动
```powershell
python run.py --prod --open-browser
```

#### 方式三：开发沙盒模式启动
用于代码调试与功能开发，数据仅保存在仓库内部沙盒，不影响生产数据：
```powershell
python run.py
```

### 3. 命令行参数说明

| 参数项 | 默认值 | 功能说明 |
| :--- | :--- | :--- |
| `--prod` | `False` | 启用生产数据根（落盘至用户目录，与源码解耦） |
| `--open-browser` | `False` | 服务健康检查就绪后自动在浏览器打开控制台 |
| `--port <PORT>` | `2077` | 指定监听端口（例如指定 `2078` 可同时运行第二实例） |
| `--migrate` | `False` | 一次性安全迁移旧版本数据到生产数据根 |
| `--reset <TARGET>` | - | 显式清理开发沙盒 `.local/runtime-dev` 内的指定路径（受安全沙盒限制） |

---

## 💾 数据存储说明

为了保证用户资产与源代码严格解耦，神工坊实施严格的“三态三根”物理隔离规范：

```
 用户本地系统
 └── %LOCALAPPDATA%\GodsWorkbench\ (生产数据根 - 完全脱离 Git 源码树)
     ├── data/                  # 核心资产注册表、项目快照、提示词库 (确定性落盘 JSON)
     ├── auth/auth.sqlite3      # 本地安全认证与用户会话数据库
     └── video/                 # 视频产物、媒体缓存与转码任务流
```

- **生产态（Production Mode）**：
  默认路径位于用户目录 `%LOCALAPPDATA%\GodsWorkbench\`。源码目录更新、代码重构或 Git 分支切换**绝不会**覆盖、篡改或污染您的任何实际工程资产。
- **开发沙盒态（Development Mode）**：
  默认落盘于仓库下的 `.local/runtime-dev/`，该目录受 `.gitignore` 保护，仅供功能调试，不读取亦不污染生产用户资产。
- **测试态（Test Mode）**：
  自动化测试运行于每次独立的系统临时隔离路径（`tmp_path`），测试结束后即刻清理。
- **文件系统防越界保护**：
  存储引擎内置重解析点（Reparse Point / Junction / Symlink）严密探测，杜绝任何利用软链接逃逸出指定根目录或误删敏感系统目录的风险。

---

## 🛡️ 数据安全与资产主权

1. **绝对主权属于用户**
   在神工坊中创建的所有数据（包括工程拓扑、Prompt 词元、图片资产、训练标签及分镜脚本）均采用开放、结构化的 JSON 与标准文件格式直接落盘在用户本地。您拥有 100% 的所有权、迁移权与导出权。
2. **零隐蔽外传与遥测**
   系统不包含任何隐蔽的数据回传探针、不进行非授权的遥测分析、不私自上传用户的创意成果。所有的外部模型接口调用均经过明示的 Provider 协议边界管理。
3. **本地身份认证与访问控制**
   系统内置本地账户管理体系，初始启动无默认弱口令；支持细粒度角色权限隔离，写操作与生命周期变更受到本地鉴权门禁严格护航。
4. **单调序号与孤儿防复用**
   底层持久化预留机制确保：即使系统遭遇意外崩溃，已经分配过的序列号亦绝不二次复用，从根源消除资源错位与覆写隐患。

---

## 📄 开源许可证

本项目基于 [Apache License 2.0](LICENSE) 许可证正式开源发布。关于第三方依赖、组件、字体与数据集的完整许可声明，请参阅 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

---

## 📈 Star History

[![Star History Chart](https://api.star-history.com/svg?repos=qinxuedong/Gods-Workbench&type=Date)](https://star-history.com/#qinxuedong/Gods-Workbench&Date)

---

<div align="center">
  <sub>Made with ❤️ for AI Creators and Digital Artisans.</sub>
</div>
