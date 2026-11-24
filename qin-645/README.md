# QIN-645 迁出取证原始证据（只读）

本分支只存放 issue #798 / QIN-645 的取证产物，**不参与任何构建**，可直接删除。

- 迁出前实例：主仓 `master` @ `011f4d49`（含 `web/css/base/signal-flow.css`），端口 2089
- 迁出后实例：本票 worktree（分支 `qin-645` @ `e247e4c0`），端口 2090
- 两侧同一套 dev 数据目录、同一本地账号 `qa_local`，同一批采集脚本

## §4.3 反证（head `e247e4c0`）

三个「关键接线」各配一份可复现补丁，`git apply` 后**必须红**、`git checkout --` 还原后**必须绿**。
执行器 `tools/abtest.py`，原始输出 `r3/abtest-e247e4c0.log`。

| 反证 | 改坏点（补丁） | 契约测试 | 运行态矩阵 | 红态证据 |
| --- | --- | --- | --- | --- |
| m1 文档标记门控 | `r3/patches/m1-marker-gate.patch`：给 `web/embeds/asset-manager.html` 的 body 加 `gw-legacy-signal` | exit 1 | exit 1，asset-manager 8 格 3034 个元素差异 | `r3/red/m1-marker-gate-diff.json.gz` |
| m2 变量层级 | `r3/patches/m2-variable-level.patch`：把 `:root:where(:has(...))` 降回 body 级 | exit 1 | exit 1，canvas-interactive 4 个元素差异（`.ws-status.show` 底色 `rgb(232,237,245)`→`rgb(105,214,255)`） | `r3/red/m2-variable-level-diff.json.gz` |
| m3 圆角层提权 | `r3/patches/m3-radius-prefix.patch`：删掉圆角规则的 `html ` 前缀 | exit 1 | exit 1，task-center 32 个元素 `border-radius: 6px → 8px` | `r3/red/m3-radius-prefix-diff.json.gz` |

还原后两条判据都回到 exit 0（`0 差异明细、0 硬判据变化`），工作树 `git diff --quiet` exit 0。

结论：三条接线的失效都能被**运行态读数**直接观测到，不是只有静态断言变红。
`tools/diff.py` 为此新增 `--fail-on-diff`：有差异即 exit 1。

## 本轮重拍（head `e247e4c0`）

`e247e4c0` 处理 r2 复审建议：`asset-share.html` 把 charset 提到新增 link 之前、
分享页旧名别名块收窄为 `:root:where(:has(> body.gw-page--asset-share))`（特异性仍 (0,1,0)）、
`issue-798.json` 补登记 `web/css/pages/asset-share.css`、`qin-473` 断言文案去掉已下线文件名。
四个改动都过 72 格重拍：**0 差异明细、0 硬判据变化**（`r3/evidence-4.1-matrix.log`，exit 0）。

上一轮（`19497942`）的说明保留在下方，便于对照两轮之间的像素噪声口径。

## 上一轮重拍（head `19497942`）

`19497942` 只改 `hardware-design-system.css` 里的注释文案（更正「51 行」的表述），
剥掉 CSS 注释后与上一 head `00099f47` 的同文件**逐字节相同**
（sha256 `96051b61f2b61c0361ed82ab7ae98f139cc110c50091c7659d3fed41c7d35dce`）。
即便渲染输入未变，`after` 侧仍在新 head 上整批重拍 72 格，`before` 侧沿用同一份
master 产物，避免出现「证据钉旧 head」的口子。

两次 `after` 之间的如实记录：72 张 PNG 里有 7 张字节不同。逐像素比对后，
6 张是 3–4 个像素的抗锯齿噪声（如 `asset-manager__724__dark` 仅 4 像素），
最大一处 `task-center__724__light` 有 2052 像素，逐行定位落在页面下方的
「加载更多」按钮上——该按钮的加载瞬时态在两次运行取到不同帧，与样式迁移无关。
本轮所有判据（computedStyle 逐元素逐属性、硬判据）都用**样式读数**判定，
不受这类像素噪声影响；噪声本身如实保留，不挑选「好看」的那一张替换。

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
| `tools/` | 采集、对拍、清单、规则枚举、变量审计脚本，可原样复跑；`tools/abtest.py` 为 §4.3 反证执行器，`tools/diff.py --fail-on-diff` 有差异即 exit 1 |
| `r3/patches/` | §4.3 三份改坏补丁（LF 字节，`git apply` 直接可复跑） |
| `r3/red/` | 三个反证「改坏后」的差异明细（gzip），含元素结构路径、class 与逐属性 before/after |
| `r3/*.log` | 反证与 §4.1 门禁的原始输出（含每条命令的退出码） |

`manifest.json` 里的 `cell_sha256` 是对**解压后**该格 `nodes/overflow/collapsed`
重新序列化（`sort_keys=True`、`ensure_ascii=False`）后取的 sha256；`shot_sha256`
直接对 PNG 字节取。两者都可离线复核。顶层 `head` 记录该侧取证所对应的提交
（`before` = `011f4d49`，`after` = `19497942`），`generated_at` 为 UTC 秒级时间。

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
