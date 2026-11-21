# 桌面端与 NAS 兼容契约（`gw-compat-v1`）

本文件定义 Gods Workbench 桌面端连接本机或远程后端时的身份、版本与能力判定规则。它适用于源码开发、标准 Windows 安装版和 NAS 部署；**远程 NAS 不以「与桌面精确同版本」为既定策略**。两端版本可以不同，但必须通过同一能力契约。

## 1. 身份与连接边界

- 桌面端只把 `127.0.0.1` / `[::1]` 的非反向代理直连视为本机连接；`localhost`、局域网地址、反代域名一律按远程处理。
- 任何连接都先完成身份、版本与能力握手，再决定是否放行。发现列表只是候选，不能凭单次 `/healthz` 直接进入。
- 远程 / NAS 连接永不注入本机一次性票据，也不保存本机设备密钥。一次性票据只在 loopback 与内存中产生和交付。
- 本机自动进入还要求安装版清单、loopback、能力契约、ACL 与安装目录/数据根互不包含全部通过；任一不通过即回退密码登录，不自动放宽权限。

## 2. 能力契约 `gw-compat-v1`

### 2.1 必需探测

所有探测均为只读，且未认证即可执行：

| 探测 | 要求 |
| :--- | :--- |
| `GET /healthz` | HTTP 200；`status == "ok"`；`mode == "cleanroom"`；包含 `auth_mode`、`oidc_ready`、`frozen_contracts`、`release_authorized`、`mode`、`status` 键 |
| `GET /openapi.json` | `info.title == "Gods-Workbench Cleanroom API"`；包含下方全部方法与路径 |
| `GET /api/asset-auth/status` | JSON 含 `auth_mode`、`oidc_ready`、`authenticated`、`login_available`、`logout_available`、`reason`、`release_authorized` 键；无 Cookie 时 `authenticated == false`；响应含 `Cache-Control: no-store` |
| `GET /api/asset-registry/projects`（无凭据） | HTTP 401；标准错误包 `detail.code == "UNAUTHORIZED"`；只读探测，不写数据 |

必需 OpenAPI 操作：

```text
GET /healthz
GET /api/asset-auth/status
POST /api/asset-auth/local/login
POST /api/asset-auth/local/setup
GET /api/asset-registry/projects
POST /api/asset-registry/projects
PATCH /api/asset-registry/projects/{project_id}
GET /api/canvases
POST /api/canvases
PATCH /api/canvases/{canvas_id}
```

可选能力：`POST /api/asset-auth/local/trusted-bootstrap`。存在时本机受信进入可用；缺失时安装版仍可连接，但只能密码登录，**不得宣称桌面免登录完成**。

### 2.2 规范化与摘要

能力摘要由 [`clients/desktop/packaging/compat_contract.py`](../../clients/desktop/packaging/compat_contract.py) 作为单一规范定义：把能力形状（profile、OpenAPI 标题与操作集合、健康与认证状态必需键、未认证 401 错误码、版本来源标记要求）规范化为键排序、紧致分隔的 JSON，再做 SHA-256。

当前基线：

```text
cc47d8d4ac74adc87f073127886e13e71ffbcd3aef3248a11bb957d55c4086e2
```

**版本号不参与 digest。** 版本不同但 digest 相同可以连接；版本相同但 digest 不同必须拒绝。新增、删除必需路径或改变必需键即提升 `capability_profile`；旧桌面遇到未知 profile 一律失败关闭并提示升级，不猜测、不编造 semver 范围。

桌面基线来自安装包内的 `bundle-manifest.json`。源码开发构建没有安装清单，不能据此验证远程 NAS，只能用于本机开发，并明确标记「开发源构建」。

## 3. 版本来源与真实性

`run.py` 按以下顺序解析产品版本，且绝不使用静态默认 `0.1.0` 冒充真实版本：

