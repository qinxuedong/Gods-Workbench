import assert from 'node:assert/strict';
import test from 'node:test';

import {
    createNotIntegratedError,
    createServiceUnavailableError,
    isServiceUnavailableError,
    isNotIntegratedError,
    isNotIntegratedResponse,
    createFetchTransport,
    NOT_INTEGRATED_MESSAGE,
    SERVICE_UNAVAILABLE_MESSAGE
} from '../../web/js/core/http-transport.js';

import { pathCompareKey } from '../../web/js/modules/asset-manager/path-utils.js';

import { createAssetClassificationHelpers } from '../../web/js/modules/asset-manager/classification.js';

test('http-transport: error constructors and predicates', () => {
    const niError = createNotIntegratedError(404);
    assert.equal(niError.name, 'NotIntegratedError');
    assert.equal(niError.unavailable, true);
    assert.equal(niError.code, 'NOT_INTEGRATED');
    assert.equal(niError.status, 404);
    assert.equal(niError.message, `${NOT_INTEGRATED_MESSAGE}（HTTP 404）`);
    assert.equal(isNotIntegratedError(niError), true);
    assert.equal(isServiceUnavailableError(niError), false);

    const suError = createServiceUnavailableError(503);
    assert.equal(suError.name, 'ServiceUnavailableError');
    assert.equal(suError.unavailable, false);
    assert.equal(suError.retryable, true);
    assert.equal(suError.code, 'SERVICE_UNAVAILABLE');
    assert.equal(suError.status, 503);
    assert.equal(suError.message, `${SERVICE_UNAVAILABLE_MESSAGE}（HTTP 503）`);
    assert.equal(isServiceUnavailableError(suError), true);
    assert.equal(isNotIntegratedError(suError), false);

    assert.equal(isNotIntegratedError(null), false);
    assert.equal(isServiceUnavailableError(undefined), false);
    assert.equal(isNotIntegratedError(new Error('test')), false);
});

test('http-transport: isNotIntegratedResponse', async () => {
    // Falsy or non-matching status
    assert.equal(await isNotIntegratedResponse(null), false);
    assert.equal(await isNotIntegratedResponse({ status: 200 }), false);
    assert.equal(await isNotIntegratedResponse({ status: 500 }), false);

    // Matching status (404/501), no JSON body -> not integrated
    let mockHtmlResponse = {
        status: 404,
        clone() { return this; },
        async json() { throw new Error('invalid json'); }
    };
    assert.equal(await isNotIntegratedResponse(mockHtmlResponse), true);

    // Standard detail object -> actual business error, NOT integrated error
    let mockBusinessError = {
        status: 404,
        clone() { return this; },
        async json() { return { detail: { code: 'CANVAS_NOT_FOUND', msg: 'Canvas not found' } }; }
    };
    assert.equal(await isNotIntegratedResponse(mockBusinessError), false);

    // FastAPI default string detail (not found or not implemented) -> integrated error
    let mockFastAPINotFound = {
        status: 404,
        clone() { return this; },
        async json() { return { detail: 'Not Found' }; }
    };
    assert.equal(await isNotIntegratedResponse(mockFastAPINotFound), true);

    // Custom string detail that is not 'not found' / 'not implemented' -> actual business error
    let mockCustomStringDetail = {
        status: 404,
        clone() { return this; },
        async json() { return { detail: 'User not found in system' }; }
    };
    assert.equal(await isNotIntegratedResponse(mockCustomStringDetail), false);
});

test('http-transport: createFetchTransport', async () => {
    // Requires fetch
    assert.throws(() => createFetchTransport(null), TypeError);

    // Normal request
    const mockFetchOk = async (url) => ({ status: 200, url });
    const transportOk = createFetchTransport(mockFetchOk);
    const resOk = await transportOk.request('/api/test');
    assert.equal(resOk.status, 200);
    assert.equal(resOk.url, '/api/test');

    // 503 Service Unavailable
    const mockFetch503 = async () => ({ status: 503 });
    const transport503 = createFetchTransport(mockFetch503);
    await assert.rejects(
        transport503.request('/api/test'),
        (err) => err.code === 'SERVICE_UNAVAILABLE' && err.status === 503
    );

    // 404 Not Integrated
    const mockFetch404 = async () => ({
        status: 404,
        clone() { return this; },
        async json() { throw new Error('Not json'); }
    });
    const transport404 = createFetchTransport(mockFetch404);
    await assert.rejects(
        transport404.request('/api/test'),
        (err) => err.code === 'NOT_INTEGRATED' && err.status === 404
    );
});

