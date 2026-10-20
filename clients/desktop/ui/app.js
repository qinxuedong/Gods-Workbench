(() => {
  const invoke = (...args) => window.__TAURI__?.core?.invoke(...args);
  const status = document.querySelector("#scan-status");
  const list = document.querySelector("#server-list");
  const urlInput = document.querySelector("#server-url");
  const form = document.querySelector("#manual-form");
  const rescan = document.querySelector("#rescan");

  function errorMessage(error) {
    return typeof error === "string" ? error : error?.message || String(error);
  }

  async function connect(serverUrl) {
    status.textContent = "正在验证 NAS 健康检查……";
    rescan.disabled = true;
    try {
      const destination = await invoke("connect_to_server", { serverUrl });
      window.location.replace(destination);
    } catch (error) {
      status.textContent = `连接失败：${errorMessage(error)}`;
      rescan.disabled = false;
    }
  }

  function makeServerButton(server) {
    const button = document.createElement("button");
    button.className = "server-item";
    button.type = "button";
    const copy = document.createElement("span");
    copy.textContent = server.name;
    const address = document.createElement("small");
    address.textContent = server.url;
    copy.append(address);
    const action = document.createElement("span");
    action.textContent = "连接 →";
    button.append(copy, action);
    button.addEventListener("click", () => connect(server.url));
    return button;
  }

  async function scan() {
    list.replaceChildren();
    status.textContent = "正在检查本机私有 IPv4 网段的 2077 端口……";
    rescan.disabled = true;
    try {
      const servers = await invoke("discover_lan");
      for (const server of servers) list.append(makeServerButton(server));
      status.textContent = servers.length
        ? `发现 ${servers.length} 台工作台 NAS。选择服务器以连接。`
        : "未发现 NAS。请确认端口 2077 已开放，或手动输入服务器地址。";
    } catch (error) {
      status.textContent = `搜索失败：${errorMessage(error)}`;
    } finally {
      rescan.disabled = false;
    }
  }

  form.addEventListener("submit", event => {
    event.preventDefault();
    if (form.reportValidity()) connect(urlInput.value);
  });
  rescan.addEventListener("click", scan);

  async function initialize() {
    if (!window.__TAURI__?.core?.invoke) {
      status.textContent = "桌面接口不可用，请重新启动客户端。";
      return;
    }
    const forceScan = new URLSearchParams(window.location.search).get("rescan") === "1";
    if (!forceScan) {
      try {
        const saved = await invoke("last_server");
        if (saved) {
          urlInput.value = saved;
          const destination = await invoke("connect_to_server", { serverUrl: saved });
          window.location.replace(destination);
          return;
        }
      } catch (error) {
        status.textContent = `上次服务器不可用：${errorMessage(error)} 正在搜索局域网。`;
      }
    }
    await scan();
  }

  initialize();
})();
