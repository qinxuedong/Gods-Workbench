/** QIN-626 / GitHub #761：Windows 标准安装版的桌面关于页与票据交付静态契约。 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const app = readFileSync("clients/desktop/ui/app.js", "utf8");
const index = readFileSync("clients/desktop/ui/index.html", "utf8");
const style = readFileSync("clients/desktop/ui/style.css", "utf8");
const readme = readFileSync("clients/desktop/README.md", "utf8");
const contract = readFileSync("docs/guides/DESKTOP-COMPATIBILITY-CONTRACT.md", "utf8");

test("AC2: 关于页展示真实版本、能力契约与安装根校验结果", () => {
  assert.match(index, /关于与连接状态/);
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

test("AC1: 已保存的本机地址在冷启动时拉起内置后端，远程地址仍直接连接", () => {
  assert.match(app, /isLocalServerUrl\(saved\)/);
  assert.match(app, /await invoke\("launch_local_backend"\)/);
  assert.match(app, /invoke\("connect_to_server", \{ serverUrl: saved \}\)/);
});

test("AC8: 票据只经导航地址交付，界面不保存、不展示、不拼接票据", () => {
  assert.match(app, /window\.location\.replace\(destination\)/);
  assert.match(index, /客户端仅保存服务器地址，不保存模型 API 密钥、设备密钥或本机票据/);
  assert.doesNotMatch(app, /gw_trusted/);
  assert.doesNotMatch(app, /localStorage|sessionStorage|indexedDB/);
  assert.doesNotMatch(app, /window\.open/);
});

test("AC7: 拉起失败呈现明确错误与备用启动方式", () => {
  assert.match(app, /本机后端拉起失败/);
  assert.match(app, /备用启动方式/);
  assert.match(app, /start\.bat/);
  assert.match(app, /python run\.py --prod/);
});

test("AC6: 升级/兼容失败与受信不可用均可见，不显示为成功", () => {
  assert.match(app, /未验证（已阻止连接）/);
  assert.match(app, /检测到包含关系，已阻断/);
  assert.match(app, /不可用，请使用密码登录/);
  assert.match(app, /握手失败项/);
  assert.match(app, /诊断不可用/);
});

test("AC1: dark/light 主题与 724/900/1440/1920 宽度均有布局约束", () => {
  assert.match(style, /prefers-color-scheme: light/);
  assert.match(style, /color-scheme:\s*dark/);
  assert.match(style, /max-width:\s*720px/);
  assert.match(style, /max-width:\s*540px/);
  assert.match(style, /min-width:\s*1440px/);
  assert.match(style, /min-width:\s*1920px/);
});

test("公开界面示例不使用内网地址", () => {
  assert.doesNotMatch(index, /(?:\b10\.|\b127\.0\.0\.1\b|192\.168\.|172\.(?:1[6-9]|2\d|3[01])\.)/);
  assert.match(index, /gw-nas\.local/);
});
