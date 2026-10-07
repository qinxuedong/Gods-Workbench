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
    lifecycleListeners.push(() => target?.removeEventListener(type, handler, options));
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
  IMAGE: "#10b981",
  VIDEO: "#dfc384",
  AUDIO: "#38bdf8",
  LATENT: "#c084fc",
  VAE: "#f43f5e",
  ANY: "#dfc384",
};

function loadLocalClipboardHistory() {
  try {
    const raw = localStorage.getItem(CLIPBOARD_STORAGE_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed.filter((x) => typeof x === "string" && x.trim()) : [];
  } catch (_) {
    return [];
  }
}

function recordClipboardHistory(text) {
  const clean = String(text || "").trim();
  if (!clean) return;
  state.clipboardHistory = [clean, ...state.clipboardHistory.filter((item) => item !== clean)].slice(0, 15);
  try {
    localStorage.setItem(CLIPBOARD_STORAGE_KEY, JSON.stringify(state.clipboardHistory));
  } catch (_) {}
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
  banner._timer = setTimeout(() => banner.classList.remove("show"), actionLabel ? 6000 : 3600);
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
  const nodes = value.nodes.map((node, index) => {
    const geometry = collapseNodePosition(node, index, value.nodes.length);
    const inputs = node.inputs && typeof node.inputs === 'object' ? node.inputs : {};
    const widgets = Array.isArray(node.widgets) ? node.widgets : Object.entries(inputs).map(([name, v]) => ({ name, value: v, value_type: typeof v === 'number' ? (Number.isInteger(v) ? 'int' : 'float') : typeof v === 'boolean' ? 'bool' : 'string' }));
    return { ...node, node_id: String(node.node_id ?? node.id ?? index + 1), id: String(node.id ?? node.node_id ?? index + 1), class_type: node.class_type || node.kind || 'Node', title: node.title || node.name || node.kind || 'Node', x: geometry.x, y: geometry.y, width: geometry.width, height: geometry.height, category: node.category || 'node', widgets };
  });
  const links = value.links || value.connections || [];
  const actuatorParams = Array.isArray(value.actuator_params)
    ? value.actuator_params
    : deriveActuatorParamsFromWidgets(nodes);
  return { ...value, workflow_id: value.workflow_id || raw?.workflow_id, nodes, links: links.map((link, i) => ({ ...link, from_node: String(link.from_node ?? link.source), to_node: String(link.to_node ?? link.target), from_slot: Number(link.from_slot ?? link.source_slot ?? 0), to_slot: Number(link.to_slot ?? link.target_slot ?? 0), id: link.id || String(i) })), actuator_params: actuatorParams, env_diff: value.env_diff || {}, name: value.name || 'workflow' };
}

/**
 * 提取列表以后端持久化的 widget.promoted 为准；缺省时从节点属性推导。
 */
function deriveActuatorParamsFromWidgets(nodes) {
  const params = [];
  for (const node of nodes || []) {
    for (const w of node.widgets || []) {
      if (!w || !w.promoted) continue;
      params.push({
        node_id: String(node.node_id),
        field_name: String(w.name),
        field_value: w.value,
        label: w.label || `${node.title} · ${w.name}`,
        value_type: w.value_type || w.field_type,
        min_val: w.min_val,
        max_val: w.max_val,
        step: w.step,
        options: w.options,
      });
    }
  }
  return params;
}

async function apiFetch(url, options = {}) {
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
    requestOptions.body = JSON.stringify({ folder_id: body.folder_id, payload: normalizeMatureWorkflow(state.currentWorkflow), expected_version: body.expected_version ?? currentRevision() });
  } else if (/\/item\/[^/]+\/params$/.test(url) && options.method === 'PUT' && options.body) {
    const body = JSON.parse(options.body);
    requestOptions.body = JSON.stringify({ widget_updates: body.widget_updates || [], expected_version: body.expected_version ?? currentRevision() });
  } else if (/\/item\/[^/]+$/.test(url) && options.method === 'PUT' && options.body) {
    const body = JSON.parse(options.body);
    requestOptions.body = JSON.stringify({ payload: { ...normalizeMatureWorkflow(body.workflow || body), nodes: (body.workflow?.nodes || body.nodes || []).map(toBackendNode) }, expected_version: body.expected_version ?? currentRevision(), source: body.source || 'canvas' });
  }
  const resp = await fetch(targetUrl, requestOptions);
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    const detail = data?.detail; const msg = detail?.message || detail || data?.message || `请求失败 (${resp.status})`; throw new Error(msg);
  }
  if (/\/export\/[^/]+$/.test(url)) return data.workflow || data;
  if (/\/auto-layout\/[^/]+$/.test(url)) return normalizeMatureWorkflow(data);
  if (/\/compare-local\/[^/]+$/.test(url)) return normalizeMatureWorkflow(data);
  if (url.endsWith('/list')) return { items: data.document_summaries || [], folders: [] };
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
  workbenchRoot = document.getElementById("workflowWorkbench");
  if (!workbenchRoot) { disposeWorkbench(); return; }
  if (lifecycleAbortController) return;
  lifecycleAbortController = new AbortController();
  applyColumnCollapseState();
  bindEvents();
  await loadSettingsState();
  await refreshMediaAssetsCache();
  await refreshWorkflowList();

  if (state.workflows.length > 0) {
    await selectWorkflow(state.workflows[0].workflow_id);
  } else {
    await handleParseLink("https://www.runninghub.cn/workflow/2104915887789797378");
  }
  startComfySyncWatcher();
}

