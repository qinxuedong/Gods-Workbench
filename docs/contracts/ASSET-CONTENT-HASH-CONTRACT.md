# 素材内容哈希契约（full / sampled）

- **状态**：CURRENT / 文档化，不改算法口径（QIN-575 / GitHub #660）
- **依据**：QIN-564（GitHub #638 / PR #643）引入 `hash_mode` 与 >512MiB 采样降级；本文件只做对外说明与防漂移约束，不改 `_sampled_hash` 实现。
- **适用范围**：`gw/asset_registry/index_jobs.py` 扫描期写入的 `sha256` / `hash_mode`，以及 `gw/asset_registry/governance.py`、`gw/asset_registry/repository.py` 对这两个字段的下游使用。
- **实现入口**：`_hash()`（完整）、`_sampled_hash()`（采样）、`_scan()` 中的阈值分支；函数名是本契约的稳定引用，不钉死行号。

## 1. 字段与阈值

- `hash_mode` 只有两个取值：`full`（完整哈希）、`sampled`（采样哈希）；仅 `hash_files=true` 的索引任务写入，无哈希素材不写该字段。取值不是分组键，只作诚实标记。
- 阈值 `HASH_LIMIT_BYTES = 512 * 1024 * 1024`（536870912 字节，512MiB）。判定为**严格大于**：`size > 512MiB` 才走采样，`size == 512MiB` 仍走完整哈希。
- `full`：标准 SHA-256 增量读取整个文件字节流后取小写十六进制 `hexdigest()`；对同一文件可直接与任意外部工具算出的标准 sha256 比对。
- `sampled`：`sha256(DOMAIN ‖ uint64_le(size) ‖ head ‖ tail)`，见第 2 节。**不是**标准 sha256，也不等于任何「首尾 + 长度」工具算出的摘要。
- 索引任务结果按 `hash_mode` 如实分列 `hashed`（full 数）与 `hash_sampled_oversize`（sampled 数），不把采样降级报告为全量成功。

## 2. sampled 摘要的精确字节结构

以下四段**按顺序**喂入 SHA-256，段与段之间无分隔符、无长度前缀、无去重：

1. `DOMAIN`：ASCII 字节串 `gw-asset-registry-sampled-sha256-v1`（实现常量 `_SAMPLED_HASH_DOMAIN`，35 字节，固定不变）。用于域分隔，使 `sampled` 与 `full` 不共摘要空间。
2. `uint64_le(size)`：`struct.pack("<Q", size)`，8 字节小端无符号 64 位，单位为字节；不是十进制 ASCII，也不放在输入末尾。
3. `head`：从 offset 0 读至多 `SAMPLE_BYTES = 4 * 1024 * 1024`（4194304 字节，4MiB）；文件更短时读到 EOF 即止。
4. `tail`：从 offset `max(0, size - SAMPLE_BYTES)` 读至多 `SAMPLE_BYTES`；文件更短时读到 EOF 即止。

输出为 64 位小写十六进制 `hexdigest()`。

**不足 8MiB 时的重叠行为**：当 `SAMPLE_BYTES < size < 2 * SAMPLE_BYTES`，`head` 与 `tail` 区间部分重叠，重叠字节被喂入两次；当 `size <= SAMPLE_BYTES`，`tail` 的 offset 退化为 0，两段完全相同，整个文件被喂两次。生产路径只在 `size > 512MiB` 时调用采样，因此这两种情形只可能来自直接调用内部函数（契约测试、外部重算工具）；公式仍按上述定义执行，不特判、不去重。

**失败关闭**：与完整哈希一致，打开句柄前后校验路径解析、`ino/dev/size` 及摘要后的 `size/mtime_ns`；任一不一致抛 409 `INDEX_FILE_CHANGED`，不产出脏指纹。

## 3. 与 QIN-561 / T7 基线 manifest 不可比对

| 维度 | 注册表采样（本契约） | QIN-561 / T7 基线 manifest |
| --- | --- | --- |
| 触发阈值 | `size > 512MiB` | `size > 4MiB` |
| 摘要输入 | `DOMAIN ‖ uint64_le(size) ‖ head ‖ tail` | `head ‖ tail ‖ str(size).encode()` |
| 域分隔前缀 | 有（35 字节常量） | 无 |
| 长度编码 | 8 字节小端 uint64，位于最前 | 十进制 ASCII，位于末尾 |
| 输出 | SHA-256 十六进制 | SHA-256 十六进制 |

