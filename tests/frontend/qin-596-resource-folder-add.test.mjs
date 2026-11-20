// [QIN-596] 「+ 添加资源文件夹」共享管道、前端拒绝清单与异步治理自动化测试
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const manager = readFileSync(new URL('../../web/js/modules/asset-manager.js', import.meta.url), 'utf8');
const organize = readFileSync(new URL('../../web/js/modules/asset-manager/organize.js', import.meta.url), 'utf8');
const settings = readFileSync(new URL('../../web/js/modules/asset-manager/settings.js', import.meta.url), 'utf8');

test('QIN-596 AC2: 侧栏按钮/分组/空态/弹窗标题文案统一', () => {
  assert.match(manager, /aria-label="\+ 添加资源文件夹"/);
  assert.match(manager, /<span>\+ 添加资源文件夹<\/span>/);
  assert.match(manager, /<span>资源文件夹<\/span>/);
  assert.match(manager, /<p>尚未添加资源文件夹<\/p>/);
  assert.match(manager, /添加后按真实磁盘子目录浏览/);
  assert.doesNotMatch(manager, /<span>挂载文件夹<\/span>/);
  assert.match(organize, /<strong id="boardSourceTitle">添加资源文件夹<\/strong>/);
  assert.doesNotMatch(organize, /素材来源设置|添加上传来源/);
});

test('QIN-596 AC3: 共享管道——attach 内置 ensure，旧建看板入口已移除', () => {
  assert.match(organize, /const allowed = options\.atomicRegistration\s*\?\s*true\s*:\s*await ensureCreateLibraryPathAllowedRoot\(cleanPath\);/);
  assert.match(organize, /authorize_root:Boolean\(options\.atomicRegistration\)/);
  assert.match(organize, /if\(!allowed\) return null;/);
  assert.doesNotMatch(organize, /async function createPresetAssetBoard/);
  assert.match(manager, /gwCall\('organize', 'openBoardSourceModal', '', 'new'\)/);
});

test('QIN-596 AC5: 前端拒绝清单纯函数逐条（双端一致）', () => {
  const start = settings.indexOf('const RESOURCE_FOLDER_BLOCKED_WIN_SEGMENTS');
  const end = settings.indexOf('async function ensureCreateLibraryPathAllowedRoot');
  assert.ok(start > 0 && end > start);
  const extract = new Function(`${settings.slice(start, end)}\nreturn describeResourceFolderPathRejection;`);
  const reject = extract();
  // 拒绝样例（与后端契约测试 BLOCKED_SAMPLES 一致）
  assert.equal(reject('C:\\'), '盘符根目录不可添加为资源文件夹');
  assert.equal(reject('C:'), '盘符根目录不可添加为资源文件夹');
  assert.equal(reject('\\\\NAS'), 'UNC 机器根不可添加为资源文件夹，请指定具体共享目录');
  assert.equal(reject('/'), '文件系统根目录不可添加为资源文件夹');
  assert.match(reject('C:\\Windows'), /系统目录不可添加/);
  assert.match(reject('C:\\Windows\\Fonts'), /系统目录不可添加/);
  assert.match(reject('E:/Program Files/工具'), /系统目录不可添加/);
  assert.match(reject('C:\\ProgramData'), /系统目录不可添加/);
  assert.match(reject('C:\\Users'), /用户配置目录根不可添加/);
  assert.match(reject('C:\\Users\\某用户'), /用户配置目录根不可添加/);
  assert.match(reject('/Users'), /用户配置目录根不可添加/);
  assert.match(reject('/Users/某用户'), /用户配置目录根不可添加/);
  assert.match(reject('/System'), /系统目录不可添加/);
  assert.match(reject('/usr/bin'), /系统目录不可添加/);
  // 不拒样例
  assert.equal(reject('D:\\Assets'), '');
  assert.equal(reject('\\\\NAS\\share'), '');
  assert.equal(reject('C:\\Users\\某用户\\Documents'), '');
  assert.equal(reject('/Volumes/素材'), '');
  assert.equal(reject(''), '路径为空');
  // 审核 FIX_REQUIRED：点段消解——`C:/foo/../Windows` 不得绕过清单。
  assert.match(reject('C:/foo/../Windows'), /系统目录不可添加/);
  assert.match(reject('C:\\foo\\..\\Windows'), /系统目录不可添加/);
  assert.match(reject('/foo/../System'), /系统目录不可添加/);
  assert.match(reject('C:/../Windows'), /越界点段/);
  assert.match(reject('C:/foo/../../Windows'), /越界点段/);
  // 合法点段（消解后非系统目录）不扩大拒绝面。
  assert.equal(reject('D:/Assets/sub/../Assets2'), '');
});

