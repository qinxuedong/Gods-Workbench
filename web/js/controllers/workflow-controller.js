// -*- coding: utf-8 -*-
/**
 * Gods-Workbench · RunningHub / ComfyUI 工作流全息解析与双端执行前端控制器
 *
 * 核心职责：
 * 1. 左侧『工作流目录』：多文件夹分类管理（支持新增、改名、删除与拖拽归类），右侧显示来源徽标（RH / LOCAL / LIB），最底部固定『凭证与环境设置』；
 * 2. 中栏『参数执行矩阵』上下双行架构：
 *    - 上行『用户选中的节点』：展示当前选中节点的全部输入属性，未提升属性左侧配备缩小版『⬆ 提升』按钮（已提升属性不再显示已提升按钮）；
 *    - 下行『工作流提取列表』：展示当前工作流所有被提取出的属性，支持直接修改替换内容并与节点及画布实时双向同步；
 * 3. 右侧全息画布：
 *    - 上传类节点（LoadImage / LoadVideo 等）配备『📤 上传 / 🗂️ 选择』双按钮，完成后内嵌显示图像或视频预览与详情；
 *    - 连线粗细减半（1px），选中节点后触发双向循环流光动画（从上一节点流入、从当前节点流出，取消选中后停止）；
 *    - 顶部工具栏『🖥️ 打开 ComfyUI』支持自动检测/后台静默启动本地 ComfyUI 并直接加载打开当前激活工作流；
 *    - 全面支持本地任意格式工作流（非 API 画布 JSON、子图 Subgraphs JSON、API JSON、PNG/WebP）解析加载。
 */

let workbenchRoot = null;
  let lifecycleAbortController = null;
  let lifecycleListeners = [];

  function listen(target, type, handler, options) {
    target?.addEventListener(type, handler, options);
    const remove = () => {
      target?.removeEventListener(type, handler, options);
      const index = lifecycleListeners.indexOf(remove);
      if (index >= 0) lifecycleListeners.splice(index, 1);
    };
    lifecycleListeners.push(remove);
    return remove;
  }
function wfGet(id) { return workbenchRoot?.querySelector(`#${CSS.escape(id)}`) || null; }
function wfQuery(selector) { return workbenchRoot?.querySelector(selector) || null; }
function wfQueryAll(selector) { return workbenchRoot ? workbenchRoot.querySelectorAll(selector) : []; }

const CLIPBOARD_STORAGE_KEY = "gods_workbench_clipboard_history_v1";
const COL_COLLAPSE_STORAGE_KEY = "gods_workbench_col_collapse_v1";
const WORKFLOW_API = "/api/god_workflow";
// Target lifecycle routes: /api/god_workflow/documents, /folders and /canvas/*.

function loadColumnCollapsePrefs() {
  try {
    const raw = localStorage.getItem(COL_COLLAPSE_STORAGE_KEY);
    const parsed = raw ? JSON.parse(raw) : {};
    return {
      left: Boolean(parsed?.left),
      center: Boolean(parsed?.center),
    };
  } catch (_) {
    return { left: false, center: false };
  }
}

function saveColumnCollapsePrefs(left, center) {
  try {
    localStorage.setItem(
      COL_COLLAPSE_STORAGE_KEY,
      JSON.stringify({ left: Boolean(left), center: Boolean(center) })
    );
  } catch (_) {}
}

const initialColPrefs = loadColumnCollapsePrefs();

const state = {
  workflows: [],
  openWorkflowIds: [],
  _openTabsInitialized: false,
  folders: [],
  collapsedFolders: new Set(),
  renamingFolderId: null,
  renamingWorkflowId: null,
  contextMenuWfId: null,
  activeFolderId: "parsed_default",
  currentWorkflow: null,
  selectedNodeId: null,
  executionTarget: "rh_cloud",
  zoom: 1.0,
  panX: 20,
  panY: 20,
  activeTaskId: null,
  pollTimer: null,
  paramSaveTimer: null,
  parameterSaves: new Map(),
  submitting: false,
  submissionAttempt: null,
  pollGeneration: 0,
  clipboardHistory: loadLocalClipboardHistory(),
  mediaAssets: [],
  mediaAssetsByFilename: {},
  assetModalFilter: "all",
  assetFilterType: "all",
  pendingMediaTarget: null,
  leftColCollapsed: initialColPrefs.left,
  centerColCollapsed: initialColPrefs.center,
  comfyWindowRef: null,
  syncRevisions: {},
  comfySyncTimer: null,
};

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
};

function loadLocalClipboardHistory() {
  // 身份确认前不读取浏览器共享历史；持久历史只从主体化服务端接口召回。
  return [];
}

function recordClipboardHistory(text) {
  const clean = String(text || "").trim();
  if (!clean) return;
  state.clipboardHistory = [clean, ...state.clipboardHistory.filter((item) => item !== clean)].slice(0, 15);
}

function isLikelyRhLink(text) {
  const s = String(text || "").trim();
  if (!s) return false;
  if (/runninghub\.(cn|ai)/i.test(s)) return true;
  if (/liblib\.(art|tv|ai)/i.test(s)) return true;
  if (/opencomfy=workflowdata-/i.test(s)) return true;
  if (/^\d{15,22}$/.test(s)) return true;
  if (/(workflow|post)\/(\d{15,22})/i.test(s)) return true;
  if (s.startsWith("{") && (s.includes('"nodes"') || s.includes('"class_type"') || s.includes('"definitions"'))) {
    return true;
  }
  return false;
}

function formatByteSize(bytes) {
  const b = Number(bytes) || 0;
  if (b <= 0) return "";
  if (b < 1024) return `${b} B`;
  if (b < 1024 * 1024) return `${(b / 1024).toFixed(1)} KB`;
  return `${(b / (1024 * 1024)).toFixed(2)} MB`;
}

function isMediaUploadNode(node) {
  if (!node) return null;
  const ct = String(node.class_type || "").toLowerCase();
  if (ct.includes("loadvideo") || ct.includes("vhs_loadvideo")) {
    return "video";
  }
  if (
    ct.includes("loadimage") ||
    ct.includes("vhs_loadimage") ||
    ct.includes("loadmask") ||
    ct.includes("loadmedia")
  ) {
    return "image";
  }
  const widgets = node.widgets || [];
  if (node.category === "input") {
    for (const w of widgets) {
      const vt = String(w.value_type || w.field_type || "").toLowerCase();
      const wn = String(w.name || "").toLowerCase();
      if (vt === "video" || wn === "video") return "video";
      if (vt === "image" || vt === "media" || ["image", "file", "media"].includes(wn)) return "image";
    }
  }
  return null;
}

function getMediaModeForWidget(node, fieldName, fieldType, value) {
  const fname = String(fieldName || "").toLowerCase();
  const ftype = String(fieldType || "").toLowerCase();
  const valStr = String(value ?? "").toLowerCase();
  const nodeMode = isMediaUploadNode(node);
  if (fname === "video" || ftype === "video" || /\.(mp4|mov|webm|mkv|avi)$/i.test(valStr)) {
    return "video";
  }
  if (
    ftype === "media" ||
    ftype === "image" ||
    fname === "image" ||
    (nodeMode && ["image", "video", "file", "media"].includes(fname))
  ) {
    return nodeMode === "video" ? "video" : "image";
  }
  return null;
}

function normalizePortList(ports) {
  if (Array.isArray(ports)) {
    return ports.map((p) => {
      if (p && typeof p === "object") {
        const name = String(p.name || p.label || "");
        let dType = String(p.data_type || p.type || "DEFAULT");
        if (dType === "DEFAULT") {
          if (/model/i.test(name)) dType = "MODEL";
          else if (/clip|positive|negative|prompt/i.test(name)) dType = "CLIP";
          else if (/image|images|filename/i.test(name)) dType = "IMAGE";
          else if (/latent|samples/i.test(name)) dType = "LATENT";
          else if (/vae/i.test(name)) dType = "VAE";
        }
        return { name, data_type: dType };
      }
      return { name: String(p ?? ""), data_type: "DEFAULT" };
    });
  }
  if (ports && typeof ports === "object" && ports !== null) {
    return Object.entries(ports).map(([name, val]) => {
      let dType = "DEFAULT";
      if (/model/i.test(name)) dType = "MODEL";
      else if (/clip|positive|negative|prompt/i.test(name)) dType = "CLIP";
      else if (/image|images|filename/i.test(name)) dType = "IMAGE";
      else if (/latent|samples/i.test(name)) dType = "LATENT";
      else if (/vae/i.test(name)) dType = "VAE";
      else if (Array.isArray(val)) dType = "LINK";
      else if (typeof val === "number") dType = "INT";
      else dType = "STRING";
      return { name, data_type: dType };
    });
  }
  return [];
}

function getMediaWidgetOfNode(node) {
  const widgets = node?.widgets || [];
  if (widgets.length === 0) return null;
  return (
    widgets.find((w) => {
      const vt = String(w.value_type || w.field_type || "").toLowerCase();
      const wn = String(w.name || "").toLowerCase();
      return vt === "media" || vt === "image" || vt === "video" || ["image", "video", "file", "media", "audio"].includes(wn);
    }) || widgets[0]
  );
}

function showToast(message, isError = false, actionLabel = "", onAction = null) {
  if (typeof window.showWorkbenchToast === "function") {
    window.showWorkbenchToast(message, isError, actionLabel, onAction, 5000);
    return;
  }
  const banner = wfGet("toastBanner");
  if (!banner) return;
  banner.style.borderColor = isError ? "#f43f5e" : "#dfc384";
  const btnHtml = actionLabel
    ? `<button type="button" class="toast-action-btn" id="toastActionBtn">${escapeHtml(actionLabel)}</button>`
    : "";
  banner.innerHTML = `<span>${isError ? "⚠️" : "✨"}</span><span>${escapeHtml(message)}</span>${btnHtml}`;
  banner.classList.add("show");
  if (actionLabel && typeof onAction === "function") {
    wfGet("toastActionBtn")?.addEventListener("click", () => {
      banner.classList.remove("show");
      onAction();
    });
  }
  clearTimeout(banner._timer);
  banner._timer = setTimeout(() => banner.classList.remove("show"), 5000);
}

