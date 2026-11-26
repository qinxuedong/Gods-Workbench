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

export function pathCompareKey(value=''){
    return String(value || '').trim().replace(/\\/g, '/').replace(/\/+$/, '').toLowerCase();
}
