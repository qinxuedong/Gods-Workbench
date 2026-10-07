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

function ensureDirectorySettingsNav() {
    if (document.querySelector('.settings-index [data-section="directories"]')) {
        if (window.lucide && typeof window.lucide.createIcons === 'function') window.lucide.createIcons();
        return;
    }
    const nav = document.querySelector('.settings-index');
    const general = document.querySelector('[data-panel="general"]');
    if (!nav || !general) return;
    const button = document.createElement('button');
    button.type = 'button';
    button.dataset.section = 'directories';
    button.className = (nav.querySelector('button')?.className || '')
        .replace(/\bactive\b/g, '')
        .replace(/\btext-white\b/g, '');
    button.setAttribute('aria-pressed', 'false');
    button.innerHTML = '<i data-lucide="folder-cog" class="w-4 h-4 text-amber-300"></i><span>目录设置</span>';
    nav.firstElementChild.after(button);
    const panel = document.createElement('section');
    panel.dataset.panel = 'directories';
    panel.className = 'directory-settings-panel';
    panel.innerHTML = '<iframe title="目录设置" class="directory-settings-frame" src="/static/asset-manager.html?settings=directories&embedded=1" loading="lazy"></iframe>';
    general.after(panel);
    if (window.lucide && typeof window.lucide.createIcons === 'function') window.lucide.createIcons();
}

(function () {
    ensureDirectorySettingsNav();
    window.addEventListener('gw:route-loaded', ensureDirectorySettingsNav);
})();
