// gods-workbench-adapter-version: 2
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
    const parseGraph = async () => {
      const graph = structuredClone(app.graph.serialize());
      const signature = JSON.stringify(graph);
      const parsed = await app.graphToPrompt();
      // 异步widget序列化期间有编辑时拒绝回存，不能给新原图签旧执行缓存。
      if (JSON.stringify(app.graph.serialize()) !== signature) throw new Error('编译期间工作流已改变，请重新回存');
      const fieldMaps = {}, virtualNodes = [];
      for (const node of app.graph._nodes || []) {
        if (node.isVirtualNode || node.isSubgraphNode?.()) virtualNodes.push(String(node.id));
        const fields = parsed.output?.[String(node.id)]?.inputs;
        if (!fields) continue;
        let index = 0;
        for (const widget of node.widgets || []) {
          if (widget.options?.serialize === false) continue;
          if (Object.hasOwn(fields, widget.name)) (fieldMaps[String(node.id)] ||= {})[`widget_${index}`] = widget.name;
          index++;
        }
      }
      return { graph, api_prompt: parsed.output, field_maps: fieldMaps, virtual_node_ids: virtualNodes };
    };
    const listener = async event => {
      if (event.origin !== expectedOrigin || event.source !== window.opener || event.data?.nonce !== nonce) return;
      const message = event.data;
      if (message.kind === 'gw-comfy-load' && !loading && !saving) {
        if (!message.graph || typeof message.workflow_id !== 'string' || !Number.isInteger(message.expected_version)) return;
        loading = true; button.disabled = true;
        try {
          // ComfyUI 前端 1.55 的 openWorkflow 读取 filename.path，缺省文件名会抛 TypeError。
          const loaded = message.format === 'api'
            ? await app.loadApiJson(message.graph, 'GW_' + message.workflow_id + '.json')
            : await app.loadGraphData(message.graph);
          if (loaded === false) throw new Error('宿主未载入图');
          workflowId = message.workflow_id; version = message.expected_version;
          send({ kind: 'gw-comfy-loaded', workflow_id: workflowId });
          button.textContent = '回存神工坊';
          if (message.resolve_definitions === true) {
            const parsed = await parseGraph();
            // 自动补定义只回传宿主实际解析后的图；服务端自行取得真实节点定义。
            saving = true; requestId = crypto.randomUUID(); button.textContent = '正在补齐定义…';
            saveTimer = setTimeout(() => {
              saving = false; requestId = null; button.disabled = false;
              button.textContent = '定义回传未确认 · 核对后重试';
            }, 30000);
            send({ kind: 'gw-comfy-resolved', request_id: requestId, workflow_id: workflowId,
              expected_version: version, ...parsed });
          }
        } catch (_) { send({ kind: 'gw-comfy-error', message: 'ComfyUI 解析或加载失败，请检查缺失节点' }); }
        finally { loading = false; button.disabled = !workflowId || saving; }
      } else if (message.kind === 'gw-comfy-save-result' && saving && message.request_id === requestId) {
        clearTimeout(saveTimer); saveTimer = null; requestId = null;
        saving = false; button.disabled = false;
        if (message.ok === true && Number.isInteger(message.version)) {
          version = message.version; button.textContent = '已回存 · 再次保存';
        } else button.textContent = '回存失败 · 重试';
      }
    };
    window.addEventListener('message', listener);
    button.addEventListener('click', async () => {
      if (!workflowId || loading || saving) return;
      saving = true; button.disabled = true; button.textContent = '正在回存…';
      requestId = crypto.randomUUID();
      // 无答复只解除界面锁，不判断保存失败或自动重发；用户须核对服务端版本。
      saveTimer = setTimeout(() => {
        saving = false; requestId = null; button.disabled = false;
        button.textContent = '回存未确认 · 核对后重试';
      }, 30000);
      const activeRequest = requestId;
      try {
        const parsed = await parseGraph();
        if (requestId !== activeRequest || !saving) return;
        send({ kind: 'gw-comfy-save', request_id: requestId, workflow_id: workflowId, expected_version: version, ...parsed });
      } catch (_) {
        if (requestId !== activeRequest) return;
        clearTimeout(saveTimer); saveTimer = null; requestId = null; saving = false; button.disabled = false; button.textContent = '回存失败 · 重试';
      }
    });
    window.addEventListener('pagehide', () => { clearTimeout(saveTimer); window.removeEventListener('message', listener); button.remove(); }, { once: true });
    send({ kind: 'gw-comfy-ready' });
  },
});
