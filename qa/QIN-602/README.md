# QIN-602 AC3 浏览器证据（只读分支）

- 对象：GitHub #711 / QIN-602，草稿 PR #712
- 口径：隔离测试实例 + 无头 Chrome，直接打开资产库嵌入页 `?embedded=1&vault=1`，四宽按嵌入页视口量测
- 组合：724 / 900 / 1440 / 1920 × dark / light，共 8 组；每组验证资产总览与收集箱两个视图
- 切换方式：`window.GWAssetTreeBridge.activate('mine:inbox')`；选中态取 `snapshot().selected_key`（嵌入态下外壳侧栏的唯一状态来源）
- 判据：总览卡片与工具栏各恰好 1 份、可见、不横向溢出、工具栏不遮挡内容区、外壳侧栏选中项正确

## 文件

- `ac3-<宽>-<主题>-<视图>.png`：修复后截图（16 张）
- `ac3-evidence.json`：修复后逐组合量测数据
- `baseline-724-dark-*.png`、`baseline-evidence.json`：主仓未修复代码的对照，收集箱视图顶栏计数 0 / 0（修复后 1 / 1）

二进制证据只放本只读分支，不进入主干。验收脚本与运行时数据留在本地私有目录，不入库。
