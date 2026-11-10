// QIN-547: 收集箱——合并托管资产与上传资源为默认收集文件夹
import assert from 'node:assert/strict';
import { readAssetManagerAggregate } from './asset-manager-aggregate.mjs';
import {readFileSync} from 'node:fs';
import test from 'node:test';

const source = readAssetManagerAggregate();
const styles = readFileSync(new URL('../../web/css/pages/asset-manager.css', import.meta.url), 'utf8');

function functionSource(name){
    const marker = `function ${name}(`;
    const start = source.indexOf(marker);
    assert.ok(start >= 0, `函数应存在：${name}`);
    let depth = 0;
    for(let index = source.indexOf('{', start); index < source.length; index += 1){
        if(source[index] === '{') depth += 1;
        else if(source[index] === '}'){
            depth -= 1;
            if(depth === 0) return source.slice(start, index + 1);
        }
    }
    assert.fail(`函数未闭合：${name}`);
}

function boardSource(){
    const start = source.indexOf('const SYSTEM_ASSET_BOARDS = [');
    const end = source.indexOf('];', start);
    assert.ok(start >= 0 && end > start, '系统看板清单应存在');
    return source.slice(start, end);
}

test('AC4: 左栏不再有「托管资产」「上传资源」入口', () => {
    const boards = boardSource();
    assert.ok(!boards.includes("id:'assets'"), '托管资产看板入口应移除');
    assert.ok(!boards.includes("id:'local'"), '上传资源看板入口应移除');
    assert.ok(!boards.includes('托管资产'), '托管资产文案不得留在左栏看板');
    assert.ok(!boards.includes('上传资源'), '上传资源文案不得留在左栏看板');
    const rackTabs = source.match(/const SYSTEM_BOARD_RACK_TABS = \[[^\]]*\];/)?.[0] || '';
    assert.ok(!rackTabs.includes("'assets'") && !rackTabs.includes("'local'"), '机架标签同步移除两个入口');
});