| 优先级 | 来源 | 来源标记 | 适用 |
| :--- | :--- | :--- | :--- |
| 1 | 同目录 `bundle-manifest.json` | `manifest` | 标准安装版、正式 NAS 发布镜像 |
| 2 | `GW_PRODUCT_VERSION`（须通过格式校验） | `declared` | 运维显式声明的源码部署；仅作展示，不单独构成远程兼容依据 |
| 3 | `git describe --tags --always --dirty`（只读、超时、无 shell） | `git` | 源码开发或带 Git 的源码部署 |
| 4 | 其他 | `unknown` | 明确显示未知并关闭远程兼容放行 |

应用暴露的版本形如 `<product_version>+gw.<provenance>`。`+gw.` 是来源标记，不是 semver 兼容范围：

- 无 `+gw.` 标记的 `0.1.0`、空值或 `unknown` 一律按未知处理。
- 安装版要求 OpenAPI `info.version` 精确等于 `manifest.product_version + "+gw." + manifest.build_id`；不一致视为安装损坏并阻断。
- 不经 `run.py` 启动的入口（例如直接启动 ASGI 应用）不会获得真实版本注入，按 `legacy-default` / 未知处理；不得据此判定兼容。
- `run.py --version` 输出产品版本与来源；`run.py --self-check --expect-version <v>` 只加载运行时与应用、校验版本与能力基线，不创建目录、账户或会话。

## 4. 缓存规则

- 应用内：写入版本后必须清空 OpenAPI schema 缓存，确保下一次 `/openapi.json` 反映新版本。
- 探测端：每次探测带随机 nonce 查询参数，并发送 `Cache-Control: no-cache, no-store` 与 `Pragma: no-cache`；两次探测的版本与操作集合必须一致。
- `run.py` 只对 `/openapi.json` 与 `/healthz` 增加 `Cache-Control: no-store, no-cache, must-revalidate`、`Pragma: no-cache`、`Expires: 0` 窄中间件；其他路径行为不变。
- 探测响应出现以下任一信号即判定「缓存不可信」，失败关闭并要求直连或由运维禁用缓存：`Age`、非 MISS/BYPASS 的 `X-Cache`、`CF-Cache-Status: HIT`、`Via`，或 `Cache-Control` 表明可缓存。
- 已知残余风险：不透明中间缓存可能不返回任何信号。这是运维约束——NAS 反向代理不得缓存版本/能力探测路径；排障时应使用直接地址复验。

## 5. 失败语义与恢复

未知、不兼容、缓存不可信、无 OpenAPI、旧端点、版本缺失或 ACL 不合格时：

1. 明确失败，展示双方身份、可识别的版本与来源、能力摘要状态与失败项；未知就写未知。
2. 不写项目或画布数据，不带未验证版本继续。
3. 恢复方式：升级或改用带 `bundle-manifest.json` 的正式 NAS 发布镜像；重启内置后端；恢复被替换的安装文件；取得可验证的新能力契约；检查账户目录 ACL 后使用密码登录。
4. 不提供「忽略检查」开关，不降级为仅比较版本号，不把失败显示为成功。

## 6. 与 QIN-630 的共享边界

- 后端与 CLI 由 QIN-630 提供：一次性票据、`trusted-open` 输出、兑换与 `needs_manual_claim` 状态机。
- 本桌面只在 loopback + 安装清单匹配 + 能力含受信兑换接口 + ACL 通过时调用 `trusted-open`；票据只从内存 stdout 读取，经 URL fragment 交付，兑换后清除。
- QIN-630 未部署或未合入时，能力探测判定受信进入不可用，桌面回退密码登录，不伪造免登录成功。
- 安装版桌面开箱免登录必须在 QIN-626 与 QIN-630 联合验收后才能宣称完成。

## 7. 版本演进

- 修改必需路径集合、必需键、401 错误包或摘要算法：必须提升 `capability_profile`，同步更新本文件、[`clients/desktop/packaging/compat_contract.py`](../../clients/desktop/packaging/compat_contract.py)、Rust 探针与契约测试。
- 不允许在 `gw-compat-v1` 下悄悄增删必需项；未知 profile 一律失败关闭。
- 兼容契约的验证命令：

```bash
python -m pytest tests/runtime/test_qin_626_desktop_install_lifecycle.py -q
python clients/desktop/packaging/compat_contract.py
```
