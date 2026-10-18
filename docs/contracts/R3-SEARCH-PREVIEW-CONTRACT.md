# C11 R3 跨域多维检索与代理预览契约

- **契约标识**：`C11-R3-SEARCH-PREVIEW-001`
- **状态**：`PROPOSED / IMPLEMENTATION INPUT`
- **任务**：QIN-201（C11-T1）
- **覆盖验收**：C11-AC1、C11-AC3；关联 US-10、US-12A、US-16、US-23
- **依据**：`.local/cuts/C11-2026-10-18.md`、`C11-TASKS.md`、`docs/contracts/ASSET-REGISTRY-INTERFACE-CATALOG.yaml`
- **证据锚点**：`fixture_id = c11-t1-search-filter-contract-001`；实现任务必须同时提供 API JSON、可复跑测试日志和必要的浏览器截图。

本文是 C11-T2～T5 的字段和状态输入。它定义结构化检索、游标和代理预览的共同语义，不代表运行时已经接入，也不把文档内容当作真实浏览器、真实媒体或稳定版资格证据。

## 1. 边界和不变量

1. **沿用现有服务边界**：实现复用资产注册表、项目、画布和任务的现有查询边界；本契约不授权新建平行搜索服务、向量库、插件协议或跨域复制存储。
2. **结构化检索**：支持名称/标识的普通文本匹配和结构化过滤；不承诺自然语言理解、语义相似度或全文索引。没有可求值的字段必须显式降级或失败关闭，不能静默忽略过滤。
3. **稳定标识**：跨域结果只使用 `project_id`、`canvas_id`、`entity_id`、`job_id`、`asset_id`、`version_id`、`tag_id` 等已登记字段。禁止用标题、数组下标、URL 或临时 `id` 作为主键。
4. **权限先于检索**：先按当前认证主体和项目边界过滤，再计算总数、分页、facets 或预览状态。无权对象不得通过总数、游标、错误或预览 URL 泄露存在性。
5. **事实优先**：返回的名称、标签、时间、状态、媒体尺寸和版本必须来自当前可回读事实；未知、等待、失败和缺少依赖不得伪造成成功。
6. **时间和哈希**：时间统一使用 UTC RFC 3339；查询指纹由服务端规范化计算，客户端不能修改游标内的权限、集合版本或过滤条件。

## 2. 结构化检索请求

C11 继续使用现有查询入口；下面是入口接收的逻辑请求形状。字段缺失使用默认值，未知字段或不支持的值返回标准 400，不得忽略。

```json
{
  "scope": ["asset", "project", "canvas", "job"],
  "text": "可选的名称或稳定标识匹配",
  "filters": {
    "kind": ["image", "video", "audio", "document", "model", "font", "other"],
    "project_id": ["project-0001"],
    "tag_id": ["tag-0001"],
    "created_at": {
      "from": "2026-10-18T00:00:00Z",
      "to": "2026-10-19T00:00:00Z"
    },
    "status": {
      "asset": ["active", "archived"],
      "job": ["pending", "running", "succeeded", "failed", "cancelled", "paused", "waiting_review"]
    }
  },
  "sort": "newest",
  "limit": 50,
  "cursor": null,
  "include": ["facets", "preview"]
}
```

### 2.1 过滤规则

| 字段 | 规则 |
| --- | --- |
| `scope` | 至少一个域；`asset`、`project`、`canvas`、`job` 可组合。省略时由现有入口默认域决定，不扩大到未接入的域。 |
| `text` | 对已授权对象的名称或稳定标识做普通匹配；空字符串等同未提供。不得将自然语言、向量相似度或未登记字段当作命中条件。 |
| `kind` | 只接受资产注册表已登记的七种值；同一字段内为 OR，不同字段之间为 AND。未知值返回 `UNSUPPORTED_FILTER_VALUE`。 |
| `project_id`、`tag_id` | 只接受真实稳定 ID；同一字段内为 OR。不存在或无权 ID 返回空集合，不泄露对象存在性。 |
| `created_at` | `from` 含、`to` 不含；两者均为 UTC RFC 3339。缺失一端表示无界；`from >= to` 返回 `INVALID_TIME_RANGE`。 |
| `status` | 按域提供已登记状态枚举；跨域请求必须显式给出域名。无法求值的域/状态返回 `UNSUPPORTED_FILTER_VALUE` 或空集合并在 `data_gaps` 说明，不能返回可能不满足条件的行。 |
| `sort` | 支持 `newest`、`oldest`、`name`、`asset_id`；相同主排序值按对应稳定 ID 升序打破平局。其他排序值返回 `UNSUPPORTED_SORT`。 |
| `limit` | 整数 1～200，默认 50；超出范围返回 `INVALID_LIMIT`。服务端可以返回更少，但不得超过请求值。 |
| `include` | 只接受 `facets`、`preview`。未请求 `preview` 时不得生成或探测媒体；未请求 `facets` 时不返回推测计数。 |

