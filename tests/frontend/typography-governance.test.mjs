import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync,readdirSync,writeFileSync} from 'node:fs';
import path from 'node:path';
const root=path.resolve(import.meta.dirname,'../..');
const read=p=>readFileSync(path.join(root,p),'utf8');
const walk=p=>readdirSync(p,{withFileTypes:true}).flatMap(x=>x.isDirectory()?(x.name==='vendor'?[]:walk(path.join(p,x.name))):[path.join(p,x.name)]);
const files=walk(path.join(root,'web')).filter(p=>/\.(css|html|js)$/.test(p));
// 例外只允许具体声明形态，不以整个业务文件豁免；生成快照另行检查未被页面使用。
const preview=/^(?:var\(--asset-font-preview-(?:zh|en|glyph|glyph-small)-size,\d+px\)|700 var\(--annotation-font-size,15px\)\/1\.5 "Source Han Sans CN","Microsoft YaHei",sans-serif)$/;
function inventory(){
 const rows=[];
 for(const p of files){
  const file=path.relative(root,p).replaceAll('\\','/');
  const source=readFileSync(p,'utf8');
  // 注释保留换行，避免把说明当作实际声明。
  const text=source.replace(/\/\*[\s\S]*?\*\//g,m=>m.replace(/[^\n]/g,' '));
  const rx=/(?<![\w-])(font-size|font)\s*:\s*([^;}\n]+)|\bfont-size\s*=\s*["']([^"']+)|\b(?:style\.)?(fontSize|fontFamily)\s*=\s*([^;\n]+)|(?<![\w-])text-(?:xs|sm|base|lg|xl|[2-9]xl|\[\d[^\]]+\])/g;
  for(const m of text.matchAll(rx)){
   const value=(m[2]??m[3]??m[5]??m[0]).trim();
   const line=text.slice(0,m.index).split('\n').length;
   let category='semantic-token',reason='字号由语义token或继承值决定';
   if(m[1]==='font'&&!/\b(?:var\(--(?:gw-type|sf-fs|annotation-font-size)|\d+(?:px|rem|em|pt|%))/.test(value))continue; // JS对象键font、颜色/字体族自定义属性不是font简写。
   if(file==='web/js/modules/asset-manager.js'&&(/^(?:Math\.max\((?:8|24),|`\$\{Math\.max\(12,layer\.fontSize\*scaleY\)\}px`)/.test(value)||m[0]==='font-size:${fontSize')){category='user-content-canvas';reason='用户标注/画布文本的尺寸与缩放，非UI字号';}
   else if(file==='web/css/base/tailwind-utilities.css'){category='generated-snapshot';reason='本地Tailwind生成快照；页面与动态模板禁止使用字阶utility';}
   else if(file.endsWith('.css')&&m[0].startsWith('text-')){category='legacy-selector';reason='旧类兼容选择器，不是字号声明';}
   else if(file==='web/css/pages/asset-manager.css'&&preview.test(value)){category='user-content-preview';reason='用户标注或字体试样字号，独立于UI字号';}
   else if(m[4]==='fontFamily'){category='dynamic-font';reason='字体预览动态选择，非UI字号';}
   else if(m[1]==='font'&&/var\(--(?:gw-type-|sf-fs-)/.test(value)){category='semantic-shorthand';reason='font简写字号已token化，行高不是字号';}
   else if(m[1]&&(/^(?:inherit|initial|unset|revert|var\(--(?:gw-type-|gw-icon-size-|sf-fs-))/.test(value))){category=value.includes('gw-icon-size')?'icon-token':'semantic-token';}
   else {category='violation';reason='未登记的裸字号、字阶utility、SVG字号或动态字号赋值';}
   rows.push({file,line,declaration:m[0],category,reason});
  }
 }
 return {files:files.map(p=>path.relative(root,p).replaceAll('\\','/')).sort(),declarations:rows,exceptions:rows.filter(x=>!['semantic-token','semantic-shorthand'].includes(x.category)),violations:rows.filter(x=>x.category==='violation')};
}
test('排版真实字体与兼容最小值',()=>{
 const t=read('web/css/base/hardware-tokens.css');
 assert.doesNotMatch(t,/Outfit|Manrope|JetBrains/);
 assert.match(t,/--gw-type-label-sm:\s*var\(--gw-type-body-sm\)/);
 assert.match(t,/--gw-tracking-headline-md:/);
 assert.match(t,/--font-mono:\s*var\(--gw-font-mono\)/);
});
test('真实壳层使用继承默认值，不强制覆盖所有后代',()=>{
 const t=read('web/css/base/hardware-design-system.css');
 assert.doesNotMatch(t,/\.gw-workbench\s+:where\(\*\).*font-size/);
 assert.match(read('web/css/base/hardware-tokens.css'),/body.*gw-shell/s);
});

test('页面主标题统一16/24/700、栏目标题统一14/22/500，兼容角色跟随对应token',()=>{
 const tokens=read('web/css/base/hardware-tokens.css');
 assert.match(tokens,/--gw-type-headline-md:\s*16px\s*;/);
 assert.match(tokens,/--gw-type-headline-sm:\s*14px\s*;/);
 assert.match(tokens,/--gw-leading-headline-md:\s*24px\s*;/);
 assert.match(tokens,/--gw-leading-headline-sm:\s*22px\s*;/);
 assert.match(tokens,/--gw-weight-bold:\s*700\s*;/);
 assert.match(tokens,/--gw-type-title:\s*var\(--gw-type-headline-md\)/);
 assert.match(tokens,/--gw-type-section:\s*var\(--gw-type-headline-sm\)/);
 const system=read('web/css/base/hardware-design-system.css');
 assert.match(system,/\.gw-shell :where\(h1, \.page-title, \.hero-title, \[data-type="title"\]\) \{[^}]*font-size:var\(--gw-type-headline-md\)[^}]*line-height:var\(--gw-leading-headline-md\)[^}]*font-weight:var\(--gw-weight-bold\)/s);
});
test('辅助语义角色必须有regular字重，回收徽标不能以旧11px行框挤压12px文字',()=>{
 const css=read('web/css/base/hardware-design-system.css');
 assert.match(css,/\.gw-shell :where\(small,[^}]+font-weight:var\(--gw-weight-regular\)/);
 const badge=css.match(/\.gw-shell-trash \.gw-shell-trash-badge\s*\{([^}]+)\}/)?.[1];
 assert.ok(badge,'回收徽标规则必须存在');
 assert.match(badge,/min-height:20px/);
 assert.match(badge,/\/var\(--gw-leading-body-sm\)/);
 assert.doesNotMatch(badge,/height:13px|\/11px/);
});

test('每个HTML入口加载准入字体和tokens',()=>{
 for(const p of files.filter(p=>p.endsWith('.html'))){const t=readFileSync(p,'utf8');assert.match(t,/vendor\/css\/fonts.css/,p);assert.match(t,/hardware-tokens.css/,p);}
});
test('全部非vendor CSS/HTML/JS字号门禁与精确例外清单',()=>{
 const report=inventory();
 if(process.env.GW_TYPOGRAPHY_REPORT)writeFileSync(process.env.GW_TYPOGRAPHY_REPORT,JSON.stringify(report,null,2)+'\n');
 assert.equal(report.violations.length,0,JSON.stringify(report.violations,null,2));
 assert.ok(report.files.length>=99,'扫描范围不能缩减为改动文件');
});
test('API CSS保留治理结构和并行CLI规则，不回退HEAD旧页面',()=>{
 const css=read('web/css/pages/api-settings.css');
 assert.match(css,/API设置页：唯一页面样式入口/);
 assert.match(css,/\.api-settings-page/);
 assert.match(css,/\.jimeng-output\[hidden\]/);
 assert.doesNotMatch(css,/recommend-inline-body|recommend-setup-flow/);
});
test('窄屏工作流顶栏与三栏保留滚动可达性',()=>{
 const css=read('web/css/pages/workflow-workbench.css');
 assert.match(css,/\.workflow-sub-deck\s*\{\s*overflow-x:auto/);
 assert.match(css,/\.workbench-main\s*\{\s*overflow-x:auto/);
 assert.match(css,/\.right-canvas-bay\s*\{\s*min-width:420px;\s*flex-shrink:0/);
});

test('严格中文标题tracking0，不能沿用负字距',()=>{
 const t=read('web/css/base/hardware-tokens.css');
 for(const name of ['tight','headline','headline-md'])assert.match(t,new RegExp(`--gw-tracking-${name}:\\s*0\\s*;`));
});

test('主标题动态与iframe入口不得回退24px',()=>{
 const sources=[
  ...files.filter(p=>/\.(css|html|js)$/.test(p)),
  path.join(root,'.agents/skills/gods-workbench/references/design.md'),
  path.join(root,'.local/docs/notes/TYPOGRAPHY-SYSTEM-SPEC.md'),
  path.join(root,'.local/docs/user_doc/USER-STORIES.md')
 ];
 for(const p of sources){
  const text=readFileSync(p,'utf8');
  const file=path.relative(root,p).replaceAll('\\','/');
  assert.doesNotMatch(text,/--gw-type-headline-md:\s*24px|--gw-type-title[^\n]*24px|标题24\/?32|页面标题24\/?32/,file);
 }
});
test('跨页操作输入及主名称不得误映辅助字号',()=>{
 const matrix={
  'canvas-list':['.ws-project-name','.ws-primary-btn','.ws-newproj-input'],
  'asset-manager':['.asset-card-name','.asset-btn,.asset-icon-btn','.local-caption-prompt-input'],
  'workflow-workbench':['.workflow-workbench .hw-btn','.workflow-workbench .wf-tree-name','.workflow-workbench .param-label-row'],
  'directory-settings':['.directory-page .asset-btn','.directory-mount-input'],
  'episode-pipeline':['.shot-card input, .shot-card textarea']
 };
 for(const [file,selectors] of Object.entries(matrix)){
  const css=read(`web/css/pages/${file}.css`);
  for(const selector of selectors){
   const rules=[...css.matchAll(/([^{}]+)\{([^{}]*)\}/g)].filter(m=>m[1].split('*/').at(-1).trim()===selector);
   assert.ok(rules.length,`${file} ${selector}必须有具体规则`);
   const declaration=rules.at(-1)[2];
   assert.match(declaration,/font-size:\s*var\(--gw-type-body-md\)/,`${file} ${selector}`);
   assert.match(declaration,/line-height:\s*var\(--gw-leading-body-md\)/,`${file} ${selector}`);
  }
 }
});


test('strict运行时回归：分集下拉、资产工具行wrap与普通金色操作字重',()=>{
 assert.match(read('web/css/pages/episode-pipeline.css'),/\.episode-page select \{ height: 40px;/);
 assert.match(read('web/css/pages/asset-vault.css'),/registry-toolbar-controls[^}]*flex-wrap:\s*wrap/s);
 const gold=read('web/css/pages/workflow-workbench.css').match(/\.workflow-workbench \.hw-btn-gold\s*\{([^}]+)\}/)?.[1];
 assert.match(gold,/font-weight:\s*var\(--gw-weight-medium\)/);
});

test('栏目和辅助class字重与精确CSS覆盖同最新角色，状态计数保留独立字重',()=>{
 const tokens=read('web/css/base/hardware-tokens.css');
 assert.match(tokens,/\.gw-type-headline-sm[^}]+font-weight: var\(--gw-weight-medium\)/);
 assert.match(tokens,/\.gw-type-body-sm[^}]+font-weight: var\(--gw-weight-regular\)/);
 for(const file of ['settings-workbench','directory-settings','asset-vault','workflow-workbench']){
  const css=read(`web/css/pages/${file}.css`);
  for(const m of css.matchAll(/([^{}]+)\{([^{}]*)\}/g)){
   if(/font-size:\s*var\(--gw-type-headline-sm\)/.test(m[2])) assert.doesNotMatch(m[2],/font-weight:\s*var\(--gw-weight-bold\)/,`${file} ${m[1]}`);
  }
 }
 assert.match(read('web/css/base/hardware-design-system.css'),/gw-shell-trash-badge[^}]+font:700/);
});

test('独立iframe辅助与动态辅助说明取消旧medium，英文遥测不降重',()=>{
 assert.match(read('web/css/base/hardware-tokens.css'),/small, \[data-type="meta"\], \[data-type="caption"\][^}]*font-weight:var\(--gw-weight-regular\)/);
 assert.match(read('web/css/base/hardware-tokens.css'),/\.gw-type-body-sm\.font-bold[^}]*font-weight:var\(--gw-weight-bold\)/);
 assert.doesNotMatch(read('web/js/modules/episode-pipeline.js'),/gw-type-body-sm text-primary\/90 font-medium/);
});
