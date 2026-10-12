import assert from 'node:assert/strict';
import test from 'node:test';
import {createCanvasListApi} from '../../web/js/modules/canvas-list/api.js';

test('画布生命周期把实际版本传到HTTP，保留鉴权参数', async () => {
    const requests = [];
    const api = createCanvasListApi({request:async (url,init) => { requests.push({url,init}); return {ok:true}; }});
    const init = {headers:{Authorization:'Bearer isolated-fixture'},credentials:'same-origin'};
    await api.deleteCanvas('cv-0002',4,init);
    await api.archiveCanvas('cv-0002',5,init);
    await api.unarchiveCanvas('cv-0002',6,init);
    await api.restoreCanvas('cv-0002',7,init);
    await api.purgeCanvas('cv-0002',8,init);
    assert.deepEqual(requests.map(row => row.url),[
        '/api/canvases/cv-0002?expected_version=4',
        '/api/canvases/cv-0002/touch?operation=archive&expected_version=5',
        '/api/canvases/cv-0002/touch?operation=unarchive&expected_version=6',
        '/api/canvases/cv-0002/restore',
        '/api/canvases/cv-0002/purge?expected_version=8',
    ]);
    assert.deepEqual(JSON.parse(requests[3].init.body),{expected_version:7});
    assert.ok(requests.every(row => row.init.credentials === 'same-origin' && row.init.headers.Authorization === init.headers.Authorization));
});

test('缺失或伪造版本时不发送HTTP请求，不擅自补1', () => {
    let calls=0;
    const api=createCanvasListApi({request:()=>{calls++;return Promise.resolve({ok:true});}});
    for(const action of ['deleteCanvas','archiveCanvas','unarchiveCanvas','restoreCanvas','purgeCanvas']){
        for(const version of [undefined,null,0,-1,1.5,true,'2',NaN]) assert.throws(()=>api[action]('cv-0001',version),/当前版本/);
    }
    assert.equal(calls,0);
});
