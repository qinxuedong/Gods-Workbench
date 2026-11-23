/**
 * QIN-635：安装包 JXL 缩略图兼容、扩展名准入、回退重试与慢请求超时守卫。
 */
import assert from 'node:assert/strict';
import { readAssetManagerAggregate } from './asset-manager-aggregate.mjs';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const ASSET_MANAGER = readAssetManagerAggregate();
const PREVIEW_JS = readFileSync(
  new URL('../../web/js/modules/asset-manager/preview.js', import.meta.url),
  'utf8'
);

function functionBody(source, name) {
  const start = source.indexOf(`function ${name}(`);
  assert.ok(start > 0, `未找到函数 ${name}`);
  const next = source.indexOf('\nfunction ', start + 1);
  const fallback = source.indexOf('\nasync function ', start + 1);
  const end = Math.min(...[next, fallback].filter(index => index > 0));
  return source.slice(start, Number.isFinite(end) ? end : undefined);
}

test('QIN-635：.jxl 纳入缩略图代理准入且不纳入浏览器直通集，preview.js 统一走 assetPreviewUrl', () => {
  assert.match(ASSET_MANAGER, /ASSET_IMAGE_PREVIEW_EXTENSIONS = new Set\(\[[^\]]*'jxl'/);
  assert.match(ASSET_MANAGER, /ASSET_PREVIEW_EXTENSIONS = new Set\(\[[^\]]*'jxl'/);
  assert.match(ASSET_MANAGER, /LOCAL_MEDIA_EXTS = \/\\\.\([^\n]*\|jxl\|/);
  const browserPreviewMatch = ASSET_MANAGER.match(/const ASSET_BROWSER_PREVIEW_EXTENSIONS = new Set\(\[([^\]]*)\]\);/);
  assert.ok(browserPreviewMatch);
  assert.doesNotMatch(browserPreviewMatch[1], /'jxl'/);
  assert.doesNotMatch(PREVIEW_JS, /`\/api\/media-preview\?w=220/);
  assert.match(PREVIEW_JS, /assetPreviewUrl\(item\.url \|\| url, 220, item\)/);
  assert.match(PREVIEW_JS, /class="asset-lightbox-image"[^>]*data-preview-fallback=/);
});

test('QIN-635：能力探测不支持 JXL 时 assetPreviewUrl 附带 fallback=webp，withPreviewFallbackUrl 幂等', () => {
  const factory = new Function(`
    let clientJxlSupport = false;
    let clientJxlProbePromise = null;
    const JXL_SUPPORT_PROBE_URI = 'data:image/jxl;base64,/woIELASCAgQBABw';
    let thumbnailCacheVersion = 0;
    const ASSET_PREVIEW_RENDER_VERSION = '2026.08.01.5';
    const ASSET_VIDEO_EXTENSIONS = new Set(['mp4']);
    const ASSET_PREVIEW_EXTENSIONS = new Set(['jxl', 'png']);
    ${functionBody(ASSET_MANAGER, 'assetFileExtension')}
    ${functionBody(ASSET_MANAGER, 'assetSupportsImagePreview')}
    ${functionBody(ASSET_MANAGER, 'detectJxlSupport')}
    ${functionBody(ASSET_MANAGER, 'applyClientJxlFallbackParam')}
    ${functionBody(ASSET_MANAGER, 'withPreviewFallbackUrl')}
    ${functionBody(ASSET_MANAGER, 'assetPreviewUrl')}
    return { assetPreviewUrl, withPreviewFallbackUrl };
  `);
  const { assetPreviewUrl, withPreviewFallbackUrl } = factory();
  const jxlUrl = assetPreviewUrl('/api/asset-registry/assets/a1/media', 256, {
    id: 'a1',
    name: 'photo.jxl',
    storage_root_id: 'root1',
    updated_at: 100,
  });
  assert.match(jxlUrl, /^\/api\/media-preview\?/);
  assert.match(jxlUrl, /fallback=webp/);
  assert.equal(withPreviewFallbackUrl('/api/media-preview?w=256&asset_id=a1'), '/api/media-preview?w=256&asset_id=a1&fallback=webp');
  assert.equal(withPreviewFallbackUrl('/api/media-preview?w=256&fallback=webp'), '');
});

test('QIN-635 DoD 慢档 9s/20s 与代际取消：超过 8s 触发回退与可见失败，切目录取消不写入新视图', () => {
  const timers = new Map();
  let nextTimerId = 1;
  const fakeSetTimeout = (fn, ms) => {
    const id = nextTimerId++;
    timers.set(id, { fn, ms });
    return id;
  };
  const fakeClearTimeout = (id) => {
    timers.delete(id);
  };

  let replacedWith = null;
  const runWatchdogFactory = new Function(
    'setTimeout',
    'clearTimeout',
    'document',
    `
    const REGISTRY_PREVIEW_PLACEHOLDER = 'data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7';
    const REGISTRY_PREVIEW_TIMEOUT_MS = 8000;
    const registryPreviewWatchdogs = new Map();
    let registryPreviewScheduleRevision = 1;
    let registryPreviewQueueRevision = 1;
    let registryPreviewQueue = [];
    let registryPreviewObserver = null;
    let registryPreviewSrcObserver = null;
    let registryPreviewFallbackCleanup = null;
    let registryVirtualScrollCleanup = null;
    let registryVirtualObserver = null;
    let registryVirtualScrollFrame = 0;
    let registryMasonryScheduleRevision = 1;
    let registryMasonryObserver = null;
    const gwCall = () => {};
    const refreshIcons = () => {};
    const escapeHtml = s => String(s);
    ${functionBody(ASSET_MANAGER, 'withPreviewFallbackUrl')}
    ${functionBody(ASSET_MANAGER, 'clearRegistryPreviewWatchdog')}
    ${functionBody(ASSET_MANAGER, 'clearRegistryPreviewWatchdogs')}
    ${functionBody(ASSET_MANAGER, 'replaceImageWithPreviewUnavailable')}
    ${functionBody(ASSET_MANAGER, 'attachRegistryPreviewWatchdog')}
    ${functionBody(ASSET_MANAGER, 'disconnectRegistryPreviewScheduling')}
    ${functionBody(ASSET_MANAGER, 'clearRegistryPreviewObservers')}
    return { attachRegistryPreviewWatchdog, disconnectRegistryPreviewScheduling, registryPreviewWatchdogs };
    `
  );
  const { attachRegistryPreviewWatchdog, disconnectRegistryPreviewScheduling } = runWatchdogFactory(
    fakeSetTimeout,
    fakeClearTimeout,
    { createElement: () => ({ className: '', innerHTML: '' }) }
  );

  const img = {
    isConnected: true,
    complete: false,
    naturalWidth: 0,
    dataset: { previewFallback: 'JXL' },
    attrs: { src: '/api/media-preview?w=256&asset_id=a1' },
    _src: '/api/media-preview?w=256&asset_id=a1',
    get src() { return this._src; },
    set src(v) { this._src = v; this.attrs.src = v; },
    getAttribute(k) { return this.attrs[k] || ''; },
    setAttribute(k, v) { this.attrs[k] = v; },
    removeAttribute(k) { delete this.attrs[k]; },
    addEventListener() {},
    replaceWith(node) { replacedWith = node; },
  };

  attachRegistryPreviewWatchdog(img, 1);
  // 3 秒内：尚未超时，保持 aria-busy=true 占位状态，未提前标就绪。
  assert.equal(img.attrs['aria-busy'], 'true');
  assert.notEqual(img.dataset.previewReady, 'true');
  assert.equal(timers.size, 1);

  // 超过 8 秒（模拟 9s）：首次触发 8000ms 看门狗，自动切换 fallback=webp 重试。
  const firstTimer = [...timers.values()][0];
  assert.equal(firstTimer.ms, 8000);
  timers.clear();
  firstTimer.fn();
  assert.equal(img.dataset.jxlFallbackAttempted, '1');
  assert.match(img.src, /fallback=webp/);
  assert.equal(replacedWith, null);

  // 再次超过 8 秒（模拟总计 20s 仍无响应）：触发可见失败占位，绝不无限转圈。
  const secondTimer = [...timers.values()][0];
  timers.clear();
  secondTimer.fn();
  assert.equal(img.dataset.registryPreviewFailed, 'true');
  assert.ok(replacedWith && replacedWith.className.includes('preview-unavailable'));

  // 代际取消：切目录后清理所有在途定时器，旧回调不生效。
  const staleImg = { ...img, attrs: { src: '/api/media-preview?w=256&asset_id=old' }, _src: '/api/media-preview?w=256&asset_id=old', dataset: { previewFallback: 'JXL' } };
  attachRegistryPreviewWatchdog(staleImg, 1);
  assert.equal(timers.size, 1);
  disconnectRegistryPreviewScheduling();
  assert.equal(timers.size, 0);
});
