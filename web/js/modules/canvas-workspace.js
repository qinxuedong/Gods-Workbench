/**
 * Copyright 2026 Gods-Workbench Authors
 * Licensed under the Apache License, Version 2.0
 * 
 * canvas-workspace.js — 画布工作台拓扑视图交互引擎
 * 严格与工作流（workflow-controller.js）对齐：
 * 1. 颜色体系（PORT_COLORS）一致
 * 2. 贝塞尔连线与三次曲线算法一致
 * 3. 6 级同色同心锁相纺锤流动动画（wireCurrentWave）一致
 * 4. 节点拖拽、高亮、端口微光一致
 */

(function(global) {
  'use strict';

  // 1. 语义数据类型颜色表（完全同源）
  const PORT_COLORS = {
    MODEL: "#dfc384",
    CLIP: "#38bdf8",
    CONDITIONING: "#38bdf8",
    IMAGE: "#10b981",
    VIDEO: "#dfc384",
    AUDIO: "#38bdf8",
    LATENT: "#c084fc",
    VAE: "#f43f5e",
    ANY: "#dfc384",
    DEFAULT: "#94a3b8"
  };

  // 2. 6 级同色同心锁相纺锤梯度配置（完全同源）
  const SPINDLE_STEPS = [
    { len: 44, width: 0.45, opacity: 0.16 },
    { len: 35, width: 0.75, opacity: 0.20 },
    { len: 26, width: 1.05, opacity: 0.24 },
    { len: 18, width: 1.35, opacity: 0.28 },
    { len: 11, width: 1.65, opacity: 0.32 },
    { len: 5, width: 1.95, opacity: 0.40 },
  ];

  class CanvasWorkspaceEngine {
    constructor() {
      this.currentCanvasId = null;
      this.currentCanvasMeta = null;
      this.nodes = [];
      this.connections = [];
      this.selectedNodeId = null;
      this.zoom = 1;
      this.panX = 0;
      this.panY = 0;
      this.isDraggingNode = false;
      this.activeDragNode = null;
      this.dragOffset = { x: 0, y: 0 };

      this.stageEl = null;
      this.nodesLayerEl = null;
      this.wiresSvgEl = null;
    }

    init() {
      this.stageEl = document.getElementById('canvasTopologyStage');
      this.nodesLayerEl = document.getElementById('canvasTopologyNodesLayer');
      this.wiresSvgEl = document.getElementById('canvasTopologyWiresSvg');

      if (!this.stageEl || !this.nodesLayerEl || !this.wiresSvgEl) return;

      // 监听背景点击以取消选择
      this.stageEl.addEventListener('click', (e) => {
        if (e.target === this.stageEl || e.target === this.wiresSvgEl) {
          this.selectNode(null);
        }
      });

      // 绑定全局鼠标拖拽监听
      window.addEventListener('mousemove', (e) => this.onMouseMove(e));
      window.addEventListener('mouseup', () => this.onMouseUp());

      // 导出按钮事件
      const exportBtn = document.getElementById('btnExportCanvasGodmap');
      if (exportBtn) {
        exportBtn.onclick = () => this.exportCurrentCanvas();
      }

      // 监听宿主派发的渲染事件
      window.addEventListener('gw:render-canvas-workspace', (e) => {
        if (e.detail?.id) {
          this.loadAndRenderCanvas(e.detail.id, e.detail);
        }
      });
    }

    async loadAndRenderCanvas(canvasId, meta = {}) {
      this.currentCanvasId = canvasId;
      this.currentCanvasMeta = meta;
      this.selectedNodeId = null;

      try {
        // 请求后端获取真实拓扑数据
        const res = await fetch(`/api/canvases/${encodeURIComponent(canvasId)}`);
        if (res.ok) {
          const data = await res.json();
          this.nodes = Array.isArray(data.nodes) ? data.nodes : [];
          this.connections = Array.isArray(data.connections) ? data.connections : [];
          this.currentCanvasMeta.version = data.version || meta.version || 1;
        } else {
          // 若为新画布或离线降级
          this.nodes = [];
          this.connections = [];
        }
      } catch (err) {
        console.warn('获取画布拓扑降级:', err);
        this.nodes = [];
        this.connections = [];
      }

      // 如果节点为空，提供演示种子节点以供编辑
      if (this.nodes.length === 0) {
        this.nodes = [
          {
            entity_id: 'nd-001',
            kind: 'prompt_node',
            title: '提示词编码器 (CLIP)',
            position: { x: 80, y: 100 },
            in_ports: [{ name: 'text', data_type: 'CLIP' }],
            out_ports: [{ name: 'conditioning', data_type: 'CONDITIONING' }]
          },
          {
            entity_id: 'nd-002',
            kind: 'sampler_node',
            title: 'K-采样器 (Sampler)',
            position: { x: 380, y: 120 },
            in_ports: [
              { name: 'model', data_type: 'MODEL' },
              { name: 'positive', data_type: 'CONDITIONING' },
              { name: 'latent', data_type: 'LATENT' }
            ],
            out_ports: [{ name: 'latent_out', data_type: 'LATENT' }]
          },
          {
            entity_id: 'nd-003',
            kind: 'vae_node',
            title: 'VAE 解码输出',
            position: { x: 680, y: 150 },
            in_ports: [
              { name: 'samples', data_type: 'LATENT' },
              { name: 'vae', data_type: 'VAE' }
            ],
            out_ports: [{ name: 'image', data_type: 'IMAGE' }]
          }
        ];
        this.connections = [
          {
            from_node: 'nd-001',
            from_port: 0,
            to_node: 'nd-002',
            to_port: 1,
            data_type: 'CONDITIONING'
          },
          {
            from_node: 'nd-002',
            from_port: 0,
            to_node: 'nd-003',
            to_port: 0,
            data_type: 'LATENT'
          }
        ];
      }

      this.render();
    }

    render() {
      if (!this.nodesLayerEl || !this.wiresSvgEl) return;
      this.renderNodes();
      this.renderWires();
    }

    renderNodes() {
      this.nodesLayerEl.innerHTML = '';

      this.nodes.forEach((node) => {
        const nodeEl = document.createElement('div');
        nodeEl.className = `canvas-workspace-node ${this.selectedNodeId === node.entity_id ? 'selected' : ''}`;
        nodeEl.dataset.nodeId = node.entity_id;
        nodeEl.style.left = `${node.position?.x || 50}px`;
        nodeEl.style.top = `${node.position?.y || 50}px`;

        const inPorts = Array.isArray(node.in_ports) ? node.in_ports : [{ name: 'in', data_type: 'DEFAULT' }];
        const outPorts = Array.isArray(node.out_ports) ? node.out_ports : [{ name: 'out', data_type: 'DEFAULT' }];

        const inPortsHtml = inPorts.map((p, idx) => {
          const color = PORT_COLORS[p.data_type] || PORT_COLORS.DEFAULT;
          return `
            <div class="port-item" data-port-index="${idx}" data-port-type="in">
              <span class="port-dot" style="background:${color};" title="${p.data_type}"></span>
              <span>${p.name || '输入'}</span>
            </div>
          `;
        }).join('');

        const outPortsHtml = outPorts.map((p, idx) => {
          const color = PORT_COLORS[p.data_type] || PORT_COLORS.DEFAULT;
          return `
            <div class="port-item" data-port-index="${idx}" data-port-type="out">
              <span>${p.name || '输出'}</span>
              <span class="port-dot" style="background:${color};" title="${p.data_type}"></span>
            </div>
          `;
        }).join('');

        nodeEl.innerHTML = `
          <div class="node-header">
            <span class="node-title-text" title="${node.title || node.entity_id}">${node.title || node.entity_id}</span>
            <span class="node-id-pill">${node.entity_id}</span>
          </div>
          <div class="ports-container">
            <div class="ports-col ports-col-in">${inPortsHtml}</div>
            <div class="ports-col ports-col-out">${outPortsHtml}</div>
          </div>
        `;

        // 鼠标按下开始拖拽与选中
        nodeEl.addEventListener('mousedown', (e) => {
          e.stopPropagation();
          this.selectNode(node.entity_id);
          this.startDragNode(node, e);
        });

        this.nodesLayerEl.appendChild(nodeEl);
      });
    }

    renderWires() {
      if (!this.wiresSvgEl) return;
      const pathsHtml = [];
      const selId = this.selectedNodeId;

      this.connections.forEach((conn) => {
        const fromNode = this.nodes.find(n => n.entity_id === conn.from_node);
        const toNode = this.nodes.find(n => n.entity_id === conn.to_node);
        if (!fromNode || !toNode) return;

        // 计算端口圆心坐标
        const fromNodeEl = this.nodesLayerEl.querySelector(`[data-node-id="${CSS.escape(conn.from_node)}"]`);
        const toNodeEl = this.nodesLayerEl.querySelector(`[data-node-id="${CSS.escape(conn.to_node)}"]`);

        let x1 = (fromNode.position?.x || 50) + (fromNodeEl?.offsetWidth || 195);
        let y1 = (fromNode.position?.y || 50) + 38 + (Number(conn.from_port) || 0) * 22;
        let x2 = (toNode.position?.x || 50);
        let y2 = (toNode.position?.y || 50) + 38 + (Number(conn.to_port) || 0) * 22;

        const dx = Math.max(55, Math.abs(x2 - x1) * 0.45);
        const d = `M ${x1} ${y1} C ${x1 + dx} ${y1}, ${x2 - dx} ${y2}, ${x2} ${y2}`;

        const dType = conn.data_type || "DEFAULT";
        const dotColor = PORT_COLORS[dType] || PORT_COLORS.DEFAULT;

        const isConnectedToSelected = selId !== null && (conn.from_node === selId || conn.to_node === selId);
        const baseOpacity = selId ? (isConnectedToSelected ? "0.95" : "0.22") : "0.75";
        const strokeWidth = isConnectedToSelected ? 2.2 : 1.5;

        // 6 级同色同心锁相纺锤流动动画
        let flowSpindleHtml = "";
        if (isConnectedToSelected) {
          const maxLen = 44;
          flowSpindleHtml = SPINDLE_STEPS.map((st) => {
            const shift = (maxLen - st.len) / 2;
            return `<path
              class="canvas-topology-wire-flow"
              d="${d}"
              pathLength="100"
              stroke="${dotColor}"
              stroke-width="${st.width + 0.5}"
              stroke-opacity="${st.opacity * 1.5}"
              stroke-dasharray="${st.len} 200"
              style="--taper-shift: ${shift}px;"
            />`;
          }).join("");
        }

        pathsHtml.push(`
          <g class="wire-group" data-wire-from="${conn.from_node}" data-wire-to="${conn.to_node}">
            <path class="canvas-topology-wire-base" d="${d}" stroke="${dotColor}" stroke-width="${strokeWidth}" stroke-opacity="${baseOpacity}" />
            ${flowSpindleHtml}
            <circle cx="${x1}" cy="${y1}" r="2.5" fill="${dotColor}" opacity="${baseOpacity}" />
            <circle cx="${x2}" cy="${y2}" r="2.5" fill="${dotColor}" opacity="${baseOpacity}" />
          </g>
        `);
      });

      this.wiresSvgEl.innerHTML = pathsHtml.join('');
    }

    selectNode(nodeId) {
      this.selectedNodeId = nodeId;
      const allNodes = this.nodesLayerEl.querySelectorAll('.canvas-workspace-node');
      allNodes.forEach((el) => {
        el.classList.toggle('selected', el.dataset.nodeId === nodeId);
      });
      // 选中变化时重新渲染连线与动效
      this.renderWires();
    }

    startDragNode(node, e) {
      this.isDraggingNode = true;
      this.activeDragNode = node;
      const stageRect = this.stageEl.getBoundingClientRect();
      this.dragOffset = {
        x: (e.clientX - stageRect.left) - (node.position?.x || 0),
        y: (e.clientY - stageRect.top) - (node.position?.y || 0)
      };
    }

    onMouseMove(e) {
      if (!this.isDraggingNode || !this.activeDragNode) return;
      const stageRect = this.stageEl.getBoundingClientRect();
      const newX = Math.max(10, Math.round(e.clientX - stageRect.left - this.dragOffset.x));
      const newY = Math.max(10, Math.round(e.clientY - stageRect.top - this.dragOffset.y));

      this.activeDragNode.position = { x: newX, y: newY };

      const nodeEl = this.nodesLayerEl.querySelector(`[data-node-id="${CSS.escape(this.activeDragNode.entity_id)}"]`);
      if (nodeEl) {
        nodeEl.style.left = `${newX}px`;
        nodeEl.style.top = `${newY}px`;
      }

      this.renderWires();
    }

    onMouseUp() {
      if (this.isDraggingNode) {
        this.isDraggingNode = false;
        this.activeDragNode = null;
        // 拖拽停止后可异步更新拓扑
      }
    }

    async exportCurrentCanvas() {
      if (!this.currentCanvasId) return;
      try {
        const payload = {
          canvas_id: this.currentCanvasId,
          version: this.currentCanvasMeta?.version || 1,
          nodes: this.nodes,
          connections: this.connections,
          exported_at: new Date().toISOString()
        };
        const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `${this.currentCanvasId}-topology.godmap`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
      } catch (err) {
        console.error('导出失败:', err);
      }
    }
  }

  // 挂载全局单例
  global.CanvasWorkspaceEngine = new CanvasWorkspaceEngine();

  // DOM 就绪时初始化
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => global.CanvasWorkspaceEngine.init());
  } else {
    global.CanvasWorkspaceEngine.init();
  }
})(window);