**禁止**：把注册表 `sha256`（尤其 `hash_mode="sampled"`）与基线 manifest 的 `sha256` 逐条直接对拍，据此判定「文件被篡改」或「索引写错」。两者摘要输入、长度编码、域前缀与顺序都不同，值必然不同；实现注释中的「QIN-561 基线口径」只指设计意图（首尾 + 长度），不承诺字节级互通。是否统一口径属独立决策，本票不改算法、不改本地私有基线工具。

**正确核验步骤（重算，而非对拍）**：

1. 逐条取注册表记录的 `size_bytes`、`sha256`、`hash_mode`，并定位原文件。
2. `hash_mode="full"`：对原文件全字节流算标准 sha256，与登记值比对。
3. `hash_mode="sampled"`：按第 2 节公式（DOMAIN + `<Q` + 首尾各 4MiB + 重叠不特判）**独立重算**，与登记值比对；重算工具按本文件字面量写死常量，不得复用实现常量，否则会掩盖漂移。
4. 基线 manifest 的 `sha256` 只能由基线工具按自身公式重算校验。跨工具核对同一文件身份时用「各自重算、各自匹配」加相对路径 / `size_bytes` / `mtime` 等元信息比对，不比对摘要值本身。
5. 阈值差异会造成「一侧 full / 一侧 sampled」：`4MiB < size <= 512MiB` 在基线侧是 sampled、注册表侧是 full；`size > 512MiB` 两侧都是 sampled 但公式不同。两种情形都不构成不一致证据，直接对拍必然全量不等。

## 4. 下游语义

- `governance._asset_file_records`：`sha256` 为空的资产**不参与**文件查重建议；筛选只看 `sha256`、展示路径与合法的 `size_bytes`（非法值置 0），**不看 `hash_mode`**，故 sampled 记录仍按其 `sha256` 参与分组。
- `repository.shot_source_key`：`sha256` 非空一律返回 `sha256:<小写 digest>`，**不看 `hash_mode`**；缺席才退回 `asset:<asset_id>`，宁可漏报跨资产重复也不把不同素材折叠成同一 hash。同内容的多个超限副本因采样值相同命中同一组。
- 已知限制：采样哈希对 `(4MiB, size-4MiB)` 中段内容无感；首尾相同、中段不同的两个超限文件会得到同一采样值（假阳性分组）。查重建议仅供人工确认，不据此自动删除文件。
- 域分隔前缀使 `full` 与 `sampled` 不共摘要空间，两种模式不会交叉误配；`hash_mode` 是诚实标记，不参与分组键计算。

## 5. 机器可读契约锚点（防漂移）

以下锚点由 `tests/contracts/assets/test_qin_575_hash_contract.py` 解析，用于按文档字面量独立重算采样摘要。文档或实现任一侧漂移都会让契约测试变红；变更公式、常量或阈值时必须同时更新锚点与测试，并单独评估 QIN-562 全库取证结论。

```text
QIN575_DIGEST=sha256
QIN575_HASH_DOMAIN=gw-asset-registry-sampled-sha256-v1
QIN575_SIZE_PACK=<Q
QIN575_SAMPLE_BYTES=4194304
QIN575_LIMIT_BYTES=536870912
QIN575_LIMIT_OPERATOR=strictly_greater
QIN575_HASH_MODES=full,sampled
QIN575_FORMULA=DOMAIN||uint64_le(size)||head(0,SAMPLE_BYTES)||tail(max(0,size-SAMPLE_BYTES),SAMPLE_BYTES)
QIN575_TAIL_OVERLAP=head_and_tail_may_overlap_below_2*SAMPLE_BYTES
QIN575_BASELINE_THRESHOLD_BYTES=4194304
QIN575_BASELINE_FORMULA=head(4MiB)||tail(4MiB)||str(size).encode()
```

## 6. 变更边界

- 不改 `_sampled_hash` 算法、不改 512MiB 阈值、不改基线工具（本地私有 `<本地证据包>`）。
- 本契约描述的是当前实现事实；任何口径统一、阈值调整或字段重命名都需另开票，并同步本文、锚点、防漂移测试与洁净室准入登记的哈希。
