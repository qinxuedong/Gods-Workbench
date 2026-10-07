# Gods-Workbench 行为规范目录

- **状态**：CURRENT / FROZEN_BASELINE
- **适用范围**：本目录只存放经过审核冻结的、面向外部可见行为的规范，不存放旧仓库源码或其摘录。

## 1. 当前冻结规范清单

- [`BEHAVIOR-SPEC-CANVAS.md`](BEHAVIOR-SPEC-CANVAS.md)：神工画布基础交互、视口平移缩放、节点与连线拓扑、CAS 并发防冲突。
- [`BEHAVIOR-SPEC-PRODUCTION-PROJECTS-HUB.md`](BEHAVIOR-SPEC-PRODUCTION-PROJECTS-HUB.md)：项目中心列表筛选、生产排期输入、生命周期（活跃/归档/回收站）流转。
- [`BEHAVIOR-SPEC-SMART-CANVAS.md`](BEHAVIOR-SPEC-SMART-CANVAS.md)：智能画布异步任务驱动、202 受理与任务续查、资产引用与级联执行。

## 2. 门禁与变更要求

1. 行为规范描述系统的可观察行为与响应语义，是业务实现与契约测试的唯一合法输入。
2. 规范中任何涉及写操作、拓扑保存与生命周期变更的行为，必须强制执行 `expected_version` 乐观并发控制，冲突时明确返回 409。
3. 规范文件必须在 `tests/hygiene/fixtures/cleanroom_admission_metadata.json` 中登记准入；未经准入审核的观察记录不得直接驱动业务实现。

