# -*- coding: utf-8 -*-
# Copyright 2026 Gods-Workbench Authors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""观测领域模块（Phase 10B）。

职责边界（洁净室口径）
----------------------
- 本模块只**读取**进程内既有服务的真实状态（ProjectsService 内存项目表、
  GodCanvasService 内存任务表、AssetLibraryService 内存目录、
  core.audit 已脱敏认证审计环形缓冲）；
- 本模块**不**采集宿主机 CPU/内存/磁盘等硬件遥测，也不引入任何真实时间序列、
  数据源清单或素材体积数据源；
- 未接入的数据源一律返回空集合，并在响应中显式标记 ``data_status: "not_integrated"``，
  **禁止**使用随机数、常量或任何形式的演示数据伪造遥测、任务、事件或波形。

证据边界：全部数据来自**进程内内存**，重启即丢失、多 worker 不共享；
本阶段不宣称满足任何可观测性合规留存要求，也不等于已通过第三方独立审计。
"""
