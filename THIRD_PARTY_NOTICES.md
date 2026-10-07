# Third-Party Software Notices and Information

This project, **Gods-Workbench**, incorporates third-party open source software, fonts, and data assets under their respective licenses. This document provides attribution and licensing notices for these components.

---

## 1. Python Runtime Dependencies

The following open-source Python packages are used by the backend runtime (`gw/`):

| Package | Version | License | Copyright / Upstream |
| :--- | :--- | :--- | :--- |
| **FastAPI** | 0.115+ | MIT | Copyright (c) Sebastián Ramírez |
| **Starlette** | 0.45+ | BSD-3-Clause | Copyright (c) 2018 Encode OSS Ltd. |
| **Pydantic** / **pydantic-core** | 2.10+ | MIT | Copyright (c) 2017-present Pydantic Services Inc. and Samuel Colvin |
| **Uvicorn** | 0.34+ | BSD-3-Clause | Copyright (c) 2017-present Encode OSS Ltd. |
| **AnyIO** | 4.8+ | MIT | Copyright (c) Alex Grönholm |
| **HTTPX** / **HTTPCore** | 0.28+ | BSD-3-Clause | Copyright (c) Encode OSS Ltd. |
| **idna** | 3.10+ | BSD-3-Clause | Copyright (c) 2013-2024, Kim Davies |
| **PyMuPDF** | 1.25+ | AGPL-3.0 / Commercial | Artifex Software, Inc. (Used strictly in independent audit tooling) |
| **Playwright** | 1.50+ | Apache-2.0 | Copyright (c) Microsoft Corporation (Used strictly in test suites) |

---

## 2. Frontend Vendor Assets (`web/vendor/`)

Detailed component-level licensing and byte hashes are documented in [`web/vendor/LICENSES.md`](web/vendor/LICENSES.md) and [`web/vendor/MANIFEST.md`](web/vendor/MANIFEST.md).

### 2.1 Lucide Icons (`1.16.0`)
- **License**: ISC License
- **Copyright**: Copyright (c) 2026 Lucide Icons and Contributors
- **Derived Icons (Feather)**: Copyright (c) 2013-present Cole Bemis (MIT License)

### 2.2 Three.js (`0.160.0`)
- **License**: MIT License
- **Copyright**: Copyright (c) 2010-2023 Three.js Authors

### 2.3 Adobe Source Han Sans CN (思源黑体)
- **Files**:
  - `web/vendor/fonts/SourceHanSansCN-Normal.otf` (Weight 400)
  - `web/vendor/fonts/SourceHanSansCN-Medium.otf` (Weight 500)
  - `web/vendor/fonts/SourceHanSansCN-Bold.otf` (Weight 700)
- **License**: SIL Open Font License 1.1 (OFL-1.1)
- **Copyright**: Copyright (c) 2014-2021 Adobe (http://www.adobe.com/), with Reserved Font Name 'Source'.
- **Upstream Release**: Adobe Source Han Sans Version 2.005R

### 2.4 Tailwind CSS Runtime (`3.4.17`)
- **License**: MIT License
- **Copyright**: Copyright (c) Tailwind Labs, Inc.
- **Bundled / Transitive Attributions**:
  - `didyoumean` (1.2.2): Apache-2.0, Copyright (c) 2013-2014 Dave Porter
  - `caniuse-lite`: Creative Commons Attribution 4.0 International (CC-BY-4.0), Alexis Deveria / Can I Use contributors
  - `@alloc/quick-lru` (5.3.0): MIT, Copyright (c) Sindre Sorhus
  - `postcss`: MIT, Copyright 2013 Andrey Sitnik
  - `nanoid`: MIT, Copyright 2017 Andrey Sitnik
  - `picocolors`: ISC, Copyright 2021 Alexey Raspopov
  - `source-map-js`: BSD-3-Clause, Copyright 2009-2011 Mozilla Foundation and contributors
  - `postcss-value-parser`: MIT, Copyright (c) Bogdan Chadkin
  - `normalize-range`: MIT, Copyright (c) James Talmage
  - `postcss-nested`: MIT, Copyright 2014 Andrey Sitnik
  - `postcss-js`: MIT, Copyright 2015 Andrey Sitnik
  - `postcss-selector-parser`: MIT, Copyright (c) Ben Briggs
  - `cssesc`: MIT, Copyright Mathias Bynens
  - `util-deprecate`: MIT, Copyright (c) 2014 Nathan Rajlich
  - `@tailwindcss/forms` (0.5.10): MIT, Copyright (c) Tailwind Labs, Inc.
  - `@tailwindcss/container-queries` (0.1.1): MIT, Copyright (c) Tailwind Labs, Inc.

---

## 3. Image Prompt Datasets (`web/prompt-registry/`)

Detailed attribution and origins are documented in [`web/prompt-registry/NOTICE.md`](web/prompt-registry/NOTICE.md).

| Dataset ID | Items | License | Author / Maintainer | Upstream Source |
| :--- | :--- | :--- | :--- | :--- |
| `banana-prompt-quicker` | 323 | MIT | glidea | [banana-prompt-quicker](https://github.com/glidea/banana-prompt-quicker) |
| `freestylefly-gpt-image-2` | 523 | MIT | freestylefly | [awesome-gpt-image-2](https://github.com/freestylefly/awesome-gpt-image-2) |
| `awesome-gpt-image` | 53 | MIT | Zero Lu | [awesome-gpt-image](https://github.com/ZeroLu/awesome-gpt-image) |
| `awesome-gpt4o-image-prompts`| 76 | MIT | ImgEdify | [Awesome-GPT4o-Image-Prompts](https://github.com/ImgEdify/Awesome-GPT4o-Image-Prompts) |
| `youmind-gpt-image-2` | 126 | CC-BY-4.0 | YouMind OpenLab | [awesome-gpt-image-2](https://github.com/YouMind-OpenLab/awesome-gpt-image-2) |
| `youmind-nano-banana-pro` | 129 | CC-BY-4.0 | YouMind OpenLab | [awesome-nano-banana-pro-prompts](https://github.com/YouMind-OpenLab/awesome-nano-banana-pro-prompts) |

---

## 4. License Texts

### Apache License Version 2.0
See [`LICENSE`](LICENSE) for the full Apache-2.0 license text.

### SIL Open Font License 1.1
Full OFL-1.1 text is available in [`web/vendor/LICENSES.md`](web/vendor/LICENSES.md#4-sil-open-font-license-11).

### MIT License
```text
Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

### ISC License
```text
Permission to use, copy, modify, and/or distribute this software for any
purpose with or without fee is hereby granted, provided that the above
copyright notice and this permission notice appear in all copies.

THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES
WITH REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF
MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR
ANY SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES
WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN AN
ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF
OR IN CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.
```

### Creative Commons Attribution 4.0 International (CC-BY-4.0)
You are free to share and adapt the material for any purpose, even commercially, under the condition that appropriate credit is given and a link to the license is provided: https://creativecommons.org/licenses/by/4.0/
