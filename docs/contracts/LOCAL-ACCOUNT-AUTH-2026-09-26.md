# 本地账户登录增补契约（2026-09-26）

> 状态：CURRENT / VERIFIED。依据重整计划同步三态数据根；接口字段保持稳定，已通过完整运行时与契约测试验收。

依据：用户确认恢复本地账户、数据库登录；不引入外部身份服务，不复制旧仓代码。

## 范围与边界

- 新模式 `local_account`：SQLite 持久化账户、密码哈希、服务端会话；角色只能从数据库获得。
- `python run.py` 及双击启动默认使用此模式；显式 OIDC 配置保留。历史 `local` 仅用于已有开发测试，不是账户登录。
- 账户数据库按入口隔离：测试为每例 `tmp_path/auth/auth.sqlite3`，开发为 `.local/runtime-dev/auth/auth.sqlite3`，生产为 `%LOCALAPPDATA%/GodsWorkbench/auth/auth.sqlite3`；可用 `GW_LOCAL_AUTH_DB` 指定符合当前入口边界的绝对路径。不得进入 Git，不保存明文密码或会话令牌。
- 本地账户主体唯一标识为数据库 `local_users.user_id`。团队消息把它作为受信 `AuthContext.subject`，并用身份域 `local_account` 与治理 `user_id` 做精确绑定；只保存身份引用，不复制账户凭据或会话。
- 团队/消息状态由独立 SQLite `team_messages.sqlite3` 持久化，位于当前入口的 `data/`（测试 `tmp_path/data/`、开发 `.local/runtime-dev/data/`、生产 `%LOCALAPPDATA%/GodsWorkbench/data/`；显式 `GW_DATA_DIR` 同样必须满足隔离边界）；账户库仍是账户和角色权威。治理用户快照不是会话授权依据，每个请求仍重读有效账户会话与当前角色。
- 团队消息 schema、角色矩阵、幂等/分页和故障语义以 `TEAM-MESSAGES-INTERFACE-CATALOG.yaml` 为唯一冻结契约。本地账户 ID 到治理用户的绑定只可由服务端认证上下文建立；客户端不得提交作者、身份域、角色或团队成员资格。
- 团队 SQLite 仅支持单实例、单应用进程；不支持多 worker/多实例。SQLite 事务与治理存储锁保证消息追加与团队成员撤权不发生检查-写入竞态。

## 接口

- `GET /api/asset-auth/status`：本地账户模式返回 `setup_required`、`authenticated`、`principal`、`login_available`，响应禁止缓存。
- `POST /api/asset-auth/local/setup`：仅数据库无账户时、仅回环客户端、同源浏览器请求创建首位管理员。事务锁确保只能创建一次；重复返回 409。请求仅 `username`、`password`。
- `POST /api/asset-auth/local/login`：验证账号与密码，成功轮换会话、设置 HttpOnly/SameSite=Strict Cookie；错误统一 401，不泄漏账户是否存在；有持久化限速。
- `POST /api/asset-auth/logout`：删除本地服务端会话并清除 Cookie。
- 账号 3–64 位 ASCII 字母/数字/下划线/点/短横线；创建密码 8–128 字符，至少包含一个英文字母和一个数字，不强制特殊符号；登录不重新施加创建策略，以兼容已有账户；无默认账号或默认密码。
- 密码使用独立随机盐与 scrypt 派生；会话随机生成，数据库仅存 SHA-256 摘要，8 小时到期。每次请求重新读取数据库权限。
- 本地模式拒绝伪造 Bearer、`X-User-Role`、跨源写请求；不更改已有 OIDC 契约。

## 验证计划

1. 空库首次创建、重复/并发创建、密码校验、数据库重开与会话恢复。
2. 匿名/伪造权限拒绝；管理员会话可创建项目；只读账户不可通过请求头提权。
3. 错误密码、登录限速、过期与退出撤销、跨站来源与首次创建回环限制。
4. 实际浏览器：点击头像→首次创建→刷新后已登录→退出→重新登录→业务写操作。
5. 团队消息专项验证身份域精确绑定、仅 local user_id 映射、无 owner_subject/username 冒认、移除后幂等重放拒绝、坏库/写盘失败503、同键重启读回；详见团队消息契约。
6. `pytest -v`，黄金夹具及卫生门禁；测试数据库仅位于每例临时目录，开发数据库仅位于开发沙盒，生产数据库不得位于源码仓库。
