// QIN-422 收集面板 HTTP 契约测试。
import assert from 'node:assert/strict';
import test from 'node:test';
import { createHttpCollectAdapter } from '../../web/js/modules/asset-collect-panel.js';

test('URL、文件与任务状态使用真实 HTTP 契约', async () => {
  const calls = [];
  const adapter = createHttpCollectAdapter(async (url, options = {}) => {
    calls.push({url, options});
    const result = url.endsWith('/url') ? {job_id:'job-1', status:'queued'}
      : url.endsWith('/files') ? {job_id:'job-2', accepted:[{name:'a.txt', size:3}]}
      : {job_id:'job-1', status:'done', progress:100};
    return {ok:true, json:async () => result};
  });
  const urlJob = await adapter.submitUrl('https://example.com/a.png', 'token');
  assert.equal(urlJob.jobId, 'job-1');
  assert.deepEqual(JSON.parse(calls[0].options.body), {url:'https://example.com/a.png', pair_token:'token'});
  const fileJob = await adapter.submitFiles([new File(['abc'], 'a.txt')], 'token');
  assert.deepEqual(fileJob.accepted, [{name:'a.txt', size:3}]);
  assert.equal(calls[1].options.body.get('pair_token'), 'token');
  assert.equal((await adapter.getJob('job-1')).status, 'done');
});
