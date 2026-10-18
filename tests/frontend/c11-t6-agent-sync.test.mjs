import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {runInNewContext} from 'node:vm';

const linksSource = readFileSync(new URL('../../web/js/modules/project-artifact-links.js', import.meta.url), 'utf8');
const storyboardSource = readFileSync(new URL('../../web/js/controllers/storyboard-controller.js', import.meta.url), 'utf8');
const projectsHtml = readFileSync(new URL('../../web/pages/projects.html', import.meta.url), 'utf8');
const storyboardHtml = readFileSync(new URL('../../web/pages/storyboard.html', import.meta.url), 'utf8');

function loadLinks() {
  const window = {};
  runInNewContext(linksSource, {window, document: {}, fetch: () => { throw new Error('unused'); }});
  return window.GWProjectArtifactLinks;
}

const approvedStoryboard = {
  artifact_id: 'artifact-storyboard',
  version_id: 'storyboard-v2',
  revision: 2,
  version_label: 'v002',
  review_id: 'review-storyboard-v2',
  export_formats: ['csv'],
  export_uri: '/api/agent/runs/run-2/export?format=csv&review_id=review-storyboard-v2&artifact_version_id=storyboard-v2',
  created_at: '2026-10-18T10:00:00Z',
  lifecycle: 'valid',
};

test('项目产物模块只选择最新已审核 storyboard_text CSV 版本', () => {
  const api = loadLinks();
  const older = {...approvedStoryboard, version_id: 'storyboard-v1', revision: 1, version_label: 'v001', created_at: '2026-10-17T10:00:00Z'};
  const unapprovedNewer = {...approvedStoryboard, version_id: 'storyboard-v3', revision: 3, version_label: 'v003', review_id: null, export_uri: null, created_at: '2026-10-19T10:00:00Z'};
  const selected = api.latestApprovedStoryboard([older, unapprovedNewer, approvedStoryboard]);
  assert.equal(selected.version_id, 'storyboard-v2');
  assert.equal(api.latestApprovedStoryboard([unapprovedNewer]), null);
});
test('文字分镜 CSV 解析保留带逗号与换行的引号字段', () => {
  const api = loadLinks();
  const csv = '\ufeffshot_id,scene_id,description,camera,movement,duration_seconds\r\n'
    + 'SC01_SH001,SC01,"夜雨, 霓虹\n反光",近景,推近,3.5\r\n';
  const rows = api.parseStoryboardCsv(csv);
  assert.deepEqual(JSON.parse(JSON.stringify(rows)), [{
    shot_id: 'SC01_SH001', scene_id: 'SC01', description: '夜雨, 霓虹\n反光',
    camera: '近景', movement: '推近', duration_seconds: '3.5',
  }]);
});

test('同步读取只沿用精确 export_uri，并返回 CSV 行与版本身份', async () => {
  const api = loadLinks();
  const calls = [];
  const fetchImpl = async (url) => {
    calls.push(url);
    if (url.includes('/artifacts?')) return {ok: true, json: async () => ({items: [approvedStoryboard]})};
    return {ok: true, text: async () => 'shot_id,scene_id,description\nSC01_SH001,SC01,开场\n'};
  };
  const result = await api.loadLatestApprovedStoryboard('proj/1', fetchImpl);
  assert.equal(result.artifact.version_id, 'storyboard-v2');
  assert.equal(result.rows[0].shot_id, 'SC01_SH001');
  assert.deepEqual(calls, [
    '/api/agent/projects/proj%2F1/artifacts?limit=200',
    approvedStoryboard.export_uri,
  ]);
});

test('项目卡片与分镜页接入 C11-T6 入口', () => {
  assert.match(linksSource, /agents\.html\?project_id=/);
  assert.match(linksSource, /data-gw-agent-project-link/);
  assert.match(linksSource, /data-gw-agent-status/);
  assert.match(storyboardHtml, /id="btnSyncAgentStoryboard"/);
  assert.match(storyboardHtml, /project-artifact-links\.js/);
  assert.ok(storyboardHtml.indexOf('project-artifact-links.js') < storyboardHtml.indexOf('storyboard-controller.js'));
  assert.match(storyboardSource, /syncLatestAgentStoryboard/);
  assert.match(storyboardSource, /loadLatestApprovedStoryboard/);
  assert.match(storyboardSource, /artifact_version_id/);
});
