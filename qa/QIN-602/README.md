# QIN-602 AC3 浏览器证据（只读分支）

- 对象：GitHub #711 / QIN-602，草稿 PR #712
- **绑定 head：`f128e84d42eb13ee29d7d720f3968298c5afd74a`**（第 4 轮返工后按当前 PR head 全量重取 24 组）
- `ac3-evidence.json` 的**第一个字段**就是 `head`，并附 `headShort` / `pr`；`ui/measure.json` 是同一份文件的逐字节副本，供按 `ui/measure.json` 路径核对提交归属的审查工具读取
- 第 3 轮归档曾绑定 `915c75de`（第 2 轮 head）。根因是 PR 里的证据链接使用分支名 `qin-602-qa`，`raw.githubusercontent.com` 的 CDN 缓存会返回旧提交内容；**本轮 PR 内所有证据链接一律钉死到证据分支的 commit SHA**，引用时请以 PR 正文给出的 commit-pinned 链接为准
- 第 4 轮返工只把收集箱副作用派发改回统一域网关 `gwCall('inbox','openAssetInbox')`、恢复 `qin-547` 旧断言并迁移等价性护栏；不触碰布局与 CSS，四宽 × 两主题 × 三视图的 24 组量测仍逐项复跑，数值与上一轮一致（724 总览工具栏 129px / 内容 y=351，收集箱与挂载文件夹 160px / y=382，内容区满宽 724px）
- 口径：隔离测试实例 + 无头 Chrome，直接打开资产库嵌入页 `?embedded=1&vault=1`，四宽按嵌入页视口量测
- 组合：724 / 900 / 1440 / 1920 × dark / light，共 8 组；每组验证资产总览 / 收集箱 / 挂载文件夹三个视图 → 24 张截图
- 切换方式：`window.GWAssetTreeBridge.activate('mine:inbox' / 'mine:board:library_demo')`；选中态取 `snapshot().selected_key`（嵌入态下外壳侧栏的唯一状态来源）
- 挂载文件夹夹具：`asset_board_navigation_v1` = `{customBoards:[{id:'library_demo',name:'挂载文件夹A',preset:'all'}],order:['registry','library_demo'],hidden:[]}`

## 判据（24 个视图逐个在 JSON 里落成布尔值，任一条不满足即整组失败）

1. 总览卡片 `.registry-overview-shell` 与工具栏 `.content-toolbar.registry-toolbar` 各恰好 1 份且可见（`topbarOverviewExactlyOne` / `topbarToolbarExactlyOne` / `overviewVisible` / `toolbarVisible`）；
2. 内容区 `.asset-content` 宽度 ≥ 视口 90%（`contentFullWidth`；724/900 曾塌陷到 0，是本票 AC3 的核心）；
3. 总览卡片、工具栏宽度均 ≥ 视口 90%（`overviewFullWidth` / `toolbarFullWidth`），且三者横向不溢出视口（`noHorizontalOverflow`）；
4. 工具栏底边不越过内容区顶边（`toolbarNotOverContent`）；
5. 工具栏高度 ≤ 视口 40%（`toolbarHeightWithin40pct`）、内容区顶边 ≤ 视口 70%（`contentStartsWithin70pct`）、内容区在视口内（`contentInViewport`）；724 收集箱曾因 `flex: 1 1 600px` 在列方向变成高度，工具栏 648px、内容从 y=870 才开始；
6. 主统计卡「资产总量」与「统一资产索引」两个标签不压字（`primaryLabelsNoOverlap`，≤900 宽度判定；724 曾重叠 21px）。1440/1920 存在 QIN-531 既有 2~3px 截断重叠，属范围外设计取舍：`checks` 记 `null` 表示 N/A，`criteria.primaryLabelsCheckApplies=false` 且记录实测 `primaryLabelOverlapPx`，不伪装成通过、也不判失败，建议另立票；
7. 外壳侧栏选中项 `selected_key` 与当前视图一致（`selectedStateMatches`）；顶栏标题与视图一致（`toolbarTitleMatches`；总览无标题、收集箱兜底「资源库」、挂载文件夹「挂载文件夹A」）；
8. 页面 `pageerror` 计数为 0（`pageErrorsEmpty`）；主题与 `data-theme` 一致（`themeMatches`）。

每个视图的 `criteria` 同时落 `minBlockWidth` / `maxToolbarHeight` / `maxContentY` / `viewportWidth` / `viewportHeight` 等阈值，`measure` 保留全部原始矩形数值，可独立复算判据；`cases[].passed` 与顶层 `passed` 汇总 8 组 × 3 视图 = 24 组结果。

## 文件

- `ac3-<宽>-<主题>-<视图>.png`：修复后截图，24 张（视图 = registry / inbox / library_demo）
- `ac3-evidence.json`：修复后逐组量测数据（首字段 head，逐组判据布尔值、阈值与原始数值）
- `ui/measure.json`：与 `ac3-evidence.json` 逐字节相同的副本
- `baseline-724-dark-inbox.png`、`baseline-724-dark-registry.png`、`baseline-evidence.json`：主仓未修复代码的对照（head `a4ecd510`，收集箱视图顶栏计数 0 / 0、内容区宽 20px；修复后 1 / 1 且满宽）

二进制证据只放本只读分支，不进入主干。验收脚本与运行时数据留在本地私有目录，不入库。