async function loadSettingsState() {
  try {
    const s = await apiFetch("/api/god_workflow/settings");
    wfGet("inputComfyUrl").value = s.comfy_url || "http://127.0.0.1:8188";
    const rootInput = wfGet("inputComfyRootDir");
    if (rootInput) rootInput.value = s.comfy_root_dir || "";
    wfGet("inputLocalModelsDir").value = s.local_models_dir || "";
    wfGet("maskedApiKeyHint").textContent = s.has_rh_api_key
      ? `(已配置: ${s.rh_api_key_masked})`
      : "(未配置)";
    wfGet("maskedTokenHint").textContent = s.has_rh_access_token
      ? `(已配置: ${s.rh_access_token_masked})`
      : "(未配置)";
    if (s.comfy_url_active) {
      wfGet("inputComfyUrl").value = s.comfy_url_active;
    }
    state.providerStatus = s.provider_status || {};
  } catch (err) {
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
    const res = await apiFetch("/api/god_workflow/list");
    state.workflows = res.items || [];
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
    selContainer.innerHTML = `<div style="padding:18px 8px;text-align:center;color:rgba(255,255,255,0.65);font-size:10.5px;">请从左侧『工作流目录』点击打开任意工作流。</div>`;
  }
  if (actContainer) {
    actContainer.innerHTML = `<div style="padding:18px 8px;text-align:center;color:rgba(255,255,255,0.65);font-size:10.5px;">暂无激活工作流。</div>`;
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
    const updated = await apiFetch(`/api/god_workflow/item/${encodeURIComponent(workflowId)}/name`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: clean }),
    });
    state.renamingWorkflowId = null;
    if (state.currentWorkflow?.workflow_id === workflowId) {
      state.currentWorkflow.name = updated.name;
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
    await apiFetch(`/api/god_workflow/item/${encodeURIComponent(workflowId)}`, {
      method: "DELETE",
    });
    state.openWorkflowIds = state.openWorkflowIds.filter((id) => id !== workflowId);
    if (state.renamingWorkflowId === workflowId) {
      state.renamingWorkflowId = null;
    }
    await refreshWorkflowList();
    if (state.currentWorkflow?.workflow_id === workflowId) {
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
    const updatedWf = await apiFetch(`/api/god_workflow/item/${encodeURIComponent(workflowId)}/folder`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ folder_id: targetFolderId }),
    });
    state.collapsedFolders.delete(targetFolderId);
    if (state.currentWorkflow?.workflow_id === workflowId) {
      state.currentWorkflow.folder_id = updatedWf.folder_id;
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
        <button type="button" class="hw-btn hw-btn-gold" style="padding:3px 9px;font-size:10px;">
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
  if (!wf) {
    showToast("请先选择或解析一个工作流", true);
    return;
  }

  const btn = wfGet("btnOpenComfyUi");
  const origHtml = btn ? btn.innerHTML : "";
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = `<span>⏳ 正在匹配并打开...</span>`;
  }

  showToast(`正在同步固定 ID 并匹配 ComfyUI 工作流「${wf.name}」...`);

  try {
    const res = await apiFetch(
      `/api/god_workflow/open-in-comfy/${encodeURIComponent(wf.workflow_id)}`,
      { method: "POST" }
    );
    const openUrl = res.open_url || "http://127.0.0.1:8188";
    const hasLiveTab = Boolean(state.comfyWindowRef && !state.comfyWindowRef.closed);

    let win = null;
    if (res.bridge_connected && hasLiveTab) {
      // ComfyUI 窗口已打开且桥接心跳在线：后端已下发页内切换指令，直接聚焦原窗口即可，无需重载页面
      try {
        state.comfyWindowRef.focus();
      } catch (_) {}
      win = state.comfyWindowRef;
    } else {
      // 复用固定命名窗口 RH_Auto_ComfyUI_Tab，避免多次点击弹出多个 ComfyUI 标签页
      win = window.open(openUrl, "RH_Auto_ComfyUI_Tab");
      if (win) {
        state.comfyWindowRef = win;
        try {
          win.focus();
        } catch (_) {}
      }
    }

    // 刷新当前工作流状态（更新顶部 LOCAL COMFY: ONLINE 指示灯及同步修订号）
    try {
      const refreshedWf = await apiFetch(`/api/god_workflow/item/${encodeURIComponent(wf.workflow_id)}`);
      setCurrentWorkflow(normalizeMatureWorkflow(refreshedWf), { autoFit: false });
    } catch (_) {}

    const statusPrefix = res.launched
      ? "已在后台静默启动本地 ComfyUI，"
      : res.bridge_connected && hasLiveTab
      ? "已通过固定 ID 在已打开的 ComfyUI 中自动匹配切换至"
      : "本地 ComfyUI 已在线，已自动匹配打开";
    showToast(
      `${statusPrefix}工作流「${wf.name}」`,
      false,
      win ? "" : "🖥️ 点击打开 ComfyUI",
      win
        ? null
        : () => {
            state.comfyWindowRef = window.open(openUrl, "RH_Auto_ComfyUI_Tab");
          }
    );
  } catch (err) {
    showToast(`打开 ComfyUI 失败: ${err.message}`, true);
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = origHtml;
    }
  }
}