function escapeHtml(str) {
  return String(str ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function targetApiUrl(url, options = {}) {
  const map = [
    [/^\/api\/rh-workflow\/settings$/, '/api/god_workflow/settings'],
    [/^\/api\/rh-workflow\/assets\/list$/, '/api/god_workflow/assets/list'],
    [/^\/api\/rh-workflow\/assets\/upload$/, '/api/god_workflow/assets/upload'],
    [/^\/api\/rh-workflow\/list$/, '/api/god_workflow/list'],
    [/^\/api\/rh-workflow\/item\/([^/]+)\/name$/, '/api/god_workflow/item/$1/name'],
    [/^\/api\/rh-workflow\/item\/([^/]+)\/folder$/, '/api/god_workflow/item/$1/folder'],
    [/^\/api\/rh-workflow\/item\/([^/]+)\/params$/, '/api/god_workflow/item/$1/params'],
    [/^\/api\/rh-workflow\/item\/([^/]+)$/, '/api/god_workflow/item/$1'],
    [/^\/api\/rh-workflow\/parse-upload$/, '/api/god_workflow/parse-upload'],
    [/^\/api\/rh-workflow\/parse-link$/, '/api/god_workflow/parse-link'],
    [/^\/api\/rh-workflow\/execute$/, '/api/god_workflow/execute'],
    [/^\/api\/rh-workflow\/tasks\/(.+)$/, '/api/god_workflow/tasks/$1'],
    [/^\/api\/rh-workflow\/canvas-contract\/(.+)$/, '/api/god_workflow/canvas-contract/$1'],
    [/^\/api\/rh-workflow\/export\/([^?]+)/, '/api/god_workflow/export/$1'],
    [/^\/api\/rh-workflow\/auto-layout\/(.+)$/, '/api/god_workflow/auto-layout/$1'],
    [/^\/api\/rh-workflow\/compare-local\/(.+)$/, '/api/god_workflow/compare-local/$1'],
  ];
  for (const [pattern, replacement] of map) if (pattern.test(url)) return url.replace(pattern, replacement);
  return url;
}

/**
 * 后端文档位置以 {x, y} 表示，前端画布以 node.x / node.y 表示；
 * 统一在入站时投影出扁平的 x / y，出站时再合并回 position。
 */
function collapseNodePosition(node, index, total) {
  const position = node.position || {};
  const hasX = Number.isFinite(Number(position.x ?? node.x));
  const hasY = Number.isFinite(Number(position.y ?? node.y));
  return {
    x: hasX ? Number(position.x ?? node.x) : 40 + (index % 4) * 280,
    y: hasY ? Number(position.y ?? node.y) : 40 + Math.floor(index / 4) * 190,
    width: Number(position.width ?? node.width ?? 200),
    height: Number(position.height ?? node.height ?? 120),
  };
}

function expandNodePosition(node) {
  return { ...node, position: { ...(node.position || {}), x: Number(node.x) || 0, y: Number(node.y) || 0 } };
}

function normalizeMatureWorkflow(raw) {
  const value = raw?.workflow || raw?.document || raw;
  if (!value || !Array.isArray(value.nodes)) return value;

  const rawLinks = value.links || value.connections || [];
  const normalizedLinks = rawLinks.map((link, i) => {
    const fromNode = String(link.from_node ?? link.source ?? "");
    const toNode = String(link.to_node ?? link.target ?? "");
    const rawFromSlot = link.from_slot ?? link.source_slot ?? 0;
    const rawToSlot = link.to_slot ?? link.target_slot ?? 0;
    const parsedFrom = typeof rawFromSlot === "number" ? rawFromSlot : parseInt(rawFromSlot, 10);
    const parsedTo = typeof rawToSlot === "number" ? rawToSlot : parseInt(rawToSlot, 10);
    return {
      ...link,
      id: link.id || `link_${i}`,
      from_node: fromNode,
      to_node: toNode,
      source: fromNode,
      target: toNode,
      source_slot: String(rawFromSlot),
      target_slot: String(rawToSlot),
      from_slot: !isNaN(parsedFrom) ? parsedFrom : 0,
      to_slot: !isNaN(parsedTo) ? parsedTo : 0,
    };
  });

  const nodes = value.nodes.map((node, index) => {
    const geometry = collapseNodePosition(node, index, value.nodes.length);
    const inputs = node.inputs && typeof node.inputs === "object" ? node.inputs : {};
    const widgets = Array.isArray(node.widgets)
      ? node.widgets.map((widget, widgetIndex) => widget && typeof widget === 'object' && !Array.isArray(widget) ? widget : ({
          name: `widget_${widgetIndex}`, label: `原生参数 ${widgetIndex + 1}`, value: widget,
          promoted: Boolean(node.metadata?.widget_promotions?.[`widget_${widgetIndex}`]),
          value_type: typeof widget === 'number' ? (Number.isInteger(widget) ? 'int' : 'float') : typeof widget === 'boolean' ? 'bool' : 'string',
        }))
      : Object.entries(inputs)
          // 过滤掉连线引用数组（如 ["1", 0]），避免将连线数据误显示为卡片 widget
          .filter(([_, v]) => !(Array.isArray(v) && v.length >= 2))
          .map(([name, v]) => ({
            name,
            value: v,
            value_type:
              typeof v === "number"
                ? Number.isInteger(v)
                  ? "int"
                  : "float"
                : typeof v === "boolean"
                ? "bool"
                : "string",
          }));

    const nodeId = String(node.node_id ?? node.id ?? index + 1);

    // 若 outputs 为空，从流出连线中推导该节点的输出端口
    let outputs = Array.isArray(node.raw_payload?.outputs) ? node.raw_payload.outputs : node.outputs;
    if (
      !outputs ||
      (Array.isArray(outputs) && outputs.length === 0) ||
      (typeof outputs === "object" && Object.keys(outputs).length === 0)
    ) {
      const outgoing = normalizedLinks.filter((lk) => lk.from_node === nodeId);
      if (outgoing.length > 0) {
        const derived = {};
        outgoing.forEach((lk, oIdx) => {
          const tName = String(lk.target_slot ?? lk.to_slot ?? "out");
          let dType = "MODEL";
          if (/clip|positive|negative|prompt/i.test(tName)) dType = "CLIP";
          else if (/model/i.test(tName)) dType = "MODEL";
          else if (/latent|samples/i.test(tName)) dType = "LATENT";
          else if (/vae/i.test(tName)) dType = "VAE";
          else if (/image/i.test(tName)) dType = "IMAGE";
          const portKey = String(lk.source_slot ?? lk.from_slot ?? oIdx);
          if (!derived[portKey]) {
            derived[portKey] = { name: dType, type: dType, data_type: dType };
          }
        });
        outputs = derived;
      }
    }

    return {
      ...node,
      node_id: nodeId,
      id: nodeId,
      class_type: node.class_type || node.kind || "Node",
      title: node.title || node.name || node.kind || "Node",
      x: geometry.x,
      y: geometry.y,
      width: geometry.width,
      height: geometry.height,
      category: node.category || "node",
      inputs,
      input_ports: Array.isArray(node.raw_payload?.inputs) ? node.raw_payload.inputs : inputs,
      outputs: outputs || {},
      widgets,
    };
  });

  const actuatorParams = deriveActuatorParamsFromWidgets(nodes, value.actuator_params);

  return {
    ...value,
    workflow_id: value.workflow_id || raw?.workflow_id,
    nodes,
    links: normalizedLinks,
    connections: normalizedLinks,
    actuator_params: actuatorParams,
    env_diff: value.env_diff || {},
    name: value.name || "workflow",
  };
}

/**
 * 提取列表以节点 widget.promoted 为基准真源，与已有 actuator_params 智能合并。
 */
function deriveActuatorParamsFromWidgets(nodes, existingParams = []) {
  const paramsMap = new Map();
  if (Array.isArray(existingParams)) {
    for (const p of existingParams) {
      if (p && p.node_id && p.field_name) {
        paramsMap.set(`${p.node_id}:${p.field_name}`, { ...p });
      }
    }
  }

  const derived = [];
  for (const node of nodes || []) {
    const nid = String(node.node_id ?? node.id ?? "");
    for (const w of node.widgets || []) {
      if (!w || !w.promoted) continue;
      const key = `${nid}:${w.name}`;
      const existing = paramsMap.get(key) || {};
      derived.push({
        ...existing,
        node_id: nid,
        field_name: String(w.name),
        field_value: w.value !== undefined ? w.value : existing.field_value,
        label: w.label || existing.label || `${node.title || node.name || "Node"} · ${w.name}`,
        value_type: w.value_type || w.field_type || existing.value_type || "string",
        min_val: w.min_val ?? existing.min_val,
        max_val: w.max_val ?? existing.max_val,
        step: w.step ?? existing.step,
        options: w.options || existing.options,
        class_type: node.class_type || node.kind || existing.class_type,
        node_title: node.title || node.name || node.kind || existing.node_title,
      });
    }
  }

  if (derived.length === 0 && Array.isArray(existingParams) && existingParams.length > 0) {
    return existingParams;
  }
  return derived;
}

async function apiFetch(url, options = {}) {
  const authRevision = window.GWAuthGate?.revision;
  if (!window.GWAuthGate?.isAuthenticated()) {
    const error = new Error('请先登录后使用工作流'); error.status = 401; throw error;
  }
  const targetUrl = targetApiUrl(url, options);
  const requestOptions = { credentials: 'same-origin', ...options };
  // 后端统一以 {x, y} 形态保存节点位置：出站时把前端的扁平坐标合并回 position。
  const toBackendNode = (node) => expandNodePosition(node);
  const currentRevision = () => state.currentWorkflow?.revision ?? state.currentWorkflow?.version ?? 1;
  if (url.endsWith('/name') && options.method === 'PUT' && options.body) {
    const body = JSON.parse(options.body);
    requestOptions.body = JSON.stringify({ name: body.name, expected_version: body.expected_version ?? currentRevision() });
  } else if (/\/item\/[^/]+\/folder$/.test(url) && options.method === 'PUT' && options.body) {
    const body = JSON.parse(options.body);
    requestOptions.body = JSON.stringify({ folder_id: body.folder_id, expected_version: body.expected_version });
  } else if (/\/item\/[^/]+\/params$/.test(url) && options.method === 'PUT' && options.body) {
    const body = JSON.parse(options.body);
    requestOptions.body = JSON.stringify({ widget_updates: body.widget_updates || [], positions: body.positions || [], expected_version: body.expected_version });
  } else if (/\/item\/[^/]+$/.test(url) && options.method === 'PUT' && options.body) {
    const body = JSON.parse(options.body);
    requestOptions.body = JSON.stringify({ payload: { ...normalizeMatureWorkflow(body.workflow || body), nodes: (body.workflow?.nodes || body.nodes || []).map(toBackendNode) }, expected_version: body.expected_version ?? currentRevision(), source: body.source || 'canvas' });
  }
  const resp = await fetch(targetUrl, requestOptions);
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    const detail = data?.detail; const msg = detail?.message || detail || data?.message || `请求失败 (${resp.status})`; const error = new Error(msg); error.status = resp.status;
    if (resp.status === 401) {
      if (window.GWAuthGate?.revision === authRevision) { clearInterval(state.comfySyncTimer); clearInterval(state.pollTimer); }
      window.GWAuthGate?.invalidate(401, authRevision, 'workflow');
    }
    throw error;
  }
  if (window.GWAuthGate?.revision !== authRevision || !window.GWAuthGate?.isAuthenticated()) {
    const error = new Error('登录状态已变化，请重新打开工作流'); error.status = 401; throw error;
  }
  if (data.data_gaps?.includes('asset_hub_registration_pending')) showToast('文档或素材已保存，统一资产登记待恢复', true);
  if (/\/export\/[^/]+$/.test(url)) return data.workflow || data;
  if (/\/auto-layout\/[^/]+$/.test(url)) return normalizeMatureWorkflow(data);
  if (/\/compare-local\/[^/]+$/.test(url)) return data;
  if (url.endsWith('/list')) return { ...data, items: data.items || data.document_summaries || [], folders: data.folders || [] };
  if (/\/item\/[^/]+$/.test(url) || /parse-(?:link|upload)$/.test(url)) return normalizeMatureWorkflow(data);
  if (/\/folders$/.test(url) && requestOptions.method === 'POST') return data.folder || data;
  if (/\/folders\//.test(url)) return data.folder || data;
  return data;
}

/* ==================== 1. 初始化与工作流目录 / 剪贴板链接解析 ==================== */

function applyColumnCollapseState() {
  const leftEl = wfGet("leftBusSidebar") || wfQuery(".left-bus-sidebar");
  const centerEl = wfGet("centerActuatorRack") || wfQuery(".center-actuator-rack");
  if (leftEl) {
    leftEl.classList.toggle("collapsed", Boolean(state.leftColCollapsed));
  }
  if (centerEl) {
    centerEl.classList.toggle("collapsed", Boolean(state.centerColCollapsed));
  }
}

function toggleLeftColumnCollapse(forceCollapsed) {
  state.leftColCollapsed = typeof forceCollapsed === "boolean" ? forceCollapsed : !state.leftColCollapsed;
  applyColumnCollapseState();
  saveColumnCollapsePrefs(state.leftColCollapsed, state.centerColCollapsed);
}

function toggleCenterColumnCollapse(forceCollapsed) {
  state.centerColCollapsed = typeof forceCollapsed === "boolean" ? forceCollapsed : !state.centerColCollapsed;
  applyColumnCollapseState();
  saveColumnCollapsePrefs(state.leftColCollapsed, state.centerColCollapsed);
}

async function initWorkbench() {
  const nextRoot = document.getElementById("workflowWorkbench");
  if (workbenchRoot && workbenchRoot !== nextRoot) disposeWorkbench();
  workbenchRoot = nextRoot;
  if (!window.GWAuthGate || !window.GWAuthGate.isAuthenticated()) {
    if (!window.GWAuthGate?.loaded) {
      // 支持控制器先于公共遥测脚本完成初始化：主动启动共享认证请求，避免 wait 自身死锁。
      try { await window.HardwareDeck?.syncAuth?.(); } catch (_) {}
    } else {
      try { await window.GWAuthGate?.wait?.(); } catch (_) {}
    }
  }
  // 认证未知/未认证时只停止受保护初始化；不向工作台插入额外横条或占位。
  if (!window.GWAuthGate?.isAuthenticated()) return;

  if (!workbenchRoot) { disposeWorkbench(); return; }
  if (state.authLoading) return;
  if (!lifecycleAbortController) {
    lifecycleAbortController = new AbortController();
    applyColumnCollapseState();
    bindEvents();
  }
  const initializingLifecycle = lifecycleAbortController;
  state.authLoading = true;
  try {
  await loadSettingsState();
  if (initializingLifecycle !== lifecycleAbortController) return;
  await refreshMediaAssetsCache();
  if (initializingLifecycle !== lifecycleAbortController) return;
  await refreshWorkflowList();
  if (!window.GWAuthGate?.isAuthenticated() || initializingLifecycle !== lifecycleAbortController) return;

  if (state.workflows.length > 0) {
    const requested = window.location ? new URL(window.location.href).searchParams.get('id') : null;
    await selectWorkflow(requested || state.workflows[0].workflow_id);
  }
  // 空工作台等待用户明确导入，不擅自抓取默认 RH 源。
  await restoreWorkflowTask();
  startComfySyncWatcher();
  } finally { if (initializingLifecycle === lifecycleAbortController) state.authLoading = false; }
}

async function restoreWorkflowTask() {
  try {
    const response = await apiFetch("/api/god_workflow/tasks");
    const requestedJob = new URLSearchParams(window.location.search).get('job_id');
    const task = requestedJob
      ? (response.tasks || []).find(item => (item.job_id || item.task_id) === requestedJob)
      : (response.tasks || []).find(item => ["accepted", "running", "waiting_review", "paused", "outcome_unknown"].includes(item.status));
    if (!task) return;
    state.activeTaskId = task.job_id || task.task_id;
    wfGet("taskModal")?.classList.add("open");
    startTaskPolling(state.activeTaskId, task.target);
  } catch (error) {
    showToast("任务恢复查询失败，请重新打开任务面板", true);
  }
}

function applySavedSettings(s) {
  // 保存后立即更新掩码，并清空输入；不向页面回填密钥。
  if (!s || typeof s !== "object") return;
  const apiKeyHint = wfGet("maskedApiKeyHint");
  if (apiKeyHint) {
    apiKeyHint.textContent = s.has_rh_api_key ? `(已配置${s.rh_api_key_source === "local" ? "·本地加密" : "·环境变量"}: ${s.rh_api_key_masked})` : "(未配置)";
    apiKeyHint.classList.toggle("active", Boolean(s.has_rh_api_key));
  }
  const tokenHint = wfGet("maskedTokenHint");
  if (tokenHint) {
    tokenHint.textContent = s.has_rh_access_token ? `(已配置${s.rh_access_token_source === "local" ? "·本地加密" : "·环境变量"}: ${s.rh_access_token_masked})` : "(未配置)";
    tokenHint.classList.toggle("active", Boolean(s.has_rh_access_token));
  }
  const apiKeyInput = wfGet("inputRhApiKey");
  if (apiKeyInput) apiKeyInput.value = "";
  const tokenInput = wfGet("inputRhAccessToken");
  if (tokenInput) tokenInput.value = "";
  for (const id of ["clearRhApiKey", "clearRhAccessToken"]) {
    const checkbox = wfGet(id);
    if (checkbox) checkbox.checked = false;
  }
  state.providerStatus = s.provider_status || {};
}

async function loadSettingsState() {
  try {
    const s = await apiFetch("/api/god_workflow/settings");
    const comfyUrlInput = wfGet("inputComfyUrl");
    if (comfyUrlInput) {
      comfyUrlInput.value = (s.comfy_url || s.comfy_url_active || "").trim() || "http://127.0.0.1:8188";
    }
    const rootInput = wfGet("inputComfyRootDir");
    if (rootInput) rootInput.value = s.comfy_root_dir || "";
    const modelsInput = wfGet("inputLocalModelsDir");
    if (modelsInput) modelsInput.value = s.local_models_dir || "";

    applySavedSettings(s);
  } catch (err) {
    if (err.status === 401) return;
    console.warn("加载设置失败:", err);
  }
}

async function refreshMediaAssetsCache() {
  try {
    const res = await apiFetch("/api/god_workflow/assets/list");
    state.mediaAssets = res.items || [];
    state.mediaAssetsByFilename = {};
    for (const item of state.mediaAssets) {
      if (item.filename) {
        state.mediaAssetsByFilename[item.filename] = item;
        state.mediaAssetsByFilename[item.filename.toLowerCase()] = item;
      }
    }
    const countEl = wfGet("assetCountText");
    if (countEl) countEl.textContent = `共 ${state.mediaAssets.length} 项素材`;
  } catch (err) {
    console.warn("加载素材库缓存失败:", err);
  }
}

async function refreshWorkflowList() {
  try {
    let res = await apiFetch("/api/god_workflow/list");
    const items = res.items || [];

    if (state.currentWorkflow?.workflow_id && !items.some((w) => w.workflow_id === state.currentWorkflow.workflow_id)) {
      items.unshift(state.currentWorkflow);
    }
    state.workflows = items;
    const validIds = new Set(state.workflows.map((w) => w.workflow_id));
    if (!state._openTabsInitialized) {
      state.openWorkflowIds = state.workflows.map((w) => w.workflow_id);
      state._openTabsInitialized = true;
    } else {
      state.openWorkflowIds = state.openWorkflowIds.filter((id) => validIds.has(id));
    }
    if (Array.isArray(res.folders) && res.folders.length > 0) {
      state.folders = res.folders;
    } else {
      const fRes = await apiFetch("/api/god_workflow/folders");
      state.folders = fRes.folders || [];
    }
    for (const wf of state.workflows) {
      if (wf.source_url) recordClipboardHistory(wf.source_url);
    }
    renderWorkflowCatalog();
  } catch (err) {
    console.warn("获取工作流目录失败:", err);
  }
}

async function selectWorkflow(workflowId) {
  try {
    if (state.currentWorkflow) await flushWorkflowParams(state.currentWorkflow.workflow_id);
    const wf = await apiFetch(`/api/god_workflow/item/${encodeURIComponent(workflowId)}`);
    setCurrentWorkflow(normalizeMatureWorkflow(wf));
  } catch (err) {
    showToast(err.message, true);
  }
}

function clearEmptyWorkbenchView() {
  state.currentWorkflow = null;
  state.selectedNodeId = null;
  renderWorkflowCatalog();
  const nodesLayer = wfGet("canvasNodesLayer");
  const svgGroup = wfGet("svgLinksGroup");
  if (nodesLayer) nodesLayer.innerHTML = "";
  if (svgGroup) svgGroup.innerHTML = "";
  const selContainer = wfGet("selectedNodeContainer");
  const actContainer = wfGet("actuatorCardsContainer");
  if (selContainer) {
    selContainer.innerHTML = `<div style="padding:18px 8px;text-align:center;color:rgba(255,255,255,0.65);font-size:var(--gw-type-body-sm);">请从左侧『工作流目录』点击打开任意工作流。</div>`;
  }
  if (actContainer) {
    actContainer.innerHTML = `<div style="padding:18px 8px;text-align:center;color:rgba(255,255,255,0.65);font-size:var(--gw-type-body-sm);">暂无激活工作流。</div>`;
  }
  const srcEl = wfGet("statSourceUrl");
  if (srcEl) {
    srcEl.innerHTML = `<span class="source-prefix">SOURCE:</span><span class="source-origin-text">--</span>`;
  }
}

/**
 * 关闭顶部已打开的工作流标签（保留左侧目录树中的归档，仅从顶部打开栏移除）
 */
async function closeOpenWorkflowTab(workflowId) {
  const idx = state.openWorkflowIds.indexOf(workflowId);
  if (idx === -1) return;
  state.openWorkflowIds.splice(idx, 1);

  if (state.currentWorkflow?.workflow_id === workflowId) {
    if (state.openWorkflowIds.length > 0) {
      const nextIdx = Math.min(idx, state.openWorkflowIds.length - 1);
      await selectWorkflow(state.openWorkflowIds[nextIdx]);
      return;
    }
    clearEmptyWorkbenchView();
    return;
  }
  renderWorkflowCatalog();
}

/**
 * 全部关闭顶部已打开的工作流标签（经二次弹窗确认后触发）
 */
function closeAllOpenWorkflowTabs() {
  const count = state.openWorkflowIds.length;
  state.openWorkflowIds = [];
  clearEmptyWorkbenchView();
  if (count > 0) {
    showToast(`已关闭顶部全部 ${count} 个已打开工作流标签`);
  }
}

/* ==================== 1.4 工作流右键菜单（重命名 / 删除） ==================== */

function openWorkflowContextMenu(workflowId, clientX, clientY) {
  const menu = wfGet("wfContextMenu");
  if (!menu || !workflowId) return;
  state.contextMenuWfId = workflowId;
  menu.classList.add("open");
  menu.setAttribute("aria-hidden", "false");

  const menuW = 156;
  const menuH = 76;
  const left = Math.min(clientX, window.innerWidth - menuW - 8);
  const top = Math.min(clientY, window.innerHeight - menuH - 8);
  menu.style.left = `${Math.max(6, left)}px`;
  menu.style.top = `${Math.max(6, top)}px`;
}

function closeWorkflowContextMenu() {
  const menu = wfGet("wfContextMenu");
  if (!menu) return;
  menu.classList.remove("open");
  menu.setAttribute("aria-hidden", "true");
}

function startInlineRenameWorkflow(workflowId) {
  closeWorkflowContextMenu();
  if (!workflowId) return;
  const wfItem = state.workflows.find((w) => w.workflow_id === workflowId);
  if (wfItem?.folder_id) {
    state.collapsedFolders.delete(wfItem.folder_id);
  }
  state.renamingWorkflowId = workflowId;
  renderWorkflowCatalog();
  const inp = wfQuery(`[data-rename-wf-input="${CSS.escape(workflowId)}"]`);
  if (inp) {
    inp.focus();
    inp.select();
  }
}

async function handleRenameWorkflow(workflowId, newName) {
  const clean = String(newName || "").trim();
  if (!clean) {
    showToast("工作流名称不能为空", true);
    return;
  }
  try {
    await flushWorkflowParams(workflowId);
    const target = await apiFetch(`/api/god_workflow/item/${encodeURIComponent(workflowId)}`);
    const updated = await apiFetch(`/api/god_workflow/item/${encodeURIComponent(workflowId)}/name`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: clean, expected_version: target.revision ?? target.version }),
    });
    state.renamingWorkflowId = null;
    if (state.currentWorkflow?.workflow_id === workflowId) {
      state.currentWorkflow.name = updated.name;
      state.currentWorkflow.version = state.currentWorkflow.revision = updated.version;
      renderHeaderTelemetry(state.currentWorkflow);
    }
    await refreshWorkflowList();
    showToast(`工作流已重命名为「${updated.name}」`);
  } catch (err) {
    showToast(err.message, true);
  }
}

async function handleDeleteWorkflow(workflowId) {
  closeWorkflowContextMenu();
  if (!workflowId) return;
  const target = state.workflows.find((w) => w.workflow_id === workflowId);
  const wfName = target?.name || workflowId;
  try {
    await flushWorkflowParams(workflowId);
    const document = await apiFetch(`/api/god_workflow/item/${encodeURIComponent(workflowId)}`);
    await apiFetch(`/api/god_workflow/item/${encodeURIComponent(workflowId)}`, {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ expected_version: document.revision ?? document.version }),
    });
    state.openWorkflowIds = state.openWorkflowIds.filter((id) => id !== workflowId);
    const wasCurrent = state.currentWorkflow?.workflow_id === workflowId;
    if (wasCurrent) {
      state.currentWorkflow = null;
      state.selectedNodeId = null;
      clearEmptyWorkbenchView();
    }
    if (state.renamingWorkflowId === workflowId) {
      state.renamingWorkflowId = null;
    }
    await refreshWorkflowList();
    if (wasCurrent) {
      const nextId = state.openWorkflowIds[0] || state.workflows[0]?.workflow_id || null;
      if (nextId) {
        await selectWorkflow(nextId);
      } else {
        state.currentWorkflow = null;
        state.selectedNodeId = null;
        renderWorkflowCatalog();
      }
    }
    showToast(`已删除工作流「${wfName}」`);
  } catch (err) {
    showToast(err.message, true);
  }
}

/* ==================== 1.5 文件夹管理操作（新增 / 改名 / 删除 / 拖拽移动归类） ==================== */

async function handleCreateFolder() {
  const nameInput = wfGet("inputNewFolderName");
  const iconSelect = wfGet("selectNewFolderIcon");
  const bar = wfGet("newFolderInlineBar");
  const cleanName = (nameInput?.value || "").trim();
  const icon = iconSelect?.value || "📁";
  if (!cleanName) {
    showToast("请输入新文件夹名称", true);
    nameInput?.focus();
    return;
  }
  try {
    const created = await apiFetch("/api/god_workflow/folders", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: cleanName, icon }),
    });
    if (nameInput) nameInput.value = "";
    if (bar) bar.style.display = "none";
    state.collapsedFolders.delete(created.folder_id);
    state.activeFolderId = created.folder_id;
    await refreshWorkflowList();
    showToast(`已新建分类文件夹「${created.name}」`);
  } catch (err) {
    showToast(err.message, true);
  }
}

