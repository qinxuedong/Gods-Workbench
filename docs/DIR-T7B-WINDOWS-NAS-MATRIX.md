# DIR-T7b Windows/NAS 实机矩阵

Linear：[QIN-249](https://linear.app/qinxuedong/issue/QIN-249/dir-t7b-windowsnas-实机矩阵从-qin-244-拆出)

## 环境与边界

- 执行环境：qin-home，Windows 10 build 26300；NAS `192.168.0.199`，SMB；TCP 445 可达。
- `Z:` 原有映射指向 NAS 共享；`Q:` 是本任务临时映射。仅在隔离测试目录内创建夹具；不触碰其他用户数据或生产数据。
- AC5 只断开并恢复临时 `Q:` 映射。`Z:` 和 NAS SMB 服务始终在线，因此本结果证明映射断开/重连后的别名索引，不代表 NAS 服务级 SMB 中断演练。
- 完成清理：临时 `Q:` 映射、夹具目录和隔离运行时已移除；`Z:` 仍连到原共享。

## 执行步骤与结果

| AC | 操作 | 结果与证据 | 状态 |
|---|---|---|---|
| AC4 | Windows 对 `Z:/` 与对应 UNC 路径执行 `os.path.samefile`；调用 `POST /api/asset-registry/root-aliases/suggestions` 与 `/confirm`；通过 `/api/asset-registry/assets/{asset_id}/file-operations/rename` 重命名测试文件。 | 路径相同；别名候选/确认保留 `root_id=root_46861192722fc33dab6020e9`；重命名操作 `done`，`canonical_relpath=renamed.mp4`，`asset_id=ast_0001` 不变。夹具前缀：`codex-qin249-97d24bae73a9`。 | 通过 |
| AC5 | 将已确认的 UNC 测试根纳入隔离 `GW_ALLOWED_ROOTS`；断开临时 `Q:`，通过 `POST /api/asset-registry/reindex` 从仍在线的 UNC 根重索引；重连 `Q:` 后再次重索引。 | 两次索引任务均 `succeeded`；`root_id=root_a822b9daa792180aa4c8813d`、`asset_id=ast_0001` 稳定，最终仅一条资产记录。夹具前缀：`codex-qin249-f1bb19743cdc`。 | 通过 |
| AC7 | 汇总 Windows/NAS 环境、路径操作、映射断开/恢复步骤、夹具、通过/失败与限制；结果分别记入 QIN-249 与 Slack。 | 开测报告、复验前报告、完成报告链接见下方；NAS 服务级中断不在本次实测范围。 | 通过 |

AC5 首次尝试因隔离运行态的 `GW_ALLOWED_ROOTS` 未包含已确认 UNC 测试根，返回 `INDEX_ROOT_INVALID`。这是测试配置缺项；补入该 UNC 根后用全新夹具重测通过，未修改产品代码。

## 验证命令

```text
python -m pytest tests/contracts/assets/test_dir_t7_multi_root_identity.py -q
20 passed in 8.40s

python -m pytest -v tests/e2e/test_index_jobs_browser.py::test_index_job_browser_real_file_and_controls --tb=short
1 passed in 13.01s（隔离复跑）

python -m pytest -v
3118 passed, 67 skipped, 24 warnings in 1644.50s（最终无并发全量复跑）
```

首次全量运行在其他工作树也有 pytest 活跃时出现一个浏览器等待超时（3117 passed、67 skipped、1 failed、24 warnings）；原因未证实。失败用例随后隔离复跑通过，且无并发 pytest 的最终全量复跑通过。

全量验证基线及前后核对的 `origin/master`：`752215d13ea70f08452811f43b59058d83cd2e37`。复跑日志保存在执行机临时目录，不作为仓库文件。

## 现场报告

- [Slack 开测前报告](https://w1763020101-six682561.slack.com/archives/C0C9Q8UD04A/p1792759991178419)
- [Slack AC5 复验前报告](https://w1763020101-six682561.slack.com/archives/C0C9Q8UD04A/p1792760725730749)
- [Slack 实测完成报告](https://w1763020101-six682561.slack.com/archives/C0C9Q8UD04A/p1792760948452179)
- [Linear 工单及开测、复验、完成评论](https://linear.app/qinxuedong/issue/QIN-249/dir-t7b-windowsnas-实机矩阵从-qin-244-拆出)
