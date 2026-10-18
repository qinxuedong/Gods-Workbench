import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { createCanvasListApi } from '../../web/js/modules/canvas-list/api.js';

const canvasList = readFileSync('web/js/modules/canvas-list.js', 'utf8');
const storyboard = readFileSync('web/pages/storyboard.html', 'utf8');

function response(payload, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => payload,
  };
}

test('QIN-200 总览请求聚合默认项目与所有项目并按稳定 canvas_id 去重', async () => {
  const calls = [];
  const api = createCanvasListApi({
    request: async url => {
      calls.push(url);
      const projectId = new URL(`http://127.0.0.1${url}`).searchParams.get('project_id');
      if (projectId === 'default') {
        return response({ canvases: [{ canvas_id: 'cv-default', project_id: 'default' }] });
      }
      return response({ canvases: [
        { canvas_id: `cv-${projectId}`, project_id: projectId },
        { canvas_id: 'cv-default', project_id: 'default' },
      ] });
    },
  });

  const result = await api.listCanvasesForOverview(['project-b', 'default', 'project-a', 'project-b']);
  assert.equal(result.ok, true);
  assert.deepEqual((await result.json()).canvases.map(item => item.canvas_id), [
    'cv-project-b', 'cv-default', 'cv-project-a',
  ]);
  assert.deepEqual(calls, [
    '/api/canvases?project_id=project-b',
    '/api/canvases?project_id=default',
    '/api/canvases?project_id=project-a',
  ]);
});

test('QIN-200 总览加载使用多项目聚合，创建后广播分类联动', () => {
  assert.match(canvasList, /listCanvasesForOverview\(/);
  assert.match(canvasList, /type:\s*['"]canvas-created['"]/);
  assert.match(canvasList, /canvasScope\s*===\s*['"]project['"]\s*\?\s*['"]project['"]\s*:\s*['"]all['"]/);
  assert.match(canvasList, /if\s*\(CANVAS_LIST_OVERVIEW\)\s*\{?\s*return canvases/);
  assert.doesNotMatch(canvasList, /el\.dataset\.floatingClose\s*=\s*['"]closeCreateCard['"]/);
});

test('QIN-200 分镜宿主接收画布创建消息并同步当前总览分类', () => {
  assert.match(storyboard, /event\.data\?\.type === ['"]canvas-created['"]/);
  assert.match(storyboard, /canvas-filter/);
});
