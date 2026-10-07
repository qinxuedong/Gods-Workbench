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

(function(){
    const currentScript = document.currentScript;
    const cacheQuery = currentScript?.src
        ? new URL(currentScript.src, document.baseURI).search || ''
        : '';
    const withVersion = src => cacheQuery
        ? src + cacheQuery
        : src;
    const scripts = [
        '/static/js/core/i18n-core.js',
        '/static/js/modules/i18n/common.js',
        '/static/js/modules/i18n/studio.js',
        '/static/js/modules/i18n/api-settings.js',
        '/static/js/modules/i18n/canvas.js',
        '/static/js/modules/i18n/smart-canvas.js',
        '/static/js/modules/i18n/task-center.js',
        '/static/js/modules/i18n/governance.js',
    ];
    const tags = scripts.map(src => '<script src="' + withVersion(src) + '"></script>').join('');
    if(document.readyState === 'loading' && currentScript){
        document.write(tags);
        return;
    }
    scripts.reduce((promise, src) => promise.then(() => new Promise((resolve, reject) => {
        const script = document.createElement('script');
        script.src = withVersion(src);
        script.onload = resolve;
        script.onerror = reject;
        document.head.appendChild(script);
    })), Promise.resolve()).then(() => window.StudioI18n?.apply?.()).catch(err => console.error('Failed to load i18n modules', err));
})();
