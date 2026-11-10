// Copyright 2026 Gods-Workbench Authors
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

// QIN-585（GitHub #679）资产页首屏脚本减负：
// - 外壳页不再加载本地 Tailwind 运行时（836KB，进页做 JIT 编译），改用仓库静态快照；
// - 收集箱与治理建议这两条非首屏链路不再与首屏列表并发发起。
//
// 断言面按「契约」而非「某一行」写：静态快照必须覆盖外壳页实际使用的全部 Tailwind
// 工具类（防止以后往页面里新增工具类却忘记补快照，重新出现无样式卡片）。

import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';

const read = path => readFileSync(new URL(`../../${path}`, import.meta.url), 'utf8');

const SHELL_PAGE = read('web/pages/assets.html');
const EMBED_PAGE = read('web/embeds/asset-manager.html');
const UTILITIES = read('web/css/base/tailwind-utilities.css');
const MANAGER = read('web/js/modules/asset-manager.js');

// 从快照反解选择器：CSS 选择器里的 `[`/`]`/`#`/`/` 等字符带反斜杠，反解后再与页面 class 比对，
// 避免在测试里重复实现一套转义规则（`_` 是否转义这类细节最容易导致假红）。
function utilitySelectors(css){
    const tokens = new Set();
    // 取 `.` 后的类名主干：遇到未转义的组合符（`:>` `~` `+` `(` `[`）、`{`、空白、逗号即止。
    // 这样 `.space-x-1>:not([hidden])~:not([hidden])` 归一到 `space-x-1`，
    // 而 `.hover\:scale-105:hover` 里的转义冒号仍属于类名本身。
    for(const match of css.matchAll(/\.((?:[^\\{}\s,;:>~+()[\] ]|\\.)+)/g)){
        tokens.add(match[1].replace(/\\(.)/g, '$1'));
    }
    return tokens;
}

// 本仓库自定义类前缀（部件/布局类），不属于 Tailwind 工具类。
const PROJECT_CLASS = /^(?:gw-|hw-|asset-|orb-|oled-|topbar-|trapezoid-|uv-|recessed-|master-|launch-|nav-|pill-|active$|select-none$)/;

// Tailwind 工具类的形式特征：变体前缀（hover:/active:）或工具族前缀。
const TAILWIND_LIKE = /^(?:(?:hover|active|focus|group|disabled|sm|md|lg|xl):)?(?:flex|block|inline|inline-flex|grid|hidden|absolute|relative|fixed|sticky|isolate|sr-only|items-|justify-|space-|self-|px-|py-|p-|pt-|pb-|pl-|pr-|m-|mt-|mb-|ml-|mr-|w-|h-|min-|max-|text-|bg-|border|rounded|shadow|z-|top-|bottom-|left-|right-|inset-|overflow-|font-|animate-|transition|cursor-|shrink-|grow-|basis-|from-|via-|to-|antialiased|pointer-events-|gap-|opacity-|leading-|tracking-|whitespace-|truncate|uppercase|backdrop-|ring-|divide-|col-|row-|order-|aspect-|object-|fill-|stroke-|align-|list-|float-|clear-)/;

function pageTailwindClasses(html){
    const found = new Set();
    for(const match of html.matchAll(/class="([^"]*)"/g)){
        for(const token of match[1].split(/\s+/)){
            if(!token || PROJECT_CLASS.test(token)) continue;
            if(!TAILWIND_LIKE.test(token)) continue;
            found.add(token);
        }
    }
    return [...found].sort();
}

test('AC1: 资产库外壳页不再加载 Tailwind 运行时，改用仓库静态快照', () => {
    assert.doesNotMatch(
        SHELL_PAGE,
        /tailwindcss-cdn\.js/,
        '外壳页不得再加载 Tailwind 运行时（进页 JIT 编译会把首屏拖住）'
    );
    assert.doesNotMatch(
        SHELL_PAGE,
        /cdn\.tailwindcss\.com/,
        '外壳页不得引用外部 Tailwind CDN'
    );
    assert.match(
        SHELL_PAGE,
        /<link rel="stylesheet" href="\/static\/css\/base\/tailwind-utilities\.css\?[^"]+">/,
        '外壳页必须显式引用静态工具类快照（带缓存键）'
    );
    // 内嵌素材管理页本来只用自有 CSS，不得为了「顺手」又把运行时装回来。
    assert.doesNotMatch(EMBED_PAGE, /tailwindcss-cdn\.js/);
});

test('AC1: 静态快照覆盖外壳页实际使用的全部 Tailwind 工具类', () => {
    const tokens = pageTailwindClasses(SHELL_PAGE);
    assert.ok(tokens.length >= 100, `外壳页 Tailwind 工具类数量异常：${tokens.length}`);
    const present = utilitySelectors(UTILITIES);
    const missing = tokens.filter(token => !present.has(token));
    assert.deepEqual(missing, [], `静态快照缺少外壳页使用的工具类：${missing.join(', ')}`);
});

test('AC1: 静态快照自带 preflight，不依赖运行时补齐基础样式', () => {
    // 去掉运行时后若丢了 preflight，卡片与表单控件会出现浏览器默认外观。
    assert.match(UTILITIES, /box-sizing:border-box/);
    assert.match(UTILITIES, /margin:0/);
    assert.match(UTILITIES, /--tw-border-spacing-x:0/);
});

test('AC2: 收集箱与治理建议不再与首屏列表并发发起', () => {
    const deferred = /function scheduleDeferredBootRequests\(\)\{([\s\S]*?)\n\}/.exec(MANAGER);
    assert.ok(deferred, '缺少首屏延后块 scheduleDeferredBootRequests');
    assert.match(deferred[1], /loadAssetInbox\(\)/, '收集箱属于延后链路');
    assert.match(deferred[1], /loadGovernanceSuggestions\(\);/, '治理建议属于延后链路');
    // 空闲调度优先，无 requestIdleCallback 的宿主用定时器兜底，两条链路都必须跑。
    assert.match(deferred[1], /window\.requestIdleCallback\(run, \{timeout:2000\}\)/);
    assert.match(deferred[1], /setTimeout\(run, 200\)/);
});

test('AC2: 延后块只在首屏就绪后调度，且失败分支同样兜底', () => {
    assert.match(
        MANAGER,
        /markRegistryFirstScreenReady\(\);\s*startRegistryAutomaticIndexPolling\(\);[\s\S]{0,240}?scheduleDeferredBootRequests\(\);/,
        '首屏就绪后才允许补收集箱与治理建议'
    );
    assert.match(
        MANAGER,
        /\.catch\(err => \{[\s\S]{0,240}?scheduleDeferredBootRequests\(\);[\s\S]{0,40}?\}\);/,
        '首屏失败也要补延后链路，否则收集箱与治理抽屉永久不可用'
    );
});

test('AC2: 首屏 DOMContentLoaded 段不再同步等待非首屏链路', () => {
    const boot = MANAGER.slice(MANAGER.indexOf("document.addEventListener('DOMContentLoaded'"));
    const head = boot.slice(0, 900);
    assert.doesNotMatch(head, /void loadAssetInbox\(\)/, '首屏段不得同步发起收集箱请求');
    assert.doesNotMatch(head, /^\s*loadGovernanceSuggestions\(\);/m, '首屏段不得同步发起治理建议请求');
});
