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

/**
 * Gods' Workbench - 分镜画布矩阵控制器 (storyboard-controller.js)
 */

window.WorkbenchStoryboard = (function () {
  'use strict';

  const state = {
    shots: [
      {
        id: 'sb-01',
        code: 'SC03_SH01',
        title: '宏观俯瞰 · 钛金母体神殿外景',
        shotType: '远景 · 24mm',
        dialogue: '【艾伦 独白】我们用了三百年才找到这里，而它已经在黑暗中沉睡了数个纪元。',
        duration: '4.5s',
        img: 'https://images.unsplash.com/photo-1518709268805-4e9042af9f23?q=80&w=600&auto=format&fit=crop',
        status: 'not_integrated'
      },
      {
        id: 'sb-02',
        code: 'SC03_SH02',
        title: '推进中景 · 艾伦穿过离子光幕门禁',
        shotType: '中景 · 35mm',
        dialogue: '【系统音】身份识别通过。欢迎归来，席位 #01 工程师。',
        duration: '3.2s',
        img: 'https://images.unsplash.com/photo-1509198397868-475647b2a1e5?q=80&w=600&auto=format&fit=crop',
        status: 'not_integrated'
      },
      {
        id: 'sb-03',
        code: 'SC03_SH03',
        title: '微距特写 · 艾伦凝视中枢核心瞳孔倒影',
        shotType: '特写 · 85mm',
        dialogue: '【艾伦】你早就知道我会来，对吗，AURA？',
        duration: '5.0s',
        img: 'https://images.unsplash.com/photo-1534447677768-be436bb09401?q=80&w=600&auto=format&fit=crop',
        status: 'not_integrated'
      },
      {
        id: 'sb-04',
        code: 'SC03_SH04',
        title: '对峙全景 · 巨大环状加速器在头顶运转',
        shotType: '全景 · 35mm',
        dialogue: '【AURA 语音】我没有预测未来，我只是穷尽了所有因果概率的交点。',
        duration: '6.0s',
        img: 'https://images.unsplash.com/photo-1511447333015-45b65e60f6d5?q=80&w=600&auto=format&fit=crop',
        status: 'not_integrated'
      },
      {
        id: 'sb-05',
        code: 'SC03_SH05',
        title: '动态摇镜 · 环形管道超载冷光脉冲',
        shotType: '运动镜头 · 50mm',
        dialogue: '【警告音】核心显存压力告警，神经流即将反噬外部矩阵！',  // 示例台词（虚构剧情文本，非遥测读数）
        duration: '3.8s',
        img: 'https://images.unsplash.com/photo-1618005182384-a83a8bd57fbe?q=80&w=600&auto=format&fit=crop',
        status: 'not_integrated'
      },
      {
        id: 'sb-06',
        code: 'SC03_SH06',
        title: '高潮静止 · 艾伦将控制插头接入主接口',
        shotType: '特写 · 50mm 宽银幕',
        dialogue: '【艾伦】那么，让真正的重构法则开始运转。',
        duration: '4.0s',
        img: 'https://images.unsplash.com/photo-1511447333015-45b65e60f6d5?q=80&w=600&auto=format&fit=crop',
        status: 'not_integrated'
      }
    ]
  };

  const esc = str => String(str ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

  function renderGrid() {
    const grid = document.getElementById('storyboardGrid');
    if (!grid) return;

    grid.innerHTML = state.shots.map((sh, idx) => `
      <div class="champagne-card p-2.5 flex flex-col justify-between group transition hover:border-[#dfc384]/50">
        <div>
          <!-- 头部编号与景别标签 -->
          <div class="flex items-center justify-between pb-1.5 mb-1.5 border-b border-white/5 text-xs">
            <div class="flex items-center space-x-1.5">
              <span class="text-[8.5px] font-mono px-1.5 py-0.5 rounded bg-black/60 border border-[#dfc384]/30 text-[#eddab3] font-bold">
                #${idx + 1} · ${sh.code}
              </span>
              <span class="text-[8.5px] font-mono text-cyan-300">${sh.shotType}</span>
            </div>
            <div class="flex items-center space-x-1.5 text-[8.5px] font-mono">
              <span class="text-slate-400">${sh.duration}</span>
              <span class="${sh.status === 'generating' ? 'text-amber-300 animate-pulse' : (sh.status === 'not_integrated' ? 'text-slate-400' : 'text-emerald-400')}"${sh.status === 'not_integrated' ? ' data-gw-degradation="not_integrated"' : ''}>
                ${sh.status === 'generating' ? '渲染中' : '未接入（无渲染端点）'}
              </span>
            </div>
          </div>

          <!-- 分镜构图画面 (16:9 画幅) -->
          <div class="aspect-video w-full rounded-lg overflow-hidden bg-black relative border border-white/10 mb-2">
            <img src="${sh.img}" class="w-full h-full object-cover group-hover:scale-105 transition duration-300" alt="${esc(sh.title)}">
            <div class="absolute top-1 right-1 px-1.5 py-0.5 rounded bg-black/70 text-[8px] font-mono text-[#eddab3]">
              2.39:1
            </div>
          </div>

          <!-- 台词对白与叙事意图 -->
          <div class="text-xs font-bold text-slate-100 mb-1 truncate">${esc(sh.title)}</div>
          <div class="text-[10px] text-slate-300 bg-black/40 p-1.5 rounded-lg border border-white/5 leading-relaxed font-sans mb-2">
            ${esc(sh.dialogue)}
          </div>
        </div>

        <!-- 底部快捷操作条 -->
        <div class="pt-1.5 border-t border-white/5 flex items-center justify-between text-xs font-mono">
          <div class="flex items-center space-x-2 text-slate-400 text-[9px]">
            <button class="hover:text-white" title="上移镜头" onclick="WorkbenchStoryboard.moveShot(${idx}, -1)">&uarr;</button>
            <button class="hover:text-white" title="下移镜头" onclick="WorkbenchStoryboard.moveShot(${idx}, 1)">&darr;</button>
          </div>
          <div class="flex items-center space-x-1.5">
            <button class="tactile-keycap px-2 py-0.5 rounded text-[8.5px] text-slate-300 hover:text-white" onclick="WorkbenchStoryboard.regenerateShot(${idx})">重绘画面</button>
            <a href="production.html" class="tactile-keycap px-2 py-0.5 rounded text-[8.5px] text-[#eddab3]">进入制片</a>
          </div>
        </div>

      </div>
    `).join('');

    window.lucide?.createIcons();
  }

  function renderNav() {
    const nav = document.getElementById('storyboardSceneNav');
    if (!nav) return;
    const scenes = [
      { id: '1', title: '第 01 幕 · 序幕起航', shots: 4, active: false },
      { id: '2', title: '第 02 幕 · 钛金废墟', shots: 6, active: false },
      { id: '3', title: '第 03 幕 · 钛金圣殿对峙', shots: 6, active: true },
      { id: '4', title: '第 04 幕 · 黎明信标', shots: 3, active: false }
    ];

    nav.innerHTML = scenes.map(s => `
      <button type="button" class="tree-node-card w-full text-left px-2.5 py-2 ${s.active ? 'active' : ''} flex items-center justify-between">
        <span>${s.title}</span>
        <span class="text-[8px] font-mono px-1 rounded bg-black/60 text-[#dfc384]">${s.shots}镜</span>
      </button>
    `).join('');
  }

  function moveShot(idx, dir) {
    const target = idx + dir;
    if (target < 0 || target >= state.shots.length) return;
    const temp = state.shots[idx];
    state.shots[idx] = state.shots[target];
    state.shots[target] = temp;
    renderGrid();
  }

  // 显式降级：本仓当前切片无分镜渲染端点，禁止用 setTimeout 假装生成完成。
  function regenerateShot(idx) {
    state.shots[idx].status = 'not_integrated';
    renderGrid();
    alert('分镜渲染端点尚未接入（未纳入当前切片），不会伪造生成结果。');
  }

  function batchGenerate() {
    state.shots.forEach(s => s.status = 'not_integrated');
    renderGrid();
    alert('批量渲染端点尚未接入（未纳入当前切片），不会伪造生成结果。');
  }

  function addNewShot() {
    const n = state.shots.length + 1;
    state.shots.push({
      id: 'sb-0' + n,
      code: `SC03_SH0${n}`,
      title: `新追加分镜镜头 #${n}`,
      shotType: '特写 · 50mm',
      dialogue: '【剧本情节】补充过渡镜头与情绪留白...',
      duration: '3.0s',
      img: 'https://images.unsplash.com/photo-1518709268805-4e9042af9f23?q=80&w=600&auto=format&fit=crop',
      status: 'not_integrated'
    });
    renderGrid();
  }

  function exportPdf() {
    alert('分镜 PDF 导出端点尚未接入（未纳入当前切片），未生成任何文件。');
  }

  function init() {
    renderNav();
    if (new URLSearchParams(window.location.search).get('view') !== 'canvas') renderGrid();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  return {
    moveShot,
    regenerateShot,
    batchGenerate,
    addNewShot,
    exportPdf,
    rebind: init
  };
})();
