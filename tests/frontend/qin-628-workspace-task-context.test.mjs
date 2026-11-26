// Copyright 2026 qinxuedong (Gods-Workbench Authors)
// SPDX-License-Identifier: FSL-1.1-Apache-2.0
//
// Licensed under the Functional Source License, Version 1.1, Apache 2.0
// Future License (the "License"); you may not use this file except in
// compliance with the License. You may obtain a copy of the License at
//     https://fsl.software/
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

// QIN-628 R1：页头不能只回显工作区快照；索引任务返回的执行端与结果落点
// 必须覆盖到指示器，并按任务 job_id 可回读。

import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const MANAGER = readFileSync(
    new URL('../../web/js/modules/asset-manager.js', import.meta.url),
    'utf8'
);

function sliceFunction(source, name){
    const match = new RegExp(`^(?:async )?function ${name}\\(`, 'm').exec(source);
    assert.ok(match, `未找到顶层函数：${name}`);
    const start = match.index;
    const end = source.indexOf('\n}', start);
    assert.ok(end > start, `顶层函数 ${name} 缺少行首闭合括号`);
    return source.slice(start, end + 2);
}

function createHarness(){
    const host = {hidden:true, innerHTML:''};
    const transfer = {hidden:true};
    const context = vm.createContext({
        console, Math, Number, String, Array, Object, JSON, Boolean,
        document: {
            querySelector(selector){
                return selector === '[data-workspace-indicator]' ? host : null;
            },
            querySelectorAll(selector){
                return selector === '[data-transfer-open]' ? [transfer] : [];
            },
        },
    });
    const api = vm.runInNewContext(`(() => {
        let workspaceContext = null;
        let workspaceTaskContext = null;
        const host = document.querySelector('[data-workspace-indicator]');
        function escapeHtml(value){ return String(value ?? ''); }
        function refreshIcons(){}
        ${sliceFunction(MANAGER, 'setWorkspaceContext')}
        ${sliceFunction(MANAGER, 'setWorkspaceTaskContext')}
        ${sliceFunction(MANAGER, 'renderWorkspaceIndicator')}
        return {setWorkspaceContext, setWorkspaceTaskContext, host};
    })()`, context, {filename: 'qin-628-workspace-indicator-harness.js'});
    api.transfer = transfer;
    return api;
}

test('页头先显示工作区快照，再由任务上下文覆盖执行端与结果落点', () => {
    const api = createHarness();
    api.setWorkspaceContext({
        active_workspace_id:'ws_local_01',
        compatibility:{status:'compatible'},
        execution_machine_id:'machine_local_01',
        result_root_label:'独立本机工作区',
    });
    assert.equal(api.host.hidden, false);
    assert.match(api.host.innerHTML, /执行：machine_local_01/);
    assert.match(api.host.innerHTML, /结果：独立本机工作区/);

    api.setWorkspaceTaskContext({
        workspace_id:'ws_nas_01',
        job_id:'job_index_00000001',
        execution_machine_id:'machine_nas_exec_01',
        result_root_label:'连接 NAS 工作区 · machine_nas_exec_01',
    });
    assert.match(api.host.innerHTML, /连接 NAS 工作区/);
    assert.match(api.host.innerHTML, /执行：machine_nas_exec_01/);
    assert.match(api.host.innerHTML, /结果：连接 NAS 工作区 · machine_nas_exec_01/);
    assert.match(api.host.innerHTML, /任务 job_index_00000001/);
    assert.doesNotMatch(api.host.innerHTML, /执行：machine_local_01/);
});

test('传输入口随当前工作区切换，未知状态及本机隐藏', () => {
    const api = createHarness();
    api.setWorkspaceContext(null);
    assert.equal(api.transfer.hidden, true);
    api.setWorkspaceContext({active_workspace_id:'ws_nas_01'});
    assert.equal(api.transfer.hidden, false);
    api.setWorkspaceTaskContext({workspace_id:'ws_nas_01'});
    api.setWorkspaceContext({active_workspace_id:'ws_local_01'});
    assert.equal(api.transfer.hidden, true);
});

test('切换工作区后丢弃旧任务上下文，避免旧执行端串到新工作区', () => {
    const api = createHarness();
    api.setWorkspaceContext({
        active_workspace_id:'ws_nas_01',
        compatibility:{status:'compatible'},
        execution_machine_id:'machine_local_01',
        result_root_label:'连接 NAS 工作区',
    });
    api.setWorkspaceTaskContext({
        workspace_id:'ws_nas_01',
        job_id:'job_index_00000002',
        execution_machine_id:'machine_nas_exec_01',
        result_root_label:'连接 NAS 工作区 · machine_nas_exec_01',
    });
    assert.match(api.host.innerHTML, /任务 job_index_00000002/);

    api.setWorkspaceContext({
        active_workspace_id:'ws_local_01',
        compatibility:{status:'compatible'},
        execution_machine_id:'machine_local_01',
        result_root_label:'独立本机工作区',
    });
    assert.doesNotMatch(api.host.innerHTML, /任务 job_index_00000002/);
    assert.match(api.host.innerHTML, /执行：machine_local_01/);
});