过滤条件在同一字段内取 OR，在不同字段之间取 AND。空过滤返回当前主体有权访问的默认排序集合，不能被解释为“所有主体”。

## 3. 游标和分页

响应必须包含以下分页对象：

```json
{
  "page": {
    "limit": 50,
    "has_more": true,
    "next_cursor": "<opaque-base64url>",
    "collection_revision": 17
  }
}
```

- `next_cursor` 是服务端生成的不透明 base64url 值，至少绑定规范化查询指纹、`scope`、授权主体、集合 `collection_revision` 和最后一项的稳定排序位置。客户端不得解析、编辑或把它当作授权凭据。
- 将游标与另一组过滤、排序、scope、主体或集合 revision 一起提交，返回 400 `INVALID_CURSOR`；集合 revision 已变化且无法安全续页时返回 409 `VERSION_CONFLICT`，并要求从第一页重新读取。
- 首次请求的 `cursor` 为 `null`/省略。`has_more=false` 时 `next_cursor` 必须为 `null`。空集合也必须返回稳定的 `page` 对象。
- 兼容现有资产入口时可以继续返回 `next_offset`，但它不能取代本契约的 `next_cursor`，也不能与不一致的游标同时作为分页依据。
- 每页不得重复或跳过已授权集合中的同一稳定记录；集合变化后的最终一致性必须在 `data_status` 和 `data_gaps` 中说明。

## 4. 结构化检索响应

```json
{
  "items": [
    {
      "entity_type": "asset",
      "asset_id": "asset-0001",
      "version_id": "asset-0001-v001",
      "project_id": "project-0001",
      "name": "reference.png",
      "kind": "image",
      "tag_id": ["tag-0001"],
      "status": "active",
      "created_at": "2026-10-18T08:00:00Z",
      "preview": {
        "availability": "ready",
        "artifact_id": "artifact-0001",
        "media_type": "image/png",
        "source": "proxy",
        "url": "/api/asset-registry/assets/asset-0001/media",
        "width": 1280,
        "height": 720,
        "duration_ms": null
      }
    }
  ],
  "page": {
    "limit": 50,
    "has_more": false,
    "next_cursor": null,
    "collection_revision": 17
  },
  "applied_filters": {},
  "facets": null,
  "data_status": "complete",
  "data_gaps": []
}
```

- `entity_type` 决定必须存在的稳定 ID；不得另加通用 `id`。项目、画布、任务结果使用各自的 `project_id`、`canvas_id`、`job_id`，资产版本同时携带 `asset_id`、`version_id` 和 `version`（若已有版本字段）。
- `applied_filters` 是服务端规范化后的实际条件，不能只回显客户端原文。`facets` 只统计主体可见、实际求值的记录；未知字段不计为零。
- `data_status` 取 `complete`、`degraded` 或 `not_integrated`。降级时必须列出机器可读的 `data_gaps`，并禁止以“无结果”掩盖“无法求值”。
- 所有失败遵循统一外层 `{"detail":{"code":"...","message":"..."}}`；响应不得含令牌、Cookie、绝对路径或未授权对象的标题。

## 5. 轻量代理媒体预览

代理是原片/原图的派生读取表示，不是新的业务资产版本，也不覆盖原始文件。代理请求必须绑定精确的 `asset_id`、`version_id`（必要时还包括 `version`）和主体权限。

### 5.1 预览状态

`preview` 只允许以下状态：

| `availability` | 语义 | 客户端处理 |
| --- | --- | --- |
| `ready` | 已登记的代理可读，字段与来源版本可回读 | 播放/显示代理；仍保留原片回读入口 |
| `pending` | 代理生成任务真实存在但尚未终态 | 显示等待状态并使用真实 `job_id`；不得伪造媒体 URL |
| `unavailable` | 源离线、文件不可读或依赖缺失 | 显示规范占位和下一步；原片不被覆盖 |
| `not_integrated` | 当前部署没有代理能力 | 显示明确降级，不宣称已生成 |

`ready` 必须携带真实 `artifact_id` 或现有媒体入口、MIME、尺寸及（视频）时长；`pending` 必须携带可回读的 `job_id`；其余状态必须携带 `reason_code` 和可执行的 `next_action`。不得用 data URI、内嵌二进制或随机占位图冒充代理。