async function handleRenameFolder(folderId, newName) {
  const clean = String(newName || "").trim();
  if (!clean) {
    showToast("文件夹名称不能为空", true);
    return;
  }
  try {
    const updated = await apiFetch(`/api/god_workflow/folders/${encodeURIComponent(folderId)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: clean }),
    });
    state.renamingFolderId = null;
    await refreshWorkflowList();
    showToast(`文件夹已重命名为「${updated.name}」`);
  } catch (err) {
    showToast(err.message, true);
  }
}

async function handleDeleteFolder(folderId) {
  const folder = state.folders.find((f) => f.folder_id === folderId);
  if (!folder || folder.is_system) {
    showToast("系统默认文件夹不可删除", true);
    return;
  }
  try {
    const res = await apiFetch(`/api/god_workflow/folders/${encodeURIComponent(folderId)}`, {
      method: "DELETE",
    });
    state.collapsedFolders.delete(folderId);
    if (state.activeFolderId === folderId) {
      state.activeFolderId = "parsed_default";
    }
    if (state.currentWorkflow && state.currentWorkflow.folder_id === folderId) {
      state.currentWorkflow.folder_id = "parsed_default";
    }
    await refreshWorkflowList();
    showToast(
      res.migrated_workflows_count > 0
        ? `已删除文件夹「${folder.name}」，其下 ${res.migrated_workflows_count} 个工作流已移回「已解析工作流」`
        : `已删除文件夹「${folder.name}」`
    );
  } catch (err) {
    showToast(err.message, true);
  }
}

async function moveWorkflowToFolder(workflowId, targetFolderId) {
  if (!workflowId || !targetFolderId) return;
  try {
    await flushWorkflowParams(workflowId);
    const target = await apiFetch(`/api/god_workflow/item/${encodeURIComponent(workflowId)}`);
    const updatedWf = await apiFetch(`/api/god_workflow/item/${encodeURIComponent(workflowId)}/folder`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ folder_id: targetFolderId, expected_version: target.revision ?? target.version }),
    });
    state.collapsedFolders.delete(targetFolderId);
    if (state.currentWorkflow?.workflow_id === workflowId) {
      state.currentWorkflow.folder_id = updatedWf.folder_id;
      state.currentWorkflow.version = state.currentWorkflow.revision = updatedWf.version;
    }
    await refreshWorkflowList();
    const targetFolder = state.folders.find((f) => f.folder_id === targetFolderId);
    showToast(`已将「${updatedWf.name}」归类至文件夹「${targetFolder?.name || targetFolderId}」`);
  } catch (err) {
    showToast(err.message, true);
  }
}

async function fetchClipboardSnapshot() {
  let browserText = "";
  try {
    if (navigator.clipboard && navigator.clipboard.readText) {
      browserText = (await navigator.clipboard.readText())?.trim() || "";
      if (browserText) recordClipboardHistory(browserText);
    }
  } catch (_) {}

  let serverData = { latest: null, items: [] };
  try {
    const clipUrl = browserText
      ? `/api/god_workflow/clipboard?text=${encodeURIComponent(browserText)}`
      : "/api/god_workflow/clipboard";
    serverData = await apiFetch(clipUrl);
    if (serverData?.latest?.text) {
      recordClipboardHistory(serverData.latest.text);
    }
    for (const it of serverData?.items || []) {
      if (it?.text) recordClipboardHistory(it.text);
    }
  } catch (_) {}

  const latestText = browserText || serverData?.latest?.text || "";
  return { latestText, serverItems: serverData?.items || [] };
}

async function openClipboardModal() {
  const modal = wfGet("clipboardModal");
  if (!modal) return;
  modal.classList.add("open");
  await renderClipboardModalList();
}

async function renderClipboardModalList() {
  const container = wfGet("clipboardListContainer");
  if (!container) return;
  container.innerHTML = `<div style="padding:14px;text-align:center;">正在读取剪贴板最近复制内容...</div>`;

  const { latestText } = await fetchClipboardSnapshot();
  const merged = [];
  const seen = new Set();

  for (const raw of [latestText, ...state.clipboardHistory]) {
    const t = String(raw || "").trim();
    if (!t || seen.has(t)) continue;
    seen.add(t);
    const isLib = /liblib\.(art|tv|ai)|opencomfy=workflowdata-/i.test(t);
    merged.push({
      text: t,
      isRh: isLikelyRhLink(t),
      isLib,
      isLatest: t === latestText && !!latestText,
    });
  }

  if (merged.length === 0) {
    container.innerHTML = `
      <div class="hw-bay-row" style="text-align:center;padding:18px;">
        <div>当前剪贴板为空。请先复制 RunningHub / Liblib 链接或工作流 JSON，然后点击上方「🔄 刷新读取剪贴板」或直接按 Ctrl+V。</div>
      </div>
    `;
    return;
  }

  container.innerHTML = merged
    .map(
      (item, idx) => `
      <div class="clipboard-item-row" data-clip-idx="${idx}">
        <div class="clipboard-item-meta">
          <div class="clipboard-item-tag">
            ${item.isLatest ? "● [当前剪贴板最新内容] " : ""}${
        item.isLib ? "🔗 LiblibAI 工作流链接" : item.isRh ? "🔗 工作流链接 / JSON" : "📄 最近复制文本"
      }
          </div>
          <div class="clipboard-item-text">${escapeHtml(item.text.slice(0, 280))}</div>
        </div>
        <button type="button" class="hw-btn hw-btn-gold" style="padding:3px 9px;font-size:var(--gw-type-body-md);">
          <span>⚡ 选择并解析</span>
        </button>
      </div>
    `
    )
    .join("");

  container.querySelectorAll(".clipboard-item-row").forEach((rowEl) => {
    rowEl.addEventListener("click", () => {
      const idx = Number(rowEl.getAttribute("data-clip-idx"));
      const chosen = merged[idx];
      if (!chosen) return;
      rowEl.classList.add("active");
      wfGet("clipboardModal")?.classList.remove("open");
      handleParseLink(chosen.text);
    });
  });
}

function showPublicWorkflowPreview(preview) {
  const modal = wfGet('publicWorkflowPreviewModal');
  const body = wfGet('publicWorkflowPreviewBody');
  if (!modal || !body) return;
  const list = (values) => (Array.isArray(values) && values.length)
    ? `<ul>${values.map(value => `<li>${escapeHtml(String(value))}</li>`).join('')}</ul>`
    : '<p>公开详情未提供</p>';
  body.innerHTML = `
    <h3>${escapeHtml(preview.name || 'RunningHub 公开信息')}</h3>
    <p class="public-preview-notice">${escapeHtml(preview.message || '缺少原始拓扑，不能执行或导出为工作流。')}</p>
    <p>工作流 ID：${escapeHtml(preview.workflow_id || '')}</p>
    <section><h4>节点类型清单（${preview.node_types?.length || 0} 种）</h4>
      <p>类型清单不代表节点实例或连接关系。</p>${list(preview.node_types)}</section>
    <section><h4>模型清单（${preview.models?.length || 0} 项）</h4>${list(preview.models)}</section>`;
  // 解析期间按钮被禁用，需保留控件引用，关闭时再返回已恢复的按钮。
  modal._workflowReturnFocus = wfGet('btnParseLink');
  modal.classList.add('open');
}

async function handleParseLink(rawLink) {
  let text = String(rawLink ?? "").trim();

  if (!text) {
    const { latestText } = await fetchClipboardSnapshot();
    if (latestText && isLikelyRhLink(latestText)) {
      text = latestText;
    } else {
      showToast(
        "剪贴板最新内容未检测到有效工作流链接或 JSON，请选择最近复制的链接",
        true,
        "📋 打开剪贴板",
        () => openClipboardModal()
      );
      await openClipboardModal();
      return;
    }
  }

  recordClipboardHistory(text);

  const btn = wfGet("btnParseLink");
  const origText = btn ? btn.innerHTML : "";
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = `<span>⏳ 正在解析...</span>`;
  }

  try {
    const wf = await apiFetch("/api/god_workflow/parse-link", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        url_or_text: text,
        folder_id: state.activeFolderId || "parsed_default",
      }),
    });
    if (wf.public_preview) {
      showPublicWorkflowPreview(wf.public_preview);
      return;
    }
    if (wf.folder_id) {
      state.collapsedFolders.delete(wf.folder_id);
      state.activeFolderId = wf.folder_id;
    }
    await refreshWorkflowList();
    setCurrentWorkflow(normalizeMatureWorkflow(wf));
    if (wf.split_group_count && wf.split_group_count > 1) {
      showToast(
        `✨ 检测到多工作流合集，已自动拆解为 ${wf.split_group_count} 个独立子工作流并归入专属目录！`
      );
    } else {
      showToast(`已成功解析工作流「${wf.name}」(${wf.nodes.length} 节点)`);
    }
  } catch (err) {
    showToast(err.message, true, "📋 打开剪贴板", () => openClipboardModal());
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = origText;
    }
  }
}

async function handleUploadFile(file) {
  if (!file) return;
  const formData = new FormData();
  formData.append("file", file);
  formData.append("folder_id", state.activeFolderId || "parsed_default");

  try {
    showToast(`正在解析本地文件 ${file.name}...`);
    const wf = await apiFetch("/api/god_workflow/parse-upload", {
      method: "POST",
      body: formData,
    });
    if (wf.folder_id) {
      state.collapsedFolders.delete(wf.folder_id);
      state.activeFolderId = wf.folder_id;
    }
    await refreshWorkflowList();
    setCurrentWorkflow(normalizeMatureWorkflow(wf));
    if (wf.split_group_count && wf.split_group_count > 1) {
      showToast(
        `✨ 检测到本地多工作流合集，已自动拆解为 ${wf.split_group_count} 个独立子工作流并归入专属目录！`
      );
    } else {
      showToast(`本地工作流「${wf.name}」解析完成 (${wf.nodes.length} 节点)！`);
    }
  } catch (err) {
    showToast(err.message, true);
  }
}

/**
 * 顶部工具栏「🖥️ 打开 ComfyUI」：
 * 1. 自动检测并后台静默启动本地 ComfyUI；
 * 2. 按固定 ID 匹配已有工作流（不产生 工作流(1)、工作流(2) 等重复副本）；
 * 3. 若已打开 ComfyUI 标签页且桥接插件在线，直接复用该标签页页内切换对应工作流，不重复打开多个浏览器标签页。
 */
async function handleOpenInComfyUi() {
  const wf = state.currentWorkflow;
  const url = wf && wf.workflow_id
    ? `/api/god_workflow/open-in-comfy/${encodeURIComponent(wf.workflow_id)}`
    : `/api/god_workflow/open-in-comfy`;

  const btn = wfGet("btnOpenComfyUi");
  const origHtml = btn ? btn.innerHTML : "";
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = `<span>⏳ 正在启动并打开...</span>`;
  }

  showToast(wf ? `正在同步并匹配 ComfyUI 工作流「${wf.name}」...` : "正在连接并启动本地 ComfyUI...");

  try {
    if (wf) await flushWorkflowParams(wf.workflow_id);
    const res = await apiFetch(url, { method: "POST" });
    const bridgeUrl = new URL(res.open_url);
    const nonce = crypto.randomUUID();
    bridgeUrl.searchParams.set('gw_nonce', nonce);
    bridgeUrl.searchParams.set('gw_origin', window.location.origin);
    const openUrl = bridgeUrl.href;
    const hasLiveTab = Boolean(state.comfyWindowRef && !state.comfyWindowRef.closed);

    let win = null;
    if (res.bridge_connected && hasLiveTab) {
      try {
        state.comfyWindowRef.focus();
      } catch (_) {}
      win = state.comfyWindowRef;
    } else {
      win = window.open(openUrl, "RH_Auto_ComfyUI_Tab");
      if (win) {
        state.comfyWindowRef = win;
        try {
          win.focus();
        } catch (_) {}
      }
    }
    state.comfyBridgeSession = wf ? { nonce, origin: bridgeUrl.origin, workflow_id: wf.workflow_id,
      expected_version: res.expected_version, graph: res.workflow_graph, window: win,
      principalKey: state.authPrincipalKey, expires: Date.now() + 30 * 60 * 1000 } : null;

    if (wf && wf.workflow_id) {
      try {
        const refreshedWf = await apiFetch(`/api/god_workflow/item/${encodeURIComponent(wf.workflow_id)}`);
        setCurrentWorkflow(normalizeMatureWorkflow(refreshedWf), { autoFit: false });
      } catch (_) {}
    }

    let statusPrefix = "";
    if (res.launched) {
      statusPrefix = "已在后台启动本地 ComfyUI，";
    } else if (res.online) {
      statusPrefix = "本地 ComfyUI 已在线，";
    } else {
      statusPrefix = "正在尝试连接本地 ComfyUI，";
    }

    const toastMsg = wf
      ? `${statusPrefix}已发送工作流打开请求「${wf.name}」，请在 ComfyUI 确认加载`
      : `${statusPrefix}已打开 ComfyUI 控制台`;

    showToast(
      toastMsg,
      false,
      win ? "" : "🖥️ 点击打开 ComfyUI",
      win
        ? null
        : () => {
            state.comfyWindowRef = window.open(openUrl, "RH_Auto_ComfyUI_Tab");
            if (state.comfyBridgeSession?.nonce === nonce) state.comfyBridgeSession.window = state.comfyWindowRef;
          }
    );
  } catch (err) {
    showToast(`打开 ComfyUI 失败：${err.message}`, true);
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = origHtml;
    }
  }
}

/**
 * 核对服务端文档版本；版本变化本身不证明来自 ComfyUI，编辑草稿优先保留。
 */
async function checkComfySavedSyncOnce() {
  if (!window.GWAuthGate?.isAuthenticated()) return;
  const selected = state.currentWorkflow;
  const hasDraft = () => {
    const entry = selected && state.parameterSaves.get(selected.workflow_id);
    return Boolean(entry && (entry.running || entry.pending.size || entry.positions.size));
  };
  try {
    const res = await apiFetch("/api/god_workflow/comfy-bridge/sync-status");
    const revisions = res?.revisions || {};
    const syncedNow = new Set(res?.synced_now || []);

    let currentUpdated = false;
    let updatedWfId = null;

    for (const [wfId, revNum] of Object.entries(revisions)) {
      const rev = Number(revNum || 0);
      const prev = state.syncRevisions[wfId];
      if (selected?.workflow_id === wfId && hasDraft()) continue;
      state.syncRevisions[wfId] = rev;
      if ((prev !== undefined && rev > prev) || syncedNow.has(wfId)) {
        updatedWfId = wfId;
        if (state.currentWorkflow?.workflow_id === wfId) {
          currentUpdated = true;
        }
      }
    }

    if (updatedWfId) {
      await refreshWorkflowList();
      if (currentUpdated && state.currentWorkflow === selected && !hasDraft()) {
        const latestWf = await apiFetch(
          `/api/god_workflow/item/${encodeURIComponent(selected.workflow_id)}`
        );
        if (state.currentWorkflow !== selected || hasDraft() || Number(selected.version) >= Number(latestWf.version)) return;
        setCurrentWorkflow(latestWf, { autoFit: false });
        showToast(
          `工作流版本已更新：「${latestWf.name}」(#${latestWf.workflow_id})`
        );
      }
    }
  } catch (_) {}
}

function startComfySyncWatcher() {
  clearInterval(state.comfySyncTimer);
  if (!window.GWAuthGate?.isAuthenticated()) return;
  if (window.GWAuthGate?.paused?.has('workflow')) return;
  checkComfySavedSyncOnce();
  state.comfySyncTimer = setInterval(checkComfySavedSyncOnce, 2500);
}

/* ==================== 2. 渲染状态同步与左侧工作流目录树 ==================== */

function setCurrentWorkflow(wf, options = {}) {
  state.currentWorkflow = wf;
  if (wf.workflow_id) {
    const entry = state.parameterSaves.get(wf.workflow_id);
    if (entry && !entry.running && !entry.pending.size && !entry.positions.size) entry.workflow = wf;
    state.syncRevisions[wf.workflow_id] = Number(wf.sync_revision ?? wf.version ?? 0);
    const existingIdx = state.workflows.findIndex((w) => w.workflow_id === wf.workflow_id);
    if (existingIdx === -1) {
      state.workflows.unshift(wf);
    } else {
      state.workflows[existingIdx] = { ...state.workflows[existingIdx], ...wf };
    }
  }
  if (wf.workflow_id && !state.openWorkflowIds.includes(wf.workflow_id)) {
    state.openWorkflowIds.push(wf.workflow_id);
  }
  if (Array.isArray(wf.split_workflow_ids)) {
    for (const sid of wf.split_workflow_ids) {
      if (sid && !state.openWorkflowIds.includes(sid)) {
        state.openWorkflowIds.push(sid);
      }
    }
  }
  if (wf.folder_id) {
    state.activeFolderId = wf.folder_id;
    state.collapsedFolders.delete(wf.folder_id);
  }

  // 默认自动选中第一个拥有可调属性的节点（若无则选中第一个节点）
  const nodes = wf.nodes || [];
  if (state.selectedNodeId === undefined || (state.selectedNodeId !== null && !nodes.some((n) => n.node_id === state.selectedNodeId))) {
    const firstWithWidgets = nodes.find((n) => Array.isArray(n.widgets) && n.widgets.length > 0);
    state.selectedNodeId = (firstWithWidgets || nodes[0])?.node_id || null;
  }

  const recTarget = wf.env_diff?.recommended_target || (wf.source_type === "rh_link" || wf.source === "runninghub" ? "rh_cloud" : "local_comfy");
  setExecutionTarget(recTarget);

  renderWorkflowCatalog();
  renderHeaderTelemetry(wf);
  renderEnvironmentDiff(wf);
  renderActuatorRack(wf);
  const autoFit = options.autoFit !== undefined ? Boolean(options.autoFit) : true;
  renderNodeCanvas(wf, autoFit);
}

function renderHeaderTelemetry(wf) {
  const diff = wf.env_diff || {};
  const comfyDot = wfGet("localComfyDot");
  const drawerTabDot = wfGet("drawerTabDot");
  const comfyText = wfGet("localComfyText");
  const vramText = wfGet("vramText");
  const compTag = wfGet("completenessTag");

  if (diff.local_online) {
    if (comfyDot) comfyDot.className = "led-dot led-emerald";
    if (drawerTabDot) drawerTabDot.className = diff.can_run_locally ? "led-dot led-emerald" : "led-dot led-amber";
    if (comfyText) comfyText.textContent = diff.device_name ? `ONLINE (${diff.device_name})` : "ONLINE";
    if (vramText) vramText.textContent = diff.vram_summary || "ONLINE";
  } else {
    if (comfyDot) comfyDot.className = "led-dot led-amber";
    if (drawerTabDot) drawerTabDot.className = "led-dot led-amber";
    if (comfyText) comfyText.textContent = "未连接 / 待诊断";
    if (vramText) vramText.textContent = "STANDBY";
  }

  const modeLabels = {
    full_canvas: "FULL CANVAS JSON",
    api_prompt: "API PROMPT + DAG",
    public_metadata_only: "PUBLIC METADATA PREVIEW",
  };
  if (compTag) compTag.textContent = modeLabels[wf.data_completeness] || wf.data_completeness || (wf.source === 'comfyui' ? 'API JSON' : '源图已载入');

  const subIdEl = wfGet("currentWorkflowSubId");
  if (subIdEl) subIdEl.textContent = `${wf.name} · #${wf.workflow_id}`;
  const nodesCountEl = wfGet("statNodesCount");
  if (nodesCountEl) nodesCountEl.textContent = String(wf.nodes?.length || 0);
  const wiresCountEl = wfGet("statWiresCount");
  if (wiresCountEl) wiresCountEl.textContent = String(wf.links?.length || wf.connections?.length || 0);
  const parseOverheadEl = wfGet("statParseOverhead");
  if (parseOverheadEl) parseOverheadEl.textContent = `${wf.parse_overhead_ms || 0}ms`;

  const srcEl = wfGet("statSourceUrl");
  if (srcEl) {
    if (wf.source_url && /^https?:\/\//i.test(wf.source_url)) {
      srcEl.innerHTML = `<span class="source-prefix">SOURCE:</span><a class="source-origin-link" href="${escapeHtml(
        wf.source_url
      )}" target="_blank" rel="noopener noreferrer" title="${escapeHtml(
        wf.source_url
      )}">${escapeHtml(wf.source_url)} ↗</a>`;
    } else {
      const fallbackText = wf.source_url
        ? String(wf.source_url)
        : `LOCAL (${wf.workflow_id})`;
      srcEl.innerHTML = `<span class="source-prefix">SOURCE:</span><span class="source-origin-text" title="${escapeHtml(
        fallbackText
      )}">${escapeHtml(fallbackText)}</span>`;
    }
  }
}

function getSourceBadgeText(sourceType) {
  if (["liblib_link", "liblib"].includes(sourceType)) return "LIB";
  if (["local_file", "comfyui", "canvas"].includes(sourceType)) return "LOCAL";
  if (sourceType === "plugin_ingest") return "PLUG";
  if (["rh_link", "runninghub"].includes(sourceType)) return "RH";
  return "--";
}

