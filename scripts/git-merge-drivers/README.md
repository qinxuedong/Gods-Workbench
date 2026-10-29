# 洁净室登记合并驱动

本目录提供两个 Git 合并驱动，用于消除并行 PR 在**登记热点文件**上的三路合并冲突：

| 驱动名 | 脚本 | 适用文件 | 行为 |
| --- | --- | --- | --- |
| `hygiene-json` | `merge_json_registry.py` | 基础账本、全部票面分片 | JSON 结构化并集 + 哈希重算 |
| `hygiene-lines` | `merge_line_list.py` | `tests/frontend/node-files.txt` 等行式白名单 | 行并集 + 排序去重 |

映射写在仓库根目录 `.gitattributes`，驱动实现只依赖 Python 3.11 标准库。

## 1. 安装

在仓库根目录执行任一方式：

```bash
sh scripts/git-merge-drivers/install.sh
# 或
python3 scripts/git-merge-drivers/install.py
```

安装脚本会在**当前仓库的本地 `.git/config`** 写入：

```text
merge.hygiene-json.name   = Gods-Workbench cleanroom admission JSON union merge driver
merge.hygiene-json.driver = "<python>" "<仓库绝对路径>/scripts/git-merge-drivers/merge_json_registry.py" %O %A %B %P
merge.hygiene-lines.name   = Gods-Workbench line allowlist union merge driver
merge.hygiene-lines.driver = "<python>" "<仓库绝对路径>/scripts/git-merge-drivers/merge_line_list.py" %O %A %B %P
```

注意：

- 配置**不会**提交到仓库；换机器、重新克隆或移动仓库目录后需重新运行安装脚本。
- Python 解释器取安装时的 `sys.executable`；如需指定，可先 `PYTHON=/path/to/python3 sh scripts/git-merge-drivers/install.sh`。

## 2. JSON 驱动（`hygiene-json`）

参数固定为 `%O %A %B %P`（基线、ours、theirs、仓库相对路径），结果写回 `%A`。

合并口径：

1. **顶层键取并集**：键序以 ours 为主，theirs 独有的键按其在 theirs 中的顺序追加。
2. **对象列表按键并集**：
   - `admitted_files` / `accepted_test_files` 等按 `path`；
   - `authorized_issue_file_sets` 按 `issue`，同票的 `files` 再取并集；
   - `excluded_artifacts` 等按 `source_path`。
   - 同一键的对象递归合并；顺序为 ours 原有顺序 + theirs 独有条目，稳定可复现。
3. **纯字符串列表取并集后排序去重**（包括 `files` 与各票的 `files`）。
4. **标量冲突保留 ours**；仅当 `schema` 不一致时以非零退出，让 Git 保留冲突。
5. **哈希一律重算**：任何同时含 `path` 与 `sha256` 的对象，都按工作树真实文件内容用
   `cleanroom_admission.canonical_sha256()` 重算——先把 CRLF / CR 归一为 LF 再取
   SHA-256，**从不信任任何一侧的旧哈希**。
   - 目标文件缺失时保留原哈希并向 stderr 告警，不伪造哈希；随后
     `scripts/fix-hygiene-registry.py --check` 或卫生测试会显式失败。
6. **输出格式**：UTF-8 原文、`ensure_ascii=False`、2 空格缩进、保留键序、结尾单个换行，
   与仓库现有 `cleanroom_admission_metadata.json` / `issue-*.json` 完全一致。

### 基础账本钉由谁重写

当被合并文件正是基础账本
`tests/hygiene/fixtures/cleanroom_admission_metadata.json` 时，驱动在写出合并结果后，
会立即按新账本内容重写 `tests/hygiene/test_cleanroom_hygiene.py` 里的
`ADMISSION_BASE_METADATA_SHA256`。该改动是工作树中的未暂存修改，需要随本次合并一并提交。

**`scripts/fix-hygiene-registry.py --fix` 也会重写同一个常量**，用于兜底（例如驱动未安装、
只在网页端合并，或手工改过账本）。两者算法一致：账本字节内容 → CRLF/CR→LF → SHA-256。

分片（`issue-*.json`）不触发钉重写。

## 3. 行驱动（`hygiene-lines`）

1. 文件顶部的注释行（`#` 开头）与空行块原样保留，优先取 ours，其次 theirs；
2. 其余非空、非注释行取并集后按字典序排序、去重；
3. 始终以单个换行结尾，UTF-8 读写，不做平台行尾转换。

当前唯一的行式白名单是 `tests/frontend/node-files.txt`；结构/卫生门禁若仍以 Python 集合
维护白名单，则不属于本驱动适用范围。

## 4. 自检与修复脚本

```bash
python3 scripts/fix-hygiene-registry.py --check
python3 scripts/fix-hygiene-registry.py --fix
```

`--check` 检查并向 stderr 列出全部问题后以退出码 1 结束，同时打印一条修复命令：

- 基础账本 `ADMISSION_BASE_METADATA_SHA256` 是否与账本内容一致；
- 基础账本与全部分片中所有 `path` + `sha256` 条目是否与工作树一致；
- `tests/frontend/node-files.txt` 是否排序、去重、结尾换行；
- `cleanroom_admission.py` 的结构校验（重复登记、未知字段、非法路径等）。

`--fix` 会：

- 按工作树真实内容重算基础账本与全部分片的逐条 `sha256`；
- 排序去重行式白名单（保留顶部注释块）；
- 重写基础账本钉。

`tests/hygiene/test_merge_drivers.py` 中的用例在 CI 中直接运行 `--check` 逻辑，
因此即使网页端合并绕过了合并驱动，CI 仍会失败并给出修复命令。

## 5. 局限性（务必阅读）

- **只对本地 Git 生效**：GitHub 网页端「Merge pull request」、CI checkout 后的操作都不会
  调用 `.gitattributes` 里的自定义驱动。
- **本地必须先安装**：未执行 `install.sh` / `install.py` 时，Git 会退回默认文本合并，
  仍可能产生冲突或不可用的半合并结果。
- **冲突时的正确姿势**：先安装驱动，再执行

  ```bash
  git fetch
  git rebase origin/master   # 或 git merge origin/master
  ```

  让驱动处理登记文件；若仍有残留问题，运行
  `python3 scripts/fix-hygiene-registry.py --check` 查看并按提示 `--fix`。
- **不替代人工判断**：驱动只做并集与哈希重算，不放宽 `cleanroom_admission.py` 的校验，
  也不会删除、裁剪任何一方的票面分片或条目。
- **移动仓库后需重装**：本地配置保存的是安装时的绝对路径。

## 6. 相关文件

- `.gitattributes`：驱动映射；
- `tests/hygiene/cleanroom_admission.py`：登记加载、结构与 `canonical_sha256` 唯一来源；
- `tests/hygiene/fixtures/admissions/README.md`：分片结构与本票登记规范；
- `tests/hygiene/test_merge_drivers.py`：并集、哈希重算、钉重写与真实 `git merge` 回归。
