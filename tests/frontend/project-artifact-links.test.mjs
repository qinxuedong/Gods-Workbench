import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {runInNewContext} from 'node:vm';

const source = readFileSync(new URL('../../web/js/modules/project-artifact-links.js', import.meta.url), 'utf8');
const html = readFileSync(new URL('../../web/pages/projects.html', import.meta.url), 'utf8');
const controller = readFileSync(new URL('../../web/js/controllers/projects-controller.js', import.meta.url), 'utf8');

function makeDoc() {
    const byId = new Map();
    const doc = {
        body: null,
        createElement(tag) {
            const node = {
                tagName: tag.toUpperCase(), children: [], dataset: {}, hidden: false, textContent: '', className: '',
                style: {}, attrs: {}, ownerDocument: doc, parent: null,
                setAttribute(k, v) { this.attrs[k] = v; },
                appendChild(child) { child.parent = this; this.children.push(child); if (child.id) byId.set(child.id, child); return child; },
                replaceChildren(...kids) { this.children = [...kids]; },
                remove() { if (this.parent) this.parent.children = this.parent.children.filter(c => c !== this); if (this.id) byId.delete(this.id); },
                getBoundingClientRect() { return {left: 10, bottom: 20}; },
            };
            return node;
        },
        getElementById: id => byId.get(id) || null,
    };
    doc.body = doc.createElement('body');
    return doc;
}

function load() {
    const document = makeDoc();
    const window = {};
    runInNewContext(source, {window, document: {}, fetch: () => { throw new Error('unused'); }});
    return {api: window.GWProjectArtifactLinks, document};
}

const base = {run_id: 'run-1', content_hash: 'h', export_formats: ['markdown'], lifecycle: 'draft'};
const scriptV1 = {...base, artifact_id: 'artifact-script', version_id: 'ver-1', revision: 1, version_label: 'v001', content_hash: 'h1', review_id: 'rev-1',
    created_at: '2026-10-01T00:00:00Z', export_uri: '/api/agent/runs/run-1/export?format=markdown&review_id=rev-1&artifact_version_id=ver-1'};
const scriptV2 = {...scriptV1, version_id: 'ver-2', revision: 2, version_label: 'v002', content_hash: 'h2', review_id: 'rev-2',
    created_at: '2026-10-02T00:00:00Z', export_uri: '/api/agent/runs/run-1/export?format=markdown&review_id=rev-2&artifact_version_id=ver-2'};
const board = {...base, artifact_id: 'artifact-board', version_id: 'ver-b', revision: 1, version_label: 'v001', review_id: null,
    created_at: '2026-09-30T00:00:00Z', export_formats: [], export_uri: null};

test('按 artifact_id 分组，组内最大 revision 为当前版本', () => {
    const {api} = load();
    const groups = api.groupArtifacts([scriptV1, board, scriptV2]);
    assert.equal(groups.length, 2);
    assert.equal(groups[0].artifactId, 'artifact-script');
    assert.equal(groups[0].current.version_id, 'ver-2');
    assert.deepEqual([...groups[0].versions.map(v => v.version_label)], ['v002', 'v001']);
    assert.equal(groups[1].current.version_label, 'v001');
});

test('卡片槽位只显示当前版本「查看 vNNN」，点击打开版本浮层', async () => {
    const {api, document} = load();
    const slot = document.createElement('span');
    slot.dataset.projectId = 'proj/1';
    const root = {querySelectorAll: () => [slot]};
    const calls = [];
    const fetchImpl = async (url, init) => { calls.push([url, init]); return {ok: true, json: async () => ({items: [scriptV1, scriptV2, board]})}; };
    await api.hydrate(root, fetchImpl);
    await api.hydrate(root, fetchImpl);
    assert.equal(calls.length, 1, '同一项目重复渲染复用缓存');
    assert.equal(calls[0][0], '/api/agent/projects/proj%2F1/artifacts?limit=200');
    assert.equal(calls[0][1].credentials, 'same-origin');
    assert.equal(slot.hidden, false);
    assert.equal(slot.children.length, 1);
    const trigger = slot.children[0];
    assert.equal(trigger.tagName, 'BUTTON');
    assert.equal(trigger.textContent, '查看 v002');
    assert.equal(trigger.dataset.contentHash, 'h2');
    assert.match(trigger.className, /gw-type-body-sm/);
    let stopped = false;
    trigger.onclick({stopPropagation() { stopped = true; }});
    assert.ok(stopped, '点击不应冒泡触发卡片选中重绘');
    const pop = document.getElementById('gwArtifactVersionPopover');
    assert.ok(pop);
    const [scriptSec, boardSec] = pop.children;
    const versions = scriptSec.children[1].children;
    assert.equal(versions[0].tagName, 'A');
    assert.equal(versions[0].textContent, 'v002 · 当前');
    assert.equal(versions[1].href, scriptV1.export_uri);
    assert.equal(boardSec.children[1].children[0].tagName, 'SPAN', '未审核版本只显示文字');
    trigger.onclick({stopPropagation() {}});
    assert.equal(document.getElementById('gwArtifactVersionPopover'), null, '再次点击收起');
    api.invalidate();
    await api.hydrate(root, fetchImpl);
    assert.equal(calls.length, 2);
});

test('拒绝非 Agent 导出路径；无产物时隐藏槽位', () => {
    const {api, document} = load();
    const pop = api.buildPopover(document, api.groupArtifacts([{...scriptV1, export_uri: 'https://evil.example/x'}]));
    assert.equal(pop.children[0].children[1].children[0].tagName, 'SPAN');
    const slot = document.createElement('span');
    api.renderSlot(slot, []);
    assert.equal(slot.hidden, true);
    assert.equal(slot.children.length, 0);
});

test('项目卡片左下角挂载版本槽位，左侧栏不再有产物列表', () => {
    assert.doesNotMatch(html, /projectArtifactList/);
    assert.ok(html.indexOf('project-artifact-links.js') < html.indexOf('projects-controller.js'));
    assert.match(controller, /data-gw-artifact-version-slot data-project-id="\$\{esc\(p\.id\)\}"/);
    assert.match(controller, /stream\.innerHTML = cardsHtml;\n\s*window\.GWProjectArtifactLinks\?\.hydrate\(stream\)/);
    assert.doesNotMatch(controller, /GWProjectArtifactLinks\?\.load\(/);
});

test('卡片主入口优先展示当前版本可导出的产物', () => {
    const {api} = load();
    const newerUnreviewed = {...board, created_at: '2026-10-09T00:00:00Z'};
    const groups = api.groupArtifacts([scriptV1, scriptV2, newerUnreviewed]);
    assert.equal(groups[0].artifactId, 'artifact-script');
});
