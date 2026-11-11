/** QIN-593 / GitHub #698: 桌面端启动未发现实例自动拉起本机后端契约测试 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const startBat = readFileSync("start.bat", "utf8");
const desktopSource = readFileSync("clients/desktop/src/main.rs", "utf8");
const desktopApp = readFileSync("clients/desktop/ui/app.js", "utf8");
const desktopReadme = readFileSync("clients/desktop/README.md", "utf8");
const readmeZh = readFileSync("README.md", "utf8");
const readmeEn = readFileSync("README_EN.md", "utf8");

test("AC1: start.bat 及其行为与文档语义保持不变", () => {
  assert.match(startBat, /python\s+run\.py\s+--prod\s+--open-browser/);
  assert.match(readmeZh, /start\.bat/);
  assert.match(readmeZh, /自动以生产模式启动单进程服务，并在就绪后唤起默认浏览器打开项目中心/);
  assert.match(readmeEn, /start\.bat/);
  assert.match(readmeEn, /automatically boots the server in production mode and launches your default browser/);
});

test("AC2 & AC3: 桌面端启动先走发现；未发现实例时调用 launch_local_backend 拉起本机后端", () => {
  assert.match(desktopApp, /await\s+invoke\("discover_lan"\)/);
  assert.match(desktopApp, /await\s+invoke\("launch_local_backend"\)/);
  assert.match(desktopSource, /async fn launch_local_backend/);
  assert.match(desktopSource, /launch_local_backend,/);
  assert.match(desktopSource, /--prod/);
  assert.doesNotMatch(desktopSource, /--open-browser/);
  assert.match(desktopSource, /127\.0\.0\.1:2077/);
});

test("AC4: 本机 2077 已健康时直接连接，不重复拉起第二个后端进程", () => {
  assert.match(desktopApp, /localServer/);
  assert.match(desktopApp, /127\.0\.0\.1/);
  assert.match(desktopApp, /await connect\(localServer\.url\)/);
  assert.match(desktopSource, /LocalPortStatus::HealthyInstance/);
  assert.match(desktopSource, /OccupiedForeign/);
});

test("AC5: 桌面端单实例机制：再次启动唤醒已有窗口（含托盘隐藏态），不启动第二个客户端进程", () => {
  assert.match(desktopSource, /try_wake_existing_instance/);
  assert.match(desktopSource, /arbitrate_single_instance/);
  assert.match(desktopSource, /create_new\(true\)/);
  assert.match(desktopSource, /recovering/);
  assert.match(desktopSource, /SingleInstanceDecision/);
  assert.match(desktopSource, /is_lock_holder_alive_and_matched/);
  assert.match(desktopSource, /process_start_time/);
  assert.match(desktopSource, /exe_path/);
  assert.match(desktopSource, /WakeupState/);
  assert.match(desktopSource, /WAKEUP/);
  assert.match(desktopSource, /WAKEN/);
  assert.match(desktopSource, /window\.show\(\)/);
  assert.match(desktopSource, /window\.unminimize\(\)/);
  assert.match(desktopSource, /window\.set_focus\(\)/);
});

test("AC6: 拉起失败时在窗口内呈现明确错误与备用方式提示，不静默打开系统浏览器", () => {
  assert.match(desktopApp, /本机后端拉起失败/);
  assert.match(desktopApp, /fallback-hint/);
  assert.match(desktopApp, /start\.bat/);
  assert.match(desktopApp, /python run\.py --prod/);
  assert.doesNotMatch(desktopApp, /window\.open/);
});

test("AC7: 生命周期：托盘退出时停止托管后端；关窗仅隐藏至托盘；非托管外部后端不停止", () => {
  assert.match(desktopSource, /BackendPhase/);
  assert.match(desktopSource, /BackendStateInner/);
  assert.match(desktopSource, /ManagedBackendState/);
  assert.match(desktopSource, /drain_process_output/);
  assert.match(desktopSource, /stop_managed_backend/);
  assert.match(desktopSource, /child\.kill\(\)/);
  assert.match(desktopSource, /WindowEvent::CloseRequested/);
  assert.match(desktopSource, /hide_window\.hide\(\)/);
  assert.match(desktopSource, /"quit"\s*=>\s*\{[\s\S]*?stop_managed_backend/);
});

test("AC8: 文档清楚说明桌面端 Python 与后端源码目录定位逻辑", () => {
  assert.match(desktopReadme, /Local backend discovery and automatic launch \(QIN-593\)/);
  assert.match(desktopReadme, /GW_PYTHON/);
  assert.match(desktopReadme, /GW_BACKEND_ROOT/);
  assert.match(desktopReadme, /Single Instance/);
  assert.match(readmeZh, /Tauri 本地桌面端启动/);
  assert.match(readmeEn, /Tauri Desktop Client Launch/);
});
