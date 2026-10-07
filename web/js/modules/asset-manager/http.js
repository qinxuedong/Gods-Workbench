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
 * Stateless HTTP transport for the asset manager page.
 *
 * The facade deliberately returns the native fetch result unchanged so each
 * asset-manager flow keeps its existing JSON parsing and error projection.
 */
export function createAssetManagerHttp(fetchImpl = globalThis.fetch) {
    return createFetchTransport(fetchImpl);
}

const assetManagerHttp = Object.freeze(createAssetManagerHttp());

export default assetManagerHttp;
