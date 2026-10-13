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
const relativeLuminance=hex=>{
  const channels=[1,3,5].map(offset=>parseInt(hex.slice(offset,offset+2),16)/255);
  const linear=channels.map(value=>value<=.04045?value/12.92:((value+.055)/1.055)**2.4);
  return .2126*linear[0]+.7152*linear[1]+.0722*linear[2];
};
const contrastRatio=(foreground,background)=>{
  const a=relativeLuminance(foreground),b=relativeLuminance(background);
  return (Math.max(a,b)+.05)/(Math.min(a,b)+.05);
};

test('顶栏10导航钮采用新规LG40，保持链接与各页原业务入口',()=>{
  const sys=read('web/css/base/hardware-design-system.css');
  const block=sys.match(/\.topbar-master-deck \.nav-pill-btn \{([^}]+)\}/)?.[1]||'';
  assert.match(block,/height:40px !important;\s*min-height:40px !important;/);
  assert.match(block,/font-size:var\(--gw-type-button-md\) !important; font-weight:700/);
  for(const p of pages){
    const links=[...read(p).matchAll(/<(?:a|button)\b[^>]*class="([^"]*\bnav-pill-btn\b[^"]*)"[^>]*>/g)];
    assert.equal(links.length,10,'每个主页面顶栏必须恰有10个导航按钮：'+p);
  }
});

test('发光胶囊仅顶栏导航使用，模板与动态切换不得重新引入',()=>{
  const capsule=/\b(?:pill-capsule-(?:active|inactive)|btn-pill-(?:active|inactive|disabled))\b/;
  for(const file of walk(path.join(root,'web')).filter(file=>file.endsWith('.html'))){
    for(const tag of readFileSync(file,'utf8').matchAll(/<[a-z][^>]*class="([^"]*)"[^>]*>/gi)){
      if(capsule.test(tag[1]))assert.match(tag[1],/\bnav-pill-btn\b/,'顶栏外模板胶囊：'+file);
    }
    for(const script of readFileSync(file,'utf8').matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/gi)){
      assert.doesNotMatch(script[1],capsule,'内联脚本不能重新添加胶囊：'+file);
    }
  }
  for(const file of walk(path.join(root,'web/js')).filter(file=>file.endsWith('.js'))){
    const source=readFileSync(file,'utf8');
    if(file.endsWith(path.join('core','workbench-shell.js'))){
      assert.match(source,/querySelectorAll\('\.topbar-master-deck \.nav-pill-btn'\)/);
    }else if(file.endsWith(path.join('controllers','home-controller.js'))){
      for(const line of source.split('\n').filter(line=>capsule.test(line)))assert.match(line,/nav-pill-btn/);
    }else assert.doesNotMatch(source,capsule,'动态模板不能添加胶囊：'+file);
  }
  const css=read('web/css/base/hardware-buttons.css');
  for(const rule of css.matchAll(/([^{}]+)\{([^{}]*)\}/g)){
    if(capsule.test(rule[1]) && /radial-gradient|border-radius:9999px/.test(rule[2]))
      assert.match(rule[1],/\.topbar-master-deck/,'胶囊材质必须限定顶栏');
  }
});

