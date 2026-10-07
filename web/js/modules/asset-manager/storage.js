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

export function readStoredBoolean(key, fallback=false){
    try {
        const value = localStorage.getItem(key);
        return value === null ? Boolean(fallback) : value === 'true';
    } catch(_) {
        return Boolean(fallback);
    }
}

export function readStoredNumber(key, fallback=0){
    try {
        const value = Number(localStorage.getItem(key));
        return Number.isFinite(value) && value > 0 ? value : Number(fallback || 0);
    } catch(_) {
        return Number(fallback || 0);
    }
}