function renderWorkflowCatalog() {
  const runway = wfGet("capsuleRunway");
  const treeList = wfGet("workflowTreeList");
  const btnCloseAll = wfGet("btnCloseAllWorkflows");
  const currentId = state.currentWorkflow?.workflow_id;

  // 1. 顶部已打开工作流快速切换胶囊栏（选中状态右上角带 × 关闭按钮）
  const wfById = new Map(state.workflows.map((w) => [w.workflow_id, w]));
  const openItems = state.openWorkflowIds.map((id) => wfById.get(id)).filter(Boolean);
  if (btnCloseAll) {
    btnCloseAll.disabled = openItems.length === 0;
  }

  if (runway) {
    if (openItems.length === 0) {
      runway.innerHTML = `<span style="font-size:var(--gw-type-body-sm);color:rgba(255,255,255,0.55);font-family:var(--gw-font-mono);font-variant-numeric:tabular-nums;padding-left:4px;">暂无已打开工作流（请从左侧目录点击打开）</span>`;
    } else {
      runway.innerHTML = openItems
        .map((item, idx) => {
          const isAct = item.workflow_id === currentId;
          const closeBtnHtml = isAct
            ? `<span class="wf-capsule-close" data-close-wf-tab="${escapeHtml(item.workflow_id)}" title="关闭当前工作流标签">×</span>`
            : "";
          return `
            <button
              type="button"
              class="wf-capsule ${isAct ? "active" : ""}"
              data-wf-id="${escapeHtml(item.workflow_id)}"
              title="${escapeHtml(item.name)}（右键可重命名或删除）"
            >
              <span class="led-dot ${item.can_run_locally ? "led-emerald" : "led-amber"}" style="width:6px;height:6px;"></span>
              <span>${String(idx + 1).padStart(2, "0")}. ${escapeHtml(item.name)}</span>
              ${closeBtnHtml}
            </button>
          `;
        })
        .join("");
    }

    runway.querySelectorAll(".wf-capsule[data-wf-id]").forEach((btn) => {
      const wfId = btn.getAttribute("data-wf-id");
      btn.addEventListener("click", (e) => {
        if (e.target.closest("[data-close-wf-tab]")) return;
        selectWorkflow(wfId);
      });
      btn.addEventListener("contextmenu", (e) => {
        e.preventDefault();
        e.stopPropagation();
        openWorkflowContextMenu(wfId, e.clientX, e.clientY);
      });
    });

    runway.querySelectorAll("[data-close-wf-tab]").forEach((closeBtn) => {
      closeBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        closeOpenWorkflowTab(closeBtn.getAttribute("data-close-wf-tab"));
      });
    });
  }

  // 2. 左侧多文件夹分类工作流目录树（系统默认『已解析工作流』始终置顶常驻）
  const defaultFolder = {
    folder_id: "parsed_default",
    name: "已解析工作流",
    icon: "📥",
    is_system: true,
  };
  const customFolders = (state.folders || []).filter(
    (f) => f && f.folder_id !== "parsed_default"
  );
  const folders = [defaultFolder, ...customFolders];

  const folderIds = new Set(folders.map((f) => f.folder_id));
  const groupedWfs = {};
  for (const f of folders) {
    groupedWfs[f.folder_id] = [];
  }
  for (const wf of state.workflows) {
    const fid = folderIds.has(wf.folder_id) ? wf.folder_id : "parsed_default";
    (groupedWfs[fid] = groupedWfs[fid] || []).push(wf);
  }

  treeList.innerHTML = folders
    .map((folder) => {
      const fid = folder.folder_id;
      const wfItems = groupedWfs[fid] || [];
      const isCollapsed = state.collapsedFolders.has(fid);
      const isActiveFolder = state.activeFolderId === fid;
      const isRenaming = state.renamingFolderId === fid;

      let headerHtml = "";
      if (isRenaming) {
        headerHtml = `
          <div class="wf-folder-header active-folder" data-folder-header="${escapeHtml(fid)}">
            <input
              type="text"
              class="param-input input-rename-folder"
              data-rename-input="${escapeHtml(fid)}"
              value="${escapeHtml(folder.name)}"
              style="padding:2px 5px;font-size:var(--gw-type-body-sm);flex:1;"
            />
            <button type="button" class="hw-btn hw-btn-gold btn-save-rename" data-save-rename="${escapeHtml(fid)}" style="padding:1px 6px;font-size:var(--gw-type-body-md);">✓</button>
            <button type="button" class="hw-btn btn-cancel-rename" data-cancel-rename="${escapeHtml(fid)}" style="padding:1px 5px;font-size:var(--gw-type-body-md);">✕</button>
          </div>
        `;
      } else {
        const deleteBtnHtml = folder.is_system
          ? ""
          : `<button type="button" class="folder-icon-btn danger" data-delete-folder="${escapeHtml(fid)}" title="删除文件夹（内部工作流将自动移回『已解析工作流』）">🗑</button>`;
        headerHtml = `
          <div class="wf-folder-header ${isActiveFolder ? "active-folder" : ""}" data-folder-header="${escapeHtml(fid)}">
            <div class="wf-folder-title-wrap">
              <span class="wf-folder-arrow">${isCollapsed ? "▸" : "▾"}</span>
              <span style="font-size:var(--gw-icon-size-sm);">${escapeHtml(folder.icon || "📁")}</span>
              <span class="wf-folder-name" title="${escapeHtml(folder.name)}">${escapeHtml(folder.name)}</span>
            </div>
            <div class="wf-folder-actions">
              <span class="capsule-badge" title="文件夹内工作流数量">${wfItems.length}</span>
              <button type="button" class="folder-icon-btn" data-rename-folder="${escapeHtml(fid)}" title="重命名文件夹">✎</button>
              ${deleteBtnHtml}
            </div>
          </div>
        `;
      }

      const childrenHtml =
        wfItems.length === 0
          ? `<div class="wf-empty-folder-hint">暂无工作流（可拖拽工作流至此）</div>`
          : wfItems
              .map((item, idx) => {
                const isAct = item.workflow_id === currentId;
                const isRenamingWf = state.renamingWorkflowId === item.workflow_id;
                const srcBadge = getSourceBadgeText(item.source_type || item.source);
                if (isRenamingWf) {
                  return `
                    <div class="wf-tree-item active" data-wf-rename-row="${escapeHtml(item.workflow_id)}" style="gap:4px;">
                      <input
                        type="text"
                        class="param-input"
                        data-rename-wf-input="${escapeHtml(item.workflow_id)}"
                        value="${escapeHtml(item.name)}"
                        style="padding:2px 5px;font-size:var(--gw-type-body-sm);flex:1;min-width:0;"
                      />
                      <button type="button" class="hw-btn hw-btn-gold" data-save-rename-wf="${escapeHtml(item.workflow_id)}" style="padding:1px 6px;font-size:var(--gw-type-body-md);">✓</button>
                      <button type="button" class="hw-btn" data-cancel-rename-wf="${escapeHtml(item.workflow_id)}" style="padding:1px 5px;font-size:var(--gw-type-body-md);">✕</button>
                    </div>
                  `;
                }
                return `
                  <div
                    class="wf-tree-item ${isAct ? "active" : ""}"
                    data-wf-id="${escapeHtml(item.workflow_id)}"
                    draggable="true"
                    title="${escapeHtml(item.name)}（左键打开 · 右键重命名或删除 · 拖拽移动至其他文件夹）"
                  >
                    <div style="display:flex;align-items:center;gap:5px;min-width:0;flex:1;">
                      <span class="wf-tree-idx" style="font-family:var(--gw-font-mono);font-variant-numeric:tabular-nums;font-size:var(--gw-type-body-sm);">${String(
                        idx + 1
                      ).padStart(2, "0")}</span>
                      <span class="wf-tree-name">${escapeHtml(item.name)}</span>
                    </div>
                    <span class="wf-source-badge">${escapeHtml(srcBadge)}</span>
                  </div>
                `;
              })
              .join("");

      return `
        <div class="wf-folder-group" data-folder-group="${escapeHtml(fid)}">
          ${headerHtml}
          <div class="wf-folder-children ${isCollapsed ? "collapsed" : ""}">
            ${childrenHtml}
          </div>
        </div>
      `;
    })
    .join("");

  // 绑定文件夹展开/折叠
  treeList.querySelectorAll("[data-folder-header]").forEach((hdr) => {
    const fid = hdr.getAttribute("data-folder-header");
    hdr.addEventListener("click", (e) => {
      if (e.target.closest("button") || e.target.closest("input")) {
        return;
      }
      state.activeFolderId = fid;
      if (state.collapsedFolders.has(fid)) {
        state.collapsedFolders.delete(fid);
      } else {
        state.collapsedFolders.add(fid);
      }
      renderWorkflowCatalog();
    });
  });

  // 绑定文件夹重命名触发
  treeList.querySelectorAll("[data-rename-folder]").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      state.renamingFolderId = btn.getAttribute("data-rename-folder");
      renderWorkflowCatalog();
      const inp = treeList.querySelector(`[data-rename-input="${state.renamingFolderId}"]`);
      if (inp) {
        inp.focus();
        inp.select();
      }
    });
  });

  // 绑定文件夹内联重命名保存与取消
  treeList.querySelectorAll("[data-save-rename]").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      const fid = btn.getAttribute("data-save-rename");
      const inp = treeList.querySelector(`[data-rename-input="${fid}"]`);
      handleRenameFolder(fid, inp?.value || "");
    });
  });
  treeList.querySelectorAll("[data-cancel-rename]").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      state.renamingFolderId = null;
      renderWorkflowCatalog();
    });
  });
  treeList.querySelectorAll("[data-rename-input]").forEach((inp) => {
    inp.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        handleRenameFolder(inp.getAttribute("data-rename-input"), inp.value);
      } else if (e.key === "Escape") {
        state.renamingFolderId = null;
        renderWorkflowCatalog();
      }
    });
  });

  // 绑定工作流内联重命名保存与取消
  treeList.querySelectorAll("[data-save-rename-wf]").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      const wfId = btn.getAttribute("data-save-rename-wf");
      const inp = treeList.querySelector(`[data-rename-wf-input="${CSS.escape(wfId)}"]`);
      handleRenameWorkflow(wfId, inp?.value || "");
    });
  });
  treeList.querySelectorAll("[data-cancel-rename-wf]").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      state.renamingWorkflowId = null;
      renderWorkflowCatalog();
    });
  });
  treeList.querySelectorAll("[data-rename-wf-input]").forEach((inp) => {
    inp.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        handleRenameWorkflow(inp.getAttribute("data-rename-wf-input"), inp.value);
      } else if (e.key === "Escape") {
        state.renamingWorkflowId = null;
        renderWorkflowCatalog();
      }
    });
  });

  // 绑定文件夹删除
  treeList.querySelectorAll("[data-delete-folder]").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      handleDeleteFolder(btn.getAttribute("data-delete-folder"));
    });
  });

  // 绑定工作流左键点击切换、右键重命名/删除菜单及拖拽归类
  treeList.querySelectorAll(".wf-tree-item[data-wf-id]").forEach((row) => {
    const wfId = row.getAttribute("data-wf-id");
    row.addEventListener("click", () => {
      selectWorkflow(wfId);
    });
    row.addEventListener("contextmenu", (e) => {
      e.preventDefault();
      e.stopPropagation();
      openWorkflowContextMenu(wfId, e.clientX, e.clientY);
    });
    row.addEventListener("dragstart", (e) => {
      e.dataTransfer?.setData("text/plain", wfId);
    });
  });

  treeList.querySelectorAll("[data-folder-group]").forEach((groupEl) => {
    const targetFid = groupEl.getAttribute("data-folder-group");
    groupEl.addEventListener("dragover", (e) => {
      e.preventDefault();
      groupEl.classList.add("drag-over");
    });
    groupEl.addEventListener("dragleave", () => {
      groupEl.classList.remove("drag-over");
    });
    groupEl.addEventListener("drop", (e) => {
      e.preventDefault();
      groupEl.classList.remove("drag-over");
      const draggedWfId = e.dataTransfer?.getData("text/plain");
      if (draggedWfId && targetFid) {
        moveWorkflowToFolder(draggedWfId, targetFid);
      }
    });
  });
}

/* ==================== 3. 本地 ComfyUI 环境对比诊断面板 ==================== */

function renderEnvironmentDiff(wf) {
  const diff = wf.env_diff || {};
  const totalN = diff.required_nodes_count ?? diff.total_nodes_required ?? 0;
  const verified = diff.status === "compared" && totalN > 0;
  const instN = diff.installed_nodes_count || 0;
  const totalM = diff.required_models_count ?? diff.total_models_required ?? 0;
  const instM = diff.installed_models_count || 0;

  wfGet("diffNodeRatio").textContent = verified ? `${instN} / ${totalN}` : "未确认";
  wfGet("diffModelRatio").textContent = verified ? `${instM} / ${totalM}` : "未确认";

  const nPct = !verified ? 0 : totalN > 0 ? Math.round((instN / totalN) * 100) : 100;
  const mPct = !verified ? 0 : totalM > 0 ? Math.round((instM / totalM) * 100) : 100;

  const nBar = wfGet("diffNodeBar");
  const mBar = wfGet("diffModelBar");
  nBar.style.width = `${nPct}%`;
  nBar.style.background = nPct === 100 ? "#34d399" : "#f59e0b";
  mBar.style.width = `${mPct}%`;
  mBar.style.background = mPct === 100 ? "#34d399" : "#f43f5e";

  const container = wfGet("missingItemsContainer");
  const cardsHtml = [];

  for (const m of diff.missing_models || []) {
    const dlBtn = m.download_url
      ? `<a class="mini-link-btn" href="${escapeHtml(m.download_url)}" target="_blank" rel="noopener noreferrer" title="打开模型下载直链">⬇ HF直链</a>`
      : "";
    cardsHtml.push(`
      <div class="missing-item-card">
        <div class="missing-item-head">
          <span style="font-weight:700;">[缺失模型 · ${escapeHtml(m.model_type)}]</span>
          ${dlBtn}
        </div>
        <div class="missing-item-title">${escapeHtml(m.filename)}</div>
      </div>
    `);
  }

  for (const ctype of diff.missing_nodes || []) {
    cardsHtml.push(`
      <div class="missing-item-card node-warn">
        <div class="missing-item-head">
          <span style="font-weight:700;">[缺失节点 · CUSTOM NODE]</span>
          <span>ComfyUI Manager</span>
        </div>
        <div class="missing-item-title">${escapeHtml(ctype)}</div>
      </div>
    `);
  }

  if (!verified) {
    cardsHtml.length = 0;
    cardsHtml.push(`<div class="missing-item-card node-warn">${escapeHtml(diff.diagnostic_message || "尚未诊断，本地环境就绪状态未知")}</div>`);
  } else if (cardsHtml.length === 0) {
    cardsHtml.push(`
      <div class="missing-item-card ready-ok">
        <div class="missing-item-head">
          <span style="font-weight:700;">✓ 本地环境 100% 齐备</span>
        </div>
        <div style="font-size:var(--gw-type-body-sm);">所有节点与模型均已在本地就绪，可直接本地运行！</div>
      </div>
    `);
  }

  container.innerHTML = cardsHtml.join("");
  wfGet("diffSummaryText").textContent = diff.diagnostic_message || "";
  wfGet("recommendedTargetHint").textContent =
    !verified ? "状态未知，请先完成诊断" : diff.recommended_target === "local_comfy" ? "推荐: 本地 ComfyUI 运行" : "推荐: RH 云端免装运行";
}

/* ==================== 4. 中部参数执行矩阵（上下2行：上行选中节点全属性+提升按钮，下行工作流提取列表） ==================== */

function renderActuatorRack(wf) {
  renderSelectedNodePanel(wf);
  renderExtractedParamsList(wf);
}

/**
 * 渲染通用参数输入控件（供上行『用户选中的节点』与下行『工作流提取列表』复用）
 * 当属性为图像/视频上传控件（LoadImage / LoadVideo 等）时，同步提取并渲染「📤 上传 / 🗂️ 选择」双按钮及媒体预览详情卡片！
 */
function buildWidgetControlHtml(attrKey, value, fieldType, minVal, maxVal, stepVal, options, nodeObj = null) {
  const valStr = escapeHtml(value ?? "");
  const type = fieldType || "string";
  const [nid, fname] = String(attrKey || "").split(":");
  const resolvedNode =
    nodeObj || (state.currentWorkflow?.nodes || []).find((n) => String(n.node_id) === String(nid)) || null;
  const mediaMode = getMediaModeForWidget(resolvedNode, fname, type, value);

  if (mediaMode) {
    const detailHtml = buildNodeMediaDetailHtml(
      resolvedNode || { node_id: nid },
      { name: fname, value },
      mediaMode
    );
    return `
      <input class="param-input" type="text" value="${valStr}" data-widget-control="${escapeHtml(attrKey)}" placeholder="输入或上传/选择媒体文件..." />
      <div class="node-upload-actions">
        <button
          type="button"
          class="node-upload-btn"
          data-node-upload="${escapeHtml(nid)}"
          data-node-field="${escapeHtml(fname)}"
          data-media-mode="${escapeHtml(mediaMode)}"
          title="打开本地文件夹上传图像或视频"
        >📤 上传</button>
        <button
          type="button"
          class="node-upload-btn"
          data-node-select-asset="${escapeHtml(nid)}"
          data-node-field="${escapeHtml(fname)}"
          data-media-mode="${escapeHtml(mediaMode)}"
          title="从素材库选择已有图像或视频"
        >🗂️ 选择</button>
      </div>
      ${detailHtml}
    `;
  }

  if (type === "multiline" || String(value ?? "").length > 65 || String(value ?? "").includes("\n")) {
    return `<textarea class="param-textarea" data-widget-control="${escapeHtml(attrKey)}">${valStr}</textarea>`;
  }
  if (type === "select" && Array.isArray(options) && options.length > 0) {
    const optsHtml = options
      .map((opt) => `<option value="${escapeHtml(opt)}" ${opt === value ? "selected" : ""}>${escapeHtml(opt)}</option>`)
      .join("");
    return `<select class="param-select" data-widget-control="${escapeHtml(attrKey)}">${optsHtml}</select>`;
  }
  if ((type === "int" || type === "float") && maxVal !== null && maxVal !== undefined && maxVal <= 4096) {
    const minV = minVal ?? 0;
    const maxV = maxVal ?? 100;
    const stepV = stepVal ?? (type === "float" ? 0.1 : 1);
    return `
      <div class="slider-combo">
        <input class="param-range" type="range" min="${minV}" max="${maxV}" step="${stepV}" value="${valStr}" data-widget-control="${escapeHtml(attrKey)}" />
        <input class="param-num-input" type="number" min="${minV}" max="${maxV}" step="${stepV}" value="${valStr}" data-widget-control="${escapeHtml(attrKey)}" />
      </div>
    `;
  }
  return `<input class="param-input" type="${type === "int" || type === "float" ? "number" : "text"}" value="${valStr}" data-widget-control="${escapeHtml(attrKey)}" />`;
}

/**
 * 上行渲染：用户选中的节点（展示节点全部属性，未提升项左侧配备缩小一半的『⬆ 提升』按钮，已提升项不再显示『已提升』按钮）
 */
function renderSelectedNodePanel(wf) {
  const container = wfGet("selectedNodeContainer");
  const badgeEl = wfGet("selectedNodeBadge");
  const selectEl = wfGet("selectedNodeSelect");
  if (!container || !wf) return;

  const nodes = wf.nodes || [];
  // 渲染快速节点选择下拉框
  if (selectEl) {
    selectEl.innerHTML =
      `<option value="">-- 选择画布节点 (${nodes.length}) --</option>` +
      nodes
        .map((n) => {
          const wCount = (n.widgets || []).length;
          const pCount = (n.widgets || []).filter((w) => w.promoted).length;
          const suffix = pCount > 0 ? ` [✦${pCount}/${wCount}]` : ` (${wCount}属性)`;
          return `<option value="${escapeHtml(n.node_id)}" ${
            n.node_id === state.selectedNodeId ? "selected" : ""
          }>#${escapeHtml(n.node_id)} · ${escapeHtml(n.title)}${suffix}</option>`;
        })
        .join("");
  }

  const node = nodes.find((n) => n.node_id === state.selectedNodeId);
  if (!node) {
    if (badgeEl) badgeEl.textContent = "未选中";
    container.innerHTML = `
      <div style="padding:18px 8px;text-align:center;color:rgba(255,255,255,0.65);font-size:var(--gw-type-body-sm);">
        请在右侧画布点击任意节点卡片（或从上方下拉框选择），即可在此读取该节点的全部内部属性并点击「⬆ 提升」提取至下方列表。
      </div>
    `;
    return;
  }

  const widgets = node.widgets || [];
  const promotedCount = widgets.filter((w) => w.promoted).length;
  if (badgeEl) {
    badgeEl.textContent = `#${node.node_id} · ${promotedCount}/${widgets.length} 项已提取`;
  }

  const titleHasClass = String(node.title || "")
    .toLowerCase()
    .includes(String(node.class_type || "").toLowerCase());
  const classSuffixHtml = titleHasClass
    ? ""
    : ` <span style="opacity:0.7;">(${escapeHtml(node.class_type)})</span>`;

  const metaBarHtml = `
    <div class="selected-node-meta-bar">
      <div style="min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">
        <b style="color:var(--primary);">#${escapeHtml(node.node_id)}</b> · ${escapeHtml(node.title)}${classSuffixHtml}
      </div>
      <span class="capsule-badge">${escapeHtml(node.category.toUpperCase())}</span>
    </div>
  `;

  if (widgets.length === 0) {
    const inNames = normalizePortList(node.input_ports || node.inputs).map((p) => p.name).join(", ") || "无";
    const outNames = normalizePortList(node.outputs).map((p) => p.name).join(", ") || "无";
    container.innerHTML = `
      ${metaBarHtml}
      <div class="hw-bay-row" style="padding:10px;font-size:var(--gw-type-body-sm);line-height:1.5;">
        <div>该节点为纯信号路由/连接节点，无内部标量输入属性 (widgets)。</div>
        <div style="font-family:var(--gw-font-mono);font-variant-numeric:tabular-nums;font-size:var(--gw-type-body-sm);opacity:0.85;">
          ● 输入端口 (Inputs): ${escapeHtml(inNames)}<br/>
          ● 输出端口 (Outputs): ${escapeHtml(outNames)}
        </div>
      </div>
    `;
    return;
  }

  const rowsHtml = widgets
    .map((w) => {
      const attrKey = `${node.node_id}:${w.name}`;
      const isPromoted = Boolean(w.promoted);
      const labelText = w.label || `${node.title} · ${w.name}`;
      const controlHtml = buildWidgetControlHtml(
        attrKey,
        w.value,
        w.value_type || w.field_type,
        w.min_val,
        w.max_val,
        w.step,
        w.options,
        node
      );
      const promoteBtnHtml = isPromoted
        ? ""
        : `<button
            type="button"
            class="btn-promote-param"
            data-promote-toggle="${escapeHtml(attrKey)}"
            title="点击将该输入属性提升至下方『工作流提取列表』"
          >⬆ 提升</button>`;

      return `
        <div class="node-attr-row ${isPromoted ? "is-promoted" : ""}" data-node-attr-row="${escapeHtml(attrKey)}">
          ${promoteBtnHtml}
          <div class="node-attr-main">
            <div class="param-label-row">
              <span title="${escapeHtml(labelText)}">${escapeHtml(labelText)}</span>
              <span class="param-node-badge">${escapeHtml(w.name)}</span>
            </div>
            ${controlHtml}
          </div>
        </div>
      `;
    })
    .join("");

  container.innerHTML = metaBarHtml + rowsHtml;

  // 绑定“⬆ 提升”按钮点击事件
  container.querySelectorAll("[data-promote-toggle]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const key = btn.getAttribute("data-promote-toggle");
      const [nid, fname] = key.split(":");
      togglePromoteWidget(nid, fname);
    });
  });

  // 绑定上行属性值实时编辑事件及媒体「上传/选择」按钮事件
  bindWidgetControlsInContainer(container);
}

/**
 * 下行渲染：工作流提取列表（展示当前工作流所有被提升的属性，对于图像/视频加载节点同步提取渲染「上传 / 选择」按钮与预览详情）
 */
