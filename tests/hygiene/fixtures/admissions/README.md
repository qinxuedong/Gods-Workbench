# 票面分片登记（cleanroom admission shards）

本目录是洁净室准入登记的**并发安全结构**，用于替代历史上把全部票面文件集与哈希
堆进单一 `cleanroom_admission_metadata.json` 的做法。

- 决策来源：GitHub Issue #426（QIN-465）。
- 加载与校验唯一实现：`tests/hygiene/cleanroom_admission.py`。
- 断言入口：`tests/hygiene/test_admission_sharding.py`（结构）、
  `tests/hygiene/test_cleanroom_hygiene.py`（既有准入/隔离断言，读的是同一份并集视图）。

## 1. 选定方案

**按票拆分分片 + 加载时并集 + 取消整文件 SHA 钉死。**

| 位置 | 角色 | 谁来改 |
| --- | --- | --- |
| `../cleanroom_admission_metadata.json` | 基础账本：全局边界（`accepted_roots` / `curated_files` / `admitted_files` / `excluded_*`）与迁移前的历史票条目 | **默认无人改**；只有全局准入边界变化时才动 |
| `admissions/issue-<票号>.json` | 单张票的登记分片 | 该票自己的 PR |
| `admissions/README.md` | 本说明 | 结构性调整时 |

要点：

1. **一票一文件**。两张并行票写的是两个不同文件，Git 三路合并天然无冲突，
   不需要互 rebase，也不必重算任何全局哈希。
2. **加载时并集**。`load_admission_metadata()` 把基础账本与全部分片合成
   `authorized_issue_file_sets` / `admitted_files` / `accepted_test_files` 的并集视图，
   历史断言无需改写。
3. **取消整文件钉死**。`test_cleanroom_hygiene.py` 里的
   `ADMISSION_BASE_METADATA_SHA256` 只钉**基础账本**（几乎不变）；分片内容用
   逐条 `sha256` 与真实文件内容绑定，分片自身的增删不再触发任何全局哈希更新。
   `admission_union_digest()` 仍可算出并集摘要，但它**只用于审计留痕，不作为断言钉死**。
4. **拒绝重复登记**。同一票号同时出现在基础账本与分片、同一准入路径被登记两次、
   文件名与 `issue` 字段不一致、出现未知字段，都会让测试失败，不会被静默忽略。

## 2. 分片格式

```json
{
  "schema": "gw.cleanroom-admission-shard/v1",
  "issue": 426,
  "authorization_basis": "GitHub Issue #426, 2026-10-28",
  "files": [
    "gw/example.py",
    "tests/contracts/example/test_feature.py",
    "tests/hygiene/fixtures/admissions/issue-426.json",
    "tests/hygiene/test_cleanroom_hygiene.py"
  ],
  "admitted_files": [
    {
      "path": "docs/contracts/EXAMPLE.yaml",
      "classification": "frozen_interface_contract",
      "authorization_basis": "GitHub Issue #426, 2026-10-28",
      "sha256": "<该文件内容规范化后的 64 位小写十六进制>",
      "admission": "accepted"
    }
  ],
  "accepted_test_files": [],
  "notes": "可选：一句话说明本票为何需要登记。"
}
```

约束：

- 文件名必须是 `issue-<票号>.json`，`issue` 字段必须等于文件名中的票号；
- `files` 是**本票票面可改文件集**，非空、去重、仓库内相对路径、ASCII 且无空格；
- `admitted_files` 的 `sha256` 使用 `canonical_sha256()`
  （CRLF/CR 归一为 LF 后取 SHA-256），与既有准入断言算法完全一致；
- 只允许上表列出的键，拼写错误会直接报错，不会退化为「没有登记」。

## 3. 新票怎么用（三步）

1. 新建 `admissions/issue-<票号>.json`，按上面格式登记本票文件集；
   需要新增准入文件时，把条目写在**本票分片**里，不要写进基础账本。
2. 不需要新增 `test_cleanroom_hygiene.py` 里的「某票文件集」断言：
   分片本身就是登记事实，`test_admission_sharding.py` 已覆盖结构与并集。
   确有协议级断言需求时再加，且断言只读取并集视图。
3. 本地自检：

   ```bash
   python tests/hygiene/cleanroom_admission.py .
   pytest tests/hygiene -q
   ```

   第一条命令会打印并集规模与 `union_digest`，可粘进 PR 正文留痕。

## 4. 迁移步骤（已完成，保留供追溯）

1. 基础账本原样保留历史 9 张票的 `authorized_issue_file_sets` 与 107 条
   `admitted_files`，**不搬迁、不改写**：历史断言与历史哈希继续成立。
2. 新增本目录与 `tests/hygiene/cleanroom_admission.py`，把加载逻辑抽成单一来源。
3. `test_cleanroom_hygiene.py` 改为委托加载器；整文件钉死降级为基础账本钉死。
4. 后续新票一律写分片。基础账本条目随票自然退役，不做批量搬迁。

## 5. 对「已开着的 PR」的兼容策略（双读窗口）

- **双读**：加载器同时读基础账本的 `authorized_issue_file_sets` **和**分片目录，
  所以仍按旧格式往基础账本追加条目的在途 PR，合并进来后依然能被正确加载，
  不会因为本结构上线而失效。
- **不强制中途改格式**：在途 PR 只需按正常流程解一次它本来就有的
  `cleanroom_admission_metadata.json` 冲突（这是既有成本），**无需**改成本结构。
- **迁移不做批量搬迁**：基础账本里的历史条目原地保留；新票写分片。
  因此双读窗口是长期、稳定的，不存在「某天突然全部失效」的切换点。
- **重复登记会被拒绝**：在途 PR 合入后若同时存在旧条目与同名分片，
  `merge_admission_metadata()` 会报「票号重复登记」，提示二选一，而不是静默取其一。

## 6. 禁止项

- 禁止用 `ours` / `theirs` 整文件覆盖基础账本或他人分片；
- 禁止在解冲突时删除、裁剪其他票的分片或条目（不单边砍对方准入）；
- 禁止为了「让测试变绿」而放宽 `cleanroom_admission.py` 的校验或删断言；
- 基础账本的 `ADMISSION_BASE_METADATA_SHA256` 只在全局边界**确实**变化时更新，
  并在同一次提交里说明原因。