test('微球恢复原稿棱彩环与内核，保留用户裁决32px及既有业务入口',()=>{
  const css=read('web/css/base/hardware-buttons.css');
  assert.match(css,/GW-ORB-01/);
  const rule=css.match(/:is\(\.btn-launch-orb,\.launch-orb-btn\) \{([^}]+)\}/)?.[1]||'';
  assert.match(rule,/width:32px;\s*height:32px/);
  assert.match(css,/::before[\s\S]{0,240}background:conic-gradient\(from 0deg,rgb\(34 211 238\),rgb\(168 85 247\),rgb\(245 158 11\),rgb\(236 72 153\),rgb\(34 211 238\)\)/);
  assert.match(css,/@keyframes gw-orb-spin/);
  assert.match(css,/:is\(\.btn-launch-orb,\.launch-orb-btn\):is\(:active,\.is-firing\)[\s\S]*?background:conic-gradient\(from 0deg,#fff3dc,var\(--gw-keycap-gold\),#fae2c8,#b89354,#fff3dc\)/);
  assert.match(css,/@keyframes shockwavePulse[\s\S]*?opacity:\.35[\s\S]*?scale\(1\.6\)/);
  for(const p of pages){const source=read(p);assert.match(source,/class="launch-orb-btn"/);assert.doesNotMatch(source,/from-cyan-400 via-fuchsia-500 to-amber-300/);}
  for(const p of ['projects','index','agents'])assert.match(read('web/pages/'+p+'.html'),/orb-core/);
});

test('左侧总线结构统一：五页共享首页基准（金色头卡+主干线+overflow-hidden）',()=>{
  const header=/w-full flex items-center justify-between px-3 py-2 rounded-xl bg-\[#141720\] border border-\[#dfc384\]\/35 text-\[#eddab3\] gw-type-body-sm font-semibold shadow-\[0_0_12px_rgba\(223,195,132,0\.12\)\] relative/;
  const goldBar=/absolute left-0 top-2 bottom-2 w-1 bg-\[#dfc384\] rounded-r shadow-\[0_0_8px_#dfc384\]/;
  const trunk=/absolute left-3\.5 top-2 bottom-4 w-\[1\.5px\] bg-gradient-to-b from-\[#dfc384\] via-\[#dfc384\]\/40 to-transparent pointer-events-none/;
  const dockClasses='w-64 bg-[#08090d] border-r border-[#191c24] flex flex-col shrink-0 z-30 h-full min-h-0 overflow-hidden'.split(' ');
  for(const p of ['web/pages/index.html','web/pages/projects.html','web/pages/production.html','web/pages/workshop.html','web/pages/agents.html']){
    const t=read(p);
    const asides=[...t.matchAll(/<aside\b[^>]*class="([^"]*)"/g)].map(m=>new Set(m[1].split(/\s+/)));
    assert.ok(asides.some(classes=>dockClasses.every(name=>classes.has(name))),'dock 侧栏结构必须与首页一致（允许响应式标记）：'+p);
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

test('新按钮族唯一真源，旧兼容类不得在设计系统或资产页重复定义',()=>{
  const css=read('web/css/base/hardware-buttons.css');
  for(const family of ['btn-pill-active','btn-pill-inactive','btn-hw-tactile','btn-hw-danger','btn-hw-round','btn-hw-round-inner','btn-segmented-group','btn-segmented-item','btn-rocker-housing','btn-rocker-paddle','btn-slide-toggle','btn-slide-peg','btn-launch-orb'])assert.ok(css.includes('.'+family),family);
  assert.match(read('web/css/base/hardware-design-system.css'),/hardware-buttons\.css/);
  assert.doesNotMatch(read('web/css/base/hardware-design-system.css'),/\.hw-btn \{|\.pill-capsule-active \{|\.launch-orb-btn \{/);
  assert.doesNotMatch(read('web/css/pages/asset-manager.css'),/gw-page--asset-manager \.hw-btn[^,{]*[,{]/);
  for(const m of read('web/css/pages/workflow-workbench.css').matchAll(/\.workflow-workbench \.hw-btn(-gold|-danger)?\s*\{([^}]*)\}/g))assert.doesNotMatch(m[2],/padding|background|border|min-height|box-shadow|display|cursor|gap|align-|justify-|white-space|transition|transform|color:/);
});

test('按钮标准尺寸、危险材质、禁用与动效门禁',()=>{
  const css=read('web/css/base/hardware-buttons.css'),tokens=read('web/css/base/hardware-tokens.css');
  for(const [size,h,pad,icon] of [['lg',40,20,16],['sm',26,12,12],['xs',20,8,10]]){const block=css.match(new RegExp('(?:\\.btn-size-'+size+'[^{}]*)\\{([^}]+)\\}'))?.[1]||'';assert.match(block,new RegExp('--button-height:'+h+'px'));assert.match(block,new RegExp('--button-padding:'+pad+'px'));assert.match(block,new RegExp('--button-icon:'+icon+'px'));}
  assert.match(css,/--button-height:32px;\s*--button-padding:16px;\s*--button-icon:14px/);
  assert.match(tokens,/--gw-type-button-md: 12px/);assert.match(tokens,/--gw-type-button-sm: 11px/);assert.match(tokens,/--gw-type-button-xs: 9\.5px/);assert.match(tokens,/--gw-danger-cap: #8c0000/);
  assert.match(css,/:is\(\.hw-btn-danger,\.btn-hw-danger,\.btn-abort-carmine\) \{[\s\S]*?linear-gradient\(180deg,#181113 0%,#0d0a0b 100%\)/);
  assert.match(css,/:is\(\.btn-override-carmine,\.btn-tactile-keycap\.btn-override-carmine\) \{/);
  assert.match(css,/:is\(\.btn-hw-round,\.hw-round-btn\) \{[\s\S]*?border:1\.5px solid rgb\(223 195 132 \/ \.22\) !important/);
  assert.match(css,/:is\(\.btn-hw-round,\.hw-round-btn\):is\(:hover,:active,\.active-power,\.power-engaged,\.active,\[aria-checked="true"\],\[aria-pressed="true"\]\) \{ border-color:rgb\(223 195 132 \/ \.22\) !important/);
  assert.match(css,/:is\(\.btn-hw-round,\.hw-round-btn\):is\(\.active-power,\.power-engaged,\.active,\[aria-checked="true"\],\[aria-pressed="true"\]\)[\s\S]*?linear-gradient\(180deg,#fff3dc 0%,#fae2c8 30%,var\(--gw-keycap-gold\) 70%,#b89354 100%\)/);
  assert.match(css,/:focus-visible[^}]+outline:2px solid var\(--gw-focus-color\)/);
  assert.match(css,/--gw-focus-color:var\(--gold-primary\)/);
  assert.match(css,/@media \(forced-colors: active\)[\s\S]*--gw-focus-color:Highlight/);
  assert.match(css,/prefers-reduced-motion: reduce/);assert.match(css,/\.orb-core > span,[\s\S]*?\.shockwave-ring \{ animation:none !important/);
  assert.match(css,/:is\(:disabled,\[aria-disabled="true"\]\)[^}]+box-shadow:none/);
  assert.match(css,/--gw-button-disabled-bg,#1a1d24/);assert.match(css,/--gw-button-disabled-text,#a3acb9/);
});

test('键帽金属色与主题强调色分离，点火键与通电内胆刻字对比度达标',()=>{
  const tokens=read('web/css/base/hardware-tokens.css');
  const buttons=read('web/css/base/hardware-buttons.css');
  const theme=read('web/css/pages/asset-hardware-theme.css');
  assert.match(tokens,/--gw-keycap-gold:\s*#dfc384/i);
  assert.match(tokens,/--gw-keycap-flare:\s*#fae2c8/i);
  assert.match(theme,/--gold-primary:#775617/,'浅色主题语义强调色保持独立');
  const engraved=tokens.match(/--gw-engraved-text:\s*(#[\da-f]{6})/i)?.[1];
  assert.ok(engraved,'键帽刻字色必须来自共享 token');
  for(const surface of ['#dfc384','#b89354','#fff3dc','#fae2c8'])
    assert.ok(contrastRatio(engraved,surface)>=4.5,`${engraved} on ${surface} must be >= 4.5:1`);
  assert.match(buttons,/:is\(\.hw-btn-gold,\.primary-ignite,\.btn-tactile-keycap\.primary-ignite\) \{[\s\S]*?background:linear-gradient\(180deg,var\(--gw-keycap-gold\) 0%,#b89354 100%\)[\s\S]*?color:var\(--gw-engraved-text\)/);
  assert.match(buttons,/:is\(\.btn-hw-round,\.hw-round-btn\):is\(\.active-power,\.power-engaged,\.active,\[aria-checked="true"\],\[aria-pressed="true"\]\)[\s\S]*?color:var\(--gw-engraved-text\)/);
});

test('金色主键hover与active显式保留金色材质，按压只产生内凹反馈',()=>{
  const css=read('web/css/base/hardware-buttons.css');
  const selector=':is(.hw-btn-gold,.primary-ignite,.btn-tactile-keycap.primary-ignite)';
  for(const state of ['hover','active']){
    const start=css.indexOf(selector+':'+state+':not(:disabled):not([aria-disabled="true"]) {');
    assert.ok(start>=0,'金色状态规则缺失：'+state);
    const body=css.slice(start,css.indexOf('}',start));
    assert.match(body,/background:linear-gradient\(180deg,var\(--gw-keycap-gold\) 0%,#b89354 100%\)/);
    assert.match(body,/border:1px solid var\(--gw-keycap-flare\)/);
    assert.match(body,/border-bottom-color:#8c6828/);
    assert.match(body,/color:var\(--gw-engraved-text\)/);
    if(state==='active'){
      assert.match(body,/transform:translateY\(2\.5px\) scale\(\.97\)/);
      assert.match(body,/box-shadow:inset [^;]+,inset [^;]+;/);
    }
  }
});

test('减少动效覆盖每个公开别名及其按压位移',()=>{
  const css=read('web/css/base/hardware-buttons.css');
  const reducedMotion=css.match(/@media \(prefers-reduced-motion: reduce\) \{([\s\S]*?)\n\}/)?.[1]||'';
  const transitionRule=reducedMotion.match(/:is\(([^)]*)\) \{ transition:none !important/ )?.[1]||'';
  const transformRule=reducedMotion.match(/:is\(([^)]*)\):is\(:hover,:active,.is-firing\) \{ transform:none !important/)?.[1]||'';
  for(const alias of ['primary-ignite','btn-tactile-compound','hw-btn-compound','btn-pill-disabled','btn-abort-carmine','btn-override-carmine']){
    assert.ok(transitionRule.includes('.'+alias),`transition alias missing ${alias}`);
    assert.ok(transformRule.includes('.'+alias),`transform alias missing ${alias}`);
  }
});

test('八个按钮族系按原稿拆分，整理规范引用本地真源且Dial明确未接入',()=>{
  const css=read('web/css/base/hardware-buttons.css');
  const spec=read('docs/design/BUTTON-SYSTEM-SPEC.md');
  const guide=read('docs/design/DESIGN-SYSTEM-GUIDE.md');
  for(const family of ['btn-pill-active','btn-pill-inactive','btn-tactile-keycap','primary-ignite','btn-tactile-compound','btn-hw-round','btn-segmented-deck','btn-segmented-item','btn-rocker-housing','btn-rocker-paddle','btn-slide-toggle','btn-slide-peg','btn-launch-orb'])assert.ok(css.includes('.'+family),family);
  assert.match(css,/:is\(\.btn-pill-active,\.pill-capsule-active\) \{[\s\S]*?radial-gradient\(ellipse 75% 65% at 50% 50%/);
  assert.match(css,/:is\(\.hw-btn-gold,\.primary-ignite,\.btn-tactile-keycap\.primary-ignite\) \{[\s\S]*?linear-gradient\(180deg,var\(--gw-keycap-gold\) 0%,#b89354 100%\)/);
  assert.match(css,/:is\(\.btn-segmented-deck,\.btn-segmented-group,\.hw-segmented-group\) \{/);
  assert.match(css,/:is\(\.btn-rocker-housing,\.hw-rocker-stepper\) \{/);
  assert.match(css,/:is\(\.btn-slide-toggle,\.hw-slide-toggle\) \{[\s\S]*?width:44px[\s\S]*?height:22px/);
  assert.match(css,/\.btn-pill-disabled \{[\s\S]*?background:#12141a[\s\S]*?opacity:\.6/);
  assert.match(css,/\.btn-pill-disabled:is\(:disabled,\[aria-disabled="true"\]\) \{ border-color:rgb\(255 255 255 \/ \.04\) !important/);
  const roundOuter=css.match(/:is\(\.btn-hw-round,\.hw-round-btn\) \{([^}]+)\}/)?.[1]||'';
  assert.match(roundOuter,/border:1\.5px solid rgb\(223 195 132 \/ \.22\) !important/);
  assert.match(roundOuter,/box-shadow:[^;]+!important/);
  assert.match(roundOuter,/transition:none !important/);
  for(const p of ['web/pages/assets.html','web/pages/agents.html','web/pages/collab.html','web/pages/projects.html']){
    const source=read(p);
    const label=p.endsWith('/projects.html')?'切换自动降载':'切换自动与手动模式';
    const toggle=source.match(/<button type="button" class="hw-slide-toggle active"[^>]*aria-pressed="true"[^>]*>/)?.[0]||'';
    assert.ok(toggle.includes(`aria-label="${label}"`),'Slide Toggle名称必须对应页面用途：'+p);
    assert.ok(toggle,'Slide Toggle必须是带ARIA状态的原生按钮：'+p);
    assert.match(toggle,/onclick="[^"]*aria-pressed/);
    assert.doesNotMatch(source,/\$\(\[string\]::Empty\)/);
  }
  assert.match(read('web/pages/projects.html'),/class="hw-slide-toggle active" style="transform: scale\(0\.85\); transform-origin: right center;"/);
  assert.match(spec,/\.local\/docs\/design\/按钮设计标准\/code\.html/);
  assert.match(spec,/custom-refined-overrides/);
  assert.doesNotMatch(spec,/Google Stitch/);
  for(const name of ['Pill','Tactile','Round Push','Segmented','Rocker','Slide Toggle','Launch Orb','Dial'])assert.ok(spec.includes(name),'规范缺少独立族系：'+name);
  assert.match(spec,/当前 `web\/` 无实际产品消费者[\s\S]*?不增加 Dial CSS/);
  assert.match(guide,/custom-refined-overrides/);
  assert.match(guide,/Segmented、Rocker/);
});

test('资产内嵌文档只加载硬件主题，所有弹层继承根级语义色',()=>{
  const entry=read('web/embeds/asset-manager.html');
  assert.match(entry,/asset-hardware-theme\.css/);assert.doesNotMatch(entry,/base\/(?:theme|signal-flow)\.css/);
  assert.match(entry,/js\/core\/theme\.js/,'明暗与缩放偏好仍保留');
  const theme=read('web/css/pages/asset-hardware-theme.css');
  assert.match(theme,/--sf-cyan:var\(--gold-primary\)/);assert.match(theme,/--panel-bg:var\(--gw-surface-panel\)/);assert.match(theme,/html\[data-theme="light"\]/);
  const review=read('web/js/modules/asset-review.js');assert.match(review,/aria-label="关闭登录"/);assert.match(review,/hw-btn hw-btn-gold review-primary/);
});

test('表面 token 共享层：hardware-tokens 登记 surface 与控件 token',()=>{
  const t=read('web/css/base/hardware-tokens.css');
  assert.match(t,/--gw-surface-bg:\s*var\(--chassis-bg\)/);
  assert.match(t,/--gw-surface-panel:\s*var\(--panel-dark\)/);
  assert.match(t,/--gw-surface-card:\s*var\(--card-start\)/);
  assert.match(t,/--gw-surface-card-end:\s*var\(--card-end\)/);
});

test('首批复核修补覆盖顶栏微球、禁用材质、资产主题与审阅ARIA',()=>{
  const buttons=read('web/css/base/hardware-buttons.css');
  const tokens=read('web/css/base/hardware-tokens.css');
  for(const p of pages){
    const source=read(p);
    const orb=source.match(/<button class="launch-orb-btn"[^>]*aria-label="智能体核心总线"[^>]*>[\s\S]*?<\/button>/)?.[0]||'';
    assert.ok(orb,'顶栏微球需有可访问名称：'+p);
    assert.match(orb,/class="orb-core"/,'顶栏微球必须使用共享内核：'+p);
    assert.doesNotMatch(orb,/animate-spin/,'顶栏不得保留局部动画内核：'+p);
  }
  assert.match(tokens,/--gw-button-disabled-bg:\s*#1a1d24/i);
  assert.match(tokens,/--gw-button-disabled-text:\s*#a3acb9/i);
  assert.match(tokens,/--gw-button-disabled-border:\s*#3a404c/i);
  // C1-V3：禁用态不再整体降透明度，文字/底色对比度须 ≥4.5:1（#a3acb9 on #1a1d24 ≈ 7.36:1）。
  assert.match(buttons,/opacity:1;\s*cursor:not-allowed/);
  assert.match(buttons,/border-style:dashed !important/);
  assert.match(buttons,/background:var\(--gw-button-disabled-bg/);
  const disabledRule=buttons.match(/:is\(([^)]*btn-pill-disabled[^)]*)\):is\(:disabled,\[aria-disabled="true"\]\)\s*\{([^}]+)\}/);
  assert.ok(disabledRule,'所有按钮别名必须共用原生/ARIA禁用样式');
  for(const name of ['.btn-tactile-keycap','.primary-ignite','.btn-tactile-compound','.hw-btn-compound','.btn-pill-disabled','.btn-abort-carmine','.btn-override-carmine'])
    assert.ok(disabledRule[1].includes(name),'禁用规则缺少 '+name);
  const disabledInner=buttons.match(/:is\(\.btn-hw-round,.hw-round-btn,.btn-slide-toggle,.hw-slide-toggle,.btn-launch-orb,.launch-orb-btn\):is\(:disabled,\[aria-disabled="true"\]\) :is\(([^)]*)\)\s*\{([^}]+)\}/);
  assert.ok(disabledInner,'复合控件的禁用子件规则必须存在');
  for(const name of ['.orb-core > svg','.rec-bead','.shockwave-ring'])assert.ok(disabledInner[1].includes(name),'禁用子件规则缺少 '+name);
  assert.match(disabledInner[2],/box-shadow:none !important/);
  assert.match(disabledInner[2],/transform:none !important/);
  assert.match(disabledInner[2],/animation:none !important/);
  const roundInner=buttons.match(/:is\(\.btn-hw-round-inner,.hw-round-inner\)\s*\{([^}]+)\}/)?.[1]||'';
  assert.doesNotMatch(roundInner,/\b(?:width|height)\s*:/,'圆键内胆尺寸由页面调用方控制');
  assert.match(buttons,/> \.orb-core > span\s*\{[^}]*background:linear-gradient\(to top right,#67e8f9 0%,#e879f9 50%,#fcd34d 100%\)/);

  const vault=read('web/css/pages/asset-vault.css');
  const theme=read('web/css/pages/asset-hardware-theme.css');
  for(const [alias,target] of [['--vault-surface','--gw-surface-panel'],['--vault-card','--gw-surface-card'],['--vault-card-deep','--gw-surface-card-end'],['--vault-inset','--gw-control-bg'],['--vault-line-soft','--gw-border'],['--vault-gold','--gold-primary']])
    assert.match(vault,new RegExp(alias+':\\s*var\\('+target+'\\)'));
  assert.doesNotMatch(vault,/--vault-(?:surface|card|card-deep|inset|line-soft|gold)\s*:\s*#[0-9a-f]{3,8}/i);
  assert.match(vault,/html\[data-theme="light"\]/);
  assert.match(vault,/assetManagerRoot:not\(\.asset-manager-root-registry\)/);
  assert.match(vault,/project-directory-template-sheet/);
  const lightTheme=theme.match(/html\[data-theme="light"\]:has\(> body\.gw-page--asset-manager\)\s*\{([^}]+)\}/)?.[1]||'';
  assert.match(lightTheme,/--gold-light:var\(--gold-primary\)/,'浅色主题的金色文字需维持可读对比');

  const review=read('web/js/modules/asset-review.js');
  const closeButtons=[...review.matchAll(/<button\b[^>]*data-review-close[^>]*>/g)].map(m=>m[0]);
  const iconCloseButtons=closeButtons.filter(button=>!button.includes('review-primary'));
  assert.ok(iconCloseButtons.length>0);
  for(const button of iconCloseButtons)assert.match(button,/aria-label="[^"]+"/,'审阅图标关闭键需有名称');
  assert.match(review,/data-review-tool=.*?aria-label=.*?aria-pressed=/);
  assert.match(review,/state\.drawMode===mode\?'true':'false'/);
  assert.match(review,/node\.setAttribute\('aria-pressed',String\(selected\)\)/);
  assert.match(buttons,/:is\(\.hw-btn,\.btn-hw-tactile\)\[aria-pressed="true"\]/);
});

// 硬编码背景色棘轮门禁：非 vendor CSS 的 6 位 hex 总量不得高于基线快照；
// 新增硬编码色必须走 token 或更新此基线并在任务单登记。
// 报告模式（GW_UI_GATE_REPORT 环境变量）输出逐位置清单 ui-gate.json：
// 每个 hex 位置带文件/行号/上下文，并登记许可例外（neon-orb 虹彩微球色板与
// 字体预览、用户画布内容同级处理），供人工核对与后续 token 化排期使用。
const HEX_BASELINE=2802;
// GW-ORB-01 虹彩微球许可例外色板：仅微球外观使用，登记后不再计入治理债务。
const ORB_PERMIT_HEXES=new Set(['#22d3ee','#a855f7','#f59e0b','#ec4899']);
test('非vendor CSS硬编码hex棘轮门禁（基线2026-10-10 UI治理后快照）',()=>{
  const cssFiles=walk(path.join(root,'web')).filter(p=>p.endsWith('.css'));
  let total=0;const per={};const locations=[];
  for(const p of cssFiles){const t=readFileSync(p,'utf8');const n=(t.match(/#[0-9a-fA-F]{6}\b/g)||[]).length;if(n){per[path.relative(root,p).replaceAll('\\','/')]=n;total+=n;}
    t.split('\n').forEach((ln,i)=>{for(const m of ln.matchAll(/#[0-9a-fA-F]{6}\b/g)){const hex=m[0];locations.push({file:path.relative(root,p).replaceAll('\\','/'),line:i+1,hex,context:ln.trim().slice(0,80),permit:ORB_PERMIT_HEXES.has(hex.toLowerCase())?'GW-ORB-01 虹彩微球许可例外':null});}});}
  if(process.env.GW_UI_GATE_REPORT)writeFileSync(process.env.GW_UI_GATE_REPORT,JSON.stringify({generated:new Date().toISOString(),baseline:HEX_BASELINE,total,files:cssFiles.length,per,whitelist:[{palette:[...ORB_PERMIT_HEXES],reason:'GW-ORB-01 许可例外：虹彩微球外观（顶栏10+页内3），与字体预览、用户画布内容同级登记'}],locations},null,2)+'\n');
  assert.ok(total<=HEX_BASELINE,'硬编码hex总量 '+total+' 超出基线 '+HEX_BASELINE+'，新增色值必须 token 化');
  assert.ok(cssFiles.length>=23,'扫描范围不能缩减');
});


test('SIGNAL-FLOW旧兼容不得覆盖已迁移硬件按钮的材质和字阶',()=>{
  const css=read('web/css/base/signal-flow.css');
  assert.doesNotMatch(css,/\.gw-signal\s+:where\(\*\)\s*\{[^}]*font-size[^}]*!important/);
  for(const rule of css.matchAll(/([^{}]+)\{([^{}]*)\}/g)){
    if(/:is\(button, input, select, textarea\)/.test(rule[1]) && /min-height:\s*36px/.test(rule[2]))
      assert.match(rule[1],/:not\(:where\([^)]*\.hw-btn[^)]*\.btn-hw-round[^)]*\.btn-segmented-item/);
  }
});
