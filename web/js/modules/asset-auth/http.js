/**
 * Copyright 2026 Gods-Workbench Authors
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

import { createFetchTransport } from '../../core/http-transport.js';

/**
 * Stateless HTTP transport for account, team and approval operations.
 *
 * The API facade deliberately returns native fetch responses untouched so the
 * page remains responsible for JSON parsing and authentication UI state.
 */
export function createAssetAuthHttp(fetchImpl = globalThis.fetch) {
    return createFetchTransport(fetchImpl);
}

const assetAuthHttp = Object.freeze(createAssetAuthHttp());

export default assetAuthHttp;
