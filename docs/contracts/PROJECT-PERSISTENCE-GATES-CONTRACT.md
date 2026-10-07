# 项目持久化与阶段门实施契约

状态：CURRENT / VERIFIED。依据独立设计审核 P1–P5 冻结，已通过完整实现与契约测试验收。保证单进程/单实例。

## 真源与ID
- 项目完整字段、owner_key、门、创建幂等记录同存内部projects快照；真实生产默认不注入黄金模拟项目。显式内存测试实例保留原夹具行为。
- 不采用UUID。内部独立 `project_id_reservations` 状态先持久预留单调序号；预留成功后，项目提交失败或崩溃均不回收序号。该独立预留先于视频ACL绑定，保证孤儿ACL对应的ID永不复用。
- 首次创建预留状态时，以数据根规范绝对路径的SHA-256建立确定性namespace并落盘；已存在时读取持久namespace而非重新计算，搬迁同一状态不变ID。序号为正整数。项目ID形如 `prj-p-{namespace}-{sequence}`，门ID形如 `gate-p-{namespace}-{sequence}`；namespace使用完整64位十六进制摘要，sequence十进制。不回显绝对路径，不使用随机数。
- 损坏/不可读取/写入失败不得重置计数、回退空库或黄金种子。发布失败最多遗留不可见ACL；视频所有创建/授权/恢复执行必须验证项目真源存在。跨库不宣称ACID。

## 权限与兼容
- 门只支持owner与当前已认证admin/governor；不实现/不宣称通用成员grant。普通editor只可编辑自己的门。
- 无权门详情/写操作统一404 `PROJECT_GATE_NOT_FOUND`；发现集合过滤无权门。registry snapshot不得暴露无权门、owner、创建幂等摘要或主体键。
- 原项目列表、项目编辑/生命周期继续冻结的角色共享语义，不在新增门中暗改其ACL。ProjectItem不序列化内部owner/门/幂等映射；门通过专用受权接口读取。治理角色无需伪造owner即可管理门。
- 归档、回收项目门可按权限读取但禁止写入（403 `PROJECT_READ_ONLY`）；恢复保留ID/门状态/版本/owner。门写与生命周期在同一域锁内检查和原子提交。

## 创建与幂等
- gates可选，默认空；最多32项，每项code 1–64字符（字母数字/下划线/短横线）、name 1–120字符；code trim后不区分大小写去重，存规范小写。
- 含门请求必须client_request_id（8–128字符，仅字母数字/下划线/短横线/冒号/点）；无门可选。幂等域为稳定认证主体+键，规范化摘要包含全部项目请求及有序规范化门数组。
- 同键同请求先返回原project_id，不重复预留ID/绑定ACL/建门；同键异参409 `IDEMPOTENCY_CONFLICT`。非法请求在预留/ACL副作用之前拒绝。
- 非门请求兼容原创建响应；GET专用门集合可发现gate_id。单请求门数量有界；幂等记录保留到项目数据显式处置，不静默过期导致重建。

## 门状态与CAS
- 初始pending/version=1。允许pending→approved/changes_requested，changes_requested→pending，approved→changes_requested。
- approved撤回为changes_requested仅admin/governor，必须note 1–1000字符；其他变更note可空、上限1000。非治理撤回403 `FORBIDDEN`；未允许迁移409 `INVALID_GATE_TRANSITION`；未知状态400 `INVALID_REQUEST`。
- expected_version必填正整数；缺失/类型错400，陈旧409 `VERSION_CONFLICT`；先检查权限/生命周期再CAS，不回显无权对象版本。
- 同态PATCH：仍要求权限/CAS，返回原记录、不增加门版本、不改note/时间。实际状态变化门version+1；门变更不篡改项目CAS版本。

## 迁移与验证
- 不从旧仓/名称/视频owner表猜测恢复旧内存项目。部署切换不得操作用户运行服务或清空已有运行数据；有需保留内存项目时先要求明确人工导出迁移范围。当前自动验收仅隔离临时数据。
- 完全新Python进程同根恢复项目/owner/门/幂等/生命周期；真实HTTP及浏览器创建门与状态更新；权限、损坏、ACL绑定失败、源发布失败、域锁竞争、原ID不复用必须回归。
