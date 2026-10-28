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

/** MVP1-T8b 选项页：端点二选一、Ctrl+右键开关、手动配对与最近一次收集耗时。 */
(function () {
    'use strict';

    var endpointSelect = document.getElementById('endpoint');
    var ctrlCheckbox = document.getElementById('ctrl-right-click');
    var feedback = document.getElementById('feedback');
    var lastStatus = document.getElementById('last-status');
    var lastElapsed = document.getElementById('last-elapsed');
    var lastUrl = document.getElementById('last-url');

    function setFeedback(text, tone) {
        feedback.textContent = text || '';
        feedback.setAttribute('data-tone', tone || '');
    }

    function send(message) {
        return new Promise(function (resolve) {
            chrome.runtime.sendMessage(message, function (response) {
                if (chrome.runtime.lastError || !response) {
                    resolve({ok: false, errorCode: 'app_unreachable'});
                    return;
                }
                resolve(response);
            });
        });
    }

    function renderLastCollect(record) {
        if (!record) {
            lastStatus.textContent = '暂无记录';
            lastElapsed.textContent = '—';
            lastUrl.textContent = '—';
            return;
        }
        lastStatus.textContent = record.ok ? '已完成入库' : ('失败：' + (record.message || record.errorCode || '未知原因'));
        lastElapsed.textContent = Number(record.elapsedMs || 0) + ' ms';
        lastUrl.textContent = record.url || '—';
    }

    function renderState(state) {
        if (!state || !state.ok) {
            setFeedback('无法读取扩展状态，请重新加载扩展', 'error');
            return;
        }
        endpointSelect.value = state.endpoint;
        ctrlCheckbox.checked = state.ctrlRightClick !== false;
        renderLastCollect(state.lastCollect);
        setFeedback(state.paired ? '已与本机工作台配对' : '尚未配对：打开工作台或点「立即配对」', state.paired ? 'ok' : '');
    }

    function refresh() {
        return send({type: 'gw:state'}).then(renderState);
    }

    document.getElementById('save').addEventListener('click', function () {
        var settings = {
            endpoint: endpointSelect.value,
            ctrlRightClick: ctrlCheckbox.checked
        };
        send({type: 'gw:save-settings', settings: settings}).then(function (saved) {
            if (!saved || !saved.ok) {
                setFeedback('保存失败：端点仅支持本机 2077 端口', 'error');
                return null;
            }
            return send({type: 'gw:pair-now'}).then(function (result) {
                if (result && result.ok) {
                    setFeedback('已保存并配对成功', 'ok');
                } else {
                    setFeedback('设置已保存，但配对失败：请先启动工作台', 'error');
                }
                return refresh();
            });
        });
    });

    document.getElementById('pair').addEventListener('click', function () {
        send({type: 'gw:pair-now'}).then(function (result) {
            if (result && result.ok) {
                setFeedback('配对成功，令牌有效 1 小时', 'ok');
            } else {
                setFeedback('配对失败：请先启动工作台并在浏览器中登录', 'error');
            }
            return refresh();
        });
    });

    document.getElementById('open-app').addEventListener('click', function () {
        var endpoint = endpointSelect.value;
        chrome.tabs.create({url: endpoint + '/'}, function () {
            void chrome.runtime.lastError;
        });
    });

    refresh();
})();
