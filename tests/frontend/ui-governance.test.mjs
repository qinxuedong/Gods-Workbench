import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync,readdirSync,writeFileSync} from 'node:fs';
import path from 'node:path';
// UI 治理门禁：按钮契约、虹彩微球真源、左侧总线结构、表面 token。
// 与排版门禁 (typography-governance.test.mjs) 并存，互不覆盖。
const root=path.resolve(import.meta.dirname,'../..');
const read=p=>readFileSync(path.join(root,p),'utf8');
const walk=p=>readdirSync(p,{withFileTypes:true}).flatMap(x=>x.isDirectory()?(x.name==='vendor'?[]:walk(path.join(p,x.name))):[path.join(p,x.name)]);
const pages=['index.html','projects.html','workshop.html','production.html','agents.html','storyboard.html','assets.html','collab.html','workflow.html','settings.html'].map(n=>'web/pages/'+n);

test('顶栏10导航钮减高契约：32px 且仅作用于 .topbar-master-deck 作用域',()=>{
  const sys=read('web/css/base/hardware-design-system.css');
  const block=sys.match(/\.topbar-master-deck \.nav-pill-btn \{([^}]+)\}/)?.[1]||'';
  assert.match(block,/height:32px !important;\s*min-height:32px !important;/,'顶栏导航钮必须 32px（用户授权 34px→32px 减高2px）');
  // 页内胶囊共享规则保持 36px（分段控件契约另行覆盖圆角，不改此高度）。
  const pillBlock=sys.match(/\.filter-pill-btn, \.pill-capsule-active, \.pill-capsule-inactive \{([^}]+)\}/)?.[1]||'';
  assert.match(pillBlock,/min-height:36px/,'页内胶囊共享规则高度不受顶栏减高影响');
});

