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

// QIN-628 QA：登记映射时必须先捕获完整表单再重绘，避免读到重绘后的默认机器。

import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const SETTINGS = readFileSync(
    new URL('../../web/js/modules/asset-manager/settings.js', import.meta.url),
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
    const context = vm.createContext({
        console, Math, Number, String, Object, JSON, Boolean, Array, Promise, Error,
    });
    return vm.runInNewContext(`(() => {
        let workspaceSettingsState = {
            snapshot: {
                schema_version: 1,
                revision: 1,
                active_workspace_id: 'ws_local_01',
                workspaces: [{workspace_id:'ws_local_01', mode:'local', display_name:'独立本机工作区', data_namespace:'workspace_local_01'}],
                mappings: [],
                compatibility: {status:'pending', detail:'待接入'},
                writer_guard: null,
            },
            loading: false,
            saving: false,
            target: 'ws_local_01',
            machine: 'machine_local_01',
            path: '',
            error: '',
            generation: 0,
        };
        const form = {workspaceMapMachine:'machine_local_01', workspaceMapPath:''};
        let lastPayload = null;
        let lastHtml = '';
        let lastStatus = '';
        let renderCount = 0;
        let failNext = false;
        const document = {
            getElementById(id){
                if(id === 'workspaceMapMachine') return {get value(){ return form.workspaceMapMachine; }, set value(v){ form.workspaceMapMachine = v; }};
                if(id === 'workspaceMapPath') return {get value(){ return form.workspaceMapPath; }, set value(v){ form.workspaceMapPath = v; }};
                return null;
            },
        };
        function escapeHtml(value){ return String(value ?? ''); }
        function escapeAttr(value){ return String(value ?? ''); }
        function setStatus(value){ lastStatus = String(value || ''); }
        function notifyWorkspaceChanged(){}
        function loadAssetAllowedRootPaths(){ return Promise.resolve(); }
        async function workspaceRequest(path, method, payload){
            lastPayload = {path, method, payload:JSON.parse(JSON.stringify(payload))};
            if(failNext){ failNext = false; throw new Error('mock failure'); }
            if(path.endsWith('/verify')){
                return {status:'verified', verified_by:'machine_local_01', verification_source:'server_context'};
            }
            if(path.endsWith('/mappings')){
                const next = JSON.parse(JSON.stringify(workspaceSettingsState.snapshot));
                next.revision += 1;
                next.mappings.push({
                    mapping_id:'map_' + next.mappings.length,
                    workspace_id:payload.workspace_id,
                    logical_source_id:'src_' + next.mappings.length,
                    access_path:payload.access_path,
                    execution_machine_id:payload.execution_machine_id,
                    verification:{status:'verified', verified_by:'machine_local_01', verification_source:'server_context'},
                });
                return next;
            }
            return workspaceSettingsState.snapshot;
        }
        function applyWorkspaceSnapshot(snapshot){
            workspaceSettingsState = {...workspaceSettingsState, snapshot:snapshot || null, loading:false, saving:false, error:''};
        }
        ${sliceFunction(SETTINGS, 'setWorkspaceSettingsState')}
        ${sliceFunction(SETTINGS, 'captureWorkspaceMappingForm')}
        ${sliceFunction(SETTINGS, 'workspaceErrorMessage')}
        ${sliceFunction(SETTINGS, 'verifyWorkspaceMapping')}
        ${sliceFunction(SETTINGS, 'addWorkspaceMapping')}
        ${sliceFunction(SETTINGS, 'renderWorkspaceSettingsBody')}
        function renderGlobalDirectorySettings(){
            renderCount += 1;
            // 模拟真实重绘：输入框按当前状态重建，历史缺陷会在这里重置为默认机器。
            form.workspaceMapMachine = workspaceSettingsState.machine;
            form.workspaceMapPath = workspaceSettingsState.path;
            lastHtml = renderWorkspaceSettingsBody();
        }
        return {
            setForm(machine, path){ form.workspaceMapMachine = machine; form.workspaceMapPath = path; },
            setFailNext(value){ failNext = Boolean(value); },
            verifyWorkspaceMapping,
            addWorkspaceMapping,
            get state(){ return workspaceSettingsState; },
            get form(){ return {...form}; },
            get lastPayload(){ return lastPayload; },
            get lastHtml(){ return lastHtml; },
            get lastStatus(){ return lastStatus; },
            get renderCount(){ return renderCount; },
        };
    })()`, context, {filename: 'qin-628-workspace-mapping-form-harness.js'});
}

test('登记映射在重绘前捕获执行机器，落盘与回读均为输入值', async () => {
    const api = createHarness();
    api.setForm('qa_machine_remote_628', '/nas/share-one');
    await api.addWorkspaceMapping();

    assert.ok(api.renderCount >= 1);
    assert.equal(api.lastPayload.payload.execution_machine_id, 'qa_machine_remote_628');
    assert.equal(api.state.snapshot.mappings[0].execution_machine_id, 'qa_machine_remote_628');
    assert.match(api.lastHtml, /执行机器：qa_machine_remote_628/);
    assert.match(api.lastHtml, /核验宿主：machine_local_01/);
    assert.equal(api.state.path, '');
});

test('失败重试保留输入，两个不同机器的映射各自登记', async () => {
    const api = createHarness();
    api.setForm('qa_machine_remote_628', '/nas/share-one');
    api.setFailNext(true);
    await api.addWorkspaceMapping();
    assert.equal(api.state.error, 'mock failure');
    assert.equal(api.form.workspaceMapMachine, 'qa_machine_remote_628');
    assert.equal(api.form.workspaceMapPath, '/nas/share-one');

    api.setFailNext(false);
    await api.addWorkspaceMapping();
    api.setForm('qa_machine_remote_629', '/nas/share-two');
    await api.addWorkspaceMapping();

    assert.deepEqual(
        api.state.snapshot.mappings.map(item => item.execution_machine_id),
        ['qa_machine_remote_628', 'qa_machine_remote_629']
    );
});

test('核验状态显示服务端执行宿主且不采信客户端机器标签', async () => {
    const api = createHarness();
    api.setForm('qa_machine_remote_628', '/nas/share-one');
    await api.verifyWorkspaceMapping();
    assert.equal(api.lastPayload.payload.execution_machine_id, 'qa_machine_remote_628');
    assert.match(api.lastStatus, /核验宿主：machine_local_01/);
    assert.equal(api.form.workspaceMapMachine, 'qa_machine_remote_628');
    assert.equal(api.form.workspaceMapPath, '/nas/share-one');
});
