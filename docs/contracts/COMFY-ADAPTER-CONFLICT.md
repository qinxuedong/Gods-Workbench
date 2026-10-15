# ComfyUI 页面适配冲突与升级契约（COMFY_ADAPTER_CONFLICT）

## 背景
- 神工坊在打开本地 ComfyUI 时，把页面适配脚本安装到宿主前端目录 `extensions/gods_workbench/workbench.js`（接口返回的 `extension_url` 即 `/extensions/gods_workbench/workbench.js`）。
- 适配脚本首行携带版本标记：`// gods-workbench-adapter-version: 2`。服务端常量 `ADAPTER_VERSION` 直接从随包脚本解析，前端无需自行判断版本号。
- 目标位置已有内容且与本次要安装的脚本不一致时，服务端一律失败关闭（409），绝不静默覆盖宿主上的文件。

## 触发接口
- `POST /api/god_workflow/open-in-comfy` 与 `POST /api/god_workflow/open-in-comfy/{workflow_id}`。
- 仅当请求携带工作流且 ComfyUI 地址为本机回环地址时才安装适配；其余路径行为不变。

## 409 响应形状
`detail` 在既有 `code`、`message` 之外新增以下字段（`installed_version`、`required_version`、`fix_hint`、`upgradable`）：

```json
{
  "detail": {
    "code": "COMFY_ADAPTER_CONFLICT",
    "message": "页面适配文件已有不同内容，已保留原文件，请核对后更新",
    "installed_version": "legacy-9b0ff8a",
    "required_version": "2",
    "fix_hint": "检测到旧版神工坊页面适配（legacy-9b0ff8a），可自动升级：重新打开 ComfyUI 时携带 adapter_upgrade=true，升级完成后请刷新 ComfyUI 页面加载新脚本",
    "upgradable": true
  }
}
```

字段语义：
- `installed_version`：宿主上已安装文件的版本标识。带版本标记时为标记数字字符串（如 `"2"`、`"3"`）；无标记但内容命中既往官方发布指纹时为 `legacy-<短提交号>`；其余为 `"unknown"`。
- `required_version`：本次要安装的适配版本，字符串形式（当前 `"2"`）。
- `upgradable`：仅当已安装文件确认为神工坊官方旧版（legacy 指纹，或标记版本低于所需版本）时为 `true`。
- `fix_hint`：面向用户的中文修复建议，前端可直接展示。

前端处理原则：
- `upgradable=true`：提示用户可一键升级，携带 `adapter_upgrade=true` 重试同一请求。
- `upgradable=false`（`unknown`、同版本被改动、或更高版本标记）：不得自动重试升级；按 `fix_hint` 引导用户先备份并手动移除 `extensions/gods_workbench/workbench.js` 后重试。服务端对未知文件永不覆盖。

## 升级重试
同一请求追加升级开关即可，查询串与 JSON 体二选一；只有字面量 `true` 生效：

```
POST /api/god_workflow/open-in-comfy/{workflow_id}?adapter_upgrade=true
```

或：

```json
POST /api/god_workflow/open-in-comfy/{workflow_id}
{"adapter_upgrade": true}
```

行为约定：
- 升级只原子替换适配脚本这一个文件（临时文件写入后 `os.replace`），不触碰宿主工作流与任何其他文件；ComfyUI 未保存的画布状态保存在浏览器中，不受影响。
- 成功时响应的 `browser_adapter` 为 `{"mode": "managed", "extension_url": "...", "adapter_version": 2, "upgraded": true}`；未发生升级的常规成功返回同一形状、`upgraded: false`。
- 无法升级时仍返回原 409（如 `upgradable=false` 或请求未带 `adapter_upgrade=true`），原文件保持不变。
- **升级后必须刷新 ComfyUI 页面**：浏览器只会页面加载时执行一次扩展脚本，刷新前仍在运行旧脚本。

## 边界
- 本契约只约束 409 明细与升级开关；open-in-comfy 的其余响应字段、认证与宿主在线前置条件维持原状。
- 版本标记只在脚本首行生效；宿主文件中其他位置的同样注释不构成版本声明。