/**
 * 监听 ComfyUI 端的保存事件与磁盘修改时间，当用户在 ComfyUI 中修改保存后自动同步回工作台
 */
async function checkComfySavedSyncOnce() {
  try {
    const res = await apiFetch("/api/god_workflow/comfy-bridge/sync-status");
    const revisions = res?.revisions || {};
    const syncedNow = new Set(res?.synced_now || []);

    let currentUpdated = false;
    let updatedWfId = null;

    for (const [wfId, revNum] of Object.entries(revisions)) {
      const rev = Number(revNum || 0);
      const prev = state.syncRevisions[wfId];
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
      if (currentUpdated && state.currentWorkflow?.workflow_id) {
        const latestWf = await apiFetch(
          `/api/god_workflow/item/${encodeURIComponent(state.currentWorkflow.workflow_id)}`
        );
        setCurrentWorkflow(latestWf, { autoFit: false });
        showToast(
          `🔄 已自动同步 ComfyUI 保存的最新修改：「${latestWf.name}」(#${latestWf.workflow_id})`
        );
      }
    }
  } catch (_) {}
}

function startComfySyncWatcher() {
  clearInterval(state.comfySyncTimer);
  checkComfySavedSyncOnce();
  state.comfySyncTimer = setInterval(checkComfySavedSyncOnce, 2500);
}

/* ==================== 2. 渲染状态同步与左侧工作流目录树 ==================== */