test('QIN-596 AC4/AC6: 权限提示、409 RESOLVING 与 known 必 reindex', () => {
  // 403 PATH_OUTSIDE_ALLOWED_ROOTS → 「该文件夹尚未授权，请联系管理员」
  assert.match(organize, /该文件夹尚未授权，请联系管理员/);
  // 409 ASSET_ROOT_ALREADY_REGISTERED → RESOLVING（刷新 settings 按 pathCompareKey 解析）
  assert.match(organize, /ASSET_ROOT_ALREADY_REGISTERED/);
  assert.match(organize, /applyStorageSettingsData\(saved \|\| \{\}\);\s*\n\s*const existing = localLibraries\.find\(lib => pathCompareKey\(lib\.path\) === pathCompareKey\(cleanPath\)\);/);
  // known 命中必提交纯 reindex（attach 内，与 existingId 分支同口径）
  const attachStart = organize.indexOf('async function attachPathToAssetBoard');
  const attachEnd = organize.indexOf('function renderBoardSourceModal');
  const attachFn = organize.slice(attachStart, attachEnd);
  assert.match(attachFn, /reindexAssetSource\(source\.id\)/);
  assert.match(attachFn, /正在重建/);
  // attemptKey 会话复用
  assert.match(attachFn, /const attemptKey = `asset-register:/);
  assert.match(attachFn, /'Idempotency-Key':attemptKey/);
});

test('QIN-596 AC6/AC7: 忙碌守卫、generation 丢弃迟到返回、facets 刷新代际', () => {
  assert.match(organize, /if\(boardSourceState\.saving\) return;/);
  assert.match(organize, /boardSourceState\.generation = Number\(boardSourceState\.generation \|\| 0\) \+ 1;/);
  assert.match(organize, /if\(Number\(boardSourceState\.generation \|\| 0\) !== generation\) return;/);
  assert.match(manager, /registryFacetRefreshGeneration/);
  assert.match(manager, /if\(generation !== registryFacetRefreshGeneration\) return;/);
});

test('QIN-596 §2: generation guard 运行时行为——乱序响应只采用最新代际（移除 guard 即覆盖）', async () => {
  // 审核 FIX_REQUIRED：源码匹配不能证明行为。此处把 refreshRegistryFacetData
  // 抽进受控异步环境运行：先发起两次刷新，让第 2 次（最新代际）先返回、
  // 第 1 次（旧代际）后返回；有 guard 时旧响应必须被丢弃。
  const start = manager.indexOf('let registryFacetRefreshGeneration');
  const end = manager.indexOf('function registryStatusJobKey');
  assert.ok(start > 0 && end > start);
  const source = manager.slice(start, end);
  const GUARD = 'if(generation !== registryFacetRefreshGeneration) return;';

  const build = (withoutGuard) => {
    const guarded = withoutGuard ? source.replace(GUARD, '') : source;
    if(withoutGuard) assert.notEqual(guarded, source, '移除 guard 的替换必须真实生效');
    const pending = [];
    const factory = new Function('apiJsonResponse', 'assetManagerApi', `
      let registryFacets = { roots: [], kinds: [], categories: [], tags: [], collections: [] };
      ${guarded}
      return { refresh: refreshRegistryFacetData, get facets() { return registryFacets; } };
    `);
    const harness = factory(
      () => new Promise(resolve => pending.push(resolve)),
      { getRegistryFacets: () => ({ url: '/api/asset-registry/facets' }) },
    );
    return { harness, pending };
  };

  // 有 guard：旧代际响应迟到被丢弃，视图保留最新数据。
  const guarded = build(false);
  const first = guarded.harness.refresh();
  const second = guarded.harness.refresh();
  assert.equal(guarded.pending.length, 2);
  guarded.pending[1]({ roots: ['NEW'], kinds: [], categories: [], tags: [], collections: [] });
  await second;
  guarded.pending[0]({ roots: ['OLD'], kinds: [], categories: [], tags: [], collections: [] });
  await first;
  assert.deepEqual(guarded.harness.facets.roots, ['NEW']);

  // 无 guard（同一驱动顺序）：旧响应覆盖新视图——证明 guard 是真实行为屏障。
  const broken = build(true);
  const third = broken.harness.refresh();
  const fourth = broken.harness.refresh();
  broken.pending[1]({ roots: ['NEW'], kinds: [], categories: [], tags: [], collections: [] });
  await fourth;
  broken.pending[0]({ roots: ['OLD'], kinds: [], categories: [], tags: [], collections: [] });
  await third;
  assert.deepEqual(broken.harness.facets.roots, ['OLD']);
});
