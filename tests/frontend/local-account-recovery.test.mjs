import assert from 'node:assert/strict';
import test from 'node:test';

import {createAssetAuthApi} from '../../web/js/modules/asset-auth/api.js';

test('asset-auth local recovery calls use same-origin POST JSON requests', async () => {
    const requests = [];
    const api = createAssetAuthApi({
        request(url, init) {
            requests.push({url, init});
            return Promise.resolve({status: 200});
        },
    });
    const payloads = [
        ['setupLocalAccount', '/api/asset-auth/local/setup', {username: 'owner', password: 'Test2026'}],
        ['loginLocalAccount', '/api/asset-auth/local/login', {username: 'owner', password: 'Test2026'}],
        ['resetLocalPassword', '/api/asset-auth/local/reset-password', {username: 'owner', recovery_code: 'RC-code', new_password: 'Reset2026'}],
        ['regenerateLocalRecoveryCodes', '/api/asset-auth/local/recovery-codes/regenerate', {current_password: 'Reset2026'}],
    ];

    for (const [method, url, payload] of payloads) {
        await api[method](payload);
        const request = requests.at(-1);
        assert.equal(request.url, url);
        assert.equal(request.init.method, 'POST');
        assert.equal(request.init.credentials, 'same-origin');
        assert.match(request.init.headers['Content-Type'], /application\/json/i);
        assert.deepEqual(JSON.parse(request.init.body), payload);
    }
});
