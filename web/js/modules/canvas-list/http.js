/**
 * Copyright 2026 qinxuedong (Gods-Workbench Authors)
 * SPDX-License-Identifier: FSL-1.1-Apache-2.0
 *
 * Licensed under the Functional Source License, Version 1.1, Apache 2.0
 * Future License (the "License"); you may not use this file except in
 * compliance with the License. You may obtain a copy of the License at
 *     https://fsl.software/
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

import { createLazyFetchTransport } from '../../core/http-transport.js';

/**
 * Stateless HTTP transport for the legacy project canvas-list page.
 *
 * The default transport resolves globalThis.fetch at request time so browser
 * instrumentation installed after module evaluation remains observable.
 */
export function createCanvasListHttp(fetchImpl) {
    return createLazyFetchTransport(fetchImpl);
}

const canvasListHttp = Object.freeze(createCanvasListHttp());
globalThis.GodsWorkbenchCanvasListHttp = canvasListHttp;

export default canvasListHttp;
