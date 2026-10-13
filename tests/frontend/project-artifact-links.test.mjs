import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {runInNewContext} from 'node:vm';

const source = readFileSync(new URL('../../web/js/modules/project-artifact-links.js', import.meta.url), 'utf8');
const html = readFileSync(new URL('../../web/pages/projects.html', import.meta.url), 'utf8');
const controller = readFileSync(new URL('../../web/js/controllers/projects-controller.js', import.meta.url), 'utf8');

function makeNode(tag) {
    const node = {
        tagName: tag.toUpperCase(), children: [], dataset: {}, hidden: false, textContent: '', className: '',
        appendChild(child) { this.children.push(child); return child; },
        replaceChildren(...kids) { this.children = [...kids]; },
    };
    return node;
}

function load() {
    const document = {createElement: makeNode, getElementById: () => null};
    const window = {};
    runInNewContext(source, {window, document, fetch: () => { throw new Error('unused'); }});
    return {api: window.GWProjectArtifactLinks, document};
}

const reviewed = {
    run_id: 'run-1', artifact_id: 'artifact-script', version_id: 'ver-7', revision: 7, version_label: 'v007',
    content_hash: 'sha256:abc', review_id: 'rev-1', export_formats: ['markdown'],
    export_uri: '/api/agent/runs/run-1/export?format=markdown&review_id=rev-1&artifact_version_id=ver-7',
};
const unreviewed = {...reviewed, artifact_id: 'artifact-board', version_id: 'ver-2', version_label: 'v002', review_id: null, export_formats: [], export_uri: null};

test('读取项目产物接口并为每项追加「查看 vNNN」文字链接', async () => {
    const {api, document} = load();
    const container = makeNode('ul');
    container.ownerDocument = document;
    const calls = [];
    const fetchImpl = async (url, init) => {
        calls.push([url, init]);
        return {ok: true, json: async () => ({items: [reviewed, unreviewed]})};
    };
    const items = await api.load('proj/1', container, fetchImpl);
    assert.equal(items.length, 2);
    assert.equal(calls[0][0], '/api/agent/projects/proj%2F1/artifacts?limit=200');
    assert.equal(calls[0][1].credentials, 'same-origin');
    assert.equal(container.hidden, false);
    assert.equal(container.children.length, 2);
    const [first, second] = container.children;
    assert.equal(first.children[0].textContent, 'artifact-script ');
    const link = first.children[1];
    assert.equal(link.tagName, 'A');
    assert.equal(link.textContent, '查看 v007');
    assert.equal(link.href, reviewed.export_uri);
    assert.equal(link.dataset.contentHash, 'sha256:abc');
    assert.match(link.className, /gw-type-body-sm/);
    assert.equal(second.children[1].tagName, 'SPAN', '无 export_uri 时不伪造链接');
    assert.equal(second.children[1].textContent, 'v002');
});

test('拒绝非 Agent 导出路径，接口失败时清空列表', async () => {
    const {api, document} = load();
    const container = makeNode('ul');
    container.ownerDocument = document;
    api.renderItems(container, [{...reviewed, export_uri: 'https://evil.example/x'}]);
    assert.equal(container.children[0].children[1].tagName, 'SPAN');
    await api.load('p', container, async () => ({ok: false, json: async () => ({})}));
    assert.equal(container.children.length, 0);
    assert.equal(container.hidden, true);
});

test('项目页挂载产物列表并在选中项目时刷新', () => {
    assert.match(html, /<ul id="projectArtifactList"[^>]*hidden/);
    assert.ok(html.indexOf('project-artifact-links.js') < html.indexOf('projects-controller.js'));
    assert.match(controller, /GWProjectArtifactLinks\?\.load\(id\)/);
    assert.match(controller, /GWProjectArtifactLinks\?\.load\(state\.activeProjectId\)/);
});
