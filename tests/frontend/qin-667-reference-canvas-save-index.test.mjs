import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createContext, runInContext } from 'node:vm';
import test from 'node:test';

const mediaCanvasSource = readFileSync(
  new URL('../../web/js/modules/asset-manager/media-canvas.js', import.meta.url),
  'utf8',
);

function extractFunction(source, startMarker, endMarker) {
  const start = source.indexOf(startMarker);
  const end = source.indexOf(endMarker, start);
  assert.notEqual(start, -1, `缺少函数起始标记：${startMarker}`);
  assert.notEqual(end, -1, `缺少函数结束标记：${endMarker}`);
  return source.slice(start, end);
}

const saveFunction = extractFunction(
  mediaCanvasSource,
  'async function saveDetailReferenceCanvas(){',
  '\nfunction replaceDetailImageEditorCanvasItem',
);

function viewerState({referenceCanvasId = '', referenceCanvasTitle = '测试参考画布'} = {}) {
  return {
    source: 'registry',
    id: 'asset-source-1',
    referenceCanvasId,
    referenceCanvasTitle,
    referenceCanvasSaving: false,
    mediaCanvasLayout: {'image-1': {x: 10, y: 20, width: 320, height: 180}},
    mediaCanvasAnnotations: [],
    mediaCanvasX: 0,
    mediaCanvasY: 0,
    mediaCanvasScale: 1,
  };
}

function createHarness({
  state = viewerState(),
  save = async () => ({canvas: {id: 'canvas-reference-1', canvas_id: 'canvas-reference-1'}}),
  refresh = async () => referenceIndex({canvas_id: 'canvas-reference-1', kind: 'reference'}),
} = {}) {
  const calls = {
    savePayloads: [],
    indexRequests: 0,
    statuses: [],
    notices: [],
    failures: [],
    renders: 0,
    normalized: 0,
  };
  let context;
  context = createContext({
    detailViewerState: state,
    canvasAssetsData: {categories: [], canvases: [], items: []},
    activeCanvasAssetCategory: '',
    activeCanvasAssetCanvasId: '',
    detailMediaCanvasItems: () => [{
      id: 'image-1',
      asset_id: 'asset-1',
      name: 'sample.png',
      media_kind: 'image',
      url: '/assets/asset-1',
    }],
    detailMediaCanvasNaturalSize: () => ({width: 320, height: 180}),
    detailMediaCanvasPersistId: item => item.asset_id,
    detailMediaCanvasPersistKey: () => 'reference-key-1',
    detailViewerUrl: (_source, item) => item.url,
    detailViewerType: () => 'image',
    detailMediaCanvasDrawings: () => [],
    detailMediaCanvasGroups: () => [],
    syncDetailReferenceTitleFromDom: () => {},
    apiJsonResponse: response => response,
    assetManagerApi: {
      saveReferenceCanvas(payload) {
        calls.savePayloads.push(payload);
        return save(payload, calls);
      },
      getCanvasAssets() {
        calls.indexRequests += 1;
        return refresh(calls);
      },
    },
    setCanvasAssetsData(value) {
      context.canvasAssetsData = value;
    },
    setActiveCanvasAssetCategory(value) {
      context.activeCanvasAssetCategory = value;
    },
    setActiveCanvasAssetCanvasId(value) {
      context.activeCanvasAssetCanvasId = value;
    },
    normalizeCanvasAssetState() {
      calls.normalized += 1;
    },
    renderDetailViewer() {
      calls.renders += 1;
    },
    setStatus(message) {
      calls.statuses.push(message);
    },
    showAssetNotice(message, type) {
      calls.notices.push({message, type});
    },
    showAssetFailure(message, error) {
      calls.failures.push({message, detail: error?.message || ''});
    },
  });
  runInContext(`${saveFunction}\nglobalThis.runSaveDetailReferenceCanvas = saveDetailReferenceCanvas;`, context);
  return {
    context,
    calls,
    save: () => context.runSaveDetailReferenceCanvas(),
  };
}