function setCurrentWorkflow(wf, options = {}) {
  state.currentWorkflow = wf;
  if (wf.workflow_id) {
    state.syncRevisions[wf.workflow_id] = Number(wf.sync_revision || 0);
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

  const recTarget = wf.env_diff?.recommended_target || "rh_cloud";
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
    comfyDot.className = "led-dot led-emerald";
    if (drawerTabDot) drawerTabDot.className = diff.can_run_locally ? "led-dot led-emerald" : "led-dot led-amber";
    comfyText.textContent = diff.device_name ? `ONLINE (${diff.device_name})` : "ONLINE";
    vramText.textContent = diff.vram_summary || "ONLINE";
  } else {
    comfyDot.className = "led-dot led-amber";
    if (drawerTabDot) drawerTabDot.className = "led-dot led-amber";
    comfyText.textContent = "AUTO-START READY";
    vramText.textContent = "STANDBY";
  }

  const modeLabels = {
    full_canvas: "FULL CANVAS JSON",
    api_prompt: "API PROMPT + DAG",
    public_metadata_only: "PUBLIC METADATA PREVIEW",
  };
  compTag.textContent = modeLabels[wf.data_completeness] || wf.data_completeness;

  wfGet("currentWorkflowSubId").textContent = `${wf.name} · #${wf.workflow_id}`;
  wfGet("statNodesCount").textContent = String(wf.nodes?.length || 0);
  wfGet("statWiresCount").textContent = String(wf.links?.length || 0);
  wfGet("statParseOverhead").textContent = `${wf.parse_overhead_ms || 0}ms`;

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
  if (sourceType === "liblib_link") return "LIB";
  if (sourceType === "local_file") return "LOCAL";
  if (sourceType === "plugin_ingest") return "PLUG";
  return "RH";
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

  if (openItems.length === 0) {
    runway.innerHTML = `<span style="font-size:10px;color:rgba(255,255,255,0.55);font-family:var(--font-mono);padding-left:4px;">暂无已打开工作流（请从左侧目录点击打开）</span>`;
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

  // 2. 左侧多文件夹分类工作流目录树（右侧展示紧凑来源标记 RH / LOCAL / LIB，支持右键重命名/删除与拖拽跨文件夹归类）
  const folders =
    state.folders.length > 0
      ? state.folders
      : [{ folder_id: "parsed_default", name: "已解析工作流", icon: "📥", is_system: true }];

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
              style="padding:2px 5px;font-size:10.5px;flex:1;"
            />
            <button type="button" class="hw-btn hw-btn-gold btn-save-rename" data-save-rename="${escapeHtml(fid)}" style="padding:1px 6px;font-size:9px;">✓</button>
            <button type="button" class="hw-btn btn-cancel-rename" data-cancel-rename="${escapeHtml(fid)}" style="padding:1px 5px;font-size:9px;">✕</button>
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
              <span style="font-size:11px;">${escapeHtml(folder.icon || "📁")}</span>
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
                const srcBadge = getSourceBadgeText(item.source_type);
                if (isRenamingWf) {
                  return `
                    <div class="wf-tree-item active" data-wf-rename-row="${escapeHtml(item.workflow_id)}" style="gap:4px;">
                      <input
                        type="text"
                        class="param-input"
                        data-rename-wf-input="${escapeHtml(item.workflow_id)}"
                        value="${escapeHtml(item.name)}"
                        style="padding:2px 5px;font-size:10.5px;flex:1;min-width:0;"
                      />
                      <button type="button" class="hw-btn hw-btn-gold" data-save-rename-wf="${escapeHtml(item.workflow_id)}" style="padding:1px 6px;font-size:9px;">✓</button>
                      <button type="button" class="hw-btn" data-cancel-rename-wf="${escapeHtml(item.workflow_id)}" style="padding:1px 5px;font-size:9px;">✕</button>
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
                      <span class="wf-tree-idx" style="font-family:var(--font-mono);font-size:9px;">${String(
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
  const totalN = diff.total_nodes_required || 0;
  const instN = diff.installed_nodes_count || 0;
  const totalM = diff.total_models_required || 0;
  const instM = diff.installed_models_count || 0;

  wfGet("diffNodeRatio").textContent = `${instN} / ${totalN}`;
  wfGet("diffModelRatio").textContent = `${instM} / ${totalM}`;

  const nPct = totalN > 0 ? Math.round((instN / totalN) * 100) : 100;
  const mPct = totalM > 0 ? Math.round((instM / totalM) * 100) : 100;

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

  if (cardsHtml.length === 0) {
    cardsHtml.push(`
      <div class="missing-item-card ready-ok">
        <div class="missing-item-head">
          <span style="font-weight:700;">✓ 本地环境 100% 齐备</span>
        </div>
        <div style="font-size:10px;">所有节点与模型均已在本地就绪，可直接本地运行！</div>
      </div>
    `);
  }

  container.innerHTML = cardsHtml.join("");
  wfGet("diffSummaryText").textContent = diff.diagnostic_message || "";
  wfGet("recommendedTargetHint").textContent =
    diff.recommended_target === "local_comfy" ? "推荐: 本地 ComfyUI 运行" : "推荐: RH 云端免装运行";
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
      <div style="padding:18px 8px;text-align:center;color:rgba(255,255,255,0.65);font-size:10.5px;">
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
    const inNames = (node.inputs || []).map((p) => p.name).join(", ") || "无";
    const outNames = (node.outputs || []).map((p) => p.name).join(", ") || "无";
    container.innerHTML = `
      ${metaBarHtml}
      <div class="hw-bay-row" style="padding:10px;font-size:10.5px;line-height:1.5;">
        <div>该节点为纯信号路由/连接节点，无内部标量输入属性 (widgets)。</div>
        <div style="font-family:var(--font-mono);font-size:9.5px;opacity:0.85;">
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

  const params = wf.actuator_params || [];
  if (countBadge) {
    countBadge.textContent = `${params.length} 项已提取`;
  }

  if (params.length === 0) {
    container.innerHTML = `
      <div style="padding:18px 8px;text-align:center;color:rgba(255,255,255,0.65);font-size:10.5px;">
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

      // 同步上下两行中所有绑定了相同 attrKey 的其余输入控件
      wfQueryAll(`[data-widget-control="${CSS.escape(attrKey)}"]`).forEach((peer) => {
        if (peer !== inputEl) peer.value = String(newVal);
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
  const nodeObj = (wf.nodes || []).find((n) => n.node_id === nodeId);
  if (!nodeObj) return;
  const wObj = (nodeObj.widgets || []).find((w) => w.name === fieldName);
  if (!wObj) return;

  const nextPromoted = !wObj.promoted;
  wObj.promoted = nextPromoted;

  try {
    const updatedWf = await apiFetch(
      `/api/god_workflow/item/${encodeURIComponent(wf.workflow_id)}/params`,
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          widget_updates: [
            {
              node_id: nodeId,
              field_name: fieldName,
              value: wObj.value,
              promoted: nextPromoted,
            },
          ],
        }),
      }
    );
    state.currentWorkflow = updatedWf;
    renderActuatorRack(updatedWf);
    renderNodeCanvas(updatedWf, false);
    showToast(
      nextPromoted
        ? `已将「#${nodeId} ${wObj.label || fieldName}」提升至工作流提取列表`
        : `已从工作流提取列表移除「#${nodeId} ${wObj.label || fieldName}」`
    );
  } catch (err) {
    showToast(err.message, true);
  }
}

function schedulePersistWorkflowParams(widgetUpdates) {
  const wf = state.currentWorkflow;
  if (!wf) return;
  clearTimeout(state.paramSaveTimer);
  state.paramSaveTimer = setTimeout(async () => {
    try {
      await apiFetch(`/api/god_workflow/item/${encodeURIComponent(wf.workflow_id)}/params`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ widget_updates: widgetUpdates }),
      });
    } catch (err) {
      console.warn("自动保存工作流参数失败:", err);
    }
  }, 280);
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
        style="display:flex;align-items:center;justify-content:center;font-size:18px;color:var(--text-muted);"
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
    { node_id: String(nodeId), field_name: actualFieldName, value: asset.filename },
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
    const res = await fetch("/api/god_workflow/assets/upload", {
      method: "POST",
      body: form,
    });
    if (!res.ok) {
      let msg = `HTTP ${res.status}`;
      try {
        const errData = await res.json();
        msg = errData.detail || msg;
      } catch (_) {}
      throw new Error(msg);
    }
    const asset = await res.json();
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
      <div style="grid-column: 1 / -1; padding: 28px 12px; text-align: center; color: var(--text-muted); font-size: 11px;">
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

      const inPortsHtml = (node.inputs || [])
        .map((inp) => {
          const color = PORT_COLORS[inp.data_type] || "#dfc384";
          return `
            <div class="port-item">
              <span class="port-dot" style="background:${color};"></span>
              <span>${escapeHtml(inp.name)}</span>
            </div>
          `;
        })
        .join("");

      const outPortsHtml = (node.outputs || [])
        .map((outp) => {
          const color = PORT_COLORS[outp.data_type] || "#dfc384";
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

  const nodeMap = {};
  for (const n of wf.nodes || []) {
    nodeMap[n.node_id] = n;
  }

  const selId = state.selectedNodeId ? String(state.selectedNodeId) : null;
  const pathsHtml = [];

  for (const lk of wf.links || []) {
    const src = nodeMap[lk.from_node];
    const dst = nodeMap[lk.to_node];
    if (!src || !dst) continue;

    const srcWidth = isMediaUploadNode(src) ? Math.max(Number(src.width) || 195, 200) : Number(src.width) || 195;
    const x1 = src.x + srcWidth;
    const y1 = src.y + 38 + lk.from_slot * 17;
    const x2 = dst.x;
    const y2 = dst.y + 38 + lk.to_slot * 17;

    const dx = Math.max(55, Math.abs(x2 - x1) * 0.45);
    // 路径方向固定为 src (from_node) -> dst (to_node)
    const d = `M ${x1} ${y1} C ${x1 + dx} ${y1}, ${x2 - dx} ${y2}, ${x2} ${y2}`;

    const gradId = PORT_COLORS[lk.data_type]
      ? `grad${
          lk.data_type === "VIDEO" || lk.data_type === "ANY"
            ? "MODEL"
            : lk.data_type === "AUDIO"
            ? "CLIP"
            : lk.data_type
        }`
      : "gradMODEL";
    const dotColor = PORT_COLORS[lk.data_type] || "#dfc384";

    const isIncomingToSelected = selId !== null && String(lk.to_node) === selId;
    const isOutgoingFromSelected = selId !== null && String(lk.from_node) === selId;
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
      <path d="${d}" fill="none" stroke="url(#${gradId})" stroke-width="1" opacity="${baseOpacity}" />
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
  };

  window.addEventListener("mousemove", onMove);
  window.addEventListener("mouseup", onUp);
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
    const updatedWf = await apiFetch(
      `/api/god_workflow/auto-layout/${encodeURIComponent(wf.workflow_id)}`,
      { method: "POST" }
    );
    state.currentWorkflow = updatedWf;
    renderNodeCanvas(updatedWf, true);
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

async function handleExecuteWorkflow() {
  const wf = state.currentWorkflow;
  if (!wf) {
    showToast("请先加载或解析一个工作流", true);
    return;
  }

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
      <div style="color:var(--primary);font-family:var(--font-mono);font-weight:700;">
        ⏳ 正在向 ${state.executionTarget === "rh_cloud" ? "RunningHub 云端算力集群" : "本地 ComfyUI 引擎（若未启动将自动后台静默拉起）"} 提交任务...
      </div>
      <div style="font-size:11px;color:var(--text-muted);">
        工作流: ${escapeHtml(wf.name)} (#${escapeHtml(wf.workflow_id)}) · 提取参数覆盖项: ${nodeInfoList.length} 项
      </div>
    </div>
  `;

  try {
    const res = await apiFetch("/api/god_workflow/execute", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        workflow_id: wf.workflow_id,
        target: state.executionTarget,
        node_info_list: nodeInfoList,
      }),
    });
    state.activeTaskId = res.task_id;
    startTaskPolling(res.task_id, res.target);
  } catch (err) {
    bodyEl.innerHTML = `
      <div class="missing-item-card">
        <div style="color:var(--rose);font-weight:700;font-family:var(--font-mono);">❌ 任务提交失败</div>
        <div style="color:var(--primary-soft);font-size:11.5px;margin-top:4px;">${escapeHtml(err.message)}</div>
      </div>
    `;
  }
}

function startTaskPolling(taskId, target) {
  clearInterval(state.pollTimer);
  const bodyEl = wfGet("taskModalBody");

  const pollOnce = async () => {
    try {
      const statusRes = await apiFetch(`/api/god_workflow/tasks/${encodeURIComponent(taskId)}`);
      const st = statusRes.status || "RUNNING";
      const outputs = statusRes.outputs || [];

      if (st === "SUCCESS") {
        clearInterval(state.pollTimer);
        const mediaHtml = outputs
          .map((item) => {
            const url = item.fileUrl || item.url || "";
            return `
              <div class="hw-bay-row" style="align-items:center;">
                <img src="${escapeHtml(url)}" alt="Generated Output" style="max-width:100%;max-height:320px;border-radius:4px;border:1px solid var(--border-gold);" />
                <a class="mini-link-btn" href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">在新窗口打开原文件</a>
              </div>
            `;
          })
          .join("");
        bodyEl.innerHTML = `
          <div class="missing-item-card ready-ok">
            <div style="color:var(--emerald-bright);font-weight:700;font-family:var(--font-mono);">✓ 任务执行完成 (Task ID: ${escapeHtml(taskId)})</div>
          </div>
          ${mediaHtml || '<div style="color:var(--text-muted);">任务已完成，无媒体输出文件返回。</div>'}
        `;
      } else if (st === "FAILED" || st === "ERROR") {
        clearInterval(state.pollTimer);
        bodyEl.innerHTML = `
          <div class="missing-item-card">
            <div style="color:var(--rose);font-weight:700;">❌ 任务执行中断 (${escapeHtml(st)})</div>
            <div style="font-size:11px;color:var(--text-muted);">${escapeHtml(statusRes.message || "")}</div>
          </div>
        `;
      } else {
        bodyEl.innerHTML = `
          <div class="hw-bay-row">
            <div style="color:var(--compute);font-family:var(--font-mono);font-weight:700;">
              🔄 任务正在执行中 (${escapeHtml(target === "rh_cloud" ? "RunningHub Cloud" : "Local ComfyUI")})...
            </div>
            <div style="font-family:var(--font-mono);font-size:10.5px;color:var(--text-muted);">
              TASK ID: ${escapeHtml(taskId)} · 状态: ${escapeHtml(st)}
            </div>
          </div>
        `;
      }
    } catch (err) {
      clearInterval(state.pollTimer);
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

function bindEvents() {
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
  window.addEventListener("focus", () => {
    checkComfySavedSyncOnce();
  });

  // 工作流名称右键菜单（重命名 / 删除）事件绑定
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
  document.addEventListener("click", (e) => {
    if (!e.target.closest("#wfContextMenu")) {
      closeWorkflowContextMenu();
    }
  });
  window.addEventListener("keydown", (e) => {
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
  wfGet("btnOpenComfyUi")?.addEventListener("click", handleOpenInComfyUi);
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
  wfGet("btnRefreshClipboardList")?.addEventListener("click", () => renderClipboardModalList());
  clipboardModal?.addEventListener("mousedown", (e) => {
    if (e.target === clipboardModal) clipboardModal.classList.remove("open");
  });

  window.addEventListener("paste", (e) => {
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

  wfGet("btnReloadDemo").addEventListener("click", () => {
    const demoUrl = "https://www.runninghub.cn/workflow/2104915887789797378";
    handleParseLink(demoUrl);
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
    try {
      const wf = await apiFetch(
        `/api/god_workflow/compare-local/${encodeURIComponent(state.currentWorkflow.workflow_id)}`,
        { method: "POST" }
      );
      setCurrentWorkflow(normalizeMatureWorkflow(wf));
      showToast("已刷新本地 ComfyUI 节点与模型对比结果");
    } catch (err) {
      showToast(err.message, true);
    }
  };
  wfGet("btnRefreshDiff").addEventListener("click", refreshDiffHandler);
  wfGet("localComfyBadge").addEventListener("click", refreshDiffHandler);

  function nextWorkflowSeed() {
  if (globalThis.crypto?.getRandomValues) {
    const buffer = new Uint32Array(1);
    globalThis.crypto.getRandomValues(buffer);
    return (buffer[0] % 9000000000) + 100000;
  }
  const now = Date.now() % 9000000000;
  return now + 100000;
}


  wfGet("btnRandomizeSeeds").addEventListener("click", () => {
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

  wfGet("btnTargetRH").addEventListener("click", () => setExecutionTarget("rh_cloud"));
  wfGet("btnTargetLocal").addEventListener("click", () => setExecutionTarget("local_comfy"));
  wfGet("btnExecuteWorkflow").addEventListener("click", handleExecuteWorkflow);

  wfGet("btnExportApiJson").addEventListener("click", () => {
    if (!state.currentWorkflow) return;
    window.open(workflowExportUrl(state.currentWorkflow.workflow_id, "api"), "_blank");
  });
  wfGet("btnExportNodeList").addEventListener("click", () => {
    if (!state.currentWorkflow) return;
    window.open(workflowExportUrl(state.currentWorkflow.workflow_id, "node_info_list"), "_blank");
  });

  wfGet("btnZoomIn").addEventListener("click", () => {
    state.zoom = Math.min(1.8, +(state.zoom + 0.1).toFixed(2));
    applyCanvasTransform();
  });
  wfGet("btnZoomOut").addEventListener("click", () => {
    state.zoom = Math.max(0.25, +(state.zoom - 0.1).toFixed(2));
    applyCanvasTransform();
  });
  wfGet("btnZoomFit").addEventListener("click", () => {
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
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
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
  wfGet("btnOpenSettings").addEventListener("click", async () => {
    await loadSettingsState();
    settingsModal.classList.add("open");
  });
  wfGet("btnCloseSettings").addEventListener("click", () => settingsModal.classList.remove("open"));
  wfGet("btnCancelSettings").addEventListener("click", () => settingsModal.classList.remove("open"));
  wfGet("btnSaveSettings").addEventListener("click", async () => {
    const payload = {
      comfy_url: wfGet("inputComfyUrl").value.trim(),
      comfy_root_dir: wfGet("inputComfyRootDir")?.value?.trim() || "",
      local_models_dir: wfGet("inputLocalModelsDir").value.trim(),
    };
    const apiKeyVal = wfGet("inputRhApiKey").value.trim();
    const tokenVal = wfGet("inputRhAccessToken").value.trim();
    if (apiKeyVal) payload.rh_api_key = apiKeyVal;
    if (tokenVal) payload.rh_access_token = tokenVal;

    try {
      await apiFetch("/api/god_workflow/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      wfGet("inputRhApiKey").value = "";
      wfGet("inputRhAccessToken").value = "";
      settingsModal.classList.remove("open");
      showToast("配置已保存，正在刷新工作流与本地对比状态...");
      if (state.currentWorkflow?.source_url) {
        await handleParseLink(state.currentWorkflow.source_url);
      } else if (state.currentWorkflow) {
        await refreshDiffHandler();
      }
    } catch (err) {
      showToast(err.message, true);
    }
  });

  const taskModal = wfGet("taskModal");
  wfGet("btnViewTaskDrawer").addEventListener("click", () => taskModal.classList.add("open"));
  wfGet("btnCloseTaskModal").addEventListener("click", () => taskModal.classList.remove("open"));
  wfGet("btnCloseTaskFooter").addEventListener("click", () => taskModal.classList.remove("open"));
}

function disposeWorkbench() {
  clearInterval(state.comfySyncTimer);
  clearTimeout(state.paramSaveTimer);
  lifecycleListeners.splice(0).forEach((remove) => remove());
  lifecycleAbortController?.abort();
  lifecycleAbortController = null;
  workbenchRoot = null;
}
function rebindWorkbench() { initWorkbench(); }
window.WorkbenchWorkflow = Object.freeze({ init: rebindWorkbench, rebind: rebindWorkbench, dispose: disposeWorkbench });
if (document.readyState === "loading") window.addEventListener("DOMContentLoaded", rebindWorkbench, { once: true }); else rebindWorkbench();