function renderExtractedParamsList(wf) {
  const container = wfGet("actuatorCardsContainer");
  const countBadge = wfGet("extractedCountBadge");
  if (!container || !wf) return;

  // 健壮性兜底：如果 wf.actuator_params 为空或未包含全部已提升参数，重新从 nodes 推导合并
  const derived = deriveActuatorParamsFromWidgets(wf.nodes, wf.actuator_params || []);
  if (derived.length > 0 || !Array.isArray(wf.actuator_params)) {
    wf.actuator_params = derived;
  }

  const params = wf.actuator_params || [];
  if (countBadge) {
    countBadge.textContent = `${params.length} 项已提取`;
  }

  if (params.length === 0) {
    container.innerHTML = `
      <div style="padding:18px 8px;text-align:center;color:rgba(255,255,255,0.65);font-size:var(--gw-type-body-sm);">
        当前『工作流提取列表』为空。<br/>请在上方『用户选中的节点』中点击任意属性左侧的「⬆ 提升」按钮将其提取至此。
      </div>
    `;
    return;
  }

  const nodeMap = new Map((wf.nodes || []).map((n) => [String(n.node_id), n]));

  container.innerHTML = params
    .map((p) => {
      const attrKey = `${p.node_id}:${p.field_name}`;
      const isSelectedNode = p.node_id === state.selectedNodeId;
      const nodeObj = nodeMap.get(String(p.node_id)) || {
        node_id: p.node_id,
        class_type: p.class_type,
        title: p.node_title,
      };
      const controlHtml = buildWidgetControlHtml(
        attrKey,
        p.field_value,
        p.value_type,
        p.min_val,
        p.max_val,
        p.step,
        p.options,
        nodeObj
      );
      return `
        <div
          class="node-attr-row is-promoted ${isSelectedNode ? "highlighted" : ""}"
          data-actuator-node="${escapeHtml(p.node_id)}"
          data-extracted-row="${escapeHtml(attrKey)}"
        >
          <div class="node-attr-main">
            <div class="param-label-row">
              <span title="${escapeHtml(p.label)}">${escapeHtml(p.label)}</span>
              <span style="display:inline-flex;align-items:center;gap:4px;">
                <span
                  class="param-node-badge"
                  style="cursor:pointer;"
                  data-jump-node="${escapeHtml(p.node_id)}"
                  title="点击在上方『用户选中的节点』及右侧画布中定位该节点"
                >#${escapeHtml(p.node_id)} · ${escapeHtml(p.field_name)}</span>
                <button
                  type="button"
                  class="btn-unpromote-icon"
                  data-promote-toggle="${escapeHtml(attrKey)}"
                  title="从工作流提取列表移除该属性"
                >✕</button>
              </span>
            </div>
            ${controlHtml}
          </div>
        </div>
      `;
    })
    .join("");

  // 绑定取消提取图标
  container.querySelectorAll("[data-promote-toggle]").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      const key = btn.getAttribute("data-promote-toggle");
      const [nid, fname] = key.split(":");
      togglePromoteWidget(nid, fname);
    });
  });

  // 绑定点击节点徽章定位到上方『用户选中的节点』
  container.querySelectorAll("[data-jump-node]").forEach((badge) => {
    badge.addEventListener("click", (e) => {
      e.stopPropagation();
      selectCanvasNode(badge.getAttribute("data-jump-node"));
    });
  });

  // 绑定下行输入框及媒体「上传/选择」按钮与上行、画布的双向同步
  bindWidgetControlsInContainer(container);
}

/**
 * 绑定容器内媒体「📤 上传」「🗂️ 选择」按钮与缩略图分辨率探测
 */
function bindMediaUploadControlsInContainer(rootContainer) {
  rootContainer.querySelectorAll("[data-node-upload]").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      const nid = btn.getAttribute("data-node-upload");
      const fname = btn.getAttribute("data-node-field");
      const mode = btn.getAttribute("data-media-mode");
      selectCanvasNode(nid);
      triggerLocalMediaUploadForNode(nid, fname, mode);
    });
  });

  rootContainer.querySelectorAll("[data-node-select-asset]").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      const nid = btn.getAttribute("data-node-select-asset");
      const fname = btn.getAttribute("data-node-field");
      const mode = btn.getAttribute("data-media-mode");
      selectCanvasNode(nid);
      openAssetLibraryModal(nid, fname, mode);
    });
  });

  rootContainer.querySelectorAll("[data-media-preview-node]").forEach((mediaEl) => {
    const detailCard = mediaEl.closest(".node-media-detail");
    const resSpan = detailCard
      ? detailCard.querySelector("[data-media-res]")
      : rootContainer.querySelector(`[data-media-res="${CSS.escape(mediaEl.getAttribute("data-media-preview-node"))}"]`);
    if (mediaEl.tagName.toLowerCase() === "img") {
      mediaEl.addEventListener("load", () => {
        if (resSpan && mediaEl.naturalWidth > 0) {
          resSpan.textContent = `${mediaEl.naturalWidth}×${mediaEl.naturalHeight}`;
        }
      });
      mediaEl.addEventListener("error", () => {
        mediaEl.style.display = "none";
        if (resSpan) resSpan.textContent = "待上传/选择";
      });
    } else if (mediaEl.tagName.toLowerCase() === "video") {
      mediaEl.addEventListener("loadedmetadata", () => {
        if (resSpan && mediaEl.videoWidth > 0) {
          resSpan.textContent = `${mediaEl.videoWidth}×${mediaEl.videoHeight}`;
        }
      });
      mediaEl.addEventListener("error", () => {
        mediaEl.style.display = "none";
        if (resSpan) resSpan.textContent = "待上传/选择";
      });
    }
  });
}

/**
 * 绑定容器内所有 [data-widget-control="nodeId:fieldName"] 控件的实时修改与三处联动
 */
function bindWidgetControlsInContainer(rootContainer) {
  bindMediaUploadControlsInContainer(rootContainer);

  rootContainer.querySelectorAll("[data-widget-control]").forEach((inputEl) => {
    const attrKey = inputEl.getAttribute("data-widget-control");
    const [nid, fname] = attrKey.split(":");

    const onValueChange = () => {
      const wf = state.currentWorkflow;
      if (!wf) return;
      const nodeObj = (wf.nodes || []).find((n) => n.node_id === nid);
      const wObj = nodeObj?.widgets?.find((w) => w.name === fname);
      const pObj = (wf.actuator_params || []).find((p) => p.node_id === nid && p.field_name === fname);

      const vType = wObj?.value_type || wObj?.field_type || pObj?.value_type || "string";
      let newVal = inputEl.value;
      if (vType === "int") newVal = parseInt(newVal, 10) || 0;
      else if (vType === "float") newVal = parseFloat(newVal) || 0.0;
      else if (vType === "bool") newVal = Boolean(inputEl.checked);

      if (wObj) wObj.value = newVal;
      if (pObj) pObj.field_value = newVal;
      if (nodeObj) { nodeObj.inputs ||= {}; nodeObj.inputs[fname] = newVal; }

      // 同步上下两行中所有绑定了相同 attrKey 的其余输入控件
      wfQueryAll(`[data-widget-control="${CSS.escape(attrKey)}"]`).forEach((peer) => {
        if (peer !== inputEl) {
          if (vType === "bool") peer.checked = newVal;
          else peer.value = String(newVal);
        }
      });

      // 同步更新右侧画布节点卡片上的属性值或 Note 备注内容
      const canvasValEl = wfQuery(`[data-canvas-widget="${CSS.escape(`${nid}.${fname}`)}"]`);
      if (canvasValEl) {
        canvasValEl.textContent = String(newVal);
      }

      // 防抖持久化到后端
      schedulePersistWorkflowParams([{ node_id: nid, field_name: fname, value: newVal }]);
    };

    inputEl.addEventListener("input", onValueChange);
    inputEl.addEventListener("change", onValueChange);
  });
}

/**
 * 切换单个节点属性的“提升（Promoted）”状态并同步更新上下两行与后端
 */
async function togglePromoteWidget(nodeId, fieldName) {
  const wf = state.currentWorkflow;
  if (!wf) return;
  const nidStr = String(nodeId);
  const nodeObj = (wf.nodes || []).find((n) => String(n.node_id ?? n.id) === nidStr);
  if (!nodeObj) return;
  const wObj = (nodeObj.widgets || []).find((w) => String(w.name) === String(fieldName));
  if (!wObj) return;

  const nextPromoted = !wObj.promoted;
  wObj.promoted = nextPromoted;

  // 1. 立即执行乐观更新：同步推导最新提取列表并即时重绘左右面板
  wf.actuator_params = deriveActuatorParamsFromWidgets(wf.nodes, wf.actuator_params);
  renderActuatorRack(wf);
  renderNodeCanvas(wf, false);

  try {
    schedulePersistWorkflowParams([{ node_id: nidStr, field_name: fieldName, value: wObj.value, promoted: nextPromoted }]);
    await flushWorkflowParams(wf.workflow_id);
    if (state.currentWorkflow?.workflow_id !== wf.workflow_id) return;
    const updatedRaw = wf;
    // 2. 规范化后端返回，确保 links、ports 与 actuator_params 100% 完整
    const normalizedWf = normalizeMatureWorkflow(updatedRaw);
    state.currentWorkflow = normalizedWf;
    renderActuatorRack(normalizedWf);
    renderNodeCanvas(normalizedWf, false);
    showToast(
      nextPromoted
        ? `已将「#${nidStr} ${wObj.label || fieldName}」提升至工作流提取列表`
        : `已从工作流提取列表移除「#${nidStr} ${wObj.label || fieldName}」`
    );
  } catch (err) {
    // 异常时回滚乐观更新
    wObj.promoted = !nextPromoted;
    wf.actuator_params = deriveActuatorParamsFromWidgets(wf.nodes, wf.actuator_params);
    renderActuatorRack(wf);
    renderNodeCanvas(wf, false);
    showToast(err.message, true);
  }
}

function schedulePersistWorkflowParams(widgetUpdates, positions = []) {
  const wf = state.currentWorkflow;
  if (!wf) return;
  let entry = state.parameterSaves.get(wf.workflow_id);
  if (!entry) {
    entry = { workflow: wf, pending: new Map(), positions: new Map(), running: null, timer: null };
    state.parameterSaves.set(wf.workflow_id, entry);
  }
  for (const update of widgetUpdates) {
    const key = JSON.stringify([String(update.node_id), update.field_name]);
    entry.pending.set(key, { ...entry.pending.get(key), ...update });
  }
  for (const position of positions) entry.positions.set(String(position.node_id), position);
  clearTimeout(entry.timer);
  entry.timer = setTimeout(() => flushWorkflowParams(wf.workflow_id).catch((err) => {
    showToast(`参数尚未保存：${err.message}`, true);
  }), 280);
  state.paramSaveTimer = entry.timer;
}

async function flushWorkflowParams(workflowId) {
  const entry = state.parameterSaves.get(workflowId);
  if (!entry) return;
  clearTimeout(entry.timer);
  while (entry.running) await entry.running;
  if (!entry.pending.size && !entry.positions.size) return;
  const updates = [...entry.pending.values()];
  const positions = [...entry.positions.values()];
  entry.pending.clear();
  entry.positions.clear();
  entry.running = (async () => {
    try {
      const saved = await apiFetch(`/api/god_workflow/item/${encodeURIComponent(workflowId)}/params`, {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ widget_updates: updates, positions, expected_version: entry.workflow.revision ?? entry.workflow.version }),
      });
      // 只回写版本，保留请求期间用户的新输入，不覆盖当前另一份工作流。
      entry.workflow.version = entry.workflow.revision = saved.version;
      state.syncRevisions[workflowId] = saved.version;
      if (state.currentWorkflow?.workflow_id === workflowId) {
        state.currentWorkflow.version = state.currentWorkflow.revision = saved.version;
      }
    } catch (err) {
      for (const update of updates) {
        const key = JSON.stringify([String(update.node_id), update.field_name]);
        entry.pending.set(key, { ...update, ...entry.pending.get(key) });
      }
      for (const position of positions) {
        const key = String(position.node_id);
        if (!entry.positions.has(key)) entry.positions.set(key, position);
      }
      throw err;
    } finally { entry.running = null; }
  })();
  await entry.running;
  if (entry.pending.size || entry.positions.size) await flushWorkflowParams(workflowId);
}

/* ==================== 4.5 外部画布加载精简契约预览与导出 ==================== */

function workflowExportUrl(workflowId, format) {
  const base = `/api/god_workflow/export/${encodeURIComponent(workflowId)}`;
  return `${base}?format=${encodeURIComponent(format)}`;
}

async function openContractModal() {
  const wf = state.currentWorkflow;
  if (!wf) {
    showToast("请先选择或解析一个工作流", true);
    return;
  }
  const modal = wfGet("contractModal");
  const pre = wfGet("contractJsonPreview");
  if (!modal || !pre) return;
  modal.classList.add("open");
  pre.textContent = "正在生成画布对接契约...";
  try {
    const contract = await apiFetch(workflowExportUrl(wf.workflow_id, "canvas_contract"));
    pre.textContent = JSON.stringify(contract, null, 2);
  } catch (err) {
    pre.textContent = `加载失败: ${err.message}`;
  }
}

/* ==================== 4.8 上传节点（图像/视频）本地上传与素材库选择 ==================== */

function formatFileSize(bytes) {
  const n = Number(bytes) || 0;
  if (n <= 0) return "";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(2)} MB`;
}

function buildNodeMediaDetailHtml(node, mediaWidget, uploadMode) {
  if (!mediaWidget) return "";
  const valStr = String(mediaWidget.value ?? "").trim();
  const asset =
    node?._mediaAsset ||
    state.mediaAssetsByFilename[valStr] ||
    state.mediaAssetsByFilename[valStr.toLowerCase()] ||
    null;
  if (!asset && !valStr) return "";

  const filename = asset ? asset.filename : valStr;
  const mediaType = asset ? asset.media_type : uploadMode;
  const mediaUrl = asset ? asset.url : "";
  const sizeText = asset && asset.size_bytes ? formatFileSize(asset.size_bytes) : "";
  const initialRes = !asset
    ? "待上传/选择"
    : asset.width > 0 && asset.height > 0
      ? `${asset.width}×${asset.height}`
      : "解析中...";

  const previewHtml = !asset
    ? `<div
        class="node-media-thumb"
        style="display:flex;align-items:center;justify-content:center;font-size:var(--gw-icon-size-lg);color:var(--text-muted);"
        title="本地暂未上传该文件，请点击上方「📤 上传」或「🗂️ 选择」"
      >${mediaType === "video" ? "🎬" : "🖼️"}</div>`
    : mediaType === "video" || /\.(mp4|mov|webm|mkv|avi|gif)$/i.test(filename)
      ? `<video
          class="node-media-thumb"
          src="${escapeHtml(mediaUrl)}"
          muted
          loop
          playsinline
          preload="metadata"
          data-media-preview-node="${escapeHtml(node.node_id)}"
        ></video>`
      : `<img
          class="node-media-thumb"
          src="${escapeHtml(mediaUrl)}"
          alt="${escapeHtml(filename)}"
          loading="lazy"
          data-media-preview-node="${escapeHtml(node.node_id)}"
        />`;

  return `
    <div class="node-media-detail" data-node-media-detail="${escapeHtml(node.node_id)}">
      ${previewHtml}
      <div class="node-media-meta">
        <span class="node-media-filename" title="${escapeHtml(filename)}">${escapeHtml(filename)}</span>
        <div class="node-media-specs">
          <span>${mediaType === "video" ? "🎬 VIDEO" : "🖼️ IMAGE"}</span>
          <span data-media-res="${escapeHtml(node.node_id)}">${escapeHtml(initialRes)}</span>
          ${sizeText ? `<span>${escapeHtml(sizeText)}</span>` : ""}
        </div>
      </div>
    </div>
  `;
}

async function applyMediaAssetToNode(nodeId, fieldName, asset) {
  const wf = state.currentWorkflow;
  if (!wf || !asset) return;
  state.mediaAssetsByFilename[asset.filename] = asset;

  const nodeObj = (wf.nodes || []).find((n) => String(n.node_id) === String(nodeId));
  if (!nodeObj) return;
  nodeObj._mediaAsset = asset;

  let targetWidget = (nodeObj.widgets || []).find((w) => w.name === fieldName);
  if (!targetWidget && nodeObj.widgets && nodeObj.widgets.length > 0) {
    targetWidget = nodeObj.widgets[0];
  }
  const actualFieldName = targetWidget ? targetWidget.name : fieldName;
  if (targetWidget) {
    targetWidget.value = asset.filename;
    if (Array.isArray(targetWidget.options) && !targetWidget.options.includes(asset.filename)) {
      targetWidget.options.unshift(asset.filename);
    }
  }

  for (const p of wf.actuator_params || []) {
    if (String(p.node_id) === String(nodeId) && p.field_name === actualFieldName) {
      p.field_value = asset.filename;
      if (Array.isArray(p.options) && !p.options.includes(asset.filename)) {
        p.options.unshift(asset.filename);
      }
    }
  }

  renderActuatorRack(wf);
  renderNodeCanvas(wf, false);
  schedulePersistWorkflowParams([
    { node_id: String(nodeId), field_name: actualFieldName, value: asset.filename, asset_id: asset.asset_id },
  ]);
  showToast(`已为节点 #${nodeId} 加载媒体: ${asset.filename}`);
}

function triggerLocalMediaUploadForNode(nodeId, fieldName, mediaMode) {
  state.pendingMediaTarget = { nodeId, fieldName, mediaMode };
  const fileInput = wfGet("nodeMediaUploadInput");
  if (!fileInput) return;
  fileInput.accept =
    mediaMode === "video"
      ? "video/*,.mp4,.mov,.webm,.mkv,.gif,image/*"
      : "image/*,.png,.jpg,.jpeg,.webp,.bmp,.gif,video/*";
  fileInput.value = "";
  fileInput.click();
}

async function handleNodeMediaFileSelected(file) {
  if (!file) return;
  const target = state.pendingMediaTarget;
  try {
    showToast(`正在上传媒体文件: ${file.name}...`);
    const form = new FormData();
    form.append("file", file);
    const asset = await apiFetch("/api/god_workflow/assets/upload", {
      method: "POST",
      body: form,
    });
    state.mediaAssetsByFilename[asset.filename] = asset;
    if (!state.mediaAssets.some((a) => a.asset_id === asset.asset_id)) {
      state.mediaAssets.unshift(asset);
    }

    if (target && target.nodeId) {
      await applyMediaAssetToNode(target.nodeId, target.fieldName, asset);
    }
    const assetModal = wfGet("assetLibraryModal");
    if (assetModal?.classList.contains("open")) {
      renderAssetLibraryGrid();
    }
  } catch (err) {
    showToast(`媒体上传失败: ${err.message}`, true);
  }
}

async function openAssetLibraryModal(nodeId, fieldName, mediaMode) {
  state.pendingMediaTarget = { nodeId, fieldName, mediaMode };
  state.assetFilterType = "all";
  wfQueryAll("#assetLibraryModal [data-asset-filter]").forEach((btn) => {
    btn.classList.toggle("active", btn.getAttribute("data-asset-filter") === state.assetFilterType);
  });

  const subText = wfGet("assetModalTargetHint");
  if (subText) {
    subText.textContent = `当前目标节点: #${nodeId} (${fieldName || "media"}) · 点击任一素材卡片即可直接载入节点`;
  }

  const modal = wfGet("assetLibraryModal");
  if (modal) modal.classList.add("open");

  await refreshMediaAssetsCache();
  renderAssetLibraryGrid();
}

function renderAssetLibraryGrid() {
  const grid = wfGet("assetLibraryGrid");
  if (!grid) return;

  const filtered = (state.mediaAssets || []).filter((item) => {
    if (state.assetFilterType === "all") return true;
    return item.media_type === state.assetFilterType;
  });

  if (filtered.length === 0) {
    grid.innerHTML = `
      <div style="grid-column: 1 / -1; padding: 28px 12px; text-align: center; color: var(--text-muted); font-size:var(--gw-type-body-sm);">
        当前素材库暂无匹配的图像/视频文件。<br/>可点击右上角「📤 上传新素材至库」上传本地图片或视频。
      </div>
    `;
    return;
  }

  grid.innerHTML = filtered
    .map((item) => {
      const sizeStr = formatFileSize(item.size_bytes);
      const resStr = item.width > 0 && item.height > 0 ? `${item.width}×${item.height}` : item.media_type.toUpperCase();
      const thumbHtml =
        item.media_type === "video"
          ? `<video class="asset-card-thumb" src="${escapeHtml(item.url)}" muted loop playsinline preload="metadata"></video>`
          : `<img class="asset-card-thumb" src="${escapeHtml(item.url)}" alt="${escapeHtml(item.filename)}" loading="lazy" />`;
      return `
        <div class="asset-card" data-select-asset-filename="${escapeHtml(item.filename)}">
          ${thumbHtml}
          <div class="asset-card-name" title="${escapeHtml(item.filename)}">${escapeHtml(item.filename)}</div>
          <div class="asset-card-sub">
            <span>${escapeHtml(resStr)}</span>
            <span>${escapeHtml(sizeStr)}</span>
          </div>
        </div>
      `;
    })
    .join("");

  grid.querySelectorAll("[data-select-asset-filename]").forEach((card) => {
    card.addEventListener("click", async () => {
      const filename = card.getAttribute("data-select-asset-filename");
      const asset = state.mediaAssetsByFilename[filename] || state.mediaAssets.find((a) => a.filename === filename);
      if (asset && state.pendingMediaTarget?.nodeId) {
        await applyMediaAssetToNode(
          state.pendingMediaTarget.nodeId,
          state.pendingMediaTarget.fieldName,
          asset
        );
        wfGet("assetLibraryModal")?.classList.remove("open");
      }
    });
  });
}

