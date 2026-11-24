# QIN-645 迁出取证原始证据（只读）

本分支只存放 issue #798 / QIN-645 的取证产物，**不参与任何构建**，可直接删除。

- 迁出前实例：主仓 `master`（含 `web/css/base/signal-flow.css`），端口 2089
- 迁出后实例：本票 worktree（分支 `qin-645`），端口 2090
- 两侧同一套 dev 数据目录、同一本地账号 `qa_local`，同一批采集脚本

## 矩阵

9 个目标 × 4 个宽度 × dark/light = **72 格**，两侧各 72 格，无缺格、无缺图。

| 目标 | 为什么必须在矩阵里 |
| --- | --- |
| `task-center` / `episode-pipeline` / `governance` | 票面范围内三页 |
| `canvas-list` | 票面范围内；默认态 + 总览布局分支 |
| `canvas-interactive` | 画布页强制交互态（选中项目行 / 选中卡片 / 状态条显示）——变量类回归只在此态显色 |
| `canvas-overview` | `?layout=overview`，该分支会把 `body.className` 整串重写 |
| `asset-share` | 票面范围内；空实例提示态 |
| `asset-share-content` | 公开分享内容态（`page.route` 伪造公开分享 API 的契约形状响应驱动） |
| `asset-manager` | **范围外**页面，同为 `body.gw-signal` 但从未加载旧样式；它的金色主题是「作用域过宽」阻断项的判据 |

宽度 `724 / 900 / 1440 / 1920`，主题 `dark / light`。

## 文件

| 路径 | 内容 |
| --- | --- |
| `manifest.json` | 逐格清单：节点数、文本溢出数、按钮塌陷数、页面错误数、文档宽、body class、交互态强制结果、截图 sha256、该格 computedStyle 快照 sha256 |
| `diff.json` | 逐元素逐属性差异明细（当前为 `[]`，即 72 格零差异） |
| `before/snapshot.json.gz`、`after/snapshot.json.gz` | 64 格主矩阵的 computedStyle 快照（gzip） |
| `before/snapshot-share-content.json.gz`、`after/…` | 8 格分享内容态快照（gzip） |
| `before/shots/`、`after/shots/` | 72 + 72 张 PNG |
| `sf-baseline.css` | `git show <master>:web/css/base/signal-flow.css` 原文副本，用于复核迁移来源 |
| `tools/` | 采集、对拍、清单、规则枚举、变量审计脚本，可原样复跑 |

`manifest.json` 里的 `cell_sha256` 是对**解压后**该格 `nodes/overflow/collapsed`
重新序列化（`sort_keys=True`、`ensure_ascii=False`）后取的 sha256；`shot_sha256`
直接对 PNG 字节取。两者都可离线复核。

## 复跑

```bash
# 迁出前
CAPTURE_BASE=http://127.0.0.1:<master 实例> python tools/capture.py before
CAPTURE_BASE=http://127.0.0.1:<master 实例> python tools/capture_share.py before
# 迁出后
CAPTURE_BASE=http://127.0.0.1:<worktree 实例> python tools/capture.py after
CAPTURE_BASE=http://127.0.0.1:<worktree 实例> python tools/capture_share.py after
python tools/manifest.py
python tools/diff.py
```

单目标重采：`CAPTURE_ONLY=canvas-interactive,asset-share python tools/capture.py after`
（会与既有 `snapshot.json` 合并，只替换同名目标）。
