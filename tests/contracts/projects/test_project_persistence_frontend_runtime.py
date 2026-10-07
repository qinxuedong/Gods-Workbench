"""立项阶段门前端回读、失败重试与幂等键清理运行时契约。"""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


def test_project_creation_reads_back_real_gates_and_keeps_retry_key_until_verified(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node unavailable for frontend runtime contract")
    source_path = ROOT / "web/js/controllers/projects-controller.js"
    script = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(process.argv[2], 'utf8');
const start = source.indexOf('  function projectCreateRequestId(');
const end = source.indexOf('  function openEditProject(', start);
assert.ok(start >= 0 && end > start, 'must extract project-create flow');
const calls = [];
const toasts = [];
const dispatch = [];
const session = new Map();
let closeCount = 0;
let readbackShouldFail = true;
const fields = {
  newProjectNameInput: {value: 'Persistence UI test'},
  newProjectTypeSelect: {value: 'series'},
  newProjectDescInput: {value: 'runtime contract'},
  newProjectStartAt: {value: ''},
  newProjectDueAt: {value: ''},
  newProjectGatesInput: {value: 'script|Script review\nlayout|Layout review'}
};
const state = {projects: []};
const window = {
  crypto: require('node:crypto').webcrypto,
  sessionStorage: {
    getItem(key) { return session.has(key) ? session.get(key) : null; },
    setItem(key, value) { session.set(key, value); },
    removeItem(key) { session.delete(key); }
  }
};
const document = {
  getElementById(id) { return fields[id] || null; },
  querySelector() { return null; }
};
const localStorage = {setItem() {}};
const dateInputTimestamp = () => null;
const selectProject = () => {};
const render = () => {};
const showToast = (message, kind) => toasts.push({message, kind});
const setDispatchStatus = (...args) => dispatch.push(args);
const HardwareDeck = {closeModal() { closeCount += 1; }};
const fetch = async (url, init) => {
  calls.push({url, init});
  if (init.method === 'POST') {
    return {ok: true, async json() {return {project: {project_id: 'prj-p-test', version: 1}};}};
  }
  if (readbackShouldFail) {
    return {ok: false, async json() {return {detail: {code: 'DEPENDENCY_UNAVAILABLE'}};}};
  }
  return {ok: true, async json() {return {
    project_id: 'prj-p-test',
    gates: [
      {project_id: 'prj-p-test', gate_id: 'gate-1', code: 'script', name: 'Script review', state: 'pending', version: 1},
      {project_id: 'prj-p-test', gate_id: 'gate-2', code: 'layout', name: 'Layout review', state: 'pending', version: 1}
    ]
  };}};
};
const context = {assert, state, window, document, localStorage, dateInputTimestamp, selectProject,
  render, showToast, setDispatchStatus, HardwareDeck, fetch, console, encodeURIComponent, Uint8Array};
vm.createContext(context);
vm.runInContext(source.slice(start, end), context);
(async () => {
  await context.handleCreateProject({preventDefault() {}});
  const pending = JSON.parse(session.get('gw.pending-project-create'));
  assert.equal(calls.length, 2, 'create must be followed by gate readback');
  assert.equal(calls[0].url, '/api/asset-registry/projects');
  assert.equal(calls[0].init.method, 'POST');
  assert.equal(JSON.parse(calls[0].init.body).client_request_id, pending.client_request_id);
  assert.equal(calls[1].url, '/api/asset-registry/projects/prj-p-test/gates');
  assert.equal(closeCount, 0, 'failed readback must keep the form open');
  assert.equal(fields.newProjectNameInput.value, 'Persistence UI test');
  assert.equal(state.projects.length, 1, 'confirmed project should be upserted while retry remains safe');
  assert.ok(toasts.some(item => item.kind === 'warning'));

  readbackShouldFail = false;
  await context.handleCreateProject({preventDefault() {}});
  assert.equal(calls.length, 4);
  assert.equal(JSON.parse(calls[2].init.body).client_request_id, pending.client_request_id,
    'retry must reuse the same key');
  assert.equal(state.projects.length, 1, 'idempotent replay must not duplicate the project card');
  assert.equal(session.has('gw.pending-project-create'), false, 'verified completion clears the pending key');
  assert.equal(closeCount, 1);
  assert.equal(fields.newProjectNameInput.value, '');
  assert.ok(toasts.some(item => item.kind === 'success' && item.message.includes('v1')),
    'success message must display state/version from gate readback');
  console.log('PASS: project gate readback and retry idempotency');
})().catch(error => {console.error(error); process.exitCode = 1;});
"""
    runner = tmp_path / "project-persistence-ui.cjs"
    runner.write_text(script, encoding="utf-8")
    result = subprocess.run(
        [node, str(runner), str(source_path)], capture_output=True, text=True,
        encoding="utf-8", timeout=15, check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    assert "PASS:" in result.stdout
