(() => {
  "use strict";

  const THEME_KEY = "gw.desktop.theme";
  const localServer = Object.freeze({ url: "http://127.0.0.1:2077" });
  const invoke = (...args) => window.__TAURI__?.core?.invoke(...args);

  const byId = id => document.getElementById(id);
  const ui = {
    themeToggle: byId("theme-toggle"),
    themeLabel: byId("theme-label"),
    homeView: byId("home-view"),
    remoteView: byId("remote-view"),
    launchDot: byId("launch-dot"),
    launchEyebrow: byId("launch-eyebrow"),
    launchTitle: byId("launch-title"),
    launchDetail: byId("launch-detail"),
    steps: {
      start: byId("step-start"),
      ready: byId("step-ready"),
      verify: byId("step-verify"),
    },
    localError: byId("local-error"),
    localErrorTitle: byId("local-error-title"),
    localErrorReason: byId("local-error-reason"),
    localErrorNext: byId("local-error-next"),
    fallbackHint: byId("fallback-hint"),
    localRetry: byId("local-retry"),
    localDetails: byId("local-details"),
    localBusy: byId("local-busy"),
    remoteEntry: byId("remote-entry"),
    remoteBack: byId("remote-back"),
    remoteForm: byId("remote-form"),
    remoteUrl: byId("remote-url"),
    remoteSubmit: byId("remote-submit"),
    remoteScan: byId("remote-scan"),
    scanStatus: byId("scan-status"),
    serverList: byId("server-list"),
    remoteError: byId("remote-error"),
    remoteErrorTitle: byId("remote-error-title"),
    remoteErrorReason: byId("remote-error-reason"),
    remoteErrorNext: byId("remote-error-next"),
    remoteRetry: byId("remote-retry"),
    remoteErrorDetails: byId("remote-error-details"),
    about: byId("about"),
    aboutBody: byId("about-body"),
    aboutRefresh: byId("about-refresh"),
  };

  let generation = 0;
  let busy = false;
  let savedRemoteUrl = "";
  let lastRemoteAttempt = "";
  let progressTimers = [];

  function errorMessage(error) {
    if (typeof error === "string") return error;
    if (error && typeof error.message === "string") return error.message;
    return String(error);
  }

  function isLocalServerUrl(value) {
    try {
      const host = new URL(value).hostname.toLowerCase();
      return host === "127.0.0.1" || host === "localhost" || host === "[::1]";
    } catch {
      return false;
    }
  }

  function currentTheme() {
    return document.documentElement.dataset.theme === "light" ? "light" : "dark";
  }

  function applyTheme(theme, persist) {
    const next = theme === "light" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    ui.themeLabel.textContent = next === "light" ? "浅色" : "深色";
    ui.themeToggle.setAttribute("aria-label", next === "light" ? "切换到深色主题" : "切换到浅色主题");
    ui.themeToggle.setAttribute("aria-pressed", next === "light" ? "true" : "false");
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute("content", next === "light" ? "rgb(241, 242, 245)" : "#08090c");
    if (persist) {
      try { localStorage.setItem(THEME_KEY, next); } catch (error) {}
    }
  }

  function setBusy(value, label) {
    busy = value;
    ui.remoteEntry.disabled = value;
    ui.localRetry.disabled = value;
    ui.remoteSubmit.disabled = value;
    ui.remoteScan.disabled = value;
    if (ui.localBusy) {
      ui.localBusy.hidden = !value;
      ui.localBusy.textContent = label || "正在启动本机服务……";
    }
  }

  function stopProgressTimers() {
    for (const timer of progressTimers) clearTimeout(timer);
    progressTimers = [];
  }

  function setStep(name, state) {
    const node = ui.steps[name];
    if (node) node.dataset.state = state;
  }

  function startProgress() {
    stopProgressTimers();
    setStep("start", "active");
    setStep("ready", "pending");
    setStep("verify", "pending");
    ui.launchDot.dataset.state = "active";
    progressTimers.push(setTimeout(() => {
      setStep("start", "done");
      setStep("ready", "active");
    }, 700));
    progressTimers.push(setTimeout(() => {
      setStep("ready", "done");
      setStep("verify", "active");
    }, 2600));
  }

  function finishProgress() {
    stopProgressTimers();
    setStep("start", "done");
    setStep("ready", "done");
    setStep("verify", "done");
    ui.launchDot.dataset.state = "done";
  }

  function failProgress() {
    stopProgressTimers();
    const order = ["start", "ready", "verify"];
    const pending = order.find(name => ui.steps[name].dataset.state !== "done") || "verify";
    setStep(pending, "failed");
    ui.launchDot.dataset.state = "failed";
  }

  function resetHomeCopy() {
    ui.launchEyebrow.textContent = "本机工作台";
    ui.launchTitle.textContent = "启动神工坊";
    ui.launchDetail.textContent = "正在准备本机服务……";
    ui.localError.hidden = true;
  }

  function showHomeView() {
    ui.homeView.hidden = false;
    ui.remoteView.hidden = true;
  }

  function showRemoteView() {
    ui.homeView.hidden = true;
    ui.remoteView.hidden = false;
    if (savedRemoteUrl && !ui.remoteUrl.value) ui.remoteUrl.value = savedRemoteUrl;
    ui.remoteUrl.focus();
    ui.remoteUrl.select();
  }

  function openRemoteView() {
    generation += 1;
    stopProgressTimers();
    setBusy(false);
    showRemoteView();
  }

  function returnHomeView() {
    generation += 1;
    stopProgressTimers();
    setBusy(false);
    showHomeView();
  }

  function showLocalError(info) {
    failProgress();
    ui.launchEyebrow.textContent = "本机工作台 · 未连接";
    ui.launchTitle.textContent = info.headline;
    ui.launchDetail.textContent = "本机服务没有完成启动。请按下方原因处理后重试。";
    ui.localErrorTitle.textContent = info.headline;
    ui.localErrorReason.textContent = info.reason;
    ui.localErrorNext.textContent = info.next;
    ui.localError.hidden = false;
    ui.localError.focus?.();
  }

  function showRemoteError(info) {
    ui.remoteErrorTitle.textContent = info.headline;
    ui.remoteErrorReason.textContent = info.reason;
    ui.remoteErrorNext.textContent = info.next;
    ui.remoteError.hidden = false;
  }

  function explainLocalFailure(message) {
    const text = String(message || "");
    if (/2077|端口被占用|occupied|address already in use/i.test(text)) {
      return {
        headline: "本机端口被占用",
        reason: "2077 端口正被其他程序使用，内置服务无法绑定。",
        next: "请关闭占用 2077 端口的程序后点击「重试本机启动」。",
      };
    }
    if (/未响应唤醒|已有.*实例|无响应|not responding/i.test(text)) {
      return {
        headline: "已有实例无响应",
        reason: "检测到残留的神工坊桌面进程，但没有响应唤醒请求。",
        next: "请在任务管理器结束残留的 Gods Workbench 进程后重试；仍失败时重启电脑。",
      };
    }
    if (/安装清单|manifest|bundle-manifest/i.test(text)) {
      return {
        headline: "安装文件不完整",
        reason: "安装清单缺失或无法读取，无法确认真实版本与能力。",
        next: "请重新运行同一版本安装包修复安装，然后重试。",
      };
    }
    if (/包含关系|包含|overlap/i.test(text)) {
      return {
        headline: "安装位置不安全",
        reason: "安装目录与数据目录互相包含，已拒绝启动。",
        next: "请卸载后重新安装到与数据目录互不包含的独立目录，再重试。",
      };
    }
    if (/超时|timeout|timed out/i.test(text)) {
      return {
        headline: "本机服务启动超时",
        reason: "等待内置服务就绪超过允许时间。",
        next: "请稍后重试；若持续超时，展开「关于 / 连接详情」查看日志尾部并按备用命令启动。",
      };
    }
    if (/提前退出|exited|exit code|已退出/i.test(text)) {
      return {
        headline: "本机服务提前退出",
        reason: "内置服务在完成握手前退出。",
        next: "请重试；若仍失败，展开「关于 / 连接详情」查看最近错误并按备用命令启动。",
      };
    }
    if (/找不到|not found|missing|无法找到|no such file/i.test(text)) {
      return {
        headline: "安装文件不完整",
        reason: "未能找到内置后端或运行文件。",
        next: "请重新运行同一版本安装包修复安装，然后重试。",
      };
    }
    return {
      headline: "本机后端拉起失败",
      reason: "内置服务没有完成启动。",
      next: "请点击「重试本机启动」；若仍失败，展开「关于 / 连接详情」查看最近错误与备用启动命令。",
    };
  }

  function explainRemoteFailure(message) {
    const text = String(message || "");
    if (/no-store|未声明|缓存头/i.test(text)) {
      return {
        headline: "远端服务版本无法可信验证",
        reason: "探测响应没有声明 no-store，版本可能来自缓存。",
        next: "请升级远端工作台、关闭中间代理缓存，或改用与本客户端匹配的安装版后重试。",
      };
    }
    if (/\+gw\.|来源标记|version_source/i.test(text)) {
      return {
        headline: "远端服务版本无法可信验证",
        reason: "远端后端版本缺少 +gw. 来源标记，不能作为真实版本。",
        next: "请升级远端工作台到包含真实版本来源标记的版本后重试。",
      };
    }
    if (/能力摘要|能力契约|能力集合|缺少必需端点|capability/i.test(text)) {
      return {
        headline: "远端能力不兼容",
        reason: "远端提供的接口与当前桌面端的能力契约不一致。",
        next: "请升级远端工作台到与本客户端匹配的版本后重试。",
      };
    }
    if (/两次探测|疑似缓存/i.test(text)) {
      return {
        headline: "远端响应疑似被缓存",
        reason: "两次探测返回的版本或能力集合不一致。",
        next: "请检查反向代理缓存并关闭动态响应缓存后重试。",
      };
    }
    if (/认证状态|UNAUTHORIZED|401/i.test(text)) {
      return {
        headline: "远端认证探测异常",
        reason: "远端认证状态接口没有返回预期的未登录响应。",
        next: "请检查远端服务状态或升级远端版本后重试。",
      };
    }
    if (/private LAN|server URL|Only HTTP|地址|host/i.test(text)) {
      return {
        headline: "地址不可用",
        reason: "只支持私有局域网地址、localhost 或 .local 主机名。",
        next: "请检查地址格式、端口和网络可达性后重试。",
      };
    }
    if (/connect|连接|refused|发送请求|error sending/i.test(text)) {
      return {
        headline: "无法连接远端工作台",
        reason: "客户端未能建立到远端服务的连接。",
        next: "请确认地址、2077 端口、防火墙与局域网连通性后重试。",
      };
    }
    return {
      headline: "远端未通过兼容校验",
      reason: "远端服务未通过版本、能力或缓存防线校验。",
      next: "请升级远端工作台或改用匹配的桌面安装版；展开「关于 / 连接详情」可查看具体失败项。",
    };
  }

  async function openResult(result, delayMs = 0) {
    const destination = typeof result === "string" ? result : result?.destination;
    if (!destination) throw new Error("桌面端未返回工作台地址。请重新启动客户端。");
    const notice = typeof result === "object" ? result?.notice : "";
    if (notice) ui.launchDetail.textContent = notice;
    if (delayMs > 0) await new Promise(resolve => setTimeout(resolve, delayMs));
    window.location.replace(destination);
  }

  async function connect(serverUrl) {
    if (serverUrl === localServer.url) return await invoke("launch_local_backend");
    return invoke("connect_to_server", { serverUrl });
  }

  async function launchLocal() {
    if (busy) return;
    const token = ++generation;
    setBusy(true, "正在启动本机服务……");
    showHomeView();
    resetHomeCopy();
    startProgress();
    try {
      const result = await connect(localServer.url);
      if (token !== generation) return;
      finishProgress();
      ui.launchEyebrow.textContent = "本机工作台";
      ui.launchTitle.textContent = "工作台已就绪";
      ui.launchDetail.textContent = result?.notice || "本机服务已通过身份与能力校验，正在进入……";
      refreshDiagnostics();
      await openResult(result, result?.auto_login ? 0 : 1200);
    } catch (error) {
      if (token !== generation) return;
      const message = errorMessage(error);
      showLocalError(explainLocalFailure(message));
      refreshDiagnostics();
    } finally {
      if (token === generation) setBusy(false);
    }
  }

  async function connectRemote(serverUrl) {
    if (busy) return;
    const value = String(serverUrl || "").trim();
    if (!value) {
      showRemoteError({
        headline: "地址不可用",
        reason: "请填写远端工作台的服务器地址。",
        next: "例如 http://gw-nas.local:2077，然后重试。",
      });
      ui.remoteUrl.focus();
      return;
    }
    if (ui.remoteForm.reportValidity && !ui.remoteForm.reportValidity()) return;
    const token = ++generation;
    lastRemoteAttempt = value;
    setBusy(true, "正在连接远端工作台……");
    ui.remoteError.hidden = true;
    ui.scanStatus.textContent = "正在验证服务器身份、版本与能力契约……";
    try {
      const result = await connect(value);
      if (token !== generation) return;
      ui.scanStatus.textContent = result?.notice || "远端工作台已通过校验，正在进入……";
      refreshDiagnostics();
      await openResult(result, result?.auto_login ? 0 : 1200);
    } catch (error) {
      if (token !== generation) return;
      ui.scanStatus.textContent = "远端连接未通过校验。";
      showRemoteError(explainRemoteFailure(errorMessage(error)));
      refreshDiagnostics();
    } finally {
      if (token === generation) setBusy(false);
    }
  }

  async function scanLan() {
    if (busy) return;
    ui.serverList.replaceChildren();
    ui.remoteError.hidden = true;
    ui.scanStatus.textContent = "正在检查局域网内的神工坊服务……";
    ui.remoteScan.disabled = true;
    try {
      const servers = await invoke("discover_lan");
      const remoteServers = (servers || []).filter(server => !isLocalServerUrl(server.url));
      if (remoteServers.length === 0) {
        ui.scanStatus.textContent = "未发现其他工作台实例，可直接填写地址连接。";
        return;
      }
      for (const server of remoteServers) ui.serverList.append(makeServerButton(server));
      ui.scanStatus.textContent = `发现 ${remoteServers.length} 台工作台服务。选择后连接。`;
    } catch (error) {
      ui.scanStatus.textContent = `扫描失败：${errorMessage(error)}。仍可手动填写地址连接。`;
    } finally {
      ui.remoteScan.disabled = false;
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
    button.addEventListener("click", () => connectRemote(server.url));
    return button;
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

  function phaseSummary(diagnostics) {
    const names = { idle: "未启动", launching: "启动中", running: "运行中", failed: "启动失败" };
    const phase = names[diagnostics.backend_phase] || diagnostics.backend_phase || "未知";
    return diagnostics.backend_pid ? `${phase} · PID ${diagnostics.backend_pid}` : phase;
  }

  async function refreshDiagnostics() {
    if (!ui.aboutBody) return;
    ui.aboutBody.replaceChildren();
    try {
      const diagnostics = await invoke("get_diagnostics");
      const report = diagnostics.handshake;
      const fragments = [
        row("桌面版本", diagnostics.desktop_version),
        row("构建模式", diagnostics.build_mode === "debug" ? "调试构建" : "正式构建"),
        row("内置后端版本", diagnostics.bundled_backend_version),
        row("本机后端进程", phaseSummary(diagnostics)),
        row("连接状态", handshakeSummary(diagnostics)),
        row("对端版本", report ? `${report.remote_version || "未知"}（来源：${report.version_source || "unknown"}）` : "尚未连接"),
        row("能力契约", report ? `${report.profile || "unknown"} · ${diagnostics.capability_profile}` : diagnostics.capability_profile),
        row("能力摘要", digestSummary(diagnostics)),
        row("受信本机", trustedSummary(diagnostics)),
        row("安装目录与数据根", diagnostics.program_data_non_overlapping === true ? "已确认互不包含" : diagnostics.program_data_non_overlapping === false ? "检测到包含关系，已阻断" : "开发源构建（未校验）"),
        row("生效软件目录", diagnostics.install_dir || "未知"),
        row("生效数据目录", diagnostics.data_location || "未知"),
        row("备用启动命令", "项目根目录 start.bat 或 python run.py --prod"),
      ];
      if (diagnostics.manifest_error) fragments.push(row("安装清单", diagnostics.manifest_error));
      if (report?.failures?.length) fragments.push(row("握手失败项", report.failures.join("；")));
      if (report?.warnings?.length) fragments.push(row("握手提示", report.warnings.join("；")));
      if (diagnostics.notice) fragments.push(row("最近提示", diagnostics.notice));
      if (diagnostics.last_error) fragments.push(row("最近错误", diagnostics.last_error));
      ui.aboutBody.append(...fragments.flat());
    } catch (error) {
      ui.aboutBody.append(...row("诊断不可用", errorMessage(error)));
    }
  }

  function openDetails() {
    ui.about.open = true;
    ui.about.scrollIntoView({ block: "nearest" });
    refreshDiagnostics();
  }

  async function loadSavedServer() {
    try {
      const saved = await invoke("last_server");
      if (saved && !isLocalServerUrl(saved)) {
        savedRemoteUrl = saved;
        if (!ui.remoteUrl.value) ui.remoteUrl.value = saved;
      }
    } catch (error) {}
  }

  async function initialize() {
    applyTheme(currentTheme(), false);
    ui.themeToggle.addEventListener("click", () => {
      applyTheme(currentTheme() === "light" ? "dark" : "light", true);
    });
    ui.localRetry.addEventListener("click", launchLocal);
    ui.localDetails.addEventListener("click", openDetails);
    ui.remoteEntry.addEventListener("click", openRemoteView);
    ui.remoteBack.addEventListener("click", returnHomeView);
    ui.remoteRetry.addEventListener("click", () => connectRemote(lastRemoteAttempt || ui.remoteUrl.value));
    ui.remoteErrorDetails.addEventListener("click", openDetails);
    ui.remoteForm.addEventListener("submit", event => {
      event.preventDefault();
      connectRemote(ui.remoteUrl.value);
    });
    ui.remoteScan.addEventListener("click", scanLan);
    ui.aboutRefresh.addEventListener("click", refreshDiagnostics);

    if (!window.__TAURI__?.core?.invoke) {
      showLocalError({
        headline: "桌面接口不可用",
        reason: "安装壳无法调用桌面端命令。",
        next: "请关闭后重新启动 Gods Workbench。",
      });
      return;
    }

    refreshDiagnostics();
    loadSavedServer();
    const searchParams = new URLSearchParams(window.location.search);
    if (searchParams.get("rescan") === "1" || searchParams.get("view") === "remote") {
      openRemoteView();
      scanLan();
      return;
    }
    launchLocal();
  }

  initialize();
})();