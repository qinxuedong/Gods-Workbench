# 本地审计 Outbox 与持久收据契约

- 状态：CURRENT / VERIFIED。按独立审核 C1–C3 冻结，已通过完整实现与契约测试验收。
- 来源：真实素材删除、回收恢复、取消归档与 pending 事件在同一注册表事务提交。
- 事件固定 schema_version=1、event_id、event_type、asset_id、occurred_at、actor_key（认证主体摘要，不保存token/请求正文）。确定性SHA-256绑定不可变事件内容。
- `POST /api/asset-registry/governance/audit-outbox/reconcile`：治理权限；每批最多100条pending/failed。实际写入内部 `asset_audit_receipts.json` 并按event_id/摘要读回ACK后才replayed。同ID同内容幂等，同ID异内容拒绝；失败保留failed及安全错误码。无候选不增加revision。
- 响应保留replayed/count/revision/data_status，新增failed/remaining/scope。部分失败仍200且data_status=partial，绝不声称全部成功。
- `GET /api/asset-registry/governance/overview`：治理权限；audit_receipts最多最近50条脱敏收据，包含count/limit/scope。remaining包含pending与failed。
- 收据存放内部状态区域，不能通过通用产物读/列表/删接口访问。
- 单进程事务锁串行reconcile；ACK成功但源状态提交失败可安全重投，不重复收据。损坏/无法读写时失败关闭，不自动重置。
- 仅本机持久审计收据，不是外部服务送达，不是数字签名，不防服务器管理员篡改。

补充：reconcile使用与源状态同事务持久化的轮转游标，而非总取最前100项；持续失败旧事件不得永久阻塞后续健康事件。写源失败时游标也不推进，已产生收据可幂等重投。
