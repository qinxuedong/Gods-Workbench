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


test('严格中文标题tracking0，不能沿用负字距',()=>{
 const t=read('web/css/base/hardware-tokens.css');
 for(const name of ['tight','headline','headline-md'])assert.match(t,new RegExp(`--gw-tracking-${name}:\\s*0\\s*;`));
});
