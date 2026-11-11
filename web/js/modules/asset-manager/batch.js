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

// QIN-591：资产库延后域模块（batch）。首屏不加载；经 GW_DEFERRED 注册函数面，
// 由主文件网关（gwCall/gwLoad）按需加载或在空闲时段预热。函数体除跨文件赋值点改写外保持原样。

import { GW_DEFERRED } from './state.js';

function localAssetBatchStatus(action,succeeded,data){
    const failed = Array.isArray(data?.failed) ? data.failed.length : 0;
    const skipped = Array.isArray(data?.skipped) ? data.skipped.length : 0;
    const warnings = Array.isArray(data?.warnings) ? data.warnings.length : 0;
    const parts = [succeeded ? `已${action} ${succeeded} 个素材` : `${action}未完成`];
    if(failed) parts.push(`${failed} 个失败`);
    if(skipped) parts.push(`${skipped} 个跳过`);
    if(warnings) parts.push(`${warnings} 个附属文件警告`);
    return parts.join('，');
}

// 【QIN-591】域函数面注册：主文件 gwCall('batch', fn) 经由此对象分派。
GW_DEFERRED['batch'] = {
};

export {
    localAssetBatchStatus,
};