### 5.2 代理配置与派生约束

- 代理格式、尺寸、帧率、码率和生成耗时由服务端实际配置返回，使用 `profile`、`width`、`height`、`fps`、`bitrate`、`duration_ms` 和 `generated_at` 等字段记录；本契约不预先冻结未经实测和秦先生确认的规模、p95、首屏或 RPO/RTO 阈值。
- 视频代理必须保持原始时间轴和音画同步；图像代理必须声明尺寸和 MIME。任何转码失败、缺少 ffmpeg/ffprobe 或源不可读都返回真实降级状态，不登记成功产物。
- 派生任务应沿用统一任务状态、稳定 `job_id`、幂等语义和主体隔离；未知远端结果禁止盲目重试或伪造 `job_id`。
- 原片与代理的关系必须可按 `asset_id → version_id → artifact_id/job_id` 回读；删除或回收原片时不得留下可访问的代理 URL。
- 代理响应沿用现有媒体边界：不跟随不可信重定向，不接受 URL 内凭据，不绕过允许根目录或主体权限，不在日志中写入密钥、Cookie 或绝对路径。

### 5.3 缺失和错误

- 已登记版本没有可读取媒体时，媒体读取接口返回 503 `MEDIA_NOT_AVAILABLE`；检索列表可以保留该条记录，但 `preview.availability=unavailable`、`data_status=degraded` 并说明 `data_gaps`。
- 未认证返回 401 `UNAUTHENTICATED`；无权返回 403 `FORBIDDEN`（或现有已登记的隐藏 404），不能通过代理状态探测资源。
- 非法过滤、游标、时间范围或分页参数分别返回 400 `UNSUPPORTED_FILTER_VALUE`、`INVALID_CURSOR`、`INVALID_TIME_RANGE`、`INVALID_LIMIT`；集合 revision 冲突返回 409 `VERSION_CONFLICT`；代理依赖未接入返回 503 `MEDIA_PROXY_NOT_INTEGRATED`。

## 6. 键盘和上下文动作映射

快捷键只在工作台搜索结果区域或其打开的浮层获得作用域；输入框、文本区域和可编辑控件默认不抢占字符键。处理器只有在动作被当前作用域消费后才阻止浏览器默认行为或停止传播。

| 按键 | 作用域 | 动作 | 约束和焦点结果 |
| --- | --- | --- | --- |
| `Space` | 已聚焦可预览结果 | 打开/关闭该版本预览 | `unavailable`/`pending` 不伪造播放；浮层打开后焦点进入浮层，关闭后回到触发项 |
| `J` / `K` | 结果列表 | 下一个/上一个可见结果 | 使用当前排序和游标集合；首尾不循环，不跨越未加载页；无结果不改变焦点 |
| `Enter` | 已聚焦结果 | 打开该结果的稳定深链 | 只导航到当前主体可见对象；无深链时给出可读错误，不猜 URL |
| `Escape` | 最顶层浮层/菜单/预览 | 逐层关闭 | 关闭后焦点返还触发控件；没有浮层时不改变页面历史 |
| `Tab` / `Shift+Tab` | 当前浮层 | 在浮层内循环 | 不穿透底层页面；浮层关闭后恢复原焦点上下文 |

焦点、键盘和预览事件必须可在刷新后按稳定对象回读；实现不得用全局 document 监听器把快捷键传播到不相关页面，也不得用快捷键绕过权限或确认门。

## 7. 验收和实施交接

| 验收 | 本契约要求 | 后续证据 |
| --- | --- | --- |
| C11-AC1 | 类型、项目、标签、时间、状态组合过滤；稳定游标、空条件和边界错误；权限过滤先于计数和分页 | API JSON、`fixture_id`、契约/接口测试日志 |
| C11-AC3 | 精确版本绑定代理预览；源离线、依赖缺失和代理未接入时明确降级；原片不覆盖 | API JSON、浏览器截图、媒体测试日志 |

- C11-T2 消费第 2～4 节实现资产注册表的复合过滤和游标，不创建第二套资产或标签存储。
- C11-T3 消费第 6 节动作映射，沿用现有浮层、焦点和深链规范。
- C11-T4 消费第 5 节状态和错误语义，真实媒体与代理关系必须可回读。
- C11-T5 只在 T1～T4 的真实证据齐备后汇总长稳数据和 R3 资格报告；本文件不提前宣称通过。

## 8. 明确 OUT

自然语言/向量检索、NAS/多机部署、NLE 回套、实时语音、外部 CDN、插件运行时协议、自动改变用户产物和任何未经用户确认的性能阈值均不属于本契约。