test('AC1/AC5: 收集箱在「我的资源」第一位，带未读角标', () => {
    const mineTree = functionSource('renderAssetTreeMineTree');
    assert.match(mineTree, /mine:inbox/, '收集箱节点 key 应存在');
    assert.match(mineTree, /name:'收集箱'/, '收集箱文案应存在');
    assert.ok(mineTree.indexOf('mine:inbox') < mineTree.indexOf('assetTreeRootsList()'), '收集箱排在挂载文件夹之前');
    assert.match(mineTree, /badge:assetInboxUnreadCount\(\)/, '未读数量应作为角标传入');
    assert.match(functionSource('assetInboxUnreadCount'), /unread_count/, '角标读取后端未读数');
    const nodeStart = source.indexOf('function renderAssetTreeNode(');
    const nodeBody = source.slice(nodeStart, source.indexOf('\nfunction ', nodeStart + 10));
    assert.match(nodeBody, /asset-tree-badge/, '节点渲染应输出角标元素');
    assert.match(styles, /\.asset-tree-badge\s*\{/, '角标样式应存在');
});

test('AC1/AC2: 收集箱视图读取真实接口并区分三类来源', () => {
    assert.match(functionSource('loadAssetInbox'), /'\/api\/asset-inbox\?limit=500'/, '列表走收集箱接口');
    // QIN-591：inbox 域内对主文件状态的写入改写为 setter 调用（登记在案），读取语义不变。
    assert.match(functionSource('loadAssetInbox'), /setInboxSettings\(data\.settings/, '设置随列表一起读取');
    assert.match(functionSource('renderInboxSourceChips'), /collect_inbox/, '浏览器收集来源应可筛选');
    assert.match(functionSource('renderInboxSourceChips'), /asset_library/, '托管资产来源应可筛选');
    assert.match(functionSource('renderInboxManager'), /renderInboxCard/, '收集箱素材应渲染卡片');
    assert.match(functionSource('openAssetInbox'), /setActiveTab\('inbox'\)/, '进入收集箱切换视图');
    assert.match(functionSource('selectAssetTreeNode'), /gwCall\('inbox', 'openAssetInbox'\)/, '树节点点击应打开收集箱');
    assert.match(source, /else if\(activeTab === 'inbox'\) gwLoad\('inbox'\)\.then\(m => m\.renderInboxManager\(\)\);/, '主渲染分发应接入收集箱');
});

test('AC1: 设置里可改位置并提示是否迁移已有文件', () => {
    const panel = functionSource('renderInboxSettingsPanel');
    assert.match(panel, /data-inbox-path/, '位置输入框应存在');
    assert.match(panel, /data-inbox-migrate/, '迁移勾选项应存在');
    assert.match(panel, /迁移到新位置/, '迁移提示文案应存在');
    const save = functionSource('saveAssetInboxSettings');
    assert.match(save, /'\/api\/asset-inbox\/settings'/, '保存位置走收集箱设置接口');
    assert.match(save, /method:'PUT'/, '保存位置使用 PUT');
    assert.match(save, /migrate/, '请求体携带迁移开关');
});

test('AC2/AC3: 上传落收集箱、迁移托管资产有独立入口', () => {
    const upload = functionSource('uploadAssetInboxFiles');
    assert.match(upload, /FormData/, '上传使用 multipart');
    assert.match(upload, /'\/api\/asset-inbox\/upload'/, '上传走收集箱接口');
    const migrate = functionSource('migrateAssetLibraryToInbox');
    assert.match(migrate, /'\/api\/asset-inbox\/migrate'/, '迁移走收集箱迁移接口');
    assert.match(migrate, /moved_files/, '迁移结果回显迁入文件数');
    assert.match(migrate, /源文件保留/, '迁移提示应说明源文件保留，避免用户以为原位置被清空');
    assert.match(functionSource('renderInboxManager'), /data-inbox-migrate-run/, '视图应有迁移入口');
    assert.match(functionSource('renderInboxManager'), /data-inbox-upload/, '视图应有上传入口');
    assert.match(source, /record:'仅记录'/, '无访问地址的托管记录应有「仅记录」标签，不伪装成链接');
});

test('AC5: 角标清零后立即重绘，不等下一次 render', () => {
    const viewed = functionSource('markAssetInboxViewed');
    assert.match(viewed, /const previous = assetInboxUnreadCount\(\)/, '清零前先记住旧角标');
    assert.match(viewed, /if\(previous > 0\) render\(\)/, '角标有变化时立即重绘');
});

test('AC1: 改位置失败时留住面板并说明原因，不假装成功', () => {
    const save = functionSource('saveAssetInboxSettings');
    assert.match(save, /result\.applied === false/, '必须识别后端的 applied=false');
    assert.match(save, /setInboxSettingsOpen\(true\)/, '失败时保留设置面板供用户改路径');
    assert.match(save, /收集箱位置未变更/, '失败时给出明确提示');
    assert.ok(
        save.indexOf('result.applied === false') < save.indexOf('收集箱位置已更新'),
        '失败分支必须早于成功提示，不能先报成功再报失败',
    );
});

// 【QIN-588】以下用例用 node:vm 真实执行提取出的函数（非纯源码正则）。
import vm from 'node:vm';

function liveFunction(name, globals = {}){
    const prepared = vm.createContext({
        URLSearchParams,
        Math,
        Number,
        String,
        encodeURIComponent,
        ...globals,
    });
    return vm.runInContext(`(${functionSource(name)})`, prepared);
}

test('QIN-588 AC1/AC2: 卡片预览地址只用受控查询串拼装，且按 kind 过滤', () => {
    const inboxPreviewUrl = liveFunction('inboxPreviewUrl');
    const imageItem = {preview_query: 'inbox_file=inbox_abc.png', kind: 'image'};
    const url = inboxPreviewUrl(imageItem, 320);
    assert.match(url, /^\/api\/media-preview\?/, '预览必须走约定的媒体预览端点');
    assert.ok(url.includes('inbox_file=inbox_abc.png'), '受控参数应原样携带');
    assert.ok(url.includes('w=320'), '宽度参数应生效');

    const video = inboxPreviewUrl({preview_query: 'collect_inbox_file=old.png', kind: 'video'}, 320);
    assert.ok(video.includes('collect_inbox_file=old.png'), '视频走同一端点显示真实首帧');

    assert.equal(inboxPreviewUrl({preview_query: '', kind: 'image'}, 320), '', '无受控参数不出预览');
    assert.equal(inboxPreviewUrl({preview_query: 'inbox_file=a.png', kind: 'document'}, 320), '', '非图像视频不出预览');
    assert.equal(inboxPreviewUrl({kind: 'image'}, 320), '', '缺查询串不出预览');

    const clamped = inboxPreviewUrl(imageItem, 99999);
    assert.ok(clamped.includes('w=2048'), '超宽必须收敛到 2048 上限');
});

test('QIN-588 AC1/AC2: 卡片渲染输出真实预览图并保留失败降级', () => {
    const card = functionSource('renderInboxCard');
    assert.match(card, /inboxPreviewUrl\(item, 320\)/, '卡片应请求 320 宽预览');
    assert.match(card, /data-preview-fallback=/, '预览加载失败必须保留占位降级标记');
    assert.match(card, /asset-file-icon/, '非本地条目仍回落文件图标');
    assert.match(styles, /\.inbox-preview-img\s*\{/, '卡片预览样式应存在');
});

test('QIN-588 AC2: 点击卡片打开媒体预览浮层，浮窗规范齐全', () => {
    const opener = functionSource('openInboxMediaPreview');
    assert.match(opener, /inboxPreviewUrl\(item, 2048\)/, '浮层用大图预览');
    assert.match(opener, /setAttribute\('role', 'dialog'\)/, '浮层必须是 dialog 语义');
    assert.match(opener, /setAttribute\('aria-modal', 'true'\)/, '浮层必须模态');
    assert.match(opener, /data-inbox-preview-close/, '浮层必须带显式关闭控件');
    assert.match(opener, /Escape/, 'Escape 必须关闭浮层');
    assert.match(opener, /trigger\.focus\(\)/, '关闭后焦点返还触发卡片');
    assert.match(source, /if\(document\.querySelector\('\.inbox-media-preview'\)\) return;/, '浮层打开时页面点击委托必须短路，不得穿透底层');
    assert.match(styles, /\.inbox-media-preview\s*\{/, '浮层样式应存在');
});

test('QIN-588 AC5: 停留收集箱低频回读，签名变化才重绘且保持滚动', () => {
    const signature = liveFunction('assetInboxItemsSignature', {inboxItems: [
        {id: 'inbox:a', modified_at: 1, size_bytes: 10},
        {id: 'inbox:b', modified_at: 2, size_bytes: 20},
    ]});
    const before = signature();
    const afterSame = signature();
    assert.equal(before, afterSame, '相同内容签名必须一致（不触发重绘）');

    const changed = liveFunction('assetInboxItemsSignature', {inboxItems: [
        {id: 'inbox:a', modified_at: 1, size_bytes: 10},
        {id: 'inbox:b', modified_at: 2, size_bytes: 21},
    ]});
    assert.notEqual(changed(), before, '内容变化签名必须不同（触发重绘）');

    const polling = source.slice(
        source.indexOf('inboxBadgeTimer = window.setInterval'),
        source.indexOf('},30000);'),
    );
    assert.ok(polling.length > 0, '停留刷新必须挂在 30s 低频定时器上');
    assert.match(polling, /if\(document\.hidden\) return;/, '页面隐藏时不得轮询');
    assert.match(polling, /activeTab === 'inbox'/, '停留收集箱时也要回读列表');
    assert.match(polling, /assetInboxItemsSignature\(\)/, '回读后必须比较内容签名');
    assert.match(polling, /scrollTop/, '重绘前记录滚动位置、重绘后恢复');
    assert.ok(
        polling.indexOf('assetInboxItemsSignature()') < polling.indexOf('scrollTop'),
        '签名无变化不得走到重绘分支',
    );
});

test('QIN-588 AC4: 上传与角标清零的乱序保护保持完整', () => {
    // 写后必刷：上传后 refresh 不复用在途旧响应（QIN-585 既有口径，本票不得回退）。
    const upload = functionSource('uploadAssetInboxFiles');
    // QIN-591：上传后的写后必刷派发 gwCall 化（登记在案），刷新语义不变。
    assert.match(upload, /gwCall\('inbox', 'loadAssetInbox', \{refresh:true\}\)/, '上传后必须写后必刷');
    const load = functionSource('loadAssetInbox');
    assert.match(load, /token\.seq !== inboxRequestSeq\)? return/, '过期响应必须按代次丢弃');
    assert.match(load, /inboxInFlight/, '普通读取必须单飞复用');
    const viewed = functionSource('markAssetInboxViewed');
    assert.match(viewed, /inboxRequestSeq \+= 1/, '角标清零必须作废更早的读取响应');
});