function referenceIndex(canvas) {
  return {
    categories: [{id: 'reference', name: '参考画布', count: 1}],
    canvases: [canvas],
    items: [],
  };
}

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return {promise, resolve, reject};
}

function wait(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

test('新建参考画布按 canvas_id 对账成功并进入参考画布索引', async () => {
  const id = 'canvas-reference-authoritative';
  const h = createHarness({
    save: async () => ({canvas: {canvas_id: id, id}}),
    // 对应 GET /api/canvas-assets 的生命周期投影：只有权威 canvas_id。
    refresh: async () => referenceIndex({canvas_id: id, kind: 'reference', title: '测试参考画布'}),
  });

  const result = await h.save();

  assert.equal(result.canvas.canvas_id, id);
  assert.equal(h.calls.savePayloads.length, 1);
  assert.equal(h.calls.indexRequests, 1);
  assert.equal(h.context.detailViewerState.referenceCanvasId, id);
  assert.equal(h.context.activeCanvasAssetCategory, 'reference');
  assert.equal(h.context.activeCanvasAssetCanvasId, id);
  assert.equal(h.context.canvasAssetsData.canvases[0].canvas_id, id);
  assert.deepEqual(h.calls.statuses, ['保存参考画布：测试参考画布 成功！已保存为 .godmap']);
  assert.equal(h.calls.notices.at(-1).type, 'success');
  assert.deepEqual(h.calls.failures, []);
});

test('兼容 id 仍可用于索引对账，更新参考画布走成功路径', async () => {
  const id = 'canvas-reference-legacy';
  const h = createHarness({
    state: viewerState({referenceCanvasId: id, referenceCanvasTitle: '已有画布'}),
    save: async () => ({canvas: {id}}),
    refresh: async () => referenceIndex({id, kind: 'reference', title: '已有画布'}),
  });

  await h.save();

  assert.equal(h.calls.savePayloads[0].canvas_id, id);
  assert.equal(h.context.detailViewerState.referenceCanvasId, id);
  assert.deepEqual(h.calls.statuses, ['更新参考画布：已有画布 成功！已保存为 .godmap']);
  assert.equal(h.calls.notices.at(-1).type, 'success');
  assert.deepEqual(h.calls.failures, []);
});

test('写入失败保留真实失败原因，不伪报索引对账失败', async () => {
  const h = createHarness({save: async () => { throw new Error('写入请求被拒绝'); }});

  await assert.rejects(h.save(), error => error.message === '写入请求被拒绝');

  assert.equal(h.calls.indexRequests, 0);
  assert.equal(h.calls.failures[0].detail, '写入请求被拒绝');
  assert.doesNotMatch(h.calls.failures[0].detail, /未进入.*参考画布.*索引/);
});

test('保存响应没有画布 ID 时给出缺 ID 错误且不刷新索引', async () => {
  const h = createHarness({save: async () => ({canvas: {kind: 'reference'}})});

  await assert.rejects(h.save(), /服务端没有返回参考画布 ID/);

  assert.equal(h.calls.indexRequests, 0);
  assert.match(h.calls.failures[0].detail, /服务端没有返回参考画布 ID/);
  assert.doesNotMatch(h.calls.failures[0].detail, /未进入.*参考画布.*索引/);
});

test('索引刷新失败说明画布已写入，不改报为未进入索引', async () => {
  const id = 'canvas-reference-written';
  const h = createHarness({
    save: async () => ({canvas: {id, canvas_id: id}}),
    refresh: async () => { throw new Error('索引服务暂不可用'); },
  });

  await assert.rejects(h.save(), /画布已写入，但资产索引刷新失败/);

  assert.equal(h.context.detailViewerState.referenceCanvasId, id);
  assert.match(h.calls.failures[0].detail, /画布已写入，但资产索引刷新失败/);
  assert.doesNotMatch(h.calls.failures[0].detail, /未进入.*参考画布.*索引/);
});

test('同 ID 但不是参考画布时才报告未进入参考画布索引', async () => {
  const id = 'canvas-reference-wrong-kind';
  const h = createHarness({
    save: async () => ({canvas: {id, canvas_id: id}}),
    refresh: async () => referenceIndex({canvas_id: id, kind: 'classic'}),
  });

  await assert.rejects(h.save(), /未进入“参考画布”索引/);
  assert.match(h.calls.failures[0].detail, /未进入“参考画布”索引/);
});

test('1500ms 保存期间不提前成功，重复点击仍只提交一次', async () => {
  const id = 'canvas-reference-slow-save';
  const h = createHarness({
    save: async () => {
      await wait(1500);
      return {canvas: {id, canvas_id: id}};
    },
    refresh: async () => referenceIndex({canvas_id: id, kind: 'reference'}),
  });

  const pending = h.save();
  await h.save();
  await wait(100);

  assert.equal(h.calls.savePayloads.length, 1);
  assert.equal(h.calls.indexRequests, 0);
  assert.equal(h.calls.statuses.some(message => message.includes('成功！')), false);

  await pending;
  assert.equal(h.calls.statuses.at(-1), '保存参考画布：测试参考画布 成功！已保存为 .godmap');
});

test('5000ms 索引刷新期间不提前成功', async () => {
  const id = 'canvas-reference-slow-index';
  const h = createHarness({
    save: async () => ({canvas: {id, canvas_id: id}}),
    refresh: async () => {
      await wait(5000);
      return referenceIndex({canvas_id: id, kind: 'reference'});
    },
  });

  const pending = h.save();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(h.calls.indexRequests, 1);
  await wait(100);
  assert.equal(h.calls.statuses.some(message => message.includes('成功！')), false);

  await pending;
  assert.equal(h.calls.statuses.at(-1), '保存参考画布：测试参考画布 成功！已保存为 .godmap');
});

test('切换详情后迟到的保存响应不覆盖新参考画布', async () => {
  const oldSave = deferred();
  const newSave = deferred();
  const newIndex = deferred();
  let saveNumber = 0;
  const h = createHarness({
    save: () => [oldSave.promise, newSave.promise][saveNumber++],
    refresh: () => newIndex.promise,
  });
  const first = h.save();
  h.context.detailViewerState = viewerState({referenceCanvasTitle: '新详情'});
  const second = h.save();

  newSave.resolve({canvas: {canvas_id: 'canvas-new', id: 'canvas-new'}});
  await new Promise(resolve => setImmediate(resolve));
  newIndex.resolve(referenceIndex({canvas_id: 'canvas-new', kind: 'reference'}));
  await second;
  oldSave.resolve({canvas: {canvas_id: 'canvas-old', id: 'canvas-old'}});
  await first;

  assert.equal(h.context.detailViewerState.referenceCanvasId, 'canvas-new');
  assert.equal(h.context.canvasAssetsData.canvases[0].canvas_id, 'canvas-new');
  assert.equal(h.calls.indexRequests, 1);
  assert.deepEqual(h.calls.failures, []);
});

test('索引响应乱序时只应用当前详情对应的结果', async () => {
  const oldIndex = deferred();
  const newIndex = deferred();
  let indexNumber = 0;
  const h = createHarness({
    save: async (_payload, calls) => {
      const id = `canvas-${calls.savePayloads.length}`;
      return {canvas: {canvas_id: id, id}};
    },
    refresh: () => [oldIndex.promise, newIndex.promise][indexNumber++],
  });
  const first = h.save();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(h.calls.indexRequests, 1);

  h.context.detailViewerState = viewerState({referenceCanvasTitle: '新详情'});
  const second = h.save();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(h.calls.indexRequests, 2);

  newIndex.resolve(referenceIndex({canvas_id: 'canvas-2', kind: 'reference'}));
  await second;
  oldIndex.resolve(referenceIndex({canvas_id: 'canvas-1', kind: 'reference'}));
  await first;

  assert.equal(h.context.detailViewerState.referenceCanvasId, 'canvas-2');
  assert.equal(h.context.canvasAssetsData.canvases[0].canvas_id, 'canvas-2');
  assert.deepEqual(h.calls.failures, []);
});