/* ==================== 5. 右栏全息硬件节点与 SVG 贝塞尔连线画布（含上传节点媒体预览与选中连线流光） ==================== */

function renderNodeCanvas(wf, autoFit = true) {
  const nodesLayer = wfGet("canvasNodesLayer");
  const nodes = wf.nodes || [];

  nodesLayer.innerHTML = nodes
    .map((node) => {
      const isNoteNode =
        node.category === "note" ||
        /note/i.test(node.class_type || "") ||
        /备注|说明/.test(node.title || "");
      const uploadMode = isMediaUploadNode(node);
      const mediaWidget = uploadMode ? getMediaWidgetOfNode(node) : null;
      const missingClass = node.installed_locally === false ? "missing-local" : "";
      const selectedClass = node.node_id === state.selectedNodeId ? "selected" : "";
      const noteClass = isNoteNode ? "note-node" : "";

      const inPortsHtml = normalizePortList(node.input_ports || node.inputs)
        .map((inp) => {
          const color = PORT_COLORS[inp.data_type] || "#94a3b8";
          return `
            <div class="port-item">
              <span class="port-dot" style="background:${color};"></span>
              <span>${escapeHtml(inp.name)}</span>
            </div>
          `;
        })
        .join("");

      const outPortsHtml = normalizePortList(node.outputs)
        .map((outp) => {
          const color = PORT_COLORS[outp.data_type] || "#94a3b8";
          return `
            <div class="port-item">
              <span>${escapeHtml(outp.name)}</span>
              <span class="port-dot" style="background:${color};"></span>
            </div>
          `;
        })
        .join("");

      let bodyContentHtml = "";
      if (isNoteNode && (node.widgets || []).length > 0) {
        const firstW = node.widgets[0];
        bodyContentHtml = `
          <div
            class="node-note-body"
            data-canvas-widget="${escapeHtml(node.node_id)}.${escapeHtml(firstW.name)}"
            title="点击节点可在左侧『用户选中的节点』中编辑或提升该备注内容"
          >${escapeHtml(String(firstW.value ?? ""))}</div>
        `;
      } else {
        const widgetsHtml = (node.widgets || [])
          .slice(0, 6)
          .map(
            (w) => `
            <div class="node-widget-row ${w.promoted ? "promoted-widget" : ""}">
              <span class="node-widget-key">${w.promoted ? "✦ " : ""}${escapeHtml(w.name)}:</span>
              <span class="node-widget-val" data-canvas-widget="${escapeHtml(node.node_id)}.${escapeHtml(w.name)}" title="${escapeHtml(w.value)}">${escapeHtml(String(w.value ?? ""))}</span>
            </div>
          `
          )
          .join("");

        let uploadSectionHtml = "";
        if (uploadMode) {
          const fieldName = mediaWidget ? mediaWidget.name : uploadMode === "video" ? "video" : "image";
          const detailHtml = buildNodeMediaDetailHtml(node, mediaWidget || { name: fieldName, value: "" }, uploadMode);
          uploadSectionHtml = `
            <div class="node-upload-actions">
              <button
                type="button"
                class="node-upload-btn"
                data-node-upload="${escapeHtml(node.node_id)}"
                data-node-field="${escapeHtml(fieldName)}"
                data-media-mode="${escapeHtml(uploadMode)}"
                title="打开本地文件夹上传图像或视频"
              >📤 上传</button>
              <button
                type="button"
                class="node-upload-btn"
                data-node-select-asset="${escapeHtml(node.node_id)}"
                data-node-field="${escapeHtml(fieldName)}"
                data-media-mode="${escapeHtml(uploadMode)}"
                title="从素材库选择已有图像或视频"
              >🗂️ 选择</button>
            </div>
            ${detailHtml}
          `;
        }

        bodyContentHtml =
          widgetsHtml || uploadSectionHtml
            ? `<div class="node-widgets-box">${widgetsHtml}${uploadSectionHtml}</div>`
            : "";
      }

      const missingIcon =
        node.installed_locally === false
          ? `<span class="missing-node-icon" title="本地 ComfyUI 缺失节点: ${escapeHtml(node.class_type)}">
               <svg viewBox="0 0 16 16" width="13" height="13" fill="none" aria-label="缺失节点图标">
                 <path d="M8 1.6L14.8 13.6H1.2L8 1.6Z" stroke="#f43f5e" stroke-width="1.5" fill="rgba(244,63,94,0.22)" stroke-linejoin="round"/>
                 <path d="M8 5.8V9.2" stroke="#f43f5e" stroke-width="1.5" stroke-linecap="round"/>
                 <circle cx="8" cy="11.5" r="0.85" fill="#f43f5e"/>
               </svg>
             </span>`
          : "";

      const cardWidth = uploadMode ? Math.max(Number(node.width) || 195, 200) : node.width;

      return `
        <div
          class="hw-node-pod canvas-node ${noteClass} ${missingClass} ${selectedClass}"
          id="canvas-node-${escapeHtml(node.node_id)}"
          data-node-id="${escapeHtml(node.node_id)}"
          style="left:${node.x}px; top:${node.y}px; width:${cardWidth}px;"
        >
          <div class="node-header">
            <span class="node-title-text" title="${escapeHtml(node.class_type)}">${isNoteNode ? "📝 " : ""}${escapeHtml(node.title)}</span>
            <div style="display:flex;align-items:center;gap:4px;">
              ${missingIcon}
              <span class="node-id-pill">#${escapeHtml(node.node_id)}</span>
            </div>
          </div>
          ${inPortsHtml || outPortsHtml ? `
          <div class="node-ports-grid">
            <div class="ports-col-in">${inPortsHtml}</div>
            <div class="ports-col-out">${outPortsHtml}</div>
          </div>` : ""}
          ${bodyContentHtml}
        </div>
      `;
    })
    .join("");

  nodesLayer.querySelectorAll(".canvas-node").forEach((el) => {
    const nid = el.getAttribute("data-node-id");
    el.addEventListener("mousedown", (e) => {
      if (e.target.closest("[data-node-upload]") || e.target.closest("[data-node-select-asset]")) {
        e.stopPropagation();
        return;
      }
      startDragNode(e, nid, el);
    });
  });

  // 绑定上传节点上的「📤 上传」与「🗂️ 选择」按钮
  nodesLayer.querySelectorAll("[data-node-upload]").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      const nid = btn.getAttribute("data-node-upload");
      const fname = btn.getAttribute("data-node-field");
      const mode = btn.getAttribute("data-media-mode");
      selectCanvasNode(nid);
      triggerLocalMediaUploadForNode(nid, fname, mode);
    });
  });

  nodesLayer.querySelectorAll("[data-node-select-asset]").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      const nid = btn.getAttribute("data-node-select-asset");
      const fname = btn.getAttribute("data-node-field");
      const mode = btn.getAttribute("data-media-mode");
      selectCanvasNode(nid);
      openAssetLibraryModal(nid, fname, mode);
    });
  });

  // 媒体预览图片/视频加载后动态补全真实分辨率，若文件不存在则隐藏破损缩略图
  nodesLayer.querySelectorAll("[data-media-preview-node]").forEach((mediaEl) => {
    const nid = mediaEl.getAttribute("data-media-preview-node");
    const resSpan = nodesLayer.querySelector(`[data-media-res="${CSS.escape(nid)}"]`);
    if (mediaEl.tagName.toLowerCase() === "img") {
      mediaEl.addEventListener("load", () => {
        if (resSpan && mediaEl.naturalWidth > 0) {
          resSpan.textContent = `${mediaEl.naturalWidth}×${mediaEl.naturalHeight}`;
        }
      });
      mediaEl.addEventListener("error", () => {
        mediaEl.style.display = "none";
        if (resSpan) resSpan.textContent = "待上传/选择";
      });
    } else if (mediaEl.tagName.toLowerCase() === "video") {
      mediaEl.addEventListener("loadedmetadata", () => {
        if (resSpan && mediaEl.videoWidth > 0) {
          resSpan.textContent = `${mediaEl.videoWidth}×${mediaEl.videoHeight}`;
        }
      });
      mediaEl.addEventListener("error", () => {
        mediaEl.style.display = "none";
        if (resSpan) resSpan.textContent = "待上传/选择";
      });
    }
  });

  renderSvgWires(wf);
  if (autoFit) {
    autoFitCanvas(wf);
  }
}

function autoFitCanvas(wf) {
  const viewport = wfGet("canvasViewport");
  if (!viewport || !wf || !wf.nodes || wf.nodes.length === 0) {
    applyCanvasTransform();
    return;
  }
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  for (const n of wf.nodes) {
    const nx = Number(n.x) || 0;
    const ny = Number(n.y) || 0;
    const nw = Number(n.width) || 175;
    const nh = Number(n.height) || 130;
    minX = Math.min(minX, nx);
    minY = Math.min(minY, ny);
    maxX = Math.max(maxX, nx + nw);
    maxY = Math.max(maxY, ny + nh);
  }
  const graphW = Math.max(400, maxX - minX);
  const graphH = Math.max(300, maxY - minY);
  const vw = viewport.clientWidth || 920;
  const vh = viewport.clientHeight || 700;
  const marginX = 64;
  const marginY = 64;
  const scaleX = (vw - marginX * 2) / graphW;
  const scaleY = (vh - marginY * 2) / graphH;
  state.zoom = Math.min(1.0, Math.max(0.25, +Math.min(scaleX, scaleY).toFixed(2)));
  state.panX = Math.round((vw - graphW * state.zoom) / 2 - minX * state.zoom);
  state.panY = Math.round((vh - graphH * state.zoom) / 2 - minY * state.zoom);
  applyCanvasTransform();
}

/**
 * 渲染 SVG 贝塞尔连线：
 * 1. 基础连线粗细减小一半（stroke-width="1"）
 * 2. 当选中某个节点（state.selectedNodeId）时，所有流入该节点（to_node === selectedNodeId）
 *    与从该节点流出（from_node === selectedNodeId）的连线叠加循环流光特效，方向严格为“上一节点 -> 当前节点 -> 下一节点”
 */
function renderSvgWires(wf) {
  const svgGroup = wfGet("svgLinksGroup");
  if (!svgGroup || !wf) return;

  const canvasSvg = wfGet("canvasSvg");
  if (canvasSvg) {
    let maxW = 3200;
    let maxH = 2200;
    for (const n of wf.nodes || []) {
      maxW = Math.max(maxW, (Number(n.x) || 0) + (Number(n.width) || 200) + 600);
      maxH = Math.max(maxH, (Number(n.y) || 0) + (Number(n.height) || 200) + 600);
    }
    canvasSvg.style.width = `${maxW}px`;
    canvasSvg.style.height = `${maxH}px`;
  }

  const nodeMap = {};
  for (const n of wf.nodes || []) {
    const nid = String(n.node_id ?? n.id ?? "");
    if (nid) {
      nodeMap[nid] = n;
      if (n.node_id !== undefined) nodeMap[String(n.node_id)] = n;
      if (n.id !== undefined) nodeMap[String(n.id)] = n;
    }
  }

  const selId = state.selectedNodeId ? String(state.selectedNodeId) : null;
  const pathsHtml = [];

  for (const lk of wf.links || wf.connections || []) {
    const fromId = String(lk.from_node ?? lk.source ?? "");
    const toId = String(lk.to_node ?? lk.target ?? "");
    const src = nodeMap[fromId];
    const dst = nodeMap[toId];
    if (!src || !dst) continue;

    // 1. 计算目标节点的输入端口垂直插槽位置
    let toSlotIdx = 0;
    const targetName = String(lk.target_slot ?? lk.to_slot ?? "");
    const inPorts = normalizePortList(dst.input_ports || dst.inputs);
    const foundInIdx = inPorts.findIndex((p) => p.name === targetName);
    if (foundInIdx >= 0) {
      toSlotIdx = foundInIdx;
    } else {
      const pInt = parseInt(targetName, 10);
      toSlotIdx = (!isNaN(pInt) && pInt >= 0) ? pInt : 0;
    }

    // 2. 计算源节点的输出端口垂直插槽位置
    let fromSlotIdx = 0;
    const sourceName = String(lk.source_slot ?? lk.from_slot ?? "");
    const outPorts = normalizePortList(src.outputs);
    const foundOutIdx = outPorts.findIndex((p) => p.name === sourceName);
    if (foundOutIdx >= 0) {
      fromSlotIdx = foundOutIdx;
    } else {
      const pInt = parseInt(sourceName, 10);
      fromSlotIdx = (!isNaN(pInt) && pInt >= 0) ? pInt : 0;
    }

    const srcWidth = isMediaUploadNode(src) ? Math.max(Number(src.width) || 195, 200) : Number(src.width) || 195;
    const portPoint = (nodeId, side, slot, fallback) => {
      const stage = wfGet('canvasStage');
      const dot = wfQuery(`.canvas-node[data-node-id="${CSS.escape(nodeId)}"] .ports-col-${side} .port-item:nth-child(${slot + 1}) .port-dot`);
      if (!stage?.getBoundingClientRect || !dot?.getBoundingClientRect) return fallback;
      const bounds = stage.getBoundingClientRect(), port = dot.getBoundingClientRect();
      const zoom = state.zoom || 1;
      return [(port.left + port.width / 2 - bounds.left) / zoom, (port.top + port.height / 2 - bounds.top) / zoom];
    };
    const [x1, y1] = portPoint(fromId, 'out', fromSlotIdx, [(Number(src.x) || 0) + srcWidth, (Number(src.y) || 0) + 36 + fromSlotIdx * 17]);
    const [x2, y2] = portPoint(toId, 'in', toSlotIdx, [Number(dst.x) || 0, (Number(dst.y) || 0) + 36 + toSlotIdx * 17]);

    const dx = Math.max(55, Math.abs(x2 - x1) * 0.45);
    // 路径方向固定为 src (from_node) -> dst (to_node)
    const d = `M ${x1} ${y1} C ${x1 + dx} ${y1}, ${x2 - dx} ${y2}, ${x2} ${y2}`;

    // 数据类型推导与渐变色匹配
    let dType = lk.data_type || outPorts[fromSlotIdx]?.data_type || inPorts[toSlotIdx]?.data_type || "DEFAULT";
    if (dType === "DEFAULT" || dType === "LINK") {
      if (/clip|positive|negative|prompt/i.test(targetName)) dType = "CLIP";
      else if (/model/i.test(targetName)) dType = "MODEL";
      else if (/latent|samples/i.test(targetName)) dType = "LATENT";
      else if (/vae/i.test(targetName)) dType = "VAE";
      else if (/image/i.test(targetName)) dType = "IMAGE";
    }

    const gradId = PORT_COLORS[dType]
      ? `grad${
          dType === "VIDEO" || dType === "ANY"
            ? "MODEL"
            : dType === "AUDIO" || dType === "CONDITIONING"
            ? "CLIP"
            : dType
        }`
      : null;
    const dotColor = PORT_COLORS[dType] || "#94a3b8";
    const wireStroke = gradId ? `url(#${gradId})` : dotColor;

    const isIncomingToSelected = selId !== null && (String(toId) === selId || String(lk.to_node) === selId);
    const isOutgoingFromSelected = selId !== null && (String(fromId) === selId || String(lk.from_node) === selId);
    const isConnectedToSelected = isIncomingToSelected || isOutgoingFromSelected;
    const baseOpacity = selId ? (isConnectedToSelected ? "0.32" : "0.28") : "0.85";

    let flowSpindleHtml = "";
    if (isConnectedToSelected) {
      // 6 级同色同心锁相纺锤梯度：从首尾极细微光尖梢 (L=44, width=0.45px) 平滑过渡至中心饱满亮峰 (L=5, width=1.95px)
      const SPINDLE_STEPS = [
        { len: 44, width: 0.45, opacity: 0.16 },
        { len: 35, width: 0.75, opacity: 0.20 },
        { len: 26, width: 1.05, opacity: 0.24 },
        { len: 18, width: 1.35, opacity: 0.28 },
        { len: 11, width: 1.65, opacity: 0.32 },
        { len: 5, width: 1.95, opacity: 0.40 },
      ];
      const maxLen = 44;
      const dirAttr = isIncomingToSelected ? "in" : "out";
      flowSpindleHtml = SPINDLE_STEPS.map((st) => {
        const shift = (maxLen - st.len) / 2;
        return `<path
          class="wire-flow-glow"
          d="${d}"
          pathLength="100"
          stroke="${dotColor}"
          stroke-width="${st.width}"
          stroke-opacity="${st.opacity}"
          stroke-dasharray="${st.len} 200"
          style="--taper-shift: ${shift}px;"
          data-flow-direction="${dirAttr}"
        />`;
      }).join("");
    }

    pathsHtml.push(`
      <path d="${d}" fill="none" stroke="${wireStroke}" stroke-width="1" opacity="${baseOpacity}" />
      ${flowSpindleHtml}
      <circle cx="${x1}" cy="${y1}" r="2" fill="${dotColor}" opacity="${baseOpacity}" />
      <circle cx="${x2}" cy="${y2}" r="2" fill="${dotColor}" opacity="${baseOpacity}" />
    `);
  }

  svgGroup.innerHTML = pathsHtml.join("");
}

function selectCanvasNode(nodeId) {
  if (!state.currentWorkflow) return;
  state.selectedNodeId = nodeId ? String(nodeId) : null;
  wfQueryAll(".canvas-node").forEach((el) => {
    el.classList.toggle(
      "selected",
      state.selectedNodeId !== null && el.getAttribute("data-node-id") === state.selectedNodeId
    );
  });
  renderSelectedNodePanel(state.currentWorkflow);
  renderSvgWires(state.currentWorkflow);
  wfQueryAll("[data-actuator-node]").forEach((row) => {
    row.classList.toggle(
      "highlighted",
      state.selectedNodeId !== null && row.getAttribute("data-actuator-node") === state.selectedNodeId
    );
  });
}

function startDragNode(e, nodeId, el) {
  if (e.button !== 0) return;
  e.preventDefault();
  e.stopPropagation();
  selectCanvasNode(nodeId);

  const firstRow = wfQuery(`[data-actuator-node="${CSS.escape(nodeId)}"]`);
  if (firstRow) {
    firstRow.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  const nodeObj = state.currentWorkflow?.nodes?.find((n) => n.node_id === nodeId);
  if (!nodeObj) return;

  const startX = e.clientX;
  const startY = e.clientY;
  const origX = Number(nodeObj.x) || 0;
  const origY = Number(nodeObj.y) || 0;

  const onMove = (ev) => {
    const dx = (ev.clientX - startX) / state.zoom;
    const dy = (ev.clientY - startY) / state.zoom;
    nodeObj.x = Math.round(origX + dx);
    nodeObj.y = Math.round(origY + dy);
    el.style.left = `${nodeObj.x}px`;
    el.style.top = `${nodeObj.y}px`;
    renderSvgWires(state.currentWorkflow);
  };

  const onUp = () => {
    window.removeEventListener("mousemove", onMove);
    window.removeEventListener("mouseup", onUp);
    if (nodeObj.x !== origX || nodeObj.y !== origY) {
      schedulePersistWorkflowParams([], [{ node_id: nodeId, x: nodeObj.x, y: nodeObj.y }]);
    }
  };

  listen(window, "mousemove", onMove);
  listen(window, "mouseup", onUp);
}

function applyCanvasTransform() {
  const stage = wfGet("canvasStage");
  const pctEl = wfGet("zoomPercentText");
  if (stage) {
    stage.style.transform = `translate(${state.panX}px, ${state.panY}px) scale(${state.zoom})`;
  }
  if (pctEl) {
    pctEl.textContent = `${Math.round(state.zoom * 100)}%`;
  }
}

async function autoLayoutCurrentNodes() {
  const wf = state.currentWorkflow;
  if (!wf || !wf.nodes) return;
  try {
    await flushWorkflowParams(wf.workflow_id);
    const dimensions = {};
    wfQueryAll('.canvas-node[data-node-id]').forEach((element) => {
      dimensions[element.dataset.nodeId] = { width: element.offsetWidth, height: element.offsetHeight };
    });
    const updatedWf = await apiFetch(
      `/api/god_workflow/auto-layout/${encodeURIComponent(wf.workflow_id)}`,
      { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ expected_version: wf.revision ?? wf.version, node_dimensions: dimensions }) }
    );
    const normalizedWf = normalizeMatureWorkflow(updatedWf);
    state.currentWorkflow = normalizedWf;
    renderNodeCanvas(normalizedWf, true);
    renderActuatorRack(normalizedWf);
    showToast("已按 Sugiyama 双向重心交叉最小化算法重新整理节点拓扑");
  } catch (err) {
    showToast(`自动拓扑整理失败: ${err.message}`, true);
  }
}

