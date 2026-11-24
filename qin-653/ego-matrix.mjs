import fs from 'node:fs';
const task = await taskSpace("qin-653 四宽双主题补证");
const page = task.page("p1");
const BASE = "http://127.0.0.1:2177";
const QA = "D:/Working/Code Pro/Gods-Workbench/.local/agent_tasks/active/814-QIN-653-workbuddy/qa/";
const theme = fs.readFileSync(QA+'.matrix-theme.txt','utf8').trim();
const widths = [724, 900, 1440, 1920];
const results = [];

await page.cdp("Storage.clearDataForOrigin", {origin:BASE, storageTypes:"cache_storage,service_workers"});
// 起始宽视口，设主题后 reload 一次
await page.cdp("Emulation.setDeviceMetricsOverride", {width:1440, height:900, deviceScaleFactor:1, mobile:false});
await page.goto(BASE+"/static/pages/assets.html");
await page.waitForTimeout(1500);
await page.evaluate((t) => { for(const k of ['studio_theme','canvas_theme','theme']) localStorage.setItem(k,t); }, theme);
await page.cdp("Page.reload", {ignoreCache:true});
await page.waitForTimeout(6500);
console.log('DIAG', JSON.stringify(await page.evaluate(() => { const d=document.getElementById('assetVaultFrame').contentDocument; const c=d.querySelector('[data-registry-card]'); return {cards:d.querySelectorAll('[data-registry-card]').length, active:c?.className, frameSrc:document.getElementById('assetVaultFrame').getAttribute('src')}; })));

// 打开详情一次（可靠入口：卡片点击 + focus + Backquote，重试）
await page.waitForFunction(() => {
    const d = document.getElementById('assetVaultFrame')?.contentDocument;
    return d && d.querySelectorAll('[data-registry-card]').length > 0;
}, undefined, {timeout:15000});
await page.evaluate(() => {
    const d = document.getElementById('assetVaultFrame').contentDocument;
    const W = d.defaultView;
    const card = d.querySelector('[data-registry-card]');
    card?.dispatchEvent(new W.MouseEvent('click',{bubbles:true,button:0}));
    card?.focus();
});
await page.waitForTimeout(1200);
console.log('CLICK_DIAG', JSON.stringify(await page.evaluate(() => { const d=document.getElementById('assetVaultFrame').contentDocument; const c=d.querySelector('[data-registry-card].active'); return {active:!!c, focused:d.activeElement===c, viewer:!!d.querySelector('.asset-detail-viewer')}; })));
await page.evaluate(() => {
    const d = document.getElementById('assetVaultFrame').contentDocument;
    d.dispatchEvent(new d.defaultView.KeyboardEvent('keydown',{key:'`',code:'Backquote',bubbles:true}));
});
await page.waitForTimeout(2500);
let opened = await page.evaluate(() => !!document.getElementById('assetVaultFrame').contentDocument.querySelector('.asset-detail-viewer'));
console.log('KEY_DIAG opened=', opened);
if (!opened) throw new Error('详情面板未能打开，theme='+theme);

// 逐档改视口宽度，详情面板保持打开，CSS 媒体查询自动重算
for (const w of widths) {
    await page.cdp("Emulation.setDeviceMetricsOverride", {width:w, height:900, deviceScaleFactor:1, mobile:false});
    await page.waitForTimeout(2200);
    const info = await page.evaluate(() => {
        const top = document.documentElement;
        const frame = document.getElementById('assetVaultFrame');
        const d = frame.contentDocument;
        const R = el => { if(!el) return null; const r=el.getBoundingClientRect(); return {top:Math.round(r.top),left:Math.round(r.left),bottom:Math.round(r.bottom),w:Math.round(r.width),h:Math.round(r.height)}; };
        const v = d.querySelector('.asset-detail-viewer');
        const activeTab = d.querySelector('.asset-detail-media-sidebar-tabs button.is-active');
        const cs = activeTab && d.defaultView.getComputedStyle(activeTab);
        return {
            topTheme: top.getAttribute('data-theme'),
            frameTheme: d.documentElement.getAttribute('data-theme'),
            bodyBg: d.defaultView.getComputedStyle(d.body).backgroundColor,
            viewportW: window.innerWidth,
            frameW: Math.round(frame.getBoundingClientRect().width),
            frameInnerW: d.defaultView.innerWidth,
            viewer: !!v,
            type: v?.className.match(/asset-detail-type-(\w+)/)?.[1] || null,
            shell: R(d.querySelector('.asset-detail-media-canvas-shell')),
            toolbar: R(d.querySelector('.asset-detail-media-canvas-toolbar')),
            viewport: R(d.querySelector('[data-detail-media-canvas-viewport]')),
            sidebar: R(d.querySelector('.asset-detail-media-sidebar')),
            tabs: R(d.querySelector('.asset-detail-media-sidebar-tabs')),
            overflowX: d.documentElement.scrollWidth - d.documentElement.clientWidth,
            typeTexts: [...d.querySelectorAll('.asset-detail-media-sidebar-detail .asset-detail-title > span')].map(e=>e.innerText.trim()),
            actions: d.querySelectorAll('.asset-detail-actions').length,
            shortcuts: d.querySelectorAll('.asset-detail-shortcuts').length,
            activeBg: cs ? cs.backgroundImage.slice(0,90) : null,
            path: d.querySelector('[data-detail-path-value]')?.innerText.trim(),
        };
    });
    info.width = w; info.opened = opened;
    results.push(info);
    await page.screenshot({path: QA+`matrix-${theme}-${w}.png`});
}
fs.writeFileSync(QA+`matrix-${theme}.json`, JSON.stringify(results,null,1));
console.log(JSON.stringify(results.map(r=>({w:r.width,opened:r.opened,topTheme:r.topTheme,frameTheme:r.frameTheme,bodyBg:r.bodyBg,frameInnerW:r.frameInnerW,type:r.type,sidebar:r.sidebar?.top,viewport:r.viewport?.top,toolbar:r.toolbar?.top,overflowX:r.overflowX,tabs:r.tabs?.top,typeTexts:r.typeTexts.length,actions:r.actions,shortcuts:r.shortcuts})),null,1));
