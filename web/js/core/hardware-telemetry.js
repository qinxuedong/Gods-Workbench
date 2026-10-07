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
 * Gods Workbench - 拟物硬件遥测与交互驱动核心 (Hardware Telemetry & Deck Controller)
 * Version: 2.4.0-PRO
 */

(function(window) {
  'use strict';

  const HardwareDeck = {
    // 1. Clock Engine
    initClock: function(elementId = 'masterDeckClock') {
      const el = document.getElementById(elementId);
      const dateButton = document.getElementById(elementId === 'masterDeckClock' ? 'masterDeckDate' : 'workspaceCyberDate');
      if (dateButton && !dateButton.dataset.gwCalendarBound) {
        dateButton.addEventListener('click', () => this.openProjectCalendar(dateButton));
        dateButton.dataset.gwCalendarBound = '1';
      }
      if (!el) return;
      const monthEl = document.getElementById('masterDeckDateMonth');
      const dayEl = document.getElementById('masterDeckDateDay');
      const update = () => {
        const parts = new Intl.DateTimeFormat('en-US', {timeZone: 'Asia/Shanghai', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false, month: 'short', day: '2-digit'}).formatToParts(new Date());
        const value = Object.fromEntries(parts.map(({type, value}) => [type, value]));
        el.textContent = `${value.hour}:${value.minute}:${value.second}`;
        if (monthEl) monthEl.textContent = String(value.month || '---').toUpperCase();
        if (dayEl) dayEl.textContent = value.day || '--';
      };
      update();
      setInterval(update, 1000);
    },

    // 当前页项目排期弹窗：复用真实项目接口，日期按钮不会改变当前 URL。
    openProjectCalendar: async function(returnFocus) {
      const dialog = this.ensureProjectCalendarDialog();
      if (!dialog) return;
      this.projectCalendarReturnFocus = returnFocus || document.activeElement;
      if (!dialog.open) dialog.showModal();
      const body = dialog.querySelector('[data-project-calendar-body]');
      if (body) body.innerHTML = '<div class="gw-calendar-loading">正在读取真实项目排期…</div>';
      try {
        const response = await fetch('/api/asset-registry/projects?archived=false', {credentials: 'same-origin', cache: 'no-store'});
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const payload = await response.json();
        const projects = Array.isArray(payload) ? payload : (Array.isArray(payload?.projects) ? payload.projects : []);
        this.renderProjectCalendar(projects);
      } catch (error) {
        if (body) body.innerHTML = `<div class="gw-calendar-empty"><strong>暂时无法读取项目排期</strong><span>真实项目接口返回异常，请稍后重试。</span><button type="button" data-project-calendar-retry>重试</button></div>`;
      }
      window.requestAnimationFrame(() => dialog.querySelector('[data-project-calendar-close]')?.focus());
    },

    ensureProjectCalendarDialog: function() {
      let dialog = document.getElementById('gwProjectCalendarDialog');
      if (dialog) return dialog;
      dialog = document.createElement('dialog');
      dialog.id = 'gwProjectCalendarDialog';
      dialog.className = 'gw-project-calendar-dialog';
      dialog.setAttribute('aria-labelledby', 'gwProjectCalendarTitle');
      dialog.innerHTML = `
        <div class="gw-project-calendar-shell">
          <header class="gw-project-calendar-head">
            <div>
              <div class="gw-project-calendar-kicker">项目排期 · REAL PROJECT ROUTE</div>
              <h2 id="gwProjectCalendarTitle">工作流计划</h2>
              <p data-project-calendar-summary>正在读取真实项目数据…</p>
            </div>
            <button type="button" class="gw-project-calendar-close" data-project-calendar-close aria-label="关闭项目排期"><span aria-hidden="true">×</span></button>
          </header>
          <div class="gw-project-calendar-body" data-project-calendar-body></div>
          <footer class="gw-project-calendar-foot"><span>点击任务条进入对应真实项目</span><span>NOW · Asia/Shanghai</span></footer>
        </div>`;
      document.body.appendChild(dialog);
      dialog.addEventListener('click', event => {
        if (event.target === dialog) this.closeProjectCalendar();
        const close = event.target.closest?.('[data-project-calendar-close]');
        if (close) this.closeProjectCalendar();
        const retry = event.target.closest?.('[data-project-calendar-retry]');
        if (retry) this.openProjectCalendar(this.projectCalendarReturnFocus);
        const project = event.target.closest?.('[data-project-calendar-project]');
        if (project) {
          const id = String(project.dataset.projectCalendarProject || '').trim();
          if (id) {
            localStorage.setItem('workspace_project_id', id);
            window.location.href = `/static/pages/projects.html?project_id=${encodeURIComponent(id)}`;
          }
        }
      });
      dialog.addEventListener('cancel', event => { event.preventDefault(); this.closeProjectCalendar(); });
      dialog.addEventListener('close', () => {
        const target = this.projectCalendarReturnFocus;
        this.projectCalendarReturnFocus = null;
        target?.focus?.({preventScroll: true});
      });
      return dialog;
    },

    closeProjectCalendar: function() {
      const dialog = document.getElementById('gwProjectCalendarDialog');
      if (dialog?.open) dialog.close();
    },

    renderProjectCalendar: function(projects) {
      const dialog = document.getElementById('gwProjectCalendarDialog');
      const body = dialog?.querySelector('[data-project-calendar-body]');
      const summary = dialog?.querySelector('[data-project-calendar-summary]');
      if (!body) return;
      const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
      const time = value => {
        const parsed = value ? new Date(typeof value === 'number' && value < 1e12 ? value * 1000 : value).getTime() : 0;
        return Number.isFinite(parsed) ? parsed : 0;
      };
      const dayOrdinal = value => {
        const current = new Date(value);
        return Math.floor(Date.UTC(current.getFullYear(), current.getMonth(), current.getDate()) / 86400000);
      };
      // 契约对齐：项目中心 API 的稳定实体 ID 字段为 project_id（见 docs/contracts/PROJECTS-HUB-INTERFACE-CATALOG.yaml），
      // 此处归一化为内部 id，否则排期条 data-project-calendar-project 为空、点击跳转静默失效。
      const normalized = projects.map(project => ({ ...project, id: project.id || project.project_id }));
      const scheduled = normalized.filter(project => time(project.start_at) && time(project.due_at) && time(project.due_at) >= time(project.start_at));
      const unscheduled = normalized.length - scheduled.length;
      if (summary) summary.textContent = `${scheduled.length} 个项目已排期 · ${unscheduled} 个项目未排期`;
      const unscheduledProjects = normalized.filter(project => !scheduled.includes(project));
      const unscheduledMarkup = unscheduledProjects.length
        ? `<section class="gw-calendar-unscheduled"><h3>未排期项目</h3><div>${unscheduledProjects.map(project => `<button type="button" data-project-calendar-project="${esc(project.id)}"><span>${esc(project.name || project.title || '未命名项目')}</span><small>${esc(project.id || '')}</small></button>`).join('')}</div></section>`
        : '';
      if (!scheduled.length) {
        body.innerHTML = `<div class="gw-calendar-empty"><strong>暂无已排期项目</strong><span>项目详情仍来自真实项目接口。</span></div>${unscheduledMarkup}`;
        return;
      }
      const min = Math.min(...scheduled.map(project => time(project.start_at)));
      const max = Math.max(Date.now(), ...scheduled.map(project => time(project.due_at)));
      const span = Math.max(86400000, max - min);
      const date = value => new Intl.DateTimeFormat('zh-CN', {timeZone: 'Asia/Shanghai', month: '2-digit', day: '2-digit'}).format(new Date(value));
      const ticks = Array.from({length: 8}, (_, index) => `<span>${date(min + (span * index / 7))}</span>`).join('');
      const nowLeft = Math.max(0, Math.min(100, (Date.now() - min) / span * 100));
      const colors = ['ivory', 'obsidian', 'emerald', 'gold'];
      const rows = scheduled.sort((a, b) => time(a.start_at) - time(b.start_at)).map((project, index) => {
        const left = (time(project.start_at) - min) / span * 100;
        const width = Math.max(3, (time(project.due_at) - time(project.start_at)) / span * 100);
        const duration = Math.max(1, dayOrdinal(time(project.due_at)) - dayOrdinal(time(project.start_at)) + 1);
        return `<div class="gw-calendar-row"><strong title="${esc(project.name || project.title || '未命名项目')}">${esc(project.name || project.title || '未命名项目')}</strong><div class="gw-calendar-track"><button type="button" class="gw-calendar-bar gw-calendar-bar-${colors[index % colors.length]}" data-project-calendar-project="${esc(project.id)}" style="left:${left.toFixed(2)}%;width:${width.toFixed(2)}%"><span>${esc(project.name || project.title || '未命名项目')}</span><b>${duration} 天</b></button></div></div>`;
      }).join('');
      body.innerHTML = `<div class="gw-calendar-axis"><span>WORKFLOW PLAN</span><div>${ticks}</div></div><div class="gw-calendar-grid"><i class="gw-calendar-now" style="left:${nowLeft.toFixed(2)}%" aria-hidden="true"><b>NOW</b></i>${rows}</div>${unscheduledMarkup}`;
    },

    // 2. Dual Trapezoid VU Meters Engine
    //    Phase 12 A4：后端已真实接入 /api/observability/health（psutil CPU/内存/磁盘读数）。
    //    这里用真实响应驱动表针；请求失败或读数缺失时降级显示显式「未接入」，
    //    绝不用随机数或常量曲线伪造 CPU/RAM 负载。
    vuState: {
      cpu: { current: 0, target: 0, minAngle: -72, maxAngle: 72 },
      ram: { current: 0, target: 0, minAngle: -72, maxAngle: 72 },
      // 仅当成功读到真实遥测读数时才置为 true；否则保持 false 走显式降级。
      integrated: false,
      lastSyncAt: 0,
      refreshTimer: null
    },

    setVUMeter: function(metric, percent) {
      // 只有真实遥测接线（integrated=true）才允许驱动表针；否则一律忽略，
      // 防止调用方在无端点时把界面推成「看起来在测量」。
      if (!this.vuState.integrated || !this.vuState[metric]) return;
      const value = Number(percent);
      if (!Number.isFinite(value)) return;
      this.vuState[metric].target = Math.max(0, Math.min(100, value));
    },

    /**
     * 读取真实硬件遥测并驱动 VU 表。
     * 数据来源：GET /api/observability/health 的 hardware_telemetry 检查项；
     * 只在拿到有限数值时接线（integrated=true），失败/缺失一律显式降级。
     */
    syncTelemetry: async function() {
      try {
        const res = await fetch('/api/observability/health', {
          credentials: 'same-origin',
          cache: 'no-store'
        });
        if (!res.ok) throw new Error('health ' + res.status);
        const data = await res.json();
        const checks = Array.isArray(data && data.checks) ? data.checks : [];
        const hw = checks.filter(function (c) { return c && c.name === 'hardware_telemetry'; })[0];
        const metrics = hw && hw.metrics ? hw.metrics : null;
        const cpu = metrics ? Number(metrics.cpu_percent) : NaN;
        const mem = metrics ? Number(metrics.memory_percent) : NaN;
        if (hw && hw.status === 'ok' && Number.isFinite(cpu) && Number.isFinite(mem)) {
          this.vuState.integrated = true;
          this.vuState.lastSyncAt = Date.now();
          this.applyVUMeterReading(cpu, mem);
        } else {
          this.markTelemetryUnavailable(hw ? hw.status : 'not_integrated');
        }
        // 同一次 health 应答里消费真实 GPU 检查项（无则显式降级）。
        const gpu = checks.filter(function (c) { return c && c.name === 'gpu_telemetry'; })[0] || null;
        this.applyGpuTelemetry(gpu);
        this.applyPipelineReadouts(hw);
      } catch (e) {
        this.markTelemetryUnavailable('service_unavailable');
      }
    },

    /**
     * 真实 GPU 遥测：消费同一次 GET /api/observability/health 的 gpu_telemetry 检查项。
     * 只在 checks 里 gpu_telemetry.status===ok 且字段有限时接线；缺失一律显式降级。
     * 设备清单/显存/利用率全部来自 nvidia-smi，绝不伪造设备型号或数值。
     */
    /** 最近一次真实 GPU 检查项：供后注入的顶栏推子回放，避免出现「读取中」空窗。 */
    lastGpuCheck: null,

    applyGpuTelemetry: function(check) {
      this.lastGpuCheck = check || null;
      const ok = check && check.status === 'ok' && check.metrics;
      const util = ok ? Number(check.metrics.gpu_utilization_percent) : NaN;
      const vram = ok ? Number(check.metrics.gpu_memory_percent) : NaN;
      const devices = ok && Array.isArray(check.metrics.devices) ? check.metrics.devices : [];
      const kind = (check && check.status) === 'ok' ? 'ok'
        : (check && check.status === 'service_unavailable' ? 'service_unavailable' : 'not_integrated');

      const utilNode = document.getElementById('gpuUtilReadout');
      const vramNode = document.getElementById('gpuVramReadout');
      const cluster = document.getElementById('gpuClusterStatus');
      const capNode = document.getElementById('gpuVramCapReadout');

      if (Number.isFinite(util) && utilNode) {
        utilNode.textContent = Math.round(util) + '%';
        utilNode.className = 'text-[10px] font-mono text-cyan-300 font-bold';
        utilNode.removeAttribute('data-gw-degradation');
        utilNode.title = '真实 GPU 平均利用率（nvidia-smi，' + devices.length + ' 台设备）';
      }
      if (Number.isFinite(vram) && vramNode) {
        vramNode.textContent = Math.round(vram) + '%';
        vramNode.className = 'text-[10px] font-mono text-cyan-300 font-bold';
        vramNode.removeAttribute('data-gw-degradation');
        vramNode.title = '真实显存占用率（nvidia-smi 平均值）';
      }

      // workbench-shell.js 注入的顶栏 FLUX/VRAM 推子：同一真实读数驱动，缺失即显式降级。
      // 修复背景：该块原先永久写死「本切片无任何真实算力/GPU 遥测数据源」，与已接入的
      // nvidia-smi 遥测自相矛盾（同一条顶栏另一侧正显示真实读数）。
      [['data-gw-gpu-util', util], ['data-gw-gpu-vram', vram]].forEach(function (pair) {
        const key = pair[0];
        const value = pair[1];
        const node = document.querySelector('[' + key + ']');
        const fill = document.querySelector('[' + key + '-fill]');
        const thumb = document.querySelector('[' + key + '-thumb]');
        if (Number.isFinite(value) && ok) {
          const pct = Math.max(0, Math.min(100, Math.round(value)));
          if (node) {
            node.textContent = pct + '%';
            node.className = 'text-cyan-300';
            node.removeAttribute('data-gw-degradation');
            node.title = '真实读数（nvidia-smi via /api/observability/health，' + devices.length + ' 台设备）';
          }
          if (fill) fill.style.width = pct + '%';
          if (thumb) thumb.style.left = pct + '%';
        } else {
          if (node) {
            node.textContent = kind === 'service_unavailable' ? '暂不可用' : '未接入';
            node.className = 'text-amber-300';
            node.setAttribute('data-gw-degradation', kind);
            node.title = '无真实 GPU 遥测读数（nvidia-smi 不可用或未检出设备）';
          }
          if (fill) fill.style.width = '0%';
          if (thumb) thumb.style.left = '0%';
        }
      });

      const badge = document.getElementById('gpuClusterCountBadge');
      if (badge) {
        if (ok && devices.length) {
          badge.textContent = devices.length + ' 台真实 GPU';
          badge.className = 'text-[7.5px] font-mono px-1 py-0.5 rounded-full bg-emerald-500/15 text-emerald-400 border border-emerald-500/30 shrink-0';
          badge.removeAttribute('data-gw-degradation');
          badge.title = '真实 GPU 设备数（nvidia-smi）';
        } else {
          badge.textContent = '未接入';
          badge.className = 'text-[7.5px] font-mono px-1 py-0.5 rounded-full bg-amber-500/15 text-amber-300 border border-amber-500/30 shrink-0';
          badge.setAttribute('data-gw-degradation', kind);
        }
      }

      // 项目卡的「算力集群」格：同一真实 GPU 读数（全部匹配元素一起改写）。
      document.querySelectorAll('[data-gw-gpu-cluster]').forEach(function (el) {
        if (ok && devices.length) {
          el.textContent = devices.length + ' 台 GPU';
          el.className = 'text-[8px] font-mono font-bold text-cyan-300 leading-tight';
          el.removeAttribute('data-gw-degradation');
          el.title = '真实 GPU 设备数（nvidia-smi，' + devices.length + ' 台）';
        } else {
          el.textContent = '未接入';
          el.className = 'text-[8px] font-mono font-bold text-slate-500 leading-tight';
          el.setAttribute('data-gw-degradation', kind);
          el.title = '无真实 GPU 遥测（nvidia-smi 不可用或未检出设备）';
        }
      });

      const grid = document.getElementById('gpuDeviceGrid');
      if (grid) {
        if (ok && devices.length) {
          grid.innerHTML = devices.map(function (d) {
            const memPct = Number.isFinite(Number(d.memory_utilization_percent))
              ? Math.round(Number(d.memory_utilization_percent)) + '%' : '—';
            return '<div class="bay-inset py-0.5 px-1.5 rounded-md flex items-center justify-between text-[8px] font-mono">'
              + '<span class="text-slate-300 truncate" title="' + String(d.name || '') + '">'
              + String(d.name || ('GPU #' + d.index)) + '</span>'
              + '<span class="text-cyan-300 font-bold">' + Math.round(Number(d.utilization_percent) || 0) + '%</span>'
              + '<span class="text-slate-400">' + memPct + '</span></div>';
          }).join('');
        } else {
          grid.innerHTML = '<div class="col-span-2 bay-inset py-0.5 px-1.5 rounded-md text-[8px] font-mono text-amber-300" '
            + 'data-gw-degradation="' + kind + '">无真实 GPU 遥测（nvidia-smi 不可用或未检出设备）</div>';
        }
      }
      if (capNode) {
        if (ok && devices.length) {
          const totalMib = devices.reduce(function (a, d) { return a + (Number(d.memory_total_mib) || 0); }, 0);
          capNode.textContent = 'VRAM 合计上限 ' + (totalMib / 1024).toFixed(1) + ' GiB';
          capNode.className = 'text-[8.5px] font-mono text-slate-300 font-semibold';
          capNode.removeAttribute('data-gw-degradation');
          capNode.title = '真实显存上限合计（nvidia-smi，' + devices.length + ' 台设备）';
        } else {
          capNode.textContent = 'VRAM 上限未接入';
          capNode.setAttribute('data-gw-degradation', kind);
          capNode.title = '无真实 GPU 遥测，未读取显存上限';
        }
      }
      if (cluster) {
        if (ok) {
          cluster.className = 'text-[8.5px] font-mono text-emerald-400 flex items-center';
          cluster.innerHTML = '<span class="w-1.5 h-1.5 rounded-full bg-emerald-400 mr-1"></span>GPU 遥测已接入（' + devices.length + ' 台）';
          cluster.removeAttribute('data-gw-degradation');
          cluster.title = '真实数据源：nvidia-smi via GET /api/observability/health';
        } else {
          cluster.className = 'text-[8.5px] font-mono text-amber-300 flex items-center';
          cluster.innerHTML = '<span class="w-1.5 h-1.5 rounded-full bg-amber-400 mr-1 animate-pulse"></span>'
            + (kind === 'service_unavailable' ? 'GPU 遥测暂不可用' : 'GPU 遥测未接入');
          cluster.setAttribute('data-gw-degradation', kind);
        }
      }
    },

    /**
     * 「流程管线 / 缓冲」类状态位：统一由同一次 health 的 hardware_telemetry 真实读数驱动。
     * 无真实读数时如实降级，绝不显示伪造百分比。
     */
    applyPipelineReadouts: function(hw) {
      const ok = hw && hw.status === 'ok' && hw.metrics;
      const m = ok ? hw.metrics : null;
      const kind = (hw && hw.status === 'ok') ? 'ok'
        : (hw && hw.status === 'service_unavailable' ? 'service_unavailable' : 'not_integrated');
      const sets = [
        ['flowPipelineReadout', m ? Number(m.disk_percent) : NaN, '磁盘占用'],
        ['bufferReadout', m ? Number(m.memory_percent) : NaN, '内存占用']
      ];
      sets.forEach(function (entry) {
        const node = document.getElementById(entry[0]);
        if (!node) return;
        const value = entry[1];
        if (Number.isFinite(value)) {
          node.textContent = Math.round(value) + '%';
          node.className = node.className.replace(/text-amber-300/g, 'text-cyan-300');
          node.removeAttribute('data-gw-degradation');
          node.title = '真实宿主机读数（' + entry[2] + '，来自 /api/observability/health）';
        } else {
          node.textContent = '未接入';
          node.setAttribute('data-gw-degradation', kind);
          node.title = '无真实宿主机遥测读数，未读取任何指标';
        }
      });
    },

    /** 真实读数写入表针与读数位（不再标记降级）。 */
    applyVUMeterReading: function(cpuPercent, memoryPercent) {
      this.setVUMeter('cpu', cpuPercent);
      this.setVUMeter('ram', memoryPercent);
      [['cpu', 'cpuValText'], ['ram', 'ramValText']].forEach(function (entry) {
        const textEl = document.getElementById(entry[1]);
        if (!textEl) return;
        textEl.removeAttribute('data-gw-degradation');
        textEl.removeAttribute('title');
      });
    },

    /** 遥测不可用：表针归零并显式标注原因，绝不显示假数值。 */
    markTelemetryUnavailable: function (kind) {
      this.vuState.integrated = false;
      const label = kind === 'service_unavailable' ? '暂不可用' : '未接入';
      const reason = kind === 'service_unavailable'
        ? '硬件遥测端点暂时不可用，未读取到真实 CPU/RAM 负载数据'
        : '未读取到真实硬件遥测读数，无真实 CPU/RAM 负载数据';
      [['cpu', 'svgNeedleCPU', 'cpuValText'], ['ram', 'svgNeedleRAM', 'ramValText']].forEach(function (entry) {
        const needle = document.getElementById(entry[1]);
        const textEl = document.getElementById(entry[2]);
        if (needle) needle.setAttribute('transform', 'rotate(0 74 48)');
        if (textEl) {
          textEl.textContent = label;
          textEl.setAttribute('data-gw-degradation', kind === 'service_unavailable' ? 'service_unavailable' : 'not_integrated');
          textEl.setAttribute('title', reason);
        }
      });
    },

    initVUMeters: function() {
      const cpuNeedle = document.getElementById('svgNeedleCPU');
      const cpuText = document.getElementById('cpuValText');
      const ramNeedle = document.getElementById('svgNeedleRAM');
      const ramText = document.getElementById('ramValText');

      // 先显式降级占位，随后立即用真实 /health 读数覆盖；不启动任何随机抖动。
      this.markTelemetryUnavailable('not_integrated');

      const animate = () => {
        ['cpu', 'ram'].forEach(key => {
          const st = this.vuState[key];
          const val = Math.max(0, Math.min(100, st.target));
          st.current += (val - st.current) * 0.12;
          const angle = st.minAngle + (st.current / 100) * (st.maxAngle - st.minAngle);
          if (key === 'cpu' && cpuNeedle) {
            cpuNeedle.setAttribute('transform', 'rotate(' + angle.toFixed(1) + ' 74 48)');
            if (cpuText && this.vuState.integrated) cpuText.textContent = Math.round(st.current) + '%';
          } else if (key === 'ram' && ramNeedle) {
            ramNeedle.setAttribute('transform', 'rotate(' + angle.toFixed(1) + ' 74 48)');
            if (ramText && this.vuState.integrated) ramText.textContent = Math.round(st.current) + '%';
          }
        });
        requestAnimationFrame(animate);
      };
      requestAnimationFrame(animate);

      // 真实接线：首帧读取 + 周期刷新（15s），只消费后端真实读数。
      this.syncTelemetry();
      if (this.vuState.refreshTimer) clearInterval(this.vuState.refreshTimer);
      this.vuState.refreshTimer = setInterval(() => { this.syncTelemetry(); }, 15000);
    },

    // 3. Modal Controls
    openModal: function(modalId) {
      const el = document.getElementById(modalId);
      if (!el) return;
      el.classList.add('open');
      document.body.style.overflow = 'hidden';
    },

    closeModal: function(modalId) {
      const el = document.getElementById(modalId);
      if (!el) return;
      el.classList.remove('open');
      document.body.style.overflow = '';
    },

    // 4. Drawer Controls
    openDrawer: function(drawerId) {
      const el = document.getElementById(drawerId);
      if (!el) return;
      el.classList.add('open');
      const backdrop = document.getElementById(drawerId + '-backdrop');
      if (backdrop) backdrop.classList.add('open');
    },

    closeDrawer: function(drawerId) {
      const el = document.getElementById(drawerId);
      if (!el) return;
      el.classList.remove('open');
      const backdrop = document.getElementById(drawerId + '-backdrop');
      if (backdrop) backdrop.classList.remove('open');
    },

    // 4.5 真实在线状态探针：GET /api/observability/health（Phase 12 已接入）
    // 只依据后端返回的 status/data_status/checks 判定；未接入或异常时如实降级，
    // 绝不无条件渲染绿色「在线」。
    onlineState: { kind: 'unknown', detail: '' },

    syncOnlineStatus: async function() {
      const node = document.getElementById('gwOnlineStatus');
      if (!node) return;
      node.textContent = '探测中…';
      try {
        const res = await fetch('/api/observability/health', {
          credential: 'same-origin',
          cache: 'no-store'
        });
        const body = await res.json().catch(() => ({}));
        if (!res.ok) {
          const kind = res.status === 503 ? 'service_unavailable' : 'not_integrated';
          this.applyOnlineStatus(kind, 'health HTTP ' + res.status);
          return;
        }
        const checks = Array.isArray(body.checks) ? body.checks : [];
        const okCount = checks.filter(c => c && c.status === 'ok').length;
        const status = String(body.status || '');
        const dataStatus = String(body.data_status || '');
        const allOk = checks.length > 0 && okCount === checks.length && status === 'ok' && dataStatus === 'ok';
        this.applyOnlineStatus(allOk ? 'ok' : 'degraded',
          'health status=' + status + ' data_status=' + dataStatus + ' ok=' + okCount + '/' + checks.length);
      } catch (_) {
        this.applyOnlineStatus('service_unavailable', 'health 请求异常');
      }
    },

    applyOnlineStatus: function(kind, detail) {
      const node = document.getElementById('gwOnlineStatus');
      if (!node) return;
      this.onlineState = { kind: kind, detail: detail || '' };
      const leds = [];
      const container = node.parentElement;
      if (container) container.querySelectorAll('span').forEach(s => { if (s !== node) leds.push(s); });
      if (kind === 'ok') {
        node.textContent = '已接入';
        node.className = 'text-[9px] font-mono text-emerald-400 font-semibold';
        node.removeAttribute('data-gw-degradation');
        node.title = '真实探针全部通过（' + detail + '）';
        leds.forEach(s => {
          if (s.className.includes('rounded-full')) {
            s.className = 'relative inline-flex rounded-full h-2 w-2 bg-emerald-400 shadow-[0_0_6px_#34d399]';
          }
        });
      } else if (kind === 'degraded') {
        node.textContent = '部分降级';
        node.className = 'text-[9px] font-mono text-amber-300 font-semibold';
        node.setAttribute('data-gw-degradation', 'degraded');
        node.title = '真实探针存在降级项（' + detail + '）';
      } else if (kind === 'service_unavailable') {
        node.textContent = '暂不可用';
        node.className = 'text-[9px] font-mono text-amber-300 font-semibold';
        node.setAttribute('data-gw-degradation', 'service_unavailable');
        node.title = '健康探测端点暂时不可用（' + detail + '）';
      } else {
        node.textContent = '未接入';
        node.className = 'text-[9px] font-mono text-amber-300 font-semibold';
        node.setAttribute('data-gw-degradation', 'not_integrated');
        node.title = '健康探测端点未接入（' + detail + '）';
      }
      // 顶栏硬件总线标题：与在线胶囊共用同一次 health 探针，绝不静态谎称未接入。
      this.applyHeaderTelemetryLabels(kind, detail);
    },

    /**
     * 顶栏「引擎总线 / 硬件遥测」标题真实状态同步。
     * 只消费同一次 /api/observability/health 结果；未接入 / 暂不可用时如实降级。
     * 带 data-gw-header-prefix 的元素会被写成「前缀 · 真实状态」。
     */
    applyHeaderTelemetryLabels: function(kind, detail) {
      const label = kind === 'ok' ? '已接入'
        : (kind === 'degraded' ? '部分降级'
        : (kind === 'service_unavailable' ? '暂不可用' : '未接入'));
      const tone = kind === 'ok' ? 'text-emerald-400'
        : (kind === 'degraded' || kind === 'service_unavailable' ? 'text-amber-300' : 'text-amber-300');
      document.querySelectorAll('[data-gw-header-prefix]').forEach(function (el) {
        const prefix = el.getAttribute('data-gw-header-prefix') || '';
        el.textContent = prefix + ' · ' + label;
        if (kind === 'ok') {
          el.removeAttribute('data-gw-degradation');
          el.setAttribute('title', '真实健康探针已全部通过（' + detail + '）');
        } else {
          el.setAttribute('data-gw-degradation', kind);
          el.setAttribute('title', '真实健康探针：' + label + '（' + detail + '）');
        }
        // 只调整状态色，保留元素原有排版类。
        el.classList.remove('text-emerald-400', 'text-amber-300');
        el.classList.add(tone);
      });
    },

    // 4.6 真实阶段进度探针：GET /api/episode-pipelines
    // 侧栏阶段标签只写后端返回的 stage.status；无流水线时如实「无流水线」，
    // 端点/网络异常时如实降级，绝不显示伪造百分比。
    stageState: { kind: 'unknown', detail: '' },

    stageStatusLabel: function(status) {
      return {
        pending: '待开始', running: '进行中', completed: '已完成',
        cancelled: '已取消', failed: '失败'
      }[status] || '状态未知';
    },

    syncStageProgress: async function() {
      const targets = [
        ['busStageScript', 'script'],
        ['busStageVideo', 'video'],
        ['projStageAssets', 'assets'],
        ['projStageVideo', 'video'],
        // '*' 为真实阶段汇总位（已完成/总数）；生产页 STAGE 读数使用。
        ['prodStageReadout', '*']
      ].filter(entry => document.getElementById(entry[0]));
      if (!targets.length) return;
      targets.forEach(entry => {
        const node = document.getElementById(entry[0]);
        node.textContent = '读取中…';
        node.setAttribute('data-gw-degradation', 'not_integrated');
      });
      try {
                // 真实项目 ID 优先：URL ?project_id -> localStorage -> proj-01 回退。
        let stageProjectId = 'proj-01';
        try {
          const fromUrl = new URLSearchParams(window.location.search).get('project_id');
          stageProjectId = fromUrl || localStorage.getItem('workspace_project_id') || 'proj-01';
        } catch (_) { stageProjectId = 'proj-01'; }
        const res = await fetch('/api/episode-pipelines?project_id=' + encodeURIComponent(stageProjectId), {
          credential: 'same-origin',
          cache: 'no-store'
        });
        const body = await res.json().catch(() => null);
        if (!res.ok) {
          const kind = res.status === 503 ? 'service_unavailable' : 'not_integrated';
          this.applyStageProgress(targets, null, kind, 'HTTP ' + res.status);
          return;
        }
        const list = Array.isArray(body && body.pipelines) ? body.pipelines : [];
        const pipeline = list[0] || null;
        if (!pipeline) {
          this.applyStageProgress(targets, null, 'empty', '后端返回 pipelines=[]');
          return;
        }
        const stages = Array.isArray(pipeline.stages) ? pipeline.stages : [];
        const byKey = {};
        stages.forEach(stage => { if (stage && stage.stage) byKey[stage.stage] = stage; });
        byKey.__summary = {
          total: stages.length,
          done: stages.filter(s => s && (s.status === 'completed' || s.status === 'cancelled')).length
        };
        this.applyStageProgress(targets, byKey, 'ok', 'pipeline=' + (pipeline.pipeline_id || ''));
      } catch (_) {
        this.applyStageProgress(targets, null, 'service_unavailable', '请求异常');
      }
    },

    applyStageProgress: function(targets, byKey, kind, detail) {
      targets.forEach(entry => {
        const node = document.getElementById(entry[0]);
        if (!node) return;
        // '*' 汇总位：只写真实施工阶段计数，不伪造进度百分比。
        if (entry[1] === '*') {
          if (kind === 'ok' && byKey && byKey.__summary) {
            node.textContent = byKey.__summary.done + '/' + byKey.__summary.total + ' 阶段';
            const allDone = byKey.__summary.done === byKey.__summary.total && byKey.__summary.total > 0;
            node.className = 'text-[9px] font-mono font-bold ' + (allDone ? 'text-emerald-400' : 'text-cyan-300');
            node.removeAttribute('data-gw-degradation');
            node.title = '真实阶段状态：' + byKey.__summary.done + '/' + byKey.__summary.total + ' 已完成（' + detail + '）';
          } else if (kind === 'empty') {
            node.textContent = '无流水线';
            node.className = 'text-[9px] font-mono font-bold text-slate-400';
            node.removeAttribute('data-gw-degradation');
            node.title = '真实应答确为空流水线（' + detail + '）';
          } else {
            node.textContent = kind === 'service_unavailable' ? '暂不可用' : '未接入';
            node.className = 'text-[9px] font-mono font-bold text-amber-300';
            node.setAttribute('data-gw-degradation', kind);
            node.title = '阶段进度读取失败（' + detail + '）';
          }
          return;
        }
        if (kind === 'ok') {
          const stage = byKey ? byKey[entry[1]] : null;
          if (stage) {
            const status = String(stage.status || '');
            node.textContent = this.stageStatusLabel(status);
            if (status === 'completed') node.className = 'text-[8px] font-mono text-emerald-400 shrink-0';
            else if (status === 'running') node.className = 'text-[8px] font-mono text-cyan-300 shrink-0';
            else if (status === 'failed' || status === 'cancelled') node.className = 'text-[8px] font-mono text-rose-400 shrink-0';
            else node.className = 'text-[8px] font-mono text-slate-400 shrink-0';
            node.removeAttribute('data-gw-degradation');
            node.title = '真实阶段状态：' + status + '（' + detail + '）';
          } else {
            node.textContent = '无该阶段';
            node.className = 'text-[8px] font-mono text-slate-400 shrink-0';
            node.setAttribute('data-gw-degradation', 'not_integrated');
            node.title = '流水线中不含该阶段（' + detail + '）';
          }
        } else if (kind === 'empty') {
          node.textContent = '无流水线';
          node.className = 'text-[8px] font-mono text-slate-400 shrink-0';
          node.removeAttribute('data-gw-degradation');
          node.title = '真实应答确为空流水线（' + detail + '）';
        } else {
          node.textContent = kind === 'service_unavailable' ? '暂不可用' : '未接入';
          node.className = 'text-[8px] font-mono text-amber-300 shrink-0';
          node.setAttribute('data-gw-degradation', kind);
          node.title = '阶段进度读取失败（' + detail + '）';
        }
      });
    },

    // 5. Real Account & Auth Router Integration (/api/asset-auth)
    authState: {
      // 字段与后端 GET /api/asset-auth/status 的真实契约逐字对应（见 api/routes_auth.py）。
      auth_mode: 'local_account',
      oidc_ready: false,
      authenticated: false,
      principal: null,
      login_available: false,
      setup_required: false,
      logout_available: false,
      reason: '',
      loaded: false,
      // 显式降级标记：'ok' | 'not_integrated' | 'unavailable'（禁止用假身份兜底）
      degradation: 'ok',
      degradationStatus: 0
    },

    syncAuth: async function() {
      // 统一「无后端时显式降级」：认证接口不可用时**绝不**伪造身份，
      // 只把状态标记为「未接入 / 暂不可用」，由 UI 明说「未登录 · 认证服务未接入」。
      const authStartedAt = (window.performance && performance.now) ? performance.now() : Date.now();
      try {
        const res = await fetch('/api/asset-auth/status', {
          credentials: 'same-origin',
          cache: 'no-store'
        });
        const data = await res.json().catch(() => ({}));
        if (res.ok) {
          // 严格按后端契约读取；authenticated=false 时 principal 必须为 null，绝不臆造身份。
          const authenticated = Boolean(data.authenticated);
          this.authState.auth_mode = String(data.auth_mode || 'local_account');
          this.authState.oidc_ready = Boolean(data.oidc_ready);
          this.authState.authenticated = authenticated;
          this.authState.principal = authenticated ? (data.principal || null) : null;
          this.authState.login_available = Boolean(data.login_available);
          this.authState.setup_required = Boolean(data.setup_required);
          this.authState.logout_available = Boolean(data.logout_available);
          this.authState.reason = String(data.reason || '');
          this.authState.degradation = 'ok';
          this.authState.degradationStatus = res.status;
          this.authState.loaded = true;
          const nowMs = (window.performance && performance.now) ? performance.now() : Date.now();
          this.authState.latencyMs = Math.max(0, Math.round(nowMs - authStartedAt));
        } else {
          this.markAuthUnavailable(res.status, data && data.detail);
        }
      } catch (e) {
        this.markAuthUnavailable(0, undefined);
      }
      this.updateAuthDOM();
      if (this.authState.auth_mode === 'local_account' && this.authState.setup_required && !this.setupPromptShown) {
        this.setupPromptShown = true;
        this.openAccountModal();
      } else if (document.getElementById('accountModal')?.classList.contains('open')) {
        this.renderAccountModalContent();
      }
    },

    /**
     * 认证服务不可用 / 未接入：显式降级。
     * 绝不臆造 principal（历史缺陷：404 时伪造 admin / 本地管理员 / 系统管理员）。
     */
    markAuthUnavailable: function(status, detailValue) {
      const degradation = window.GWDegradation;
      const kind = degradation ? degradation.statusKind(status, detailValue) : 'not_integrated';
      this.authState.principal = null;
      this.authState.authenticated = false;
      this.authState.login_available = false;
      this.authState.logout_available = false;
      this.authState.oidc_ready = false;
      this.authState.degradation = kind === 'service_unavailable' ? 'unavailable' : 'not_integrated';
      this.authState.degradationStatus = status;
      this.authState.loaded = true;
    },

    updateAuthDOM: function() {
      const user = this.authState.authenticated ? this.authState.principal : null;
      const degraded = Boolean(this.authState.degradation) && this.authState.degradation !== 'ok';
      const roleMap = { admin: '管理员', editor: '编辑者', reviewer: '审阅者' };
      const roleLabel = user ? (roleMap[user.role] || user.role) : '访客';
      // 显式降级：认证服务未接入/不可用时明说原因，绝不显示「本地模式 / 已登录」。
      const stateLabel = degraded
        ? (this.authState.degradation === 'unavailable' ? '认证服务暂不可用' : '认证服务未接入')
        : (user ? `${roleLabel} · 已登录`
          : (this.authState.login_available ? '未登录 · 点击登录' : '未登录'));

      const nameText = degraded ? '未登录'
        : (user ? (user.display_name || user.username) : '未登录');
      const initial = degraded ? '?'
        : (user ? (user.display_name || user.username || 'AD').substring(0, 2).toUpperCase() : '?');

      document.querySelectorAll('.hw-user-name, #hwUserDisplayName').forEach(el => el.textContent = nameText);
      document.querySelectorAll('.hw-user-role, #hwUserRoleBadge').forEach(el => el.textContent = stateLabel);
      document.querySelectorAll('.hw-user-avatar, #hwUserAvatarInitial').forEach(el => {
        if (el.tagName === 'DIV' || el.tagName === 'SPAN') el.textContent = initial;
      });
      document.querySelectorAll('.hw-user-led, #hwUserLedStatus').forEach(el => {
        if (user && !degraded) {
          el.className = 'w-1.5 h-1.5 rounded-full bg-emerald-400 ml-1.5 shadow-[0_0_6px_#10b981]';
        } else {
          // 未接入/未登录：琥珀色脉冲，不使用绿色「就绪」灯。
          el.className = 'w-1.5 h-1.5 rounded-full bg-amber-400 ml-1.5 shadow-[0_0_6px_#f59e0b] animate-pulse';
        }
      });

      // 顶部头像键帽的状态灯：只有真实认证成功才标记 authenticated；
      // 未接入 / 不可用一律标记为未验证，绝不让静态绿色灯被读成「已登录」。
      const identityState = degraded ? 'unavailable' : (user ? 'authenticated' : 'unverified');
      document.querySelectorAll('.hw-avatar-keycap, #hwTopbarAvatar').forEach(el => {
        el.setAttribute('role', 'button');
        el.setAttribute('tabindex', '0');
        el.setAttribute('aria-label', '打开认证中心');
        el.setAttribute('data-gw-identity', identityState);
        el.title = degraded
          ? (identityState === 'unavailable' ? '认证服务暂不可用：点击查看详情' : '认证服务未接入：点击查看详情')
          : (user ? `已登录：${user.display_name || user.username}（${user.role || '—'}）`
            : '未登录：点击打开认证中心');
      });

      // 席位凭据块（index/settings 页共用 ID）：只写真实 /api/asset-auth/status 结果。
      this.syncSeatPanel();
    },

    /**
     * 真实填充「当前席位凭据与治理策略」三格 + SESSION 胶囊。
     * 数据仅来自 /api/asset-auth/status；未接入/未认证时如实降级，绝不伪造身份或凭据回退。
     */
    syncSeatPanel: function() {
      const roleLabels = { admin: '管理员', editor: '编辑者', reviewer: '审阅者' };
      const badge = document.getElementById('settingsSessionBadge');
      const roleEl = document.getElementById('settingsSeatRole');
      const chanEl = document.getElementById('settingsAuthChannel');
      const latEl = document.getElementById('settingsAuthLatency');
      if (!badge && !roleEl && !chanEl && !latEl) return;

      const setText = (node, text) => { if (node) node.textContent = text; };
      const mark = (node, kind, title) => {
        if (!node) return;
        node.setAttribute('data-gw-degradation', kind);
        if (title) node.title = title;
      };
      const clear = (node) => { if (node) node.removeAttribute('data-gw-degradation'); };

      const degraded = Boolean(this.authState.degradation) && this.authState.degradation !== 'ok';
      if (degraded) {
        const kind = this.authState.degradation === 'unavailable' ? 'service_unavailable' : 'not_integrated';
        const label = kind === 'service_unavailable' ? '认证服务暂不可用' : '认证端点未接入';
        setText(badge, 'SESSION · ' + label);
        setText(roleEl, label);
        setText(chanEl, '未取到真实凭据状态');
        setText(latEl, '未测量');
        mark(badge, kind, label);
        mark(roleEl, kind, label);
        mark(chanEl, kind, label);
        mark(latEl, kind, label);
        return;
      }

      const user = this.authState.authenticated ? this.authState.principal : null;
      if (user) {
        const who = user.display_name || user.username || '已登录';
        setText(badge, 'SESSION · ' + who);
        setText(roleEl, roleLabels[user.role] || user.role || '已登录');
      } else {
        setText(badge, 'SESSION · 未登录');
        setText(roleEl, '访客（未登录）');
      }
      clear(badge);
      clear(roleEl);

      const mode = String(this.authState.auth_mode || 'unknown');
      setText(chanEl, mode === 'local_account' ? '本地账户（真实会话）'
        : (mode === 'oidc' ? 'OIDC（真实 IdP）' : mode + '（后端声明）'));
      clear(chanEl);

      if (Number.isFinite(this.authState.latencyMs)) {
        setText(latEl, this.authState.latencyMs + ' ms（实测 HTTP ' + (this.authState.degradationStatus || 200) + '）');
        clear(latEl);
      } else {
        setText(latEl, '未测量');
        mark(latEl, 'not_integrated', '未取到真实响应耗时');
      }
    },


    // 事件委托覆盖动态 shell 替换后的头像，避免外观可点但没有响应。
    bindAccountEntry: function() {
      if (this.accountEntryBound) return;
      this.accountEntryBound = true;
      document.addEventListener('click', event => {
        const avatar = event.target.closest('.hw-avatar-keycap, #hwTopbarAvatar');
        // 兼容仍保留内联入口的页面，避免重复打开模态框。
        if (!avatar || avatar.hasAttribute('onclick')) return;
        this.openAccountModal();
      });
      document.addEventListener('keydown', event => {
        if (event.key !== 'Enter' && event.key !== ' ') return;
        const avatar = event.target.closest('.hw-avatar-keycap, #hwTopbarAvatar');
        if (!avatar || event.repeat) return;
        event.preventDefault();
        this.openAccountModal();
      });
    },

    // 呼出原生拟物账户认证与管理模态框
    openAccountModal: function() {
      let modal = document.getElementById('accountModal');
      if (!modal) {
        modal = document.createElement('div');
        modal.id = 'accountModal';
        modal.className = 'hw-modal-backdrop';
        modal.innerHTML = `
          <div class="hw-modal-dialog w-[460px] p-4 text-slate-200" id="accountModalDialog">
            <!-- 动态渲染内容 -->
          </div>
        `;
        document.body.appendChild(modal);
        modal.addEventListener('click', (e) => {
          if (e.target === modal) HardwareDeck.closeModal('accountModal');
        });
      }

      this.renderAccountModalContent();
      this.openModal('accountModal');
    },

    renderAccountModalContent: function() {
      const dialog = document.getElementById('accountModalDialog');
      if (!dialog) return;
      // 本函数内的 HTML 转义（renderProjectCalendar 里的 esc 是局部作用域，不可复用）。
      const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
      const user = this.authState.authenticated ? this.authState.principal : null;

      const degraded = Boolean(this.authState.degradation) && this.authState.degradation !== 'ok';
      const degradedKind = this.authState.degradation;

      if (degraded) {
        // 显式降级视图：认证路由未接入 / 暂不可用。不提供登录表单，避免误导为可登录。
        const degradedStatus = this.authState.degradationStatus;
        const degradedTitle = degradedKind === 'unavailable' ? '认证服务暂不可用' : '认证服务未接入';
        const degradedBody = degradedKind === 'unavailable'
          ? `后端返回可恢复错误（HTTP ${degradedStatus}），请稍后重试。`
          : `本仓当前切片未提供 /api/asset-auth/* 路由（HTTP ${degradedStatus}），无法登录或管理账户。`;
        dialog.innerHTML = `
          <div class="flex items-center justify-between pb-2 mb-3 border-b border-white/10">
            <div class="flex items-center space-x-2">
              <i data-lucide="plug-zap" class="w-4 h-4 text-amber-300"></i>
              <h3 class="text-sm font-bold text-slate-100">${degradedTitle}</h3>
            </div>
            <button class="text-slate-400 hover:text-white" onclick="HardwareDeck.closeModal('accountModal')">
              <i data-lucide="x" class="w-4 h-4"></i>
            </button>
          </div>
          <div class="bay-inset p-3 rounded-xl border border-amber-500/30 text-[11px] font-mono text-amber-200" role="status" data-hw-auth-degradation="${degradedKind}">
            <span class="block">该功能尚未接入后端（未纳入当前切片）</span>
            <span class="block mt-1 text-[9px] text-slate-500">${degradedBody}</span>
          </div>
          <div class="pt-3 mt-3 border-t border-white/10 text-right">
            <button type="button" class="px-3 py-1.5 rounded-lg text-xs font-mono text-slate-400 hover:text-white" onclick="HardwareDeck.closeModal('accountModal')">关闭</button>
          </div>
        `;
        if (window.lucide) window.lucide.createIcons();
        return;
      }

      if (user) {
        // 已登录视图
        const roleMap = { admin: '系统管理员 (Super Admin)', editor: '主创编辑者 (Editor)', reviewer: '审阅定剪席 (Reviewer)' };
        dialog.innerHTML = `
          <div class="flex items-center justify-between pb-2 mb-3 border-b border-white/10">
            <div class="flex items-center space-x-2">
              <i data-lucide="shield-check" class="w-4 h-4 text-[#dfc384]"></i>
              <h3 class="text-sm font-bold text-slate-100">认证中心 · 账户管理</h3>
            </div>
            <button class="text-slate-400 hover:text-white" onclick="HardwareDeck.closeModal('accountModal')">
              <i data-lucide="x" class="w-4 h-4"></i>
            </button>
          </div>

          <div class="space-y-3 text-xs">
            <div class="bay-inset p-3 rounded-xl flex items-center space-x-3 border border-[#dfc384]/20">
              <div class="w-10 h-10 rounded-full bg-gradient-to-tr from-[#dfc384] to-[#947a57] p-0.5 shadow-lg shrink-0">
                <div class="w-full h-full rounded-full bg-[#08090d] flex items-center justify-center text-[#eddab3] font-bold text-sm">
                  ${(user.display_name || user.username || 'AD').substring(0, 2).toUpperCase()}
                </div>
              </div>
              <div class="flex-1 min-w-0">
                <div class="text-sm font-bold text-slate-100 truncate">${user.display_name || user.username}</div>
                <div class="text-[10px] font-mono text-[#dfc384]">${roleMap[user.role] || user.role}</div>
                <div class="text-[9px] font-mono text-slate-500 mt-0.5">账户ID: ${user.username || user.id || '—'} · 已连接后端认证服务</div>
              </div>
            </div>

            <div class="p-2.5 rounded-xl bg-black/40 border border-white/5 space-y-1.5 text-[10px] font-mono text-slate-400">
              <div class="flex justify-between"><span>会话鉴权方式</span><span class="text-emerald-400">服务端会话 Cookie（HttpOnly）</span></div>
              <div class="flex justify-between"><span>角色来源</span><span class="text-slate-200">${this.authState.auth_mode === 'local_account' ? '本地账户数据库' : 'IdP 组声明映射'}</span></div>
              <div class="flex justify-between"><span>认证模式</span><span class="text-cyan-300">${escapeHtml(this.authState.auth_mode)}</span></div>
              <div class="flex justify-between"><span>状态端点</span><span class="text-slate-300">GET /api/asset-auth/status</span></div>
            </div>

            <div class="pt-2 border-t border-white/10 flex items-center justify-between">
              <a href="/static/pages/assets.html" class="text-[10px] font-mono text-[#dfc384] hover:underline flex items-center space-x-1">
                <i data-lucide="external-link" class="w-3 h-3"></i>
                <span>打开资产中心与团队分配</span>
              </a>
              <div class="flex items-center space-x-2">
                <button type="button" class="px-3 py-1.5 rounded-lg text-xs font-mono text-slate-400 hover:text-white" onclick="HardwareDeck.closeModal('accountModal')">关闭</button>
                <button type="button" class="px-3 py-1.5 rounded-lg bg-red-500/20 text-red-300 border border-red-500/40 text-xs font-bold hover:bg-red-500/30 transition" onclick="HardwareDeck.handleLogout()">退出登录</button>
              </div>
            </div>
          </div>
        `;
      } else if (this.authState.auth_mode === 'local_account') {
        this.renderLocalAccountForm(dialog);
      } else {
        // 未登录视图：OIDC 授权码 + PKCE 跳转（本仓不接收用户名/密码，凭据只交给 IdP）
        const loginAvailable = Boolean(this.authState.login_available);
        const loginHint = loginAvailable
          ? '点击下方按钮将跳转到配置的外部身份提供商（IdP）完成登录；本页面不收集、不保存您的密码。'
          : (this.authState.reason || '外部 IdP 未配置或不可用，当前无法登录。');
        dialog.innerHTML = `
          <div class="flex items-center justify-between pb-2 mb-3 border-b border-white/10">
            <div class="flex items-center space-x-2">
              <i data-lucide="lock" class="w-4 h-4 text-[#dfc384]"></i>
              <h3 class="text-sm font-bold text-slate-100">曜石身份认证 · 系统登录</h3>
            </div>
            <button class="text-slate-400 hover:text-white" onclick="HardwareDeck.closeModal('accountModal')">
              <i data-lucide="x" class="w-4 h-4"></i>
            </button>
          </div>

          <div class="space-y-3 text-xs">
            <div class="bay-inset p-3 rounded-xl border border-white/10 text-[10px] font-mono text-slate-400" role="status" data-hw-auth-login="${loginAvailable ? 'available' : 'disabled'}">
              <span class="block">${escapeHtml(loginHint)}</span>
              <span class="block mt-1 text-[9px] text-slate-500">认证模式: ${escapeHtml(this.authState.auth_mode)} · 授权方式: Authorization Code + PKCE (S256)</span>
            </div>

            <div id="hwLoginErrorMsg" class="text-[10px] font-mono text-red-400 hidden"></div>

            <div class="pt-2 border-t border-white/10 flex items-center justify-between">
              <span class="text-[9px] font-mono text-slate-500">发起端点: POST /api/asset-auth/login</span>
              <div class="flex items-center space-x-2">
                <button type="button" class="px-3 py-1.5 rounded-lg text-xs font-mono text-slate-400 hover:text-white" onclick="HardwareDeck.closeModal('accountModal')">取消</button>
                <button type="button" id="hwLoginSubmitBtn" ${loginAvailable ? '' : 'disabled'} onclick="HardwareDeck.handleLoginSubmit(event)" class="tactile-keycap px-4 py-1.5 rounded-lg text-xs font-bold text-[#eddab3] ${loginAvailable ? '' : 'opacity-50 cursor-not-allowed'}">
                  使用外部身份提供商登录
                </button>
              </div>
            </div>
          </div>
        `;
      }

      if (window.lucide) window.lucide.createIcons();
    },

    // 本地数据库账户与外部 IdP 表单分离；密码不写入浏览器持久存储。
    renderLocalAccountForm: function(dialog) {
      const setup = this.authState.setup_required;
      dialog.innerHTML = `
        <div class="flex items-center justify-between pb-3 mb-3 border-b border-white/10">
          <h3 class="text-sm font-bold text-slate-100">${setup ? '首次设置 · 创建管理员' : '本地账户登录'}</h3>
          <button type="button" aria-label="关闭登录" class="text-slate-400" onclick="HardwareDeck.closeModal('accountModal')">关闭</button>
        </div>
        <p class="mb-3 text-xs text-slate-400">${setup ? '请设置你自己的管理员账户。没有默认密码；账户保存在本机数据库，重启后仍然有效。' : '使用本机数据库中的账号密码登录，无需外部认证服务。'}</p>
        <form id="localAccountForm" class="space-y-3" onsubmit="HardwareDeck.handleLocalAccountSubmit(event)">
          <label class="block text-xs text-slate-300" for="localUsername">账号</label>
          <input id="localUsername" name="username" class="w-full bg-black/40 border border-white/20 rounded-lg p-2 text-slate-100" autocomplete="username" required minlength="3" maxlength="64" pattern="[A-Za-z0-9_.\\-]{3,64}" placeholder="3–64位字母、数字、点、短横线或下划线">
          <label class="block text-xs text-slate-300" for="localPassword">密码</label>
          <input id="localPassword" name="password" type="password" class="w-full bg-black/40 border border-white/20 rounded-lg p-2 text-slate-100" autocomplete="${setup ? 'new-password' : 'current-password'}" required minlength="8" maxlength="128" ${setup ? 'pattern="(?=.*[A-Za-z])(?=.*[0-9]).{8,128}"' : ''} placeholder="至少8位，包含字母和数字">
          ${setup ? '<label class="block text-xs text-slate-300" for="localPasswordConfirm">确认密码</label><input id="localPasswordConfirm" type="password" class="w-full bg-black/40 border border-white/20 rounded-lg p-2 text-slate-100" autocomplete="new-password" required minlength="8" maxlength="128">' : ''}
          <p id="localLoginError" role="alert" class="text-xs text-red-300 hidden"></p>
          <button id="localLoginSubmit" type="submit" class="tactile-keycap w-full rounded-lg p-3 font-bold text-[#eddab3]">${setup ? '创建管理员并登录' : '登录'}</button>
        </form>`;
    },

    handleLocalAccountSubmit: async function(event) {
      event.preventDefault();
      const form = event.target;
      const button = form.querySelector('#localLoginSubmit');
      const error = form.querySelector('#localLoginError');
      const password = form.querySelector('#localPassword').value;
      const confirmation = form.querySelector('#localPasswordConfirm');
      error.classList.add('hidden');
      if (confirmation && confirmation.value !== password) {
        error.textContent = '两次输入的密码不一致。';
        error.classList.remove('hidden');
        return;
      }
      button.disabled = true;
      try {
        const path = this.authState.setup_required ? '/api/asset-auth/local/setup' : '/api/asset-auth/local/login';
        const response = await fetch(path, {
          method: 'POST', credentials: 'same-origin', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({username: form.querySelector('#localUsername').value.trim(), password})
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail?.message || '登录失败，请稍后重试。');
        form.reset();
        // 各业务页重新读取真实会话和权限，避免保留登录前的只读状态。
        window.location.reload();
      } catch (failure) {
        error.textContent = failure.message || '无法连接本地登录服务。';
        error.classList.remove('hidden');
        button.disabled = false;
      }
    },

    handleLoginSubmit: async function(e) {
      if (e && typeof e.preventDefault === 'function') e.preventDefault();
      const errEl = document.getElementById('hwLoginErrorMsg');
      const submitBtn = document.getElementById('hwLoginSubmitBtn');
      const showError = message => {
        if (errEl) {
          errEl.textContent = message;
          errEl.classList.remove('hidden');
        }
      };

      // 本仓不接收、不传输用户名与密码：凭据只在 IdP 页面输入。
      if (!this.authState.login_available) {
        showError(this.authState.reason || '外部 IdP 未配置或不可用，当前无法登录。');
        return;
      }
      if (errEl) errEl.classList.add('hidden');
      if (submitBtn) { submitBtn.disabled = true; submitBtn.textContent = '正在跳转到身份提供商...'; }

      try {
        const res = await fetch('/api/asset-auth/login', {
          method: 'POST',
          credentials: 'same-origin'
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          const detail = data && data.detail;
          const code = detail && typeof detail === 'object' ? detail.code : '';
          const message = detail && typeof detail === 'object' ? detail.message : '';
          if (res.status === 409) {
            // 已登录：刷新状态即可，不视为失败。
            await this.syncAuth();
            this.renderAccountModalContent();
            return;
          }
          if (window.GWDegradation && typeof window.GWDegradation.statusKind === 'function') {
            const kind = window.GWDegradation.statusKind(res.status, detail);
            if (kind === 'not_integrated') throw new Error('认证服务未接入（未纳入当前切片）。');
            if (kind === 'service_unavailable') throw new Error('认证服务暂不可用，请稍后重试。');
          }
          throw new Error(message || '发起登录失败（HTTP ' + res.status + '）。');
        }
        if (!data.authorization_url) {
          throw new Error('后端未返回授权地址，无法跳转登录。');
        }
        // 跳转到 IdP；授权码回调由后端 /api/asset-auth/callback 处理。
        window.location.assign(data.authorization_url);
      } catch (err) {
        showError(err && err.message ? err.message : '发起登录失败。');
        if (submitBtn) { submitBtn.disabled = false; submitBtn.textContent = '使用外部身份提供商登录'; }
      }
    },

    /** 读取回调失败标记（?auth_error=<code>）并在界面明示原因。 */
    consumeAuthError: function() {
      let code = '';
      try {
        const params = new URLSearchParams(window.location.search || '');
        code = params.get('auth_error') || '';
        if (code) {
          params.delete('auth_error');
          const query = params.toString();
          window.history.replaceState({}, '', window.location.pathname + (query ? '?' + query : '') + window.location.hash);
        }
      } catch (e) {
        return '';
      }
      if (!code) return '';
      const messages = {
        state_mismatch: '登录校验失败：state 与本次会话不匹配，已拒绝本次登录。',
        state_expired: '登录流程已过期或已被使用，请重新发起登录。',
        token_exchange_failed: '与身份提供商交换令牌失败，请稍后重试。',
        missing_id_token: '身份提供商未返回 id_token，已拒绝登录。',
        id_token_rejected: '身份令牌校验未通过（签名/iss/aud/exp/nonce 或组映射），已拒绝登录。',
        oidc_unavailable: '认证服务暂不可用，请稍后重试。',
        invalid_callback: '回调参数不完整，已拒绝登录。'
      };
      const message = messages[code] || ('登录失败（' + code + '）。');
      try {
        if (window.alert) window.alert(message);
      } catch (e) { /* 静默：提示失败不影响页面 */ }
      return message;
    },

    handleLogout: async function() {
      try {
        await fetch('/api/asset-auth/logout', {
          method: 'POST',
          credentials: 'same-origin'
        });
      } catch (e) {
        // 网络异常也继续刷新状态：以服务端返回的实际情况为准。
      }
      this.authState.principal = null;
      this.authState.authenticated = false;
      this.authState.logout_available = false;
      await this.syncAuth();
      this.renderAccountModalContent();
    },

    // 6. Real Team & Member Management (/api/asset-auth/teams & /static/pages/assets.html & /static/pages/collab.html)
    openTeamModal: async function() {
      let modal = document.getElementById('teamModal');
      if (!modal) {
        modal = document.createElement('div');
        modal.id = 'teamModal';
        modal.className = 'hw-modal-backdrop';
        modal.innerHTML = `
          <div class="hw-modal-dialog w-[480px] p-4 text-slate-200" id="teamModalDialog"></div>
        `;
        document.body.appendChild(modal);
        modal.addEventListener('click', (e) => {
          if (e.target === modal) HardwareDeck.closeModal('teamModal');
        });
      }

      this.renderTeamModalSkeleton();
      this.openModal('teamModal');
      await this.loadTeamModalData();
    },

    renderTeamModalSkeleton: function() {
      const dialog = document.getElementById('teamModalDialog') || document.querySelector('#teamModal .hw-modal-dialog');
      if (!dialog) return;

      dialog.innerHTML = `
        <div class="flex items-center justify-between pb-2 mb-3 border-b border-white/10">
          <div class="flex items-center space-x-2">
            <i data-lucide="users" class="w-4 h-4 text-[#dfc384]"></i>
            <h3 class="text-sm font-bold text-slate-100">团队与协同席位治理</h3>
          </div>
          <button class="text-slate-400 hover:text-white" onclick="HardwareDeck.closeModal('teamModal')">
            <i data-lucide="x" class="w-4 h-4"></i>
          </button>
        </div>

        <div class="space-y-3 text-xs">
          <!-- 路由快捷直达看板 -->
          <div class="grid grid-cols-2 gap-2">
            <a href="/static/pages/collab.html" class="bay-inset p-2 rounded-xl flex items-center justify-between group hover:border-[#dfc384]/40 transition border border-white/5">
              <div class="flex items-center space-x-2">
                <div class="w-6 h-6 rounded bg-[#dfc384]/15 border border-[#dfc384]/30 flex items-center justify-center text-[#eddab3]">
                  <i data-lucide="network" class="w-3.5 h-3.5"></i>
                </div>
                <div>
                  <div class="font-bold text-slate-200 group-hover:text-[#eddab3] transition">实时协同工作台</div>
                  <div class="text-[8.5px] font-mono text-slate-400">/static/pages/collab.html</div>
                </div>
              </div>
              <i data-lucide="chevron-right" class="w-3.5 h-3.5 text-slate-500 group-hover:text-[#eddab3] transition"></i>
            </a>

            <a href="/static/pages/assets.html" class="bay-inset p-2 rounded-xl flex items-center justify-between group hover:border-[#dfc384]/40 transition border border-white/5">
              <div class="flex items-center space-x-2">
                <div class="w-6 h-6 rounded bg-cyan-500/15 border border-cyan-500/30 flex items-center justify-center text-cyan-300">
                  <i data-lucide="shield-alert" class="w-3.5 h-3.5"></i>
                </div>
                <div>
                  <div class="font-bold text-slate-200 group-hover:text-cyan-300 transition">权限与团队审批</div>
                  <div class="text-[8.5px] font-mono text-slate-400">/static/pages/assets.html</div>
                </div>
              </div>
              <i data-lucide="external-link" class="w-3.5 h-3.5 text-slate-500 group-hover:text-cyan-300 transition"></i>
            </a>
          </div>

          <!-- 动态成员/团队列表容器 -->
          <div id="teamModalList" class="space-y-2 max-h-60 overflow-y-auto pr-1">
            <div class="bay-inset p-3 rounded-xl text-[10px] font-mono text-slate-400 flex items-center justify-center space-x-2">
              <div class="w-3 h-3 border-2 border-[#dfc384] border-t-transparent rounded-full animate-spin"></div>
              <span>正在向真实后端同步团队与席位 (/api/asset-auth/teams)...</span>
            </div>
          </div>

          <div class="pt-2 border-t border-white/10 flex items-center justify-between text-[9px] font-mono text-slate-500">
            <span>预期路由: /api/asset-auth/teams · /api/asset-auth/users（未接入时下方明示）</span>
            <button type="button" class="px-3 py-1.5 rounded-lg text-xs font-mono text-slate-400 hover:text-white" onclick="HardwareDeck.closeModal('teamModal')">关闭</button>
          </div>
        </div>
      `;

      if (window.lucide) window.lucide.createIcons();
    },

    loadTeamModalData: async function(targetId) {
      const targets = targetId ? [targetId] : ['panelTeamList', 'teamModalList'];
      const containers = targets.map(id => document.getElementById(id)).filter(Boolean);
      if (containers.length === 0) return;
      const degradation = window.GWDegradation;

      const renderNotice = (kind, status) => {
        const isUnavailable = kind === 'service_unavailable';
        const html = `
          <div class="bay-inset p-2.5 rounded-xl text-center text-[10px] font-mono ${isUnavailable ? 'text-amber-300 border border-amber-500/30' : 'text-slate-400 border border-white/10'}" role="status" data-gw-degradation="${kind}">
            <i data-lucide="${isUnavailable ? 'cloud-off' : 'plug-zap'}" class="w-4 h-4 mx-auto mb-1 opacity-70" aria-hidden="true"></i>
            <span class="block">${isUnavailable ? '后端服务暂时不可用，请稍后重试' : '该功能尚未接入后端（未纳入当前切片）'}</span>
            <span class="block mt-0.5 text-[9px] text-slate-500">/api/asset-auth/teams · /api/asset-auth/users（HTTP ${status}）</span>
          </div>`;
        containers.forEach(c => c.innerHTML = html);
        if (window.lucide) window.lucide.createIcons();
      };

      try {
        const [teamsRes, usersRes] = await Promise.all([
          fetch('/api/asset-auth/teams', { credentials: 'same-origin', cache: 'no-store' }),
          fetch('/api/asset-auth/users', { credentials: 'same-origin', cache: 'no-store' })
        ]);

        const teamsData = teamsRes.ok ? await teamsRes.json().catch(() => ({})) : await teamsRes.json().catch(() => ({}));
        const usersData = usersRes.ok ? await usersRes.json().catch(() => ({})) : await usersRes.json().catch(() => ({}));

        // 统一判定：路由不存在（404/501 无标准错误包）→ 未接入；503 → 暂不可用。
        const kindOf = (res, data) => (degradation
          ? degradation.statusKind(res.status, data && data.detail)
          : (res.ok ? 'ok' : 'not_integrated'));
        const teamsKind = kindOf(teamsRes, teamsData);
        const usersKind = kindOf(usersRes, usersData);
        const degradedKind = [teamsKind, usersKind].includes('service_unavailable')
          ? 'service_unavailable'
          : ([teamsKind, usersKind].every(k => k === 'not_integrated') ? 'not_integrated' : null);
        if (degradedKind) {
          renderNotice(degradedKind, Math.max(teamsRes.status, usersRes.status));
          return;
        }

        const teams = Array.isArray(teamsData.teams) ? teamsData.teams : [];
        const users = Array.isArray(usersData.users) ? usersData.users : [];

        if (teams.length === 0 && users.length === 0) {
          // 接口已就绪但确实没有数据：真实空态，不伪造管理员席位。
          const emptyHtml = `
            <div class="bay-inset p-2.5 rounded-xl text-center text-[10px] font-mono text-slate-400">
              <i data-lucide="users" class="w-4 h-4 mx-auto mb-1 text-slate-500" aria-hidden="true"></i>
              <span class="block">暂无团队与席位数据</span>
              <span class="block mt-0.5 text-[9px] text-slate-500">接口已就绪；可前往 <a href="/static/pages/assets.html" class="text-[#dfc384] underline">资产管理页</a> 创建团队与成员。</span>
            </div>`;
          containers.forEach(c => c.innerHTML = emptyHtml);
          if (window.lucide) window.lucide.createIcons();
          return;
        }

        let html = '';
        // 只有后端确实给出运行态字段时才能声称在线/活跃；
        // 字段缺失时展示「未接入」，不得默认当作在线（用户裁决 #3）。
        const statusChip = (state) => (state === true || state === 'active' || state === 'online')
          ? { text: 'ACTIVE', cls: 'text-cyan-300', degrade: false }
          : { text: '未接入', cls: 'text-slate-500', degrade: true };

        if (teams.length > 0) {
          html += `<div class="text-[9.5px] font-mono text-[#dfc384] font-bold mb-1">团队空间 (${teams.length})</div>`;
          teams.forEach(team => {
            const chip = statusChip(team.status);
            html += `
              <div class="bay-inset p-2 rounded-xl flex items-center justify-between mb-1.5 border border-white/5">
                <div>
                  <div class="font-bold text-slate-200 text-xs">${team.name}</div>
                  <div class="text-[8.5px] font-mono text-slate-400">成员数: ${team.member_count || (team.members || []).length || 0}</div>
                </div>
                <span class="text-[8.5px] font-mono ${chip.cls}"${chip.degrade ? ' data-gw-degradation="not_integrated"' : ''}>${chip.text}</span>
              </div>
            `;
          });
        }
        if (users.length > 0) {
          html += `<div class="text-[9.5px] font-mono text-[#dfc384] font-bold mt-2 mb-1">系统席位与成员 (${users.length})</div>`;
          users.forEach(u => {
            const chip = statusChip(u.online);
            const initial = (u.display_name || u.username || 'U').substring(0, 2).toUpperCase();
            html += `
              <div class="bay-inset p-2 rounded-xl flex items-center justify-between mb-1.5 border border-white/5">
                <div class="flex items-center space-x-2">
                  <div class="w-6 h-6 rounded-full bg-slate-800 border border-white/10 text-[#eddab3] font-bold flex items-center justify-center text-[9px]">${initial}</div>
                  <div>
                    <div class="font-bold text-slate-200 text-[11px]">${u.display_name || u.username}</div>
                    <div class="text-[8px] font-mono text-slate-500">${u.username} · ${u.role}</div>
                  </div>
                </div>
                <span class="text-[8.5px] font-mono ${chip.cls}"${chip.degrade ? ' data-gw-degradation="not_integrated"' : ''}>${chip.text}</span>
              </div>
            `;
          });
        }

        containers.forEach(c => c.innerHTML = html);
        if (window.lucide) window.lucide.createIcons();
      } catch (e) {
        // 网络层失败：显式说明未接入，不得声称「单机席位已就绪」。
        renderNotice('not_integrated', 0);
      }
    },

    // 7. Real Settings Route Navigation (/static/pages/settings.html)
    openSettingsModal: function() {
      window.location.href = '/static/pages/settings.html';
    },

    renderSettingsModalContent: function() {
      const modal = document.getElementById('settingsModal');
      if (!modal) return;

      modal.innerHTML = `
        <div class="hw-modal-dialog w-[500px] p-4 text-slate-200">
          <div class="flex items-center justify-between pb-2 mb-3 border-b border-white/10">
            <div class="flex items-center space-x-2">
              <i data-lucide="settings" class="w-4 h-4 text-[#dfc384]"></i>
              <h3 class="text-sm font-bold text-slate-100">系统与工程参数控制中心</h3>
            </div>
            <button class="text-slate-400 hover:text-white" onclick="HardwareDeck.closeModal('settingsModal')">
              <i data-lucide="x" class="w-4 h-4"></i>
            </button>
          </div>

          <div class="space-y-3 text-xs">
            <!-- 原版系统三大真实设置入口 -->
            <div class="text-[10px] font-mono text-[#dfc384] font-bold">核心配置入口</div>
            <div class="grid grid-cols-3 gap-2">
              <a href="/static/pages/settings.html?section=api-settings" class="bay-inset p-2.5 rounded-xl flex flex-col justify-between group hover:border-[#dfc384]/50 transition border border-white/5">
                <div class="flex items-center space-x-1.5 mb-1 text-slate-100 group-hover:text-[#dfc384] transition">
                  <i data-lucide="key" class="w-3.5 h-3.5 text-[#dfc384]"></i>
                  <span class="font-bold text-[11px]">API 设置</span>
                </div>
                <div class="text-[8px] font-mono text-slate-400 leading-tight">大模型 Key 与 CLI 管理</div>
                <div class="mt-2 flex items-center justify-between text-[8px] font-mono text-slate-500">
                  <span>/api-settings</span>
                  <i data-lucide="external-link" class="w-2.5 h-2.5"></i>
                </div>
              </a>

              <a href="/static/pages/settings.html" class="bay-inset p-2.5 rounded-xl flex flex-col justify-between group hover:border-amber-400/50 transition border border-white/5">
                <div class="flex items-center space-x-1.5 mb-1 text-slate-100 group-hover:text-amber-300 transition">
                  <i data-lucide="sliders" class="w-3.5 h-3.5 text-amber-400"></i>
                  <span class="font-bold text-[11px]">通用偏好</span>
                </div>
                <div class="text-[8px] font-mono text-slate-400 leading-tight">启动页、语言、主题与提示词快照</div>
                <div class="mt-2 flex items-center justify-between text-[8px] font-mono text-slate-500">
                  <span>/settings</span>
                  <i data-lucide="external-link" class="w-2.5 h-2.5"></i>
                </div>
              </a>
            </div>


            <div class="pt-2 border-t border-white/10 flex items-center justify-between">
              <span class="text-[9px] font-mono text-slate-500">系统模式: 曜石香槟钛金拟物硬件总线 (v2.0)</span>
              <button type="button" class="px-3 py-1.5 rounded-lg text-xs font-mono text-slate-400 hover:text-white" onclick="HardwareDeck.closeModal('settingsModal')">完成</button>
            </div>
          </div>
        </div>
      `;

      if (window.lucide) window.lucide.createIcons();
    },

    saveSettingsConfig: function() {
      this.closeModal('settingsModal');
    },

    // 6. Global Init
    init: function() {
      this.bindAccountEntry();
      this.initClock();
      this.initVUMeters();
      this.syncOnlineStatus();
      this.syncStageProgress();
      // 若刚从 IdP 回调失败返回（?auth_error=<code>），先明示原因并清理查询参数。
      this.consumeAuthError();
      this.syncAuth();

      // Backdrop click closes modal
      document.querySelectorAll('.hw-modal-backdrop').forEach(bd => {
        bd.addEventListener('click', (e) => {
          if (e.target === bd) {
            bd.classList.remove('open');
            document.body.style.overflow = '';
          }
        });
      });

      // Backdrop click closes drawer
      document.querySelectorAll('.hw-drawer-backdrop').forEach(bd => {
        bd.addEventListener('click', () => {
          bd.classList.remove('open');
          const panel = bd.nextElementSibling;
          if (panel && panel.classList.contains('hw-drawer-panel')) {
            panel.classList.remove('open');
          }
        });
      });

      // ESC key to close active modal or drawer
      window.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') {
          if (document.querySelector('dialog[open]')) return;
          document.querySelectorAll('.hw-modal-backdrop.open').forEach(m => m.classList.remove('open'));
          document.querySelectorAll('.hw-drawer-backdrop.open').forEach(d => d.classList.remove('open'));
          document.querySelectorAll('.hw-drawer-panel.open').forEach(p => p.classList.remove('open'));
          document.body.style.overflow = '';
        }
      });
    }
  };

  window.HardwareDeck = HardwareDeck;

  // Auto initialize on DOMContentLoaded
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => HardwareDeck.init());
  } else {
    HardwareDeck.init();
  }
})(window);