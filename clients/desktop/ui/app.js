(() => {
  const invoke = (...args) => window.__TAURI__?.core?.invoke(...args);
  const status = document.querySelector("#scan-status");
  const list = document.querySelector("#server-list");
  const urlInput = document.querySelector("#server-url");
  const form = document.querySelector("#manual-form");
  const rescan = document.querySelector("#rescan");
  const notice = document.querySelector("#launch-notice");
  const aboutBody = document.querySelector("#about-body");
  const aboutRefresh = document.querySelector("#about-refresh");

  function errorMessage(error) {
    return typeof error === "string" ? error : error?.message || String(error);
  }

  function showNotice(text, tone = "") {
    if (!notice) return;
    notice.textContent = text || "";
    notice.dataset.tone = tone;
    notice.hidden = !text;
  }

  function destinationOf(result) {
    if (typeof result === "string") return result;
    return result?.destination || "";
  }

  async function openResult(result, delayMs = 0) {
    const destination = destinationOf(result);
    if (!destination) throw new Error("桌面端未返回导航地址。请重新扫描或重启客户端。");
    const message = typeof result === "object" ? result?.notice : "";
    if (message) {
      showNotice(message, result?.auto_login ? "ok" : "warn");
      status.textContent = message;
      await new Promise(resolve => setTimeout(resolve, delayMs));
    }
    window.location.replace(destination);
  }

  async function connect(serverUrl) {
    status.textContent = "正在验证服务器身份、版本与能力契约……";
    rescan.disabled = true;
    try {
      const result = await invoke("connect_to_server", { serverUrl });
      await openResult(result, result?.auto_login ? 0 : 1200);
    } catch (error) {
      showNotice(`连接失败：${errorMessage(error)}`, "error");
      status.textContent = `连接失败：${errorMessage(error)}`;
      rescan.disabled = false;
      await refreshDiagnostics();
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
    showNotice("");
    status.textContent = "正在检查本机与局域网内的 2077 端口……";
    rescan.disabled = true;
    try {
      const servers = await invoke("discover_lan");
      if (servers && servers.length > 0) {
        const localServer = servers.find(s => s.url.includes("127.0.0.1") || s.url.includes("localhost"));
        if (localServer) {
          status.textContent = "发现已在运行的本机工作台服务，正在验证身份与能力……";
          await connect(localServer.url);
          return;
        }
        for (const server of servers) list.append(makeServerButton(server));
        status.textContent = `发现 ${servers.length} 台工作台服务。选择服务器以连接。`;
        rescan.disabled = false;
        return;
      }

      status.textContent = "未发现运行中的实例，正在以生产模式启动本机后端……";
      try {
        const result = await invoke("launch_local_backend");
        if (result?.notice) {
          status.textContent = result.notice;
        } else {
          status.textContent = "本机后端已就绪，正在打开工作台……";
        }
        await openResult(result, result?.auto_login ? 0 : 1200);
      } catch (launchError) {
        showNotice(`本机后端拉起失败：${errorMessage(launchError)}`, "error");
        status.textContent = `本机后端拉起失败：${errorMessage(launchError)}`;
        const tip = document.createElement("div");
        tip.className = "fallback-hint";
        tip.innerHTML = "<p><strong>备用启动方式</strong>：您可在神工坊项目根目录下双击运行 <code>start.bat</code>，或在终端执行 <code>python run.py --prod</code> 启动后端，启动成功后点击「重新扫描」。</p>";
        list.replaceChildren(tip);
        rescan.disabled = false;
        await refreshDiagnostics();
      }
    } catch (error) {
      showNotice(`搜索失败：${errorMessage(error)}`, "error");
      status.textContent = `搜索失败：${errorMessage(error)}`;
      rescan.disabled = false;
    }
  }

  function row(label, value) {
    const term = document.createElement("dt");
    term.textContent = label;
    const description = document.createElement("dd");
    description.textContent = value || "未知";
    return [term, description];
  }

  function handshakeSummary(diagnostics) {
    const report = diagnostics.handshake;
    if (!report) return "尚未连接";
    if (report.verified) return report.local ? "已验证（本机）" : "已验证（远程/NAS）";
    if (report.local) return "未验证（本机，仅密码登录）";
    return "未验证（已阻止连接）";
  }

  function trustedSummary(diagnostics) {
    const trusted = diagnostics.trusted || {};
    if (trusted.auto_login) return "可用（已注入一次性票据）";
    if (trusted.available) return "可用（待兑换）";
    return trusted.reason || "不可用，请使用密码登录";
  }

  function digestSummary(diagnostics) {
    const report = diagnostics.handshake;
    if (!report || !report.digest) return "未知";
    const match = report.digest_match ? "与安装包基线一致" : "未与安装包基线比对";
    return `${report.digest.slice(0, 12)}…（${match}）`;
  }

  async function refreshDiagnostics() {
    if (!aboutBody) return;
    aboutBody.replaceChildren();
    try {
      const diagnostics = await invoke("get_diagnostics");
      const report = diagnostics.handshake;
      const fragments = [
        row("桌面版本", diagnostics.desktop_version),
        row("构建模式", diagnostics.build_mode === "debug" ? "调试构建" : "正式构建"),
        row("内置后端版本", diagnostics.bundled_backend_version),
        row("连接状态", handshakeSummary(diagnostics)),
        row("对端版本", report ? `${report.remote_version || "未知"}（来源：${report.version_source || "unknown"}）` : "尚未连接"),
        row("能力契约", report ? `${report.profile || "unknown"} · ${diagnostics.capability_profile}` : diagnostics.capability_profile),
        row("能力摘要", digestSummary(diagnostics)),
        row("受信本机", trustedSummary(diagnostics)),
        row("安装目录与数据根", diagnostics.program_data_non_overlapping === true ? "已确认互不包含" : diagnostics.program_data_non_overlapping === false ? "检测到包含关系，已阻断" : "开发源构建（未校验）"),
      ];
      if (diagnostics.manifest_error) fragments.push(row("安装清单", diagnostics.manifest_error));
      if (report?.failures?.length) fragments.push(row("握手失败项", report.failures.join("；")));
      if (report?.warnings?.length) fragments.push(row("握手提示", report.warnings.join("；")));
      if (diagnostics.notice) fragments.push(row("最近提示", diagnostics.notice));
      if (diagnostics.last_error) fragments.push(row("最近错误", diagnostics.last_error));
      aboutBody.append(...fragments.flat());
    } catch (error) {
      aboutBody.append(...row("诊断不可用", errorMessage(error)));
    }
  }

  form.addEventListener("submit", event => {
    event.preventDefault();
    if (form.reportValidity()) connect(urlInput.value);
  });
  rescan.addEventListener("click", scan);
  aboutRefresh?.addEventListener("click", refreshDiagnostics);

  async function initialize() {
    if (!window.__TAURI__?.core?.invoke) {
      status.textContent = "桌面接口不可用，请重新启动客户端。";
      return;
    }
    await refreshDiagnostics();
    const forceScan = new URLSearchParams(window.location.search).get("rescan") === "1";
    if (!forceScan) {
      try {
        const saved = await invoke("last_server");
        if (saved) {
          urlInput.value = saved;
          const result = await invoke("connect_to_server", { serverUrl: saved });
          await openResult(result);
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