test('虹彩微球唯一外观真源：GW-ORB-01 定义一次，13 处入口共用',()=>{
  const sys=read('web/css/base/hardware-design-system.css');
  assert.match(sys,/GW-ORB-01/,'设计系统必须登记 GW-ORB-01 真源注释');
  assert.equal((sys.match(/\.launch-orb-btn \{/g)||[]).length,1,'.launch-orb-btn 基础规则只能定义一次');
  assert.match(sys,/\.launch-orb-btn:hover \{ transform:scale\(1\.1\); \}/);
  assert.match(sys,/\.launch-orb-btn > \.orb-core \{[^}]*background:rgba\(0,0,0,0\.85\)/s);
  assert.match(sys,/@keyframes gw-orb-spin/);
  // 10 顶栏保持同款工具类串（用户锁定的基准外观，不得改动）。
  const canonical=/w-8 h-8 rounded-full bg-gradient-to-tr from-cyan-400 via-fuchsia-500 to-amber-300 p-0\.5 shadow-\[0_0_12px_rgba\(236,72,153,0\.7\),0_0_6px_rgba\(34,211,238,0\.9\)\] hover:scale-110 active:scale-95 transition flex items-center justify-center cursor-pointer shrink-0 launch-orb-btn/;
  for(const p of pages){const t=read(p);assert.match(t,canonical,'顶栏微球必须保持基准外观：'+p);}
  // 3 处页内入口改用真源结构，旧的锥面渐变与 36/30px 内联外观必须清零。
  // 页内 <style> 块里的 .hw-mini-knob 等 36px 圆钮不是微球，先剥离再检测 HTML 内联 style。
  for(const p of pages){const html=read(p).replace(/<style[\s\S]*?<\/style>/gi,'');assert.doesNotMatch(html,/conic-gradient\(from 0deg, #22d3ee/,'旧锥面微球残留：'+p);assert.doesNotMatch(html,/width:\s*36px;\s*height:\s*36px;[\s\S]*launch|launch[\s\S]*width:\s*36px;[\s\S]*height:\s*36px/,'旧36px微球残留：'+p);}
  assert.match(read('web/pages/projects.html'),/title="一键立项点火"[\s\S]{0,200}orb-core/,'立项微球必须使用真源结构');
  assert.match(read('web/pages/index.html'),/title="点火发射指令"[\s\S]{0,200}orb-core/,'指令微球必须使用真源结构');
  assert.match(read('web/pages/agents.html'),/id="agentSendButton"[\s\S]{0,200}orb-core/,'执行测试微球必须使用真源结构且保留 sparkles');
  assert.doesNotMatch(read('web/pages/projects.html'),/\.launch-orb-btn\s*\{[\s\S]*hue-rotate/,'projects 页内重复的 launch-orb-btn 覆盖必须删除');
});

test('左侧总线结构统一：五页共享首页基准（金色头卡+主干线+overflow-hidden）',()=>{
  const header=/w-full flex items-center justify-between px-3 py-2 rounded-xl bg-\[#141720\] border border-\[#dfc384\]\/35 text-\[#eddab3\] gw-type-body-sm font-semibold shadow-\[0_0_12px_rgba\(223,195,132,0\.12\)\] relative/;
  const goldBar=/absolute left-0 top-2 bottom-2 w-1 bg-\[#dfc384\] rounded-r shadow-\[0_0_8px_#dfc384\]/;
  const trunk=/absolute left-3\.5 top-2 bottom-4 w-\[1\.5px\] bg-gradient-to-b from-\[#dfc384\] via-\[#dfc384\]\/40 to-transparent pointer-events-none/;
  const dockCls='class="w-64 bg-[#08090d] border-r border-[#191c24] flex flex-col shrink-0 z-30 h-full min-h-0 overflow-hidden"';
  for(const p of ['web/pages/index.html','web/pages/projects.html','web/pages/production.html','web/pages/workshop.html','web/pages/agents.html']){
    const t=read(p);
    assert.ok(t.includes(dockCls),'dock 侧栏结构必须与首页一致：'+p);
    assert.match(t,header,'金色头卡必须与首页一致：'+p);
    assert.match(t,goldBar,'头卡金色左条必须存在：'+p);
    assert.match(t,trunk,'总线主干线必须存在：'+p);
    assert.match(t,/id="contextBusContainer"/,'总线容器标识必须存在：'+p);
  }
});

test('首页总线版块化：节点枚举右侧版块，保留真实数据绑定',()=>{
  const t=read('web/pages/index.html');
  // 四个版块节点与右侧锚点一一对应。
  for(const target of ['href="#homeRecentProjects"','href="#homeAssetOverview"','href="agents.html"','href="#homeRenderBus"'])assert.ok(t.includes(target),'首页总线必须链接右侧版块：'+target);
  for(const id of ['id="homeRecentProjects"','id="homeAssetOverview"','id="homeRenderBus"'])assert.ok(t.includes(id),'右侧版块锚点必须存在：'+id);
  // 真实数据绑定保留：GPU 计数（hardware-telemetry.js 源）、AURA 状态（/api/chat/config 源）、项目计数（/api/asset-registry/projects 源）。
  assert.match(t,/id="gpuClusterCountBadge"/,'GPU 计数绑定必须保留在总线');
  assert.match(t,/id="busAuraStatus"/,'AURA 状态绑定必须进入总线');
  assert.match(t,/id="busProjectsCount"/,'项目计数绑定必须进入总线');
  // 设计稿占位内容必须清零。
  assert.doesNotMatch(t,/busActiveProjectTitle/,'设计稿项目名绑定必须移除');
  assert.doesNotMatch(t,/busStageScript|busStageVideo/,'设计稿阶段绑定必须移除');
  const home=read('web/js/controllers/home-controller.js');
  assert.match(home,/getElementById\('busProjectsCount'\)/,'控制器必须维护总线项目计数（真实数据源，不伪造）');
  assert.match(home,/getElementById\('busAuraStatus'\)/,'控制器必须镜像 AURA 状态到总线');
});

test('按钮契约上提：hw-btn 族定义于设计系统，工作台页局部定义清零',()=>{
  const sys=read('web/css/base/hardware-design-system.css');
  assert.match(sys,/GW-BTN-01/);
  assert.match(sys,/\.hw-btn \{[^}]*min-height:36px/s,'默认钮契约必须含 36px 最小高度');
  assert.match(sys,/\.hw-btn-gold \{/);
  assert.match(sys,/\.hw-btn-danger \{/);
  assert.match(sys,/GW-BTN-02/,'分段控件契约必须登记');
  assert.match(sys,/\.ep-pill-btn, \.filter-pill-btn \{ border-radius:4px !important; min-height:32px;/,'页签/筛选必须走分段外观');
  const wb=read('web/css/pages/workflow-workbench.css');
  // 上提后工作台页只允许同值字号契约重声明（供排版门禁在本页审计），不得重定义外观。
  for(const m of wb.matchAll(/\.workflow-workbench \.hw-btn(-gold|-danger)?\s*\{([^}]*)\}/g)){
    assert.doesNotMatch(m[2],/padding|background|border|min-height|box-shadow|display|cursor|gap|align-|justify-|white-space|transition|transform|color:/,'工作台页 hw-btn 局部规则只允许字号契约声明：'+m[0].slice(0,40));
  }
});

test('表面 token 共享层：hardware-tokens 登记 surface 与控件 token',()=>{
  const t=read('web/css/base/hardware-tokens.css');
  assert.match(t,/--gw-surface-bg:\s*var\(--chassis-bg\)/);
  assert.match(t,/--gw-surface-panel:\s*var\(--panel-dark\)/);
  assert.match(t,/--gw-surface-card:\s*var\(--card-start\)/);
  assert.match(t,/--gw-surface-card-end:\s*var\(--card-end\)/);
  assert.match(t,/--gw-btn-min-height:\s*36px/);
});

// 硬编码背景色棘轮门禁：非 vendor CSS 的 6 位 hex 总量不得高于基线快照；
// 新增硬编码色必须走 token 或更新此基线并在任务单登记。
// 报告模式（GW_UI_GATE_REPORT 环境变量）输出逐位置清单 ui-gate.json：
// 每个 hex 位置带文件/行号/上下文，并登记许可例外（neon-orb 虹彩微球色板与
// 字体预览、用户画布内容同级处理），供人工核对与后续 token 化排期使用。
const HEX_BASELINE=2802;
// GW-ORB-01 虹彩微球许可例外色板：仅微球外观使用，登记后不再计入治理债务。
const ORB_PERMIT_HEXES=new Set(['#22d3ee','#d946ef','#fcd34d','#ec4899']);
test('非vendor CSS硬编码hex棘轮门禁（基线2026-10-10 UI治理后快照）',()=>{
  const cssFiles=walk(path.join(root,'web')).filter(p=>p.endsWith('.css'));
  let total=0;const per={};const locations=[];
  for(const p of cssFiles){const t=readFileSync(p,'utf8');const n=(t.match(/#[0-9a-fA-F]{6}\b/g)||[]).length;if(n){per[path.relative(root,p).replaceAll('\\','/')]=n;total+=n;}
    t.split('\n').forEach((ln,i)=>{for(const m of ln.matchAll(/#[0-9a-fA-F]{6}\b/g)){const hex=m[0];locations.push({file:path.relative(root,p).replaceAll('\\','/'),line:i+1,hex,context:ln.trim().slice(0,80),permit:ORB_PERMIT_HEXES.has(hex.toLowerCase())?'GW-ORB-01 虹彩微球许可例外':null});}});}
  if(process.env.GW_UI_GATE_REPORT)writeFileSync(process.env.GW_UI_GATE_REPORT,JSON.stringify({generated:new Date().toISOString(),baseline:HEX_BASELINE,total,files:cssFiles.length,per,whitelist:[{palette:[...ORB_PERMIT_HEXES],reason:'GW-ORB-01 许可例外：虹彩微球外观（顶栏10+页内3），与字体预览、用户画布内容同级登记'}],locations},null,2)+'\n');
  assert.ok(total<=HEX_BASELINE,'硬编码hex总量 '+total+' 超出基线 '+HEX_BASELINE+'，新增色值必须 token 化');
  assert.ok(cssFiles.length>=24,'扫描范围不能缩减');
});
