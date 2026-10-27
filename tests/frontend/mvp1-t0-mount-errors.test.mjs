import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';

const source = readFileSync(new URL('../../web/js/modules/asset-manager.js', import.meta.url), 'utf8');
const styles = readFileSync(new URL('../../web/css/pages/directory-settings.css', import.meta.url), 'utf8');

test('directory settings expose persistent roots and mark environment roots read-only', () => {
    assert.match(source, /\/api\/storage-settings\/roots/);
    assert.match(source, /系统根（只读）/);
    assert.match(source, /data-storage-root-add/);
    assert.match(source, /data-storage-root-delete/);
});

test('directory settings require explicit user action to confirm a path alias', () => {
    assert.match(source, /data-storage-root-alias-confirm/);
    assert.match(source, /aliases\/confirm/);
    assert.match(source, /确认别名/);
    assert.match(source, /root\.active \? root\.aliasCandidates\.map/);
});

test('mount errors include server error codes and fit the directory settings layout', () => {
    assert.match(source, /error\.code \? `\$\{error\.code\}：`/);
    assert.match(styles, /\.allowed-root-error/);
    assert.match(styles, /\.allowed-root-status--offline/);
});
