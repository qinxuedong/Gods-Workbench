/**
 * Copyright 2026 Gods-Workbench Authors
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

// QIN-591：资产库模块延后加载注册表。
// 主文件（asset-manager.js）与各延后域模块共同读写此对象：域模块在文件求值时把函数面
// 注册到对应域键上；主文件网关 gwCall/gwLoad 优先命中已注册函数面，否则动态 import 域文件。
// 本模块刻意不承载业务状态：跨文件写入的 let 留在主文件并以 setter 导出（保证主文件
// 其余函数体逐字不变，详见 .local/agent_tasks/active/QIN-591/）。

export const GW_DEFERRED = {};
