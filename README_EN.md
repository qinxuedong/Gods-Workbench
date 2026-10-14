# Gods-Workbench (神工坊)

<div align="center">

[![Language](https://img.shields.io/badge/Language-English%20%7C%20%E4%B8%AD%E6%96%87-blue.svg)](#-language-switcher)
[![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB.svg?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115%2B-009688.svg?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![License](https://img.shields.io/badge/License-Apache%202.0-D22128.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/Platform-Windows-0078D6.svg?logo=windows&logoColor=white)](#-quick-start)
[![Architecture](https://img.shields.io/badge/Design-Local--First-success.svg)](#-data-security--asset-sovereignty)

**A local-first, end-to-end intelligent creation and digital asset workbench built for AI creators and multimedia teams.**

[English Version](README_EN.md) | [中文说明](README.md)

</div>

---

> [!IMPORTANT]
> ### 🌟 Core Philosophy
> **Metadata Management, Unified Asset ID. Everything revolves around user assets, ensuring every generation process and output becomes a reusable, enduring asset for creators.**

---

## 📖 Overview

In the era of generative AI multimedia production, creations scattered across chat windows, temporary folders, and fleeting logs often vanish into ephemeral noise. Parameter loss, untracked prompts, and chaotic model iterations make high-quality outputs notoriously difficult to reuse or build upon.

**Gods-Workbench (神工坊)** was engineered to solve this fundamental problem. It is a **local-first** workbench designed entirely around the **full lifecycle of digital assets**. Gods-Workbench seamlessly unifies project orchestration, node-based smart canvas workflows, episodic storytelling pipelines, prompt libraries, an asset registry, and media tasks into a cohesive, deterministic, and production-grade environment.

Whether you are an independent digital artist or a multidisciplinary studio producing episodic series, Gods-Workbench guarantees that every spark of inspiration and every unit of computational power is compounded into enduring digital equity.

---

## 💡 Core Highlights & Philosophy

```
                              ┌─────────────────────────────────────────┐
                              │            Gods-Workbench Hub           │
                              │        Unified Asset Hub (gw)           │
                              └────────────────────┬────────────────────┘
                                                   │
                ┌───────────────────┬──────────────┴──────┬───────────────────┐
                ▼                   ▼                     ▼                   ▼
      ┌──────────────────┐ ┌──────────────────┐ ┌──────────────────┐ ┌──────────────────┐
      │ Unified Asset ID │ │ Metadata Trace   │ │ CAS Concurrency  │ │ Local Sovereignty│
      │ Monotonic Stable │ │ Prompts, Params, │ │ Atomic Optimistic│ │ Zero Cloud Lock,  │
      │ Identification   │ │ Full Provenance  │ │ Locking (No Lost)│ │ 100% User Owned  │
      └──────────────────┘ └──────────────────┘ └──────────────────┘ └──────────────────┘
```

### 1. Unified Asset ID
- **Deterministic and Immutable**: Replaces chaotic, volatile UUIDs with a globally deterministic, monotonic identification scheme (e.g., `local_NNNN`, `prj-p-{namespace}-{seq}`).
- **Graph and Topology Integrity**: No matter how files are renamed, relocated across folders, or referenced across disparate modules, the asset ID remains permanently stable. This eliminates dangling references and orphaned records, ensuring seamless long-term reusability.

### 2. Deep Metadata Enrichment
- **Complete Context Capture**: Automatically archives full generative context—positive and negative prompts, model fingerprints, seed numbers, sampler settings, upstream dependencies, and asset lineage.
- **Reproducible and Traceable**: No more "black-box" media. Every rendered image and synthesized video clip retains its complete generative DNA, enabling one-click reproduction and effortless creative iteration.

### 3. CAS Concurrency & Conflict Prevention
- **Optimistic Concurrency Control**: Enforces an explicit `expected_version` validation paradigm across all critical write operations.
- **Data Integrity Protection**: Prevents silent overwrites, stale state regressions, and dirty writes during multi-tab editing, asynchronous job completion, and complex canvas interactions. Conflicts trigger clear, actionable error contracts for reliable resolution.

### 4. Closed-Loop Asset Toolchain
Gods-Workbench delivers a tightly integrated suite of purpose-built creation modules:
- **Projects Hub**: Multi-project management with visual status boards, production scheduling, scene/shot progress tracking, and full lifecycle archiving.
- **God Canvas & Smart Canvas**: A responsive, node-based spatial canvas featuring viewport pan/zoom, node clustering, and lossless `.godmap` / JSON graph import and export.
- **Episode Pipeline**: Tailored for episodic storytelling, facilitating structured multi-scene breakdown, shot sequencing, and high-throughput batch generation.
- **Asset Library & Registry**: Instant indexing for vast local media collections, hierarchical categorization, multidimensional tagging, metadata search, and a safe recycle bin.
- **Prompt Library**: Structured repository for prompt templates, keyword taxonomies, and modular presets that feed directly into canvas nodes and pipeline tasks.
- **Video Tasks & Media Engine**: Asynchronous job scheduling, probe diagnostics, status recovery, and multi-resolution rendering management.

### 5. Local-First Architecture & Absolute Asset Sovereignty
- **Physical Ownership of Data**: Project state, prompts, media binaries, and auth credentials reside strictly on your local disk. Zero mandatory cloud synchronization, zero platform vendor lock-in.
- **Lean, Modern Stack**: Powered by a high-performance Python 3.11 + FastAPI backend coupled with a lightweight, buildless native HTML5/JS/CSS frontend (featuring Tailwind CSS and Lucide icons). Instant startup with minimal footprint.

---

## 🚀 Quick Start

### Prerequisites
- **Operating System**: Windows 10 / 11 (Recommended; out-of-the-box native path isolation)
- **Python Runtime**: Python 3.11+
- **Browser**: Any modern Chromium-based browser (Chrome, Edge, Brave, etc.)

### 1. Clone Repository & Install Dependencies

```powershell
# 1. Clone the repository
git clone https://github.com/qinxuedong/Gods-Workbench.git
cd Gods-Workbench

# 2. Set up virtual environment and install runtime requirements
python -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt

# Optional: Verified production install with SHA-256 hash validation
python -m pip install --require-hashes -r requirements.lock.hashes
```

### 2. Launching the Application

#### Option A: One-Click Startup on Windows (Recommended)
Double-click `start.bat` in the root directory:
```cmd
start.bat
```
This automatically boots the server in production mode and launches your default browser once the health probe succeeds.

#### Option B: Production CLI Launch
```powershell
python run.py --prod --open-browser
```

#### Option C: Development Sandbox Launch
Launches within an isolated repository sandbox without touching production user data:
```powershell
python run.py
```

### 3. CLI Arguments Reference

| Argument | Default | Description |
| :--- | :--- | :--- |
| `--prod` | `False` | Run in production mode (stores data in user AppData, decoupled from code) |
| `--open-browser` | `False` | Automatically opens the browser once `/healthz` is ready |
| `--port <PORT>` | `2077` | Binding port (e.g., specify `2078` to run a secondary instance) |
| `--migrate` | `False` | Safely migrates legacy data roots to the production store |
| `--reset <TARGET>` | - | Explicitly clears a target path inside the dev sandbox (`.local/runtime-dev`) |

### 4. RunningHub Workflow Output Collection

The RunningHub API Key in workflow settings is used for parsing, submission, and queries. Downloading cloud outputs also requires `GW_WORKFLOW_OUTPUT_HOSTS` in the **server process environment**. Set a comma-separated list of output URL hostnames you have verified and authorized (no spaces needed after commas):

```powershell
$env:GW_WORKFLOW_OUTPUT_HOSTS = 'rh-images-tos.xiaoyaoyou.com,rh-images.xiaoyaoyou.com'
```

The example above is the usual RunningHub output host pair: real RunningHub outputs are typically served from `rh-images-tos.xiaoyaoyou.com`, while `rh-images.xiaoyaoyou.com` is the output host named in RunningHub docs; list both together as needed. Matching uses exact hostnames, without schemes, paths, or wildcards. Start the server in the same PowerShell window using the instructions above. Updating an existing server requires restarting it yourself while retaining its production or development mode and data root so existing tasks remain accessible.

Downloads require an authorized HTTPS hostname on the default port or port 443. Redirects are not followed, and the connection layer checks and pins public IP addresses. An empty `GW_WORKFLOW_OUTPUT_HOSTS` produces `OUTPUT_HOSTS_NOT_CONFIGURED`; a configured list that does not include the output host produces `OUTPUT_URL_NOT_ALLOWED`. Neither case downloads the file. The server process has no default trusted output host.

Execution and collection are recorded separately. If remote generation succeeds but downloading or registration is incomplete, `execution_status` is `completed`, `collection_status` is `partial_failed`, and the overall task is `failed`. This means **collection is incomplete**; generation may already have occurred and been billed. After correcting configuration, use the workflow task window's collection retry action. It collects the original `job_id` and remote task ID without resubmitting generation. Registered outputs retain their stable `asset_id`, and repeated collection does not create duplicate assets.

Do not include API keys or full signed/token-bearing output URLs in examples, issue reports, or logs. For troubleshooting, provide the source hostname, `job_id`, and error code.

### 5. Converting ComfyUI API Graphs to Native Graphs

Converting API graphs to native graphs, including workflow/asset JSON and ZIP exports and native opening, requires the actual node schemas. When the document lacks a required schema, the server reads `/object_info` from the ComfyUI address in workflow settings. For `NODE_SCHEMA_REQUIRED`, check the address, availability, and node installation. `UNSUPPORTED_WIDGET_MAPPING` means the parameters cannot be converted losslessly; retain the source file and use the corresponding native graph.

Native saves retain the original project/canvas source and migrate asset bindings only when the same field and value still match. Changed values, removed nodes, changed node types, or connected inputs clear the old binding. If the mapping cannot be confirmed, saving is rejected and the original document and version remain intact.

### 6. Workflow Transfer and Current Limitations

Ordinary JSON and PNG/WebP with complete workflow metadata can be parsed through upload, canvas drop, or clipboard. Internal adaptation comes first; missing definitions retain the source and report the gap without guessing parameters or links. ComfyUI page conversion is a last resort and does not implicitly start the service. CF controls start/stop; Import ComfyUI opens and loads through the project-managed adapter, with success confirmed by the host receipt.

RH link parsing does not require saving or running first. The platform can still return 810 when it supplies no complete graph. Automatic retrieval from signed-in RH pages and automatic RH creation/upload for local/Lib graphs without an RH identifier remain incomplete. Submissions with a real RH identifier carry the current complete execution graph; isolated tests do not establish real platform acceptance.

Definite rejection persists a safe error code. Timeouts, missing remote task identifiers, and unconfirmed results remain `outcome_unknown` and must not automatically generate again. Real ComfyUI, RH, and cross-platform acceptance remain open.

---

## 💾 Data Storage Architecture

To guarantee that user assets remain completely untangled from source code revisions, Gods-Workbench enforces strict three-mode, three-root physical isolation:

```
 User Local System
 └── %LOCALAPPDATA%\GodsWorkbench\ (Production Root - Decoupled from Git Tree)
     ├── data/                  # Core asset registry, project snapshots, prompt stores (Deterministic JSON)
     ├── auth/auth.sqlite3      # Local cryptographic authentication and session database
     └── video/                 # Generated video binaries, media caches, and job artifacts
```

- **Production Mode (`--prod`)**:
  Default root is `%LOCALAPPDATA%\GodsWorkbench\`. Code updates, Git pulls, or branch switching **will never** overwrite, mutate, or taint your production creative assets.
- **Development Sandbox Mode (`dev`)**:
  Stores runtime data strictly inside `.local/runtime-dev/`. This folder is guarded by `.gitignore` and operates in total isolation from your production libraries.
- **Automated Testing Mode (`test`)**:
  Operates on ephemeral `tmp_path` directories for each isolated test case, cleaning up immediately after execution.
- **Path Traversal & Reparse-Point Guard**:
  The filesystem engine strictly blocks junctions and symbolic links to prevent directory escape and safeguard sensitive OS directories.

---

## 🛡️ Data Security & Asset Sovereignty

1. **Complete Ownership Remains with You**
   All projects, prompt recipes, asset metadata, image outputs, and script breakdowns are serialized into standardized, open JSON schemas and native files on your machine. You possess 100% sovereignty, exportability, and portability.
2. **Zero Telemetry & Zero Hidden Egress**
   No covert telemetry beacons, no unauthorized behavior tracking, and no unconsented asset uploads. All external model integrations strictly follow explicit, transparent Provider protocol boundaries.
3. **Local Authentication & Security Boundaries**
   Features a native local credential store without insecure default passwords. Sensitive write endpoints and state changes require local session authentication.
4. **Deterministic Sequencing & Orphan Protection**
   The sequence reservation engine ensures that IDs are allocated monotonically. In the event of an unexpected process interruption, consumed sequence numbers are never reallocated, guaranteeing zero ID collision.

---

## 📄 License

This project is licensed under the [Apache License 2.0](LICENSE). For full licensing details and third-party attributions, please refer to [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

---

## 📈 Star History

[![Star History Chart](https://api.star-history.com/svg?repos=qinxuedong/Gods-Workbench&type=Date)](https://star-history.com/#qinxuedong/Gods-Workbench&Date)

---

<div align="center">
  <sub>Made with ❤️ for AI Creators and Digital Artisans.</sub>
</div>