/* ==================== 6. 双端任务执行与轮询 ==================== */

function setExecutionTarget(target) {
  state.executionTarget = target;
  wfGet("btnTargetRH").classList.toggle("active", target === "rh_cloud");
  wfGet("btnTargetLocal").classList.toggle("active", target === "local_comfy");
}

function executionSourceContext(wf) {
  const parameters = new URLSearchParams(window.location.search);
  const project_id = parameters.get('project_id');
  const canvas_id = parameters.get('canvas_id');
  if (!project_id && !canvas_id) return wf?.metadata?.source_context || {};
  const source = {};
  for (const field of ['project_id', 'canvas_id', 'project_version', 'canvas_version']) {
    if (parameters.has(field)) source[field] = parameters.get(field);
  }
  return source;
}

async function handleExecuteWorkflow() {
  if (state.submitting) return;
  const wf = state.currentWorkflow;
  if (!wf) {
    showToast("请先加载或解析一个工作流", true);
    return;
  }

  state.submitting = true;
  try { await flushWorkflowParams(wf.workflow_id); }
  catch (err) { state.submitting = false; showToast(`请先处理保存失败：${err.message}`, true); return; }
  const nodeInfoList = (wf.actuator_params || []).map((p) => ({
    nodeId: String(p.node_id),
    fieldName: String(p.field_name),
    fieldValue: p.field_value,
  }));

  const modal = wfGet("taskModal");
  const bodyEl = wfGet("taskModalBody");
  modal.classList.add("open");
  bodyEl.innerHTML = `
    <div class="hw-bay-row">
      <div style="color:var(--primary);font-family:var(--gw-font-mono);font-variant-numeric:tabular-nums;font-weight:700;">
        ⏳ 正在向 ${state.executionTarget === "rh_cloud" ? "RunningHub 云端算力集群" : "本地 ComfyUI 引擎（需已配置并运行）"} 提交任务...
      </div>
      <div style="font-size:var(--gw-type-body-sm);color:var(--text-muted);">
        工作流: ${escapeHtml(wf.name)} (#${escapeHtml(wf.workflow_id)}) · 提取参数覆盖项: ${nodeInfoList.length} 项
      </div>
    </div>
  `;

  try {
    const payload = JSON.stringify({ workflow_id: wf.workflow_id, expected_version: wf.revision ?? wf.version,
      target: state.executionTarget, node_info_list: nodeInfoList, source_context: executionSourceContext(wf) });
    // 响应丢失后保留同一请求键；收到公共任务 ID 后下一次操作才创建新键。
    if (state.submissionAttempt?.payload !== payload) {
      state.submissionAttempt = { payload, key: crypto.randomUUID() };
    }
    const res = await apiFetch("/api/god_workflow/execute", {
      method: "POST",
      headers: { "Content-Type": "application/json", "Idempotency-Key": state.submissionAttempt.key },
      body: payload,
    });
    state.activeTaskId = res.job_id || res.task_id;
    state.submissionAttempt = null;
    startTaskPolling(state.activeTaskId, res.target);
  } catch (err) {
    bodyEl.innerHTML = `
      <div class="missing-item-card">
        <div style="color:var(--rose);font-weight:700;font-family:var(--gw-font-mono);font-variant-numeric:tabular-nums;">❌ 任务提交失败</div>
        <div style="color:var(--primary-soft);font-size:var(--gw-type-body-sm);margin-top:4px;">${escapeHtml(err.message)}</div>
      </div>
    `;
  } finally { state.submitting = false; }
}

async function openWorkflowTasks() {
  wfGet('taskModal')?.classList.add('open');
  try {
    const response = await apiFetch('/api/god_workflow/tasks');
    const tasks = response.tasks || [];
    const selector = wfGet('workflowTaskSelect');
    if (selector) selector.innerHTML = tasks.map(task => `<option value="${escapeHtml(task.job_id || task.task_id)}" data-target="${escapeHtml(task.target)}">${escapeHtml(task.status)} · ${escapeHtml(task.job_id || task.task_id)}</option>`).join('');
    const selected = tasks.find(task => (task.job_id || task.task_id) === state.activeTaskId) || tasks[0];
    if (selected) {
      state.activeTaskId = selected.job_id || selected.task_id;
      if (selector) selector.value = state.activeTaskId;
      startTaskPolling(state.activeTaskId, selected.target);
    } else wfGet('taskModalBody').textContent = '暂无任务记录';
  } catch (error) { showToast(`任务历史读取失败：${error.message}`, true); }
}

function startTaskPolling(taskId, target) {
  clearInterval(state.pollTimer);
  const generation = state.pollGeneration = (state.pollGeneration || 0) + 1;
  const bodyEl = wfGet("taskModalBody");
  const terminal = new Set(["completed", "failed", "cancelled", "outcome_unknown"]);
  const label = { accepted: "已受理", running: "执行中", waiting_review: "等待审核", paused: "已暂停", completed: "已完成", failed: "失败", cancelled: "已取消", outcome_unknown: "结果未知" };

  let inFlight = false;
  const pollOnce = async () => {
    if (inFlight) return;
    inFlight = true;
    try {
      const statusRes = await apiFetch(`/api/god_workflow/tasks/${encodeURIComponent(taskId)}`);
      if (generation !== state.pollGeneration) return;
      const sourceStatus = String(statusRes.status || "accepted").toLowerCase();
      const st = ({ queued: "accepted", succeeded: "completed", success: "completed", canceled: "cancelled", interrupted: "cancelled", error: "failed" })[sourceStatus] || sourceStatus;
      const collectionIncomplete = (statusRes.execution_status || st) === "completed" && statusRes.collection_status && statusRes.collection_status !== "completed";
      const delivered = st === "completed" && !collectionIncomplete;
      const retryButton = wfGet('btnRetryTaskCollection');
      if (retryButton) retryButton.hidden = !collectionIncomplete;
      const outputs = Array.isArray(statusRes.outputs) ? statusRes.outputs : [];
      if (terminal.has(st)) {
        clearInterval(state.pollTimer);
        const mediaHtml = outputs.filter((item) => item.registered && item.url).map((item) => {
          const url = item.url;
          const preview = item.media_type === 'video'
            ? `<video src="${escapeHtml(url)}" controls preload="metadata" style="max-width:100%;max-height:320px;"></video>`
            : item.media_type === 'document' ? `<span>${escapeHtml(item.filename || '产物文件')} · 下载原件</span>`
            : `<img src="${escapeHtml(url)}" alt="生成产物" style="max-width:100%;max-height:320px;" />`;
          return `<div class="hw-bay-row" style="align-items:center;">${preview}<a class="mini-link-btn" href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">打开受控输出</a></div>`;
        }).join("");
        const message = statusRes.messages?.join?.("；") || (st === "outcome_unknown" ? "外部请求可能已产生副作用，请人工核实，不会自动重试。" : "");
        bodyEl.innerHTML = `<div class="missing-item-card ${delivered ? "ready-ok" : ""}"><div style="color:${delivered ? "var(--emerald-bright)" : "var(--rose)"};font-weight:700;font-family:var(--gw-font-mono);font-variant-numeric:tabular-nums;">${escapeHtml(collectionIncomplete ? "执行已结束，输出回收未完成" : (label[st] || st))} (Job ID: ${escapeHtml(taskId)})</div><div style="font-size:var(--gw-type-body-sm);color:var(--text-muted);">${escapeHtml(message)}</div></div>${mediaHtml}`;
        return;
      }
      bodyEl.innerHTML = `<div class="hw-bay-row"><div style="color:var(--compute);font-family:var(--gw-font-mono);font-variant-numeric:tabular-nums;font-weight:700;">${escapeHtml(label[st] || st)} (${escapeHtml(target === "rh_cloud" ? "RunningHub Cloud" : "Local ComfyUI")})...</div><div style="font-family:var(--gw-font-mono);font-variant-numeric:tabular-nums;font-size:var(--gw-type-body-sm);color:var(--text-muted);">JOB ID: ${escapeHtml(taskId)} · 状态: ${escapeHtml(st)}</div></div>`;
    } catch (err) {
      if (generation !== state.pollGeneration) return;
      clearInterval(state.pollTimer);
      bodyEl.innerHTML = `<div class="missing-item-card"><div style="color:var(--rose);font-weight:700;">状态查询失败，可关闭后重试</div><div style="font-size:var(--gw-type-body-sm);color:var(--text-muted);">${escapeHtml(err.message || "请求失败")}</div></div>`;
    } finally {
      inFlight = false;
    }
  };
  pollOnce();
  state.pollTimer = setInterval(pollOnce, 3000);
}
/* ==================== 7. 事件绑定 ==================== */

function setEnvDrawerOpen(isOpen) {
  const drawer = wfGet("envDiffDrawer");
  if (!drawer) return;
  const open = Boolean(isOpen);
  drawer.classList.toggle("collapsed", !open);
  drawer.classList.toggle("open", open);
}

function bindWorkflowOverlays() {
  if (typeof MutationObserver === 'undefined') return;
  const backdrops = [...wfQueryAll('.modal-backdrop')];
  const stack = [];
  const entries = new Map();
  let trigger = null;
  const controls = (modal) => [...modal.querySelectorAll('button, input, textarea, select, a[href], [tabindex]')]
    .filter(element => !element.disabled && element.tabIndex >= 0 && element.getClientRects().length);
  const formValues = modal => JSON.stringify([...modal.querySelectorAll('input:not([readonly]), textarea, select')]
    .map(element => [element.id, element.type === 'checkbox' ? element.checked : element.value]));
  const reconcile = modal => {
    const open = modal.classList.contains('open');
    const previous = entries.get(modal);
    if (open && !previous) {
      delete modal._workflowSettingsSaved;
      const card = modal.querySelector('.modal-card') || modal;
      card.setAttribute('role', 'dialog'); card.setAttribute('aria-modal', 'true'); card.tabIndex = -1;
      card.setAttribute('aria-label', modal.querySelector('.modal-title')?.textContent?.trim() || '工作流对话框');
      entries.set(modal, { trigger: modal._workflowReturnFocus || (trigger?.isConnected ? trigger : document.activeElement), values: formValues(modal) });
      stack.push(modal);
      (controls(modal)[0] || card).focus();
    } else if (!open && previous) {
      if (modal.id === 'settingsModal' && !modal._workflowSettingsSaved && previous.values !== formValues(modal) && !window.confirm('配置尚未保存，确认关闭并保留当前草稿？')) {
        modal.classList.add('open'); return;
      }
      entries.delete(modal); stack.splice(stack.indexOf(modal), 1);
      delete modal._workflowReturnFocus;
      delete modal._workflowSettingsSaved;
      if (previous.trigger?.isConnected && !modal.contains(previous.trigger)) previous.trigger.focus();
    }
  };
  listen(workbenchRoot, 'pointerdown', event => { trigger = event.target.closest?.('button, input, textarea, select, a[href]'); }, true);
  listen(workbenchRoot, 'keydown', event => { if (event.key === 'Enter' || event.key === ' ') trigger = document.activeElement; }, true);
  const observer = new MutationObserver(records => { for (const { target } of records) reconcile(target); });
  backdrops.forEach(modal => {
    observer.observe(modal, { attributes: true, attributeFilter: ['class'] });
    listen(modal, 'click', event => {
      if (event.target === modal && stack.at(-1) === modal) {
        event.preventDefault(); event.stopImmediatePropagation(); modal.classList.remove('open'); reconcile(modal);
      }
    });
    reconcile(modal);
  });
  lifecycleListeners.push(() => observer.disconnect());
  listen(document, 'keydown', event => {
    const modal = stack.at(-1);
    if (!modal) return;
    if (event.key === 'Escape') {
      event.preventDefault(); event.stopImmediatePropagation(); modal.classList.remove('open'); reconcile(modal);
    } else if (event.key === 'Tab') {
      const elements = controls(modal);
      const first = elements[0] || modal.querySelector('.modal-card');
      const last = elements.at(-1) || first;
      if (!modal.contains(document.activeElement) || (event.shiftKey && document.activeElement === first) || (!event.shiftKey && document.activeElement === last)) {
        event.preventDefault(); (event.shiftKey ? last : first)?.focus();
      }
    }
  }, true);
  listen(document, 'focusin', event => {
    const modal = stack.at(-1);
    if (modal && !modal.contains(event.target)) (controls(modal)[0] || modal.querySelector('.modal-card'))?.focus();
  });
}

