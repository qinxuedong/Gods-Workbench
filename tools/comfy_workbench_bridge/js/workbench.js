import { app } from '../../scripts/app.js';

// 仅与显式打开本窗口的神工坊页通信，不接收凭据或任意远端指令。
app.registerExtension({
  name: 'GodsWorkbench.WorkflowBridge',
  async setup() {
    const parameters = new URL(location.href).searchParams;
    const nonce = parameters.get('gw_nonce');
    const origin = parameters.get('gw_origin');
    if (!window.opener || !nonce || !origin) return;
    let expectedOrigin;
    try {
      const url = new URL(origin);
      if (!['http:', 'https:'].includes(url.protocol) || url.origin !== origin) return;
      expectedOrigin = url.origin;
    } catch (_) { return; }
    let workflowId = null, version = null, loading = false, saving = false;
    let saveTimer = null, requestId = null;
    const button = document.createElement('button');
    button.type = 'button'; button.textContent = '回存神工坊'; button.disabled = true;
    Object.assign(button.style, { position: 'fixed', right: '24px', bottom: '24px', zIndex: '9999', padding: '8px 16px' });
    button.setAttribute('aria-label', '将当前工作流回存神工坊');
    document.body.appendChild(button);
    const send = value => window.opener?.postMessage({ ...value, nonce }, expectedOrigin);
    const listener = async event => {
      if (event.origin !== expectedOrigin || event.source !== window.opener || event.data?.nonce !== nonce) return;
      const message = event.data;
      if (message.kind === 'gw-comfy-load' && !loading && !saving) {
        if (!message.graph || typeof message.workflow_id !== 'string' || !Number.isInteger(message.expected_version)) return;
        loading = true; button.disabled = true;
        try {
          await app.loadGraphData(message.graph);
          workflowId = message.workflow_id; version = message.expected_version;
          send({ kind: 'gw-comfy-loaded', workflow_id: workflowId });
          button.textContent = '回存神工坊';
        } catch (_) { send({ kind: 'gw-comfy-error', message: 'ComfyUI 原生图加载失败，请检查缺失节点' }); }
        finally { loading = false; button.disabled = !workflowId; }
      } else if (message.kind === 'gw-comfy-save-result' && saving && message.request_id === requestId) {
        clearTimeout(saveTimer); saveTimer = null; requestId = null;
        saving = false; button.disabled = false;
        if (message.ok === true && Number.isInteger(message.version)) {
          version = message.version; button.textContent = '已回存 · 再次保存';
        } else button.textContent = '回存失败 · 重试';
      }
    };
    window.addEventListener('message', listener);
    button.addEventListener('click', () => {
      if (!workflowId || loading || saving) return;
      saving = true; button.disabled = true; button.textContent = '正在回存…';
      requestId = crypto.randomUUID();
      // 无答复只解除界面锁，不判断保存失败或自动重发；用户须核对服务端版本。
      saveTimer = setTimeout(() => {
        saving = false; requestId = null; button.disabled = false;
        button.textContent = '回存未确认 · 核对后重试';
      }, 30000);
      try { send({ kind: 'gw-comfy-save', request_id: requestId, workflow_id: workflowId, expected_version: version, graph: app.graph.serialize() }); }
      catch (_) { clearTimeout(saveTimer); saveTimer = null; requestId = null; saving = false; button.disabled = false; button.textContent = '回存失败 · 重试'; }
    });
    window.addEventListener('pagehide', () => { clearTimeout(saveTimer); window.removeEventListener('message', listener); button.remove(); }, { once: true });
    send({ kind: 'gw-comfy-ready' });
  },
});
