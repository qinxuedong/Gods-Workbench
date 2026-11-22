/** QIN-626 / GitHub #761：安装壳设计系统、本地优先启动与票据交付静态契约。 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const app = readFileSync("clients/desktop/ui/app.js", "utf8");
const index = readFileSync("clients/desktop/ui/index.html", "utf8");
const style = readFileSync("clients/desktop/ui/style.css", "utf8");
const readme = readFileSync("clients/desktop/README.md", "utf8");
const acceptanceReadme = readFileSync("clients/desktop/packaging/acceptance/README.md", "utf8");
const contract = readFileSync("docs/guides/DESKTOP-COMPATIBILITY-CONTRACT.md", "utf8");
const hardwareTokens = readFileSync("web/css/base/hardware-tokens.css", "utf8");
const themeCss = readFileSync("web/css/base/theme.css", "utf8");
const settingsHtml = readFileSync("web/pages/settings.html", "utf8");
const settingsJs = readFileSync("web/js/controllers/settings.js", "utf8");

function declaredValue(css, name, scope = ":root") {
  const block = css.slice(css.indexOf(scope));
  const match = block.match(new RegExp(`${name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\s*:\\s*([^;]+);`));
  return match ? match[1].replace(/\s+/g, " ").trim() : "";
}

test("AC2: 关于/连接详情展示真实版本、能力契约与安装根校验结果", () => {
  assert.match(index, /<details id="about"/);
  assert.match(index, /关于 \/ 连接详情/);
  assert.match(app, /row\("桌面版本", diagnostics\.desktop_version\)/);
  assert.match(app, /row\("内置后端版本", diagnostics\.bundled_backend_version\)/);
  assert.match(app, /row\("对端版本"/);
  assert.match(app, /row\("能力契约"/);
  assert.match(app, /row\("能力摘要"/);
  assert.match(app, /row\("安装目录与数据根"/);
  assert.match(app, /invoke\("get_diagnostics"\)/);
  assert.match(contract, /cc47d8d4ac74adc87f073127886e13e71ffbcd3aef3248a11bb957d55c4086e2/);
  assert.match(contract, /gw-compat-v1/);
});

test("AC1: 安装说明与界面不冒充正式发布，并给出可信下载与摘要核验要求", () => {
  assert.match(readme, /# Windows standard installer \(QIN-626\)/);
  assert.match(readme, /currentUser/);
  assert.match(readme, /Unsigned test builds/);
  assert.match(readme, /SHA-256/);
  assert.match(readme, /Do not disable Defender\/SmartScreen/);
  assert.match(readme, /DESKTOP-COMPATIBILITY-CONTRACT\.md/);
  assert.match(readme, /QIN-630/);
});

test("AC1: 首页只保留本地启动，版本/诊断/命令全部收进折叠详情", () => {
  const aboutAt = index.indexOf('<details id="about"');
  const remoteAt = index.indexOf('id="remote-view"');
  assert.ok(aboutAt > remoteAt, "关于详情必须在主视图之后折叠展示");
  assert.match(index, /id="home-view"/);
  assert.match(index, /id="remote-entry"/);
  assert.match(app, /ui\.localDetails\.addEventListener\("click", openDetails\)/);
  assert.match(app, /ui\.aboutRefresh\.addEventListener\("click", refreshDiagnostics\)/);
  const firstScreen = index.slice(index.indexOf('id="home-view"'), aboutAt);
  assert.doesNotMatch(firstScreen, /桌面版本|内置后端版本|能力摘要/);
  assert.doesNotMatch(firstScreen, /python run\.py --prod/);
  assert.doesNotMatch(firstScreen, /安装向导|使用说明书/);
});

test("AC1: 默认始终本地优先，保存的远端地址只预填不自动连接", () => {
  assert.match(app, /loadSavedServer\(\);[\s\S]*?get\("rescan"\) === "1"[\s\S]*?return;[\s\S]*?launchLocal\(\);/);
  assert.match(app, /savedRemoteUrl = saved;/);
  assert.match(app, /ui\.remoteUrl\.value = saved;/);
  assert.doesNotMatch(app, /connect_to_server", \{ serverUrl: saved/);
  assert.doesNotMatch(app, /invoke\("discover_lan"\)[\s\S]{0,120}initialize/);
  assert.match(app, /ui\.remoteEntry\.addEventListener\("click", openRemoteView\)/);
  assert.match(app, /ui\.remoteScan\.addEventListener\("click", scanLan\)/);
  assert.match(readme, /never scans the LAN or connects to a remote server automatically/);
  assert.match(readme, /only prefilled, never auto-connected/);
});

test("AC8: 票据只经导航地址交付，界面只持久化主题偏好", () => {
  assert.match(app, /window\.location\.replace\(destination\)/);
  assert.match(app, /const THEME_KEY = "gw\.desktop\.theme"/);
  assert.strictEqual((app.match(/localStorage\./g) || []).length, 1, "app.js 只允许主题写入一次 localStorage");
  assert.doesNotMatch(app, /localStorage\.getItem/);
  assert.match(index, /localStorage\.getItem\("gw\.desktop\.theme"\)/);
  assert.match(index, /不保存模型 API 密钥、设备密钥或本机票据/);
  assert.match(index, /不会注入本机一次性票据、设备密钥或模型 API 密钥/);
  assert.doesNotMatch(app, /gw_trusted|sessionStorage|indexedDB|document\.cookie/);
  assert.doesNotMatch(app, /window\.open/);
});

test("AC7: 本机与远端失败都给出明确原因、下一步与重试入口", () => {
  assert.match(index, /id="local-error-title"/);
  assert.match(index, /id="local-error-reason"/);
  assert.match(index, /id="local-error-next"/);
  assert.match(index, /id="local-retry"[^>]*>重试本机启动/);
  assert.match(index, /id="remote-error-reason"/);
  assert.match(index, /id="remote-retry"[^>]*>重试连接/);
  assert.match(app, /ui\.localRetry\.addEventListener\("click", launchLocal\)/);
  assert.match(app, /ui\.remoteRetry\.addEventListener\("click", \(\) => connectRemote\(/);
  for (const headline of ["本机端口被占用", "已有实例无响应", "安装文件不完整", "本机服务启动超时", "本机服务提前退出", "本机后端拉起失败"]) {
    assert.match(app, new RegExp(`headline: "${headline}"`));
  }
  for (const headline of ["远端服务版本无法可信验证", "远端能力不兼容", "远端响应疑似被缓存", "无法连接远端工作台"]) {
    assert.match(app, new RegExp(`headline: "${headline}"`));
  }
});

test("AC7: no-store 与版本来源标记保持失败关闭，不静默放行", () => {
  assert.match(app, /no-store|未声明\|缓存头/);
  assert.match(app, /headline: "远端服务版本无法可信验证"/);
  assert.match(app, /reason: "探测响应没有声明 no-store，版本可能来自缓存。"/);
  assert.match(app, /远端后端版本缺少 \+gw\. 来源标记/);
  assert.match(index, /不兼容时不会放行/);
});

test("AC6: 升级/兼容失败与受信不可用均可见，不显示为成功", () => {
  assert.match(app, /未验证（已阻止连接）/);
  assert.match(app, /检测到包含关系，已阻断/);
  assert.match(app, /不可用，请使用密码登录/);
  assert.match(app, /握手失败项/);
  assert.match(app, /诊断不可用/);
  assert.match(readme, /joint acceptance is required/);
});

test("AC1: 安装壳复用工作台黑金 token，不再使用独立米白金色风格", () => {
  for (const [name, source, scope] of [
    ["--gold-primary", hardwareTokens, ":root"],
    ["--gold-flare", hardwareTokens, ":root"],
    ["--gw-font-body", hardwareTokens, ":root"],
    ["--gw-font-mono", hardwareTokens, ":root"],
    ["--gw-font-display", hardwareTokens, ":root"],
  ]) {
    assert.equal(declaredValue(style, name), declaredValue(source, name, scope), `${name} 必须与工作台源 token 一致`);
  }
  assert.match(style, /color-scheme:\s*dark/);
  assert.match(style, /--gw-surface-bg: #08090c;/);
  assert.match(style, /--gw-surface-card: #111216;/);
  assert.match(themeCss, /#08090c/);
  assert.match(themeCss, /#111216/);
  assert.match(style, /html\[data-theme="light"\]/);
  assert.match(style, /--gw-surface-bg: rgb\(241, 242, 245\);/);
  assert.match(style, /--gw-surface-card: rgb\(255, 255, 255\);/);
  assert.match(style, /--gw-text-main: rgb\(17, 20, 26\);/);
  assert.match(themeCss, /rgb\(241, 242, 245\)/);
  assert.match(themeCss, /rgb\(17, 20, 26\)/);
  assert.doesNotMatch(style, /#f6f1e7|#fbf7ef|#fffdf8/i);
});

test("AC1: dark/light 切换可记忆，724/900/1440/1920 均有布局约束", () => {
  assert.match(index, /data-theme="dark"/);
  assert.match(index, /id="theme-toggle"/);
  assert.match(index, /name="theme-color"/);
  assert.match(app, /applyTheme\(currentTheme\(\) === "light" \? "dark" : "light", true\)/);
  assert.match(style, /@media \(max-width: 724px\)/);
  assert.match(style, /@media \(min-width: 900px\)/);
  assert.match(style, /@media \(min-width: 1440px\)/);
  assert.match(style, /@media \(min-width: 1920px\)/);
  assert.match(style, /prefers-reduced-motion/);
});

test("AC1/AC7: 安装端验收入口带 UTF-8 BOM 并覆盖真实页面/远端场景", () => {
  const acceptanceBytes = readFileSync("clients/desktop/packaging/acceptance/run_install_acceptance.ps1");
  assert.deepEqual([...acceptanceBytes.subarray(0, 3)], [0xef, 0xbb, 0xbf], "Windows PowerShell 5.1 解析中文断言需要 UTF-8 BOM");
  const acceptance = acceptanceBytes.toString("utf8");
  assert.match(acceptance, /Add-Step "local-failure-and-retry"/);
  assert.match(acceptance, /Add-Step "remote-entry"/);
  assert.match(acceptance, /Add-Step "tray-find-nas"/);
  assert.match(acceptance, /tray-find-nas\.navigation-ok/);
  assert.match(acceptance, /tray-find-nas\.no-404/);
  assert.match(acceptance, /tray-find-nas\.general-active/);
  assert.match(acceptance, /tray-find-nas\.remote-card/);
  assert.match(acceptance, /tray-find-nas\.scan-button/);
  assert.match(acceptance, /02-tray-find-nas\.page\.png/);
  assert.match(acceptance, /remote-fail-closed\.no-store-title/);
  assert.match(acceptance, /remote-fail-closed\.no-version-visible/);
  assert.match(acceptance, /port-conflict\.theme-dark/);
  assert.match(acceptance, /port-conflict\.theme-light/);
  assert.match(acceptance, /foreach \(\$layoutWidth in @\(724, 900, 1440, 1920\)\)/);
  assert.match(acceptance, /port-conflict\.layout-/);
  assert.match(acceptance, /remote-entry\.no-trusted-ticket/);
  assert.match(acceptance, /Test-NoStoreHeaders/);
  assert.match(acceptance, /first-run\.no-server-pin/);
  assert.match(acceptance, /\$parsed = \$raw \| ConvertFrom-Json/);
  assert.match(acceptance, /\$parsed -is \[System\.Array\]/);
  assert.match(acceptance, /\$Object -is \[System\.Collections\.IDictionary\]/);
  assert.match(acceptance, /\[StringComparison\]::OrdinalIgnoreCase/);
  assert.match(acceptance, /\[void\]\$socket\.ConnectAsync/);
  assert.match(acceptance, /\[void\]\$Connection\.socket\.SendAsync/);
  assert.match(acceptance, /GW_ALLOWED_HOSTS = \$Address/);
  assert.match(acceptance, /Page\.captureScreenshot/);
  assert.match(acceptance, /12-layout-.*\.page\.png/);
  assert.match(acceptance, /foreach \(\$file in \$files\)/);
  assert.match(acceptance, /if \(\$text\.Length -gt 0\) \{ return \$text \}/);
  const upgradeScript = readFileSync("clients/desktop/packaging/nsis/upgrade.ps1", "utf8");
  assert.match(upgradeScript, /Add-Content -LiteralPath \$stderrLog/);
  assert.match(upgradeScript, /\("PostInstall: " \+ \$message\)/);
  assert.match(acceptanceReadme, /GW_ALLOWED_HOSTS/);
});

test("公开界面示例不使用内网地址或真实路径", () => {
  assert.doesNotMatch(index, /(?:\b10\.|\b127\.0\.0\.1\b|192\.168\.|172\.(?:1[6-9]|2\d|3[01])\.)/);
  assert.doesNotMatch(app, /(?:\b10\.|192\.168\.|172\.(?:1[6-9]|2\d|3[01])\.)/);
  assert.match(app, /host === "127\.0\.0\.1" \|\| host === "localhost"/);
  assert.doesNotMatch(index, /[A-Za-z]:\\|\\\\[A-Za-z0-9._-]+\\/);
  assert.match(index, /gw-nas\.local/);
});

test("AC10: 设置页通用面板接入远端工作台拟物卡与安全降级", () => {
  assert.match(settingsHtml, /id="remoteWorkspaceCard"/);
  assert.match(settingsHtml, /id="remoteBrowserFallback"/);
  assert.match(settingsHtml, /id="remoteServerUrl"/);
  assert.match(settingsHtml, /id="btnConnectRemote"/);
  assert.match(settingsHtml, /id="btnScanLan"/);
  assert.match(settingsHtml, /id="btnReturnLocal"/);
  assert.match(settingsHtml, /col-start-1/);

  assert.match(settingsJs, /initRemoteWorkspaceCard/);
  assert.match(settingsJs, /window\.__TAURI__\?\.core\?\.invoke/);
  assert.match(settingsJs, /invoke\('last_server'\)/);
  assert.match(settingsJs, /invoke\('connect_to_server', \{ serverUrl: raw \}\)/);
  assert.match(settingsJs, /invoke\('discover_lan'\)/);
  assert.match(settingsJs, /invoke\('launch_local_backend'\)/);
  assert.match(settingsJs, /action=scan-nas/);
});

test("AC11: 托盘 Find NAS 路由指向 /static/pages/settings.html 且标签具备浅色高对比度", () => {
  const mainRs = readFileSync("clients/desktop/src/main.rs", "utf8");
  assert.match(mainRs, /\/static\/pages\/settings\.html\?section=general&action=scan-nas/);
  assert.match(settingsHtml, /text-slate-200[^>]*><label for="remoteServerUrl"/);
});

function createSettingsDomHarness({ search = "?section=general&action=scan-nas", invokeImpl } = {}) {
  const elements = new Map();

  function makeElement(tag, attrs = {}) {
    const classListSet = new Set((attrs.class || "").split(/\s+/).filter(Boolean));
    const listeners = new Map();
    const children = [];
    let text = attrs.textContent || "";
    let innerHtmlVal = "";
    const el = {
      tagName: tag.toUpperCase(),
      id: attrs.id || "",
      dataset: { ...(attrs.dataset || {}) },
      value: attrs.value || "",
      disabled: Boolean(attrs.disabled),
      attributes: { ...attrs },
      style: {},
      get textContent() { return text; },
      set textContent(v) { text = String(v); innerHtmlVal = text; },
      get innerHTML() { return innerHtmlVal; },
      set innerHTML(v) {
        innerHtmlVal = String(v);
        children.length = 0;
        const matches = [...innerHtmlVal.matchAll(/data-remote-select="([^"]+)"/g)];
        for (const m of matches) {
          const btn = makeElement("button", { dataset: { remoteSelect: m[1] } });
          children.push(btn);
        }
      },
      children,
      classList: {
        add: (...cls) => cls.forEach(c => classListSet.add(c)),
        remove: (...cls) => cls.forEach(c => classListSet.delete(c)),
        toggle: (c, force) => {
          if (force !== undefined) {
            force ? classListSet.add(c) : classListSet.delete(c);
            return force;
          }
          if (classListSet.has(c)) { classListSet.delete(c); return false; }
          classListSet.add(c); return true;
        },
        contains: c => classListSet.has(c)
      },
      setAttribute: (k, v) => { el.attributes[k] = String(v); },
      getAttribute: k => el.attributes[k] ?? null,
      addEventListener: (evt, fn) => {
        if (!listeners.has(evt)) listeners.set(evt, []);
        listeners.get(evt).push(fn);
      },
      click: async () => {
        const fns = listeners.get("click") || [];
        for (const fn of fns) await fn({ type: "click", target: el });
      },
      closest: (sel) => {
        if (sel === "[data-section]" && el.dataset.section) return el;
        return null;
      },
      querySelector: (sel) => el.querySelectorAll(sel)[0] || null,
      querySelectorAll: (sel) => {
        if (sel === "[data-remote-select]") {
          return children.filter(c => Boolean(c.dataset.remoteSelect));
        }
        return [];
      }
    };
    if (el.id) elements.set(el.id, el);
    return el;
  }

  const generalSectionBtn = makeElement("button", { dataset: { section: "general" } });
  const storageSectionBtn = makeElement("button", { dataset: { section: "storage" } });
  const generalPanel = makeElement("div", { dataset: { panel: "general" } });
  const storagePanel = makeElement("div", { dataset: { panel: "storage" } });

  const card = makeElement("div", { id: "remoteWorkspaceCard" });
  const fallback = makeElement("div", { id: "remoteBrowserFallback", class: "hidden" });
  const bridgeStatus = makeElement("span", { id: "remoteBridgeStatus" });
  const btnConnect = makeElement("button", { id: "btnConnectRemote" });
  const inputUrl = makeElement("input", { id: "remoteServerUrl" });
  const btnScan = makeElement("button", { id: "btnScanLan" });
  const listContainer = makeElement("div", { id: "remoteDiscoveredList", class: "hidden" });
  const errBox = makeElement("div", { id: "remoteConnectError", class: "hidden" });
  const btnReturn = makeElement("button", { id: "btnReturnLocal" });
  const defaultPage = makeElement("select", { id: "defaultPage" });
  const themeToggle = makeElement("button", { id: "themeToggle" });
  const systemInfo = makeElement("div", { id: "systemInfo" });

  const documentListeners = new Map();
  const document = {
    readyState: "complete",
    documentElement: { classList: { contains: () => false } },
    getElementById: id => elements.get(id) || null,
    querySelector: sel => {
      if (sel.startsWith("#")) return elements.get(sel.slice(1)) || null;
      if (sel === '[data-panel="general"]') return generalPanel;
      return null;
    },
    querySelectorAll: sel => {
      if (sel === "[data-section]") return [generalSectionBtn, storageSectionBtn];
      if (sel === "[data-panel]") return [generalPanel, storagePanel];
      if (sel === "[data-setting]") return [];
      if (sel.startsWith("#")) {
        const el = elements.get(sel.slice(1));
        return el ? [el] : [];
      }
      return [];
    },
    addEventListener: (type, fn) => {
      if (!documentListeners.has(type)) documentListeners.set(type, []);
      documentListeners.get(type).push(fn);
    }
  };

  const windowListeners = new Map();
  const historyStates = [];
  const location = {
    href: "http://127.0.0.1:2077/static/pages/settings.html" + search,
    pathname: "/static/pages/settings.html",
    search,
    hash: ""
  };

  const window = {
    location,
    history: {
      replaceState: (state, title, url) => historyStates.push({ state, title, url })
    },
    localStorage: {
      getItem: () => null,
      setItem: () => {}
    },
    addEventListener: (type, fn) => {
      if (!windowListeners.has(type)) windowListeners.set(type, []);
      windowListeners.get(type).push(fn);
    },
    Workspace: {
      api: async () => ({}),
      escapeHtml: s => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;"),
      navigate: () => {}
    }
  };

  if (invokeImpl) {
    window.__TAURI__ = {
      core: {
        invoke: invokeImpl
      }
    };
  }

  return {
    window,
    document,
    elements,
    generalPanel,
    generalSectionBtn,
    card,
    fallback,
    bridgeStatus,
    btnConnect,
    inputUrl,
    btnScan,
    listContainer,
    errBox,
    btnReturn
  };
}

test("AC11 (DoD §4.2 真实交互): 托盘 action=scan-nas 参数驱动设置页通用面板激活并自动触发局域网扫描与设备回填", async () => {
  const invokeCalls = [];
  const invokeImpl = async (cmd, args) => {
    invokeCalls.push({ cmd, args });
    if (cmd === "last_server") return "http://192.168.1.100:2077";
    if (cmd === "discover_lan") {
      return [
        { name: "Production-NAS", url: "http://192.168.1.120:2077" }
      ];
    }
    if (cmd === "connect_to_server") {
      return { destination: `${args.serverUrl}/static/pages/index.html` };
    }
    return null;
  };

  const harness = createSettingsDomHarness({
    search: "?section=general&action=scan-nas",
    invokeImpl
  });

  const context = {
    window: harness.window,
    document: harness.document,
    Workspace: harness.window.Workspace,
    localStorage: harness.window.localStorage,
    location: harness.window.location,
    history: harness.window.history,
    URLSearchParams,
    URL,
    Promise,
    console
  };

  vm.runInNewContext(settingsJs, context, { filename: "settings.js" });
  await new Promise(r => setTimeout(r, 20));

  // 1. 验证面板和导航激活
  assert.equal(harness.generalPanel.classList.contains("active"), true, "通用面板必须被激活");
  assert.equal(harness.generalSectionBtn.classList.contains("active"), true, "通用侧边栏项必须激活");
  assert.equal(harness.generalSectionBtn.attributes["aria-pressed"], "true");

  // 2. 验证 Tauri 客户端在线与上次服务器预填
  assert.equal(harness.bridgeStatus.textContent, "客户端在线");
  assert.equal(harness.inputUrl.value, "http://192.168.1.100:2077", "上次服务器地址自动预填");

  // 3. 验证 action=scan-nas 自动触发 discover_lan
  const scanCalls = invokeCalls.filter(c => c.cmd === "discover_lan");
  assert.equal(scanCalls.length, 1, "必须自动调用一次 discover_lan");
  assert.equal(harness.listContainer.classList.contains("hidden"), false, "局域网列表容器展示");
  assert.match(harness.listContainer.innerHTML, /Production-NAS/);
  assert.match(harness.listContainer.innerHTML, /http:\/\/192\.168\.1\.120:2077/);

  // 4. 验证点击列表中的设备能自动回填到输入框
  const selectBtns = harness.listContainer.querySelectorAll("[data-remote-select]");
  assert.equal(selectBtns.length, 1);
  await selectBtns[0].click();
  assert.equal(harness.inputUrl.value, "http://192.168.1.120:2077", "点击选择后设备 URL 写入输入框");

  // 5. 验证点击连接远端调用 connect_to_server 并重定向
  await harness.btnConnect.click();
  await new Promise(r => setTimeout(r, 20));
  const connectCalls = invokeCalls.filter(c => c.cmd === "connect_to_server");
  assert.equal(connectCalls.length, 1, "必须调用 connect_to_server");
  assert.equal(connectCalls[0].args?.serverUrl, "http://192.168.1.120:2077");
  assert.equal(harness.window.location.href, "http://192.168.1.120:2077/static/pages/index.html");
});

test("AC10/AC11 (DoD §4.2 安全降级): 无 Tauri 宿主时远端卡片显示降级提示且禁用交互", async () => {
  const harness = createSettingsDomHarness({
    search: "?section=general&action=scan-nas",
    invokeImpl: null
  });

  const context = {
    window: harness.window,
    document: harness.document,
    Workspace: harness.window.Workspace,
    localStorage: harness.window.localStorage,
    location: harness.window.location,
    history: harness.window.history,
    URLSearchParams,
    URL,
    Promise,
    console
  };

  vm.runInNewContext(settingsJs, context, { filename: "settings.js" });
  await new Promise(r => setTimeout(r, 20));

  assert.equal(harness.fallback.classList.contains("hidden"), false, "浏览器降级提示必须可见");
  assert.equal(harness.bridgeStatus.textContent, "浏览器环境 (未连接客户端)");
  assert.equal(harness.btnConnect.disabled, true);
  assert.equal(harness.inputUrl.disabled, true);
  assert.equal(harness.btnScan.disabled, true);
  assert.equal(harness.btnReturn.disabled, true);
});