function bindEvents() {
  listen(window, 'message', async event => {
    const session = state.comfyBridgeSession;
    if (!session || event.source !== session.window || event.origin !== session.origin || event.data?.nonce !== session.nonce ||
        session.principalKey !== state.authPrincipalKey || !window.GWAuthGate?.isAuthenticated() || Date.now() > session.expires) return;
    const send = value => session.window.postMessage({ ...value, nonce: session.nonce }, session.origin);
    if (event.data.kind === 'gw-comfy-ready') {
      send({ kind: 'gw-comfy-load', workflow_id: session.workflow_id, expected_version: session.expected_version, graph: session.graph });
    } else if (event.data.kind === 'gw-comfy-loaded' && event.data.workflow_id === session.workflow_id) {
      session.connected = true; showToast('ComfyUI 已确认加载工作流，可在宿主中回存');
    } else if (event.data.kind === 'gw-comfy-error') {
      showToast(event.data.message || 'ComfyUI 加载失败', true);
    } else if (event.data.kind === 'gw-comfy-save' && event.data.workflow_id === session.workflow_id && !session.saving) {
      session.saving = true;
      try {
        if (event.data.expected_version !== session.expected_version) throw new Error('宿主保存版本不一致');
        const result = await apiFetch('/api/god_workflow/comfy-bridge/sync-saved', { method: 'POST',
          headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ workflow_id: session.workflow_id,
            expected_version: session.expected_version, workflow: event.data.graph }) });
        if (state.comfyBridgeSession !== session) return;
        session.expected_version = result.version;
        send({ kind: 'gw-comfy-save-result', request_id: event.data.request_id, ok: true, version: result.version });
        if (state.currentWorkflow?.workflow_id === session.workflow_id) await selectWorkflow(session.workflow_id);
      } catch (error) {
        if (state.comfyBridgeSession === session) {
          send({ kind: 'gw-comfy-save-result', request_id: event.data.request_id, ok: false });
          showToast(`ComfyUI 回存失败：${error.message}`, true);
        }
      } finally { session.saving = false; }
    }
  });
  // 左栏「工作流目录」、中栏「参数执行矩阵」与「本地 ComfyUI 诊断」三列统一折叠/展开事件绑定
  wfGet("btnCollapseLeftCol")?.addEventListener("click", (e) => {
    e.stopPropagation();
    toggleLeftColumnCollapse(true);
  });
  wfGet("btnExpandLeftCol")?.addEventListener("click", (e) => {
    e.stopPropagation();
    toggleLeftColumnCollapse(false);
  });
  wfGet("leftCollapsedRail")?.addEventListener("click", () => {
    toggleLeftColumnCollapse(false);
  });

  wfGet("btnCollapseCenterCol")?.addEventListener("click", (e) => {
    e.stopPropagation();
    toggleCenterColumnCollapse(true);
  });
  wfGet("btnExpandCenterCol")?.addEventListener("click", (e) => {
    e.stopPropagation();
    toggleCenterColumnCollapse(false);
  });
  wfGet("centerCollapsedRail")?.addEventListener("click", () => {
    toggleCenterColumnCollapse(false);
  });

  wfGet("envCollapsedRail")?.addEventListener("click", () => {
    setEnvDrawerOpen(true);
  });

  // 顶部工作流标签栏最左侧「✕ 全部关闭」按钮与二次确认弹窗事件
  const closeAllModal = wfGet("closeAllConfirmModal");
  wfGet("btnCloseAllWorkflows")?.addEventListener("click", () => {
    if (state.openWorkflowIds.length === 0) {
      showToast("当前顶部已无打开的工作流标签");
      return;
    }
    const countEl = wfGet("closeAllCountNum");
    if (countEl) {
      countEl.textContent = String(state.openWorkflowIds.length);
    }
    closeAllModal?.classList.add("open");
  });
  wfGet("btnCloseConfirmAllModal")?.addEventListener("click", () => {
    closeAllModal?.classList.remove("open");
  });
  wfGet("btnCancelCloseAll")?.addEventListener("click", () => {
    closeAllModal?.classList.remove("open");
  });
  wfGet("btnConfirmCloseAll")?.addEventListener("click", () => {
    closeAllModal?.classList.remove("open");
    closeAllOpenWorkflowTabs();
  });
  closeAllModal?.addEventListener("mousedown", (e) => {
    if (e.target === closeAllModal) {
      closeAllModal.classList.remove("open");
    }
  });

  // 切回工作台窗口时立即检查 ComfyUI 保存更新
  listen(window, "focus", () => {
    checkComfySavedSyncOnce();
  });

  // 工作流名称右键菜单（重命名 / 删除）事件绑定
  wfGet('ctxEditNativeWorkflow')?.addEventListener('click', async event => {
    event.stopPropagation();
    const workflowId = state.contextMenuWfId;
    closeWorkflowContextMenu();
    if (!workflowId) return;
    try {
      if (state.currentWorkflow?.workflow_id !== workflowId) await selectWorkflow(workflowId);
      await handleOpenInComfyUi();
    } catch (error) { showToast(`原生工作流打开失败：${error.message}`, true); }
  });
  wfGet("ctxRenameWf")?.addEventListener("click", (e) => {
    e.stopPropagation();
    if (state.contextMenuWfId) {
      startInlineRenameWorkflow(state.contextMenuWfId);
    }
  });
  wfGet("ctxDeleteWf")?.addEventListener("click", (e) => {
    e.stopPropagation();
    if (state.contextMenuWfId) {
      handleDeleteWorkflow(state.contextMenuWfId);
    }
  });
  listen(document, "click", (e) => {
    if (!e.target.closest("#wfContextMenu")) {
      closeWorkflowContextMenu();
    }
  });
  listen(window, "keydown", (e) => {
    if (e.key === "Escape") {
      closeWorkflowContextMenu();
    }
  });

  // 左侧工作流目录：新建文件夹内联栏控制
  const newFolderBar = wfGet("newFolderInlineBar");
  const inputNewFolder = wfGet("inputNewFolderName");
  wfGet("btnNewFolder")?.addEventListener("click", () => {
    if (!newFolderBar) return;
    const isHidden = newFolderBar.style.display === "none";
    newFolderBar.style.display = isHidden ? "flex" : "none";
    if (isHidden) inputNewFolder?.focus();
  });
  wfGet("btnConfirmNewFolder")?.addEventListener("click", handleCreateFolder);
  wfGet("btnCancelNewFolder")?.addEventListener("click", () => {
    if (newFolderBar) newFolderBar.style.display = "none";
    if (inputNewFolder) inputNewFolder.value = "";
  });
  inputNewFolder?.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      handleCreateFolder();
    } else if (e.key === "Escape") {
      if (newFolderBar) newFolderBar.style.display = "none";
    }
  });

  // 中栏上行：节点下拉选择器（选择空值时取消选中）
  wfGet("selectedNodeSelect")?.addEventListener("change", (e) => {
    const nid = e.target.value;
    selectCanvasNode(nid || null);
  });

  // 顶栏「🔗 画布对接契约」预览与导出弹窗
  const contractModal = wfGet("contractModal");
  wfGet("btnOpenContractModal")?.addEventListener("click", openContractModal);
  wfGet("btnCloseContractModal")?.addEventListener("click", () => contractModal?.classList.remove("open"));
  wfGet("btnCloseContractFooter")?.addEventListener("click", () => contractModal?.classList.remove("open"));
  wfGet("btnCopyContractJson")?.addEventListener("click", async () => {
    const text = wfGet("contractJsonPreview")?.textContent || "";
    try {
      await navigator.clipboard.writeText(text);
      showToast("已复制外部画布对接精简契约 JSON");
    } catch (_) {
      showToast("已选中契约文本，请按 Ctrl+C 复制");
    }
  });
  wfGet("btnDownloadContractJson")?.addEventListener("click", () => {
    if (!state.currentWorkflow) return;
    window.open(workflowExportUrl(state.currentWorkflow.workflow_id, "canvas_contract"), "_blank");
  });

  // 第三行左侧新增：「🖥️ 打开 ComfyUI」按钮 & 「⚡ 获取并解析」按钮
  wfGet("btnParseLink").addEventListener("click", () => handleParseLink());

  // 节点媒体文件隐藏上传控件 & 素材库弹窗事件
  wfGet("nodeMediaUploadInput")?.addEventListener("change", (e) => {
    const file = e.target.files?.[0];
    if (file) handleNodeMediaFileSelected(file);
    e.target.value = "";
  });

  const assetModal = wfGet("assetLibraryModal");
  wfGet("btnCloseAssetModal")?.addEventListener("click", () => assetModal?.classList.remove("open"));
  wfGet("btnCloseAssetFooter")?.addEventListener("click", () => assetModal?.classList.remove("open"));
  assetModal?.addEventListener("mousedown", (e) => {
    if (e.target === assetModal) assetModal.classList.remove("open");
  });
  const onUploadInAssetModal = () => {
    const target = state.pendingMediaTarget || { nodeId: state.selectedNodeId, fieldName: "image", mediaMode: "image" };
    triggerLocalMediaUploadForNode(target.nodeId, target.fieldName, target.mediaMode);
  };
  wfGet("btnUploadNewAssetInModal")?.addEventListener("click", onUploadInAssetModal);
  wfGet("btnUploadInAssetModal")?.addEventListener("click", onUploadInAssetModal);
  wfQueryAll("#assetLibraryModal [data-asset-filter], #assetLibraryModal [data-filter]").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.assetFilterType = btn.getAttribute("data-asset-filter") || btn.getAttribute("data-filter") || "all";
      wfQueryAll("#assetLibraryModal [data-asset-filter], #assetLibraryModal [data-filter]").forEach((b) => {
        b.classList.toggle("active", b === btn);
      });
      renderAssetLibraryGrid();
    });
  });

  // 第三行「📋 剪贴板」按钮与剪贴板选择弹窗控制
  const clipboardModal = wfGet("clipboardModal");
  wfGet("btnOpenClipboardModal")?.addEventListener("click", () => openClipboardModal());
  wfGet("btnCloseClipboardModal")?.addEventListener("click", () => clipboardModal?.classList.remove("open"));
  wfGet("btnCloseClipboardFooter")?.addEventListener("click", () => clipboardModal?.classList.remove("open"));
  for (const id of ['btnClosePublicWorkflowPreview', 'btnClosePublicWorkflowPreviewFooter']) {
    wfGet(id)?.addEventListener('click', () => wfGet('publicWorkflowPreviewModal')?.classList.remove('open'));
  }
  wfGet("btnRefreshClipboardList")?.addEventListener("click", () => renderClipboardModalList());
  clipboardModal?.addEventListener("mousedown", (e) => {
    if (e.target === clipboardModal) clipboardModal.classList.remove("open");
  });

  listen(window, "paste", (e) => {
    const activeTag = document.activeElement?.tagName?.toLowerCase();
    if (activeTag === "input" || activeTag === "textarea") return;
    const pasted = (e.clipboardData?.getData("text") || "").trim();
    if (!pasted) return;
    recordClipboardHistory(pasted);
    if (isLikelyRhLink(pasted)) {
      clipboardModal?.classList.remove("open");
      handleParseLink(pasted);
    } else if (clipboardModal?.classList.contains("open")) {
      renderClipboardModalList();
    }
  });

  wfGet("localFileInput").addEventListener("change", (e) => {
    const file = e.target.files?.[0];
    if (file) handleUploadFile(file);
    e.target.value = "";
  });

  // 本地 ComfyUI 诊断列折叠与展开（与前两列完全统一）
  wfGet("btnOpenEnvDrawer")?.addEventListener("click", (e) => {
    e.stopPropagation();
    setEnvDrawerOpen(true);
  });
  wfGet("btnCloseEnvDrawer")?.addEventListener("click", (e) => {
    e.stopPropagation();
    setEnvDrawerOpen(false);
  });

  const refreshDiffHandler = async (e) => {
    if (e) e.stopPropagation();
    setEnvDrawerOpen(true);
    if (!state.currentWorkflow) return;
    const workflowId = state.currentWorkflow.workflow_id;
    try {
      const wf = await apiFetch(
        `/api/god_workflow/compare-local/${encodeURIComponent(workflowId)}`,
        { method: "POST" }
      );
      // 诊断是独立报告，禁止把报告当工作流替换或改写画布。
      if (state.currentWorkflow?.workflow_id !== workflowId) return;
      state.currentWorkflow.env_diff = wf;
      renderEnvironmentDiff(state.currentWorkflow);
      showToast(wf.diagnostic_message || "诊断结果未知", wf.status !== "compared");
    } catch (err) {
      if (state.currentWorkflow?.workflow_id === workflowId) {
        state.currentWorkflow.env_diff = {status: "failed", diagnostic_message: err.message};
        renderEnvironmentDiff(state.currentWorkflow);
      }
      showToast(err.message, true);
    }
  };
  wfGet("btnRefreshDiff")?.addEventListener("click", refreshDiffHandler);
  wfGet("localComfyBadge")?.addEventListener("click", refreshDiffHandler);

  function nextWorkflowSeed() {
  if (globalThis.crypto?.getRandomValues) {
    const buffer = new Uint32Array(1);
    globalThis.crypto.getRandomValues(buffer);
    return (buffer[0] % 9000000000) + 100000;
  }
  const now = Date.now() % 9000000000;
  return now + 100000;
}


  wfGet("btnRandomizeSeeds")?.addEventListener("click", () => {
    const wf = state.currentWorkflow;
    if (!wf) return;
    const updates = [];
    for (const node of wf.nodes || []) {
      for (const w of node.widgets || []) {
        if (w.name.toLowerCase().includes("seed")) {
          const newSeed = nextWorkflowSeed();
          w.value = newSeed;
          updates.push({ node_id: node.node_id, field_name: w.name, value: newSeed });
        }
      }
    }
    for (const p of wf.actuator_params || []) {
      if (p.field_name.toLowerCase().includes("seed")) {
        const match = updates.find((u) => u.node_id === p.node_id && u.field_name === p.field_name);
        p.field_value = match ? match.value : nextWorkflowSeed();
      }
    }
    renderActuatorRack(wf);
    renderNodeCanvas(wf, false);
    if (updates.length > 0) {
      schedulePersistWorkflowParams(updates);
    }
    showToast(updates.length > 0 ? "已生成新的随机采样种子 (Seed)" : "当前工作流无可变 Seed 字段");
  });

  wfGet("btnTargetRH")?.addEventListener("click", () => setExecutionTarget("rh_cloud"));
  wfGet("btnTargetLocal")?.addEventListener("click", () => setExecutionTarget("local_comfy"));
  wfGet("btnExecuteWorkflow")?.addEventListener("click", handleExecuteWorkflow);

  wfGet("btnExportApiJson")?.addEventListener("click", () => {
    if (!state.currentWorkflow) return;
    window.open(workflowExportUrl(state.currentWorkflow.workflow_id, "api"), "_blank");
  });
  wfGet("btnExportNodeList")?.addEventListener("click", () => {
    if (!state.currentWorkflow) return;
    window.open(workflowExportUrl(state.currentWorkflow.workflow_id, "node_info_list"), "_blank");
  });

  wfGet("btnZoomIn")?.addEventListener("click", () => {
    state.zoom = Math.min(1.8, +(state.zoom + 0.1).toFixed(2));
    applyCanvasTransform();
  });
  wfGet("btnZoomOut")?.addEventListener("click", () => {
    state.zoom = Math.max(0.25, +(state.zoom - 0.1).toFixed(2));
    applyCanvasTransform();
  });
  wfGet("btnZoomFit")?.addEventListener("click", () => {
    autoFitCanvas(state.currentWorkflow);
  });
  wfGet("btnAutoLayout").addEventListener("click", autoLayoutCurrentNodes);

  const viewport = wfGet("canvasViewport");
  viewport.addEventListener("mousedown", (e) => {
    if (
      e.target.closest(".canvas-node") ||
      e.target.closest("#envDiffDrawer") ||
      e.target.closest("#btnOpenEnvDrawer")
    ) {
      return;
    }
    viewport.classList.add("dragging");
    const startClientX = e.clientX;
    const startClientY = e.clientY;
    const sx = e.clientX - state.panX;
    const sy = e.clientY - state.panY;
    const onMove = (ev) => {
      state.panX = ev.clientX - sx;
      state.panY = ev.clientY - sy;
      applyCanvasTransform();
    };
    const onUp = (ev) => {
      viewport.classList.remove("dragging");
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
      // 点击空白画布（未产生实质性平移拖拽）时，取消当前选中节点并停止连线流光
      if (Math.hypot(ev.clientX - startClientX, ev.clientY - startClientY) < 5) {
        if (state.selectedNodeId !== null) {
          selectCanvasNode(null);
        }
      }
    };
    listen(window, "mousemove", onMove);
    listen(window, "mouseup", onUp);
  });

  // 支持直接将本地任意 ComfyUI 工作流文件 (.json / .png / .webp) 拖拽到画布加载
  viewport.addEventListener("dragover", (e) => {
    if (e.dataTransfer?.types?.includes("Files")) {
      e.preventDefault();
    }
  });
  viewport.addEventListener("drop", (e) => {
    const file = e.dataTransfer?.files?.[0];
    if (file && /\.(json|png|webp)$/i.test(file.name)) {
      e.preventDefault();
      handleUploadFile(file);
    }
  });

  viewport.addEventListener(
    "wheel",
    (e) => {
      if (e.target.closest("#envDiffDrawer")) return;
      e.preventDefault();
      const delta = e.deltaY < 0 ? 0.08 : -0.08;
      state.zoom = Math.min(1.8, Math.max(0.25, +(state.zoom + delta).toFixed(2)));
      applyCanvasTransform();
    },
    { passive: false }
  );

  // 设置弹窗控制（现已移至左侧最底部）
  const settingsModal = wfGet("settingsModal");
  wfGet("btnOpenSettings")?.addEventListener("click", async () => {
    try {
      await loadSettingsState();
    } catch (err) {
      console.warn("加载设置状态失败:", err);
    }
    settingsModal?.classList.add("open");
  });
  wfGet("btnCloseSettings")?.addEventListener("click", () => settingsModal?.classList.remove("open"));
  wfGet("btnCancelSettings")?.addEventListener("click", () => settingsModal?.classList.remove("open"));
  wfGet("btnSaveSettings")?.addEventListener("click", async () => {
    const payload = {
      comfy_url: wfGet("inputComfyUrl")?.value?.trim() || "",
      comfy_root_dir: wfGet("inputComfyRootDir")?.value?.trim() || "",
      local_models_dir: wfGet("inputLocalModelsDir")?.value?.trim() || "",
    };
    for (const [field, inputId, clearId] of [
      ["rh_api_key", "inputRhApiKey", "clearRhApiKey"],
      ["rh_access_token", "inputRhAccessToken", "clearRhAccessToken"],
    ]) {
      const value = wfGet(inputId)?.value?.trim() || "";
      if (wfGet(clearId)?.checked) payload[`clear_${field}`] = true;
      else if (value) payload[field] = value;
    }

    try {
      const saved = await apiFetch("/api/god_workflow/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      applySavedSettings(saved);
      if (settingsModal) settingsModal._workflowSettingsSaved = true;
      settingsModal?.classList.remove("open");
      const credentialNote = saved?.has_rh_api_key || saved?.has_rh_access_token
        ? "凭据已生效"
        : "RH 凭据未配置，可在设置中填写";
      showToast(`配置已保存，${credentialNote}；正在刷新工作流与本地对比状态...`);
      if (state.currentWorkflow) {
        await refreshDiffHandler();
      }
    } catch (err) {
      showToast(err.message, true);
    }
  });

  // 本地目录选择交互与 Models 目录智能派生
  function deriveModelsDirFromRoot(rootVal) {
    if (!rootVal) return "";
    const cleanRoot = rootVal.trim().replace(/[\\/]+$/, "");
    if (!cleanRoot) return "";
    const sep = cleanRoot.includes("/") ? "/" : "\\";
    return `${cleanRoot}${sep}ComfyUI${sep}models`;
  }

  function handleRootDirInput() {
    const rootVal = wfGet("inputComfyRootDir")?.value?.trim() || "";
    const modelsInput = wfGet("inputLocalModelsDir");
    if (modelsInput && !modelsInput.value.trim() && rootVal) {
      modelsInput.value = deriveModelsDirFromRoot(rootVal);
    }
  }

  async function handlePickLocalDirectory(targetInputId, triggerBtnId) {
    const targetInput = wfGet(targetInputId);
    if (!targetInput) return;
    const triggerBtn = triggerBtnId ? wfGet(triggerBtnId) : null;
    const originalBtnText = triggerBtn ? triggerBtn.innerHTML : "";

    try {
      if (triggerBtn) {
        triggerBtn.innerHTML = "<span>⏳ 选择中...</span>";
        triggerBtn.disabled = true;
      }

      // 1. 优先调用后端原生 Windows 目录选择对话框，直接获取真实的绝对路径
      try {
        const title = targetInputId === "inputComfyRootDir"
          ? "选择本地 ComfyUI 安装根目录"
          : "选择本地 ComfyUI Models 目录路径";
        const res = await apiFetch("/api/god_workflow/browse_directory", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            initial_dir: targetInput.value.trim() || "",
            title: title
          })
        });
        if (res && res.selected_dir) {
          targetInput.value = res.selected_dir;
          showToast(`已选择目录：${res.selected_dir}`);
          if (targetInputId === "inputComfyRootDir") {
            handleRootDirInput();
          }
          return;
        } else if (res && res.status === "ok") {
          // 用户在原生对话框中点击了“取消”
          return;
        }
      } catch (backendErr) {
        console.warn("调用后端原生目录选择失败，转入浏览器前端降级方案:", backendErr);
      }

      // 2. 降级方案：尝试现代浏览器 window.showDirectoryPicker
      if (typeof window.showDirectoryPicker === "function") {
        try {
          const dirHandle = await window.showDirectoryPicker({ mode: "read" });
          if (dirHandle && dirHandle.name) {
            const currentVal = targetInput.value.trim();
            if (currentVal && (currentVal.includes("\\") || currentVal.includes("/"))) {
              const sep = currentVal.includes("\\") ? "\\" : "/";
              const parts = currentVal.split(/[/\\]/);
              parts[parts.length - 1] = dirHandle.name;
              targetInput.value = parts.join(sep);
            } else {
              targetInput.value = dirHandle.name;
            }
            showToast(`已选择目录：${dirHandle.name}（请核对并补全磁盘盘符绝对路径）`);
            if (targetInputId === "inputComfyRootDir") {
              handleRootDirInput();
            }
            return;
          }
        } catch (err) {
          if (err.name === "AbortError") return;
          console.warn("showDirectoryPicker 不可用或被取消:", err);
        }
      }

      // 3. 降级方案：创建临时 webkitdirectory 文件选择框
      const tempFileInput = document.createElement("input");
      tempFileInput.type = "file";
      tempFileInput.webkitdirectory = true;
      tempFileInput.style.display = "none";
      document.body.appendChild(tempFileInput);
      tempFileInput.addEventListener("change", () => {
        if (tempFileInput.files && tempFileInput.files.length > 0) {
          const file = tempFileInput.files[0];
          const relPath = file.webkitRelativePath || "";
          const dirName = relPath.split("/")[0] || file.name || "";
          if (dirName) {
            targetInput.value = dirName;
            showToast(`已选择目录：${dirName}（请核对磁盘盘符完整路径）`);
            if (targetInputId === "inputComfyRootDir") {
              handleRootDirInput();
            }
          }
        }
        document.body.removeChild(tempFileInput);
      }, { once: true });
    } catch (fallbackErr) {
      showToast("无法直接调起目录选择器，已为您聚焦输入框，请直接粘贴完整目录路径", true);
      try { targetInput.focus(); targetInput.select(); } catch (_) {}
    } finally {
      if (triggerBtn) {
        triggerBtn.innerHTML = originalBtnText;
        triggerBtn.disabled = false;
      }
    }
  }

  wfGet("btnBrowseComfyRootDir")?.addEventListener("click", () => handlePickLocalDirectory("inputComfyRootDir", "btnBrowseComfyRootDir"));
  wfGet("btnBrowseLocalModelsDir")?.addEventListener("click", () => handlePickLocalDirectory("inputLocalModelsDir", "btnBrowseLocalModelsDir"));
  wfGet("inputComfyRootDir")?.addEventListener("input", handleRootDirInput);
  wfGet("inputComfyRootDir")?.addEventListener("change", handleRootDirInput);

  const taskModal = wfGet("taskModal");
  wfGet("btnViewTaskDrawer")?.addEventListener("click", openWorkflowTasks);
  wfGet('workflowTaskSelect')?.addEventListener('change', event => {
    state.activeTaskId = event.target.value;
    if (state.activeTaskId) startTaskPolling(state.activeTaskId, event.target.selectedOptions[0]?.dataset.target);
  });
  wfGet('btnRefreshWorkflowTasks')?.addEventListener('click', openWorkflowTasks);
  wfGet('btnRetryTaskCollection')?.addEventListener('click', async event => {
    if (!state.activeTaskId) return;
    event.target.disabled = true;
    try {
      const result = await apiFetch(`/api/god_workflow/tasks/${encodeURIComponent(state.activeTaskId)}/collect`, { method: 'POST' });
      startTaskPolling(state.activeTaskId, result.target);
    } catch (error) { showToast(`产物回收失败：${error.message}`, true); }
    finally { event.target.disabled = false; }
  });
  wfGet("btnCloseTaskModal")?.addEventListener("click", () => taskModal?.classList.remove("open"));
  wfGet("btnCloseTaskFooter")?.addEventListener("click", () => taskModal?.classList.remove("open"));
  bindWorkflowOverlays();
}

function disposeWorkbench() {
  state.comfyBridgeSession = null;
  state.authLoading = false;
  clearInterval(state.comfySyncTimer);
  clearInterval(state.pollTimer);
  clearTimeout(state.paramSaveTimer);
  ++state.pollGeneration;
  for (const entry of state.parameterSaves?.values() || []) clearTimeout(entry.timer);
  lifecycleListeners.splice(0).forEach((remove) => remove());
  lifecycleAbortController?.abort();
  lifecycleAbortController = null;
  workbenchRoot = null;
}
function rebindWorkbench() { initWorkbench(); }
function resetWorkflowIdentity() {
  disposeWorkbench();
  state.workflows = []; state.openWorkflowIds = []; state.folders = [];
  state.currentWorkflow = null; state.selectedNodeId = null; state.activeTaskId = null;
  state.clipboardHistory = []; state.mediaAssets = []; state.mediaAssetsByFilename = {};
  state.parameterSaves?.clear(); state.collapsedFolders?.clear(); state.syncRevisions = {};
  state.submissionAttempt = null; state.pendingMediaTarget = null; state._openTabsInitialized = false;
  state.authPrincipalKey = null;
  workbenchRoot = document.getElementById?.('workflowWorkbench') || null;
  if (workbenchRoot) {
    wfQueryAll('.modal-backdrop.open').forEach(modal => modal.classList.remove('open'));
    for (const id of ['taskModalBody', 'assetLibraryGrid', 'clipboardListContainer', 'publicWorkflowPreviewBody']) { const element = wfGet(id); if (element) element.innerHTML = ''; }
    clearEmptyWorkbenchView();
    renderHeaderTelemetry({ name: '--', workflow_id: '--', nodes: [], connections: [], env_diff: {} });
  }
}
window.WorkbenchWorkflow = Object.freeze({ init: rebindWorkbench, rebind: rebindWorkbench, dispose: disposeWorkbench });
window.addEventListener('gw-auth-state', event => {
  if (event.detail?.authenticated === true) {
    const principal = event.detail.principal || {};
    const key = JSON.stringify([principal.identity_domain || event.detail.auth_mode, principal.subject || principal.user_id || principal.username]);
    if (state.authPrincipalKey && state.authPrincipalKey !== key) resetWorkflowIdentity();
    state.authPrincipalKey = key;
    rebindWorkbench();
  } else resetWorkflowIdentity();
});
if (document.readyState === "loading") window.addEventListener("DOMContentLoaded", rebindWorkbench, { once: true }); else rebindWorkbench();

