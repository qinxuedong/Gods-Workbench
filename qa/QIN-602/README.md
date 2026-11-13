# QIN-602 AC3 浏览器证据（只读分支）

- 对象：GitHub #711 / QIN-602，草稿 PR #712
- 绑定 head：`e45d64e308d6641b4ec6c8d5231709996f0c6878`（与 `ac3-evidence.json` 的 `head` 字段一致；第 3 轮返工后按新 head 全量重取 24 组）
- 第 3 轮返工只改渲染编排、失败面板文案、测试与 ≤820 嵌入态 CSS 限定；四宽 × 两主题 × 三视图的 24 组量测逐项复跑，数值与上一轮一致，无布局回归
- 口径：隔离测试实例 + 无头 Chrome，直接打开资产库嵌入页 `?embedded=1&vault=1`，四宽按嵌入页视口量测
- 组合：724 / 900 / 1440 / 1920 × dark / light，共 8 组；每组验证资产总览 / 收集箱 / 挂载文件夹三个视图 → 24 张截图
- 切换方式：`window.GWAssetTreeBridge.activate('mine:inbox' / 'mine:board:library_demo')`；选中态取 `snapshot().selected_key`（嵌入态下外壳侧栏的唯一状态来源）
- 挂载文件夹夹具：`asset_board_navigation_v1` = `{customBoards:[{id:'library_demo',name:'挂载文件夹A',preset:'all'}],order:['registry','library_demo'],hidden:[]}`

## 判据（全部量测，任一条不满足即整组失败）

1. 总览卡片 `.registry-overview-shell` 与工具栏 `.content-toolbar.registry-toolbar` 各恰好 1 份；
2. 内容区 `.asset-content` 宽度 ≥ 视口 90%（724/900 曾塌陷到 0，是本票 AC3 的核心）；
3. 总览卡片、工具栏宽度均 ≥ 视口 90%，且三者横向不溢出视口；
4. 工具栏底边不越过内容区顶边（不遮挡）；
5. 工具栏高度 ≤ 视口 40%，内容区顶边 ≤ 视口 70%（724 收集箱曾因 `flex: 1 1 600px` 在列方向变成高度，工具栏 648px、内容从 y=870 才开始）；
6. 主统计卡「资产总量」与「统一资产索引」两个标签不压字（≤900 宽度判定；724 曾重叠 21px）；
7. 外壳侧栏选中项 `selected_key` 与当前视图一致；顶栏标题与视图一致（总览无标题、收集箱兜底「资源库」、挂载文件夹「挂载文件夹A」）；
8. 页面 `pageerror` 计数为 0。

## 文件

- `ac3-<宽>-<主题>-<视图>.png`：修复后截图，24 张（视图 = registry / inbox / library_demo）
- `ac3-evidence.json`：修复后逐组合量测数据（含 head 与判据所需的全部原始数值）
- `baseline-724-dark-inbox.png`、`baseline-724-dark-registry.png`、`baseline-evidence.json`：主仓未修复代码的对照（head `a4ecd510`，收集箱视图顶栏计数 0 / 0、内容区宽 20px；修复后 1 / 1 且满宽）

二进制证据只放本只读分支，不进入主干。验收脚本与运行时数据留在本地私有目录，不入库。