test('asset-manager/path-utils: pathCompareKey', () => {
    assert.equal(pathCompareKey(''), '');
    assert.equal(pathCompareKey(null), '');
    assert.equal(pathCompareKey(undefined), '');
    assert.equal(pathCompareKey('  /Foo/Bar/Baz  '), '/foo/bar/baz');
    assert.equal(pathCompareKey('C:\\Windows\\System32\\'), 'c:/windows/system32');
    assert.equal(pathCompareKey('trailing/slashes///'), 'trailing/slashes');
});

test('asset-manager/classification: createAssetClassificationHelpers', () => {
    const groups = [
        { id: 'tags', name: 'Tags', dims: ['tag1', 'tag2'] },
        { id: 'model', name: 'Model', dims: ['subject'] },
        { id: 'style', name: 'Style', dims: ['art_style'] }
    ];
    const labels = { tag1: 'Tag One' };
    const helpers = createAssetClassificationHelpers({ groups, labels });

    // assetClassFilterKey
    assert.equal(helpers.assetClassFilterKey({ dimension: ' subject ', tag: ' 人 ' }), 'subject::人');
    assert.equal(helpers.assetClassFilterKey({ dimension: 'subject' }), '');

    // parseAssetClassFilterKey
    assert.deepEqual(helpers.parseAssetClassFilterKey('subject::人'), { dimension: 'subject', tag: '人' });
    assert.equal(helpers.parseAssetClassFilterKey('invalid_key'), null);

    // assetClassificationChips
    const item = {
        classification: {
            flat: [
                { dimension: 'subject', tag: '人', label: '主体' },
                { dimension: 'tag1', tag: 't1' },
                { dimension: 'empty' } // no tag, should be filtered out
            ]
        }
    };
    const chips = helpers.assetClassificationChips(item);
    assert.equal(chips.length, 2);
    assert.deepEqual(chips[0], { label: '主体', tag: '人', key: 'subject::人', dimension: 'subject' });
    assert.deepEqual(chips[1], { label: 'tag1', tag: 't1', key: 'tag1::t1', dimension: 'tag1' });

    // assetClassificationEntriesForItems
    const items = [
        { classification: { flat: [{ dimension: 'subject', tag: '人', label: '主体' }] } },
        { classification: { flat: [{ dimension: 'subject', tag: '人', label: '主体' }, { dimension: 'tag1', tag: 't1' }] } }
    ];
    const entries = helpers.assetClassificationEntriesForItems(items);
    assert.equal(entries.length, 2);
    // ordered by count DESC, then label, then tag
    assert.equal(entries[0].key, 'subject::人');
    assert.equal(entries[0].count, 2);
    assert.equal(entries[1].key, 'tag1::t1');
    assert.equal(entries[1].count, 1);

    // assetClassificationEntryGroup
    assert.equal(helpers.assetClassificationEntryGroup({ dimension: 'subject', tag: '人' }).id, 'model');
    assert.equal(helpers.assetClassificationEntryGroup({ dimension: 'subject', tag: '物' }).id, 'model'); // '物' is not matched by regex but 'subject' dim matches modelGroup dims
    assert.equal(helpers.assetClassificationEntryGroup({ dimension: 'art_style', tag: 'anime' }).id, 'style');
    assert.equal(helpers.assetClassificationEntryGroup({ dimension: 'unknown' }).id, 'tags'); // fallback

    // assetClassificationSearchText
    const searchItem = {
        classification: {
            summary: 'A nice summary',
            flat: [{ dimension: 'subject', tag: '人', label: '主体' }],
            categories: { tag1: ['t1', 't2'] },
            tags: ['extra1', 'extra2']
        }
    };
    const searchText = helpers.assetClassificationSearchText(searchItem);
    assert.match(searchText, /A nice summary/);
    assert.match(searchText, /Model 主体 subject 人/);
    assert.match(searchText, /Tags Tag One tag1 t1 t2/);
    assert.match(searchText, /extra1 extra2/);

    // groupedAssetClassificationEntries
    const grouped = helpers.groupedAssetClassificationEntries(entries);
    assert.equal(grouped.length, 2); // model and tags
    const modelGroupEntry = grouped.find(g => g.id === 'model');
    assert.equal(modelGroupEntry.count, 2);
    assert.equal(modelGroupEntry.entries[0].key, 'subject::人');

    // assetClassificationGroupIdForFilter
    assert.equal(helpers.assetClassificationGroupIdForFilter('subject::人', entries), 'model');
    assert.equal(helpers.assetClassificationGroupIdForFilter('unknown', entries), '');
});
