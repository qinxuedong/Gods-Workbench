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
  assert.match(organize, /'素材来源设置' : '添加资源文件夹'/);
  assert.doesNotMatch(organize, /添加上传来源/);
});

test('QIN-596 AC3: 共享管道——attach 内置 ensure，创建资源库不再单独调用', () => {
  assert.match(organize, /const allowed = await ensureCreateLibraryPathAllowedRoot\(cleanPath\);/);
  assert.match(organize, /if\(!allowed\) return null;/);
  const createStart = organize.indexOf('async function createPresetAssetBoard');
  const createEnd = organize.indexOf('function attachSourceRecordToBoard');
  const createFn = organize.slice(createStart, createEnd);
  assert.ok(createStart > 0 && createEnd > createStart);
  assert.doesNotMatch(createFn, /ensureCreateLibraryPathAllowedRoot/);
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
