# 外部图像提示词快照 NOTICE

本目录是第三方提示词数据快照，不是 Gods-Workbench 原创内容。当前仓库仍为 **NOT AUTHORIZED FOR PUBLIC DISTRIBUTION**；本 NOTICE 是组件级来源/许可证据，不构成公开发布授权，也不替代根级许可证、全仓 NOTICE/SBOM 或最终 H04 独立审核。

## 固定来源与构建边界

- 聚合注册表：[`yukkcat/image-prompts`](https://github.com/yukkcat/image-prompts/tree/8df9939175e263538fa39757e23c80f37ab4da14/dist)，固定提交 `8df9939175e263538fa39757e23c80f37ab4da14`；固定快照时间 `2026-08-22T11:54:09+08:00`。
- 上游 `dist/manifest.json` 声明的 `registryHash`：`702d43e1146d42567b795c735bf50dec550901e34aff270656a4b4ef9cdbbd2e`；固定 schema：[`prompt.schema.json`](https://raw.githubusercontent.com/yukkcat/image-prompts/8df9939175e263538fa39757e23c80f37ab4da14/schema/prompt.schema.json)，SHA-256 `908ce723b38175e11f7a329466dcdc86347cff948dd9fc301bd78c0dedc00247`。
- 本地六个源 JSON 均逐字节重建自固定提交的 `dist/sources/` 文件；`sourceSha256` 是固定上游文件哈希，`localSha256` 是本地字节哈希，`sha256` 保留为现有 UI 消费的本地哈希兼容字段。当前 `localTransformation=identity-copy`，六组 source/local 哈希相同；未来任何改写都必须另列转换说明和新本地哈希，不得覆盖源哈希。
- 聚合注册表代码/文档的 MIT 许可不重新授予各源提示词、图片、名称或作者内容的权利；内容许可按每个原始来源分别记录。此处不复制任何预览图片，远程图片引用本身不属于本目录文件集合。

## 当前候选数据集（仅供 H04 逐项审查）

| 来源 ID | 条目 | 许可 | 版权/维护者 | 原始主页 | 固定提示词数据 | 固定许可文本 |
| --- | ---: | --- | --- | --- | --- | --- |
| `banana-prompt-quicker` | 323 | MIT | glidea | [https://glidea.github.io/banana-prompt-quicker/](https://glidea.github.io/banana-prompt-quicker/) | [`sources/banana-prompt-quicker.json`](https://raw.githubusercontent.com/yukkcat/image-prompts/8df9939175e263538fa39757e23c80f37ab4da14/dist/sources/banana-prompt-quicker.json) | [固定 LICENSE](https://github.com/glidea/banana-prompt-quicker/blob/186da165b8be690cc26278761ae12cf64acc8a0b/LICENSE) · SHA-256 `b6cf424c887549a8176ce0c522b800a9f292bdc809f4cbc311e2d649b42d9a21` |
| `freestylefly-gpt-image-2` | 523 | MIT | freestylefly | [https://github.com/freestylefly/awesome-gpt-image-2](https://github.com/freestylefly/awesome-gpt-image-2) | [`sources/freestylefly-gpt-image-2.json`](https://raw.githubusercontent.com/yukkcat/image-prompts/8df9939175e263538fa39757e23c80f37ab4da14/dist/sources/freestylefly-gpt-image-2.json) | [固定 LICENSE](https://github.com/freestylefly/awesome-gpt-image-2/blob/c12aec800b8afba0a3154aac433f4967d21fc92e/LICENSE) · SHA-256 `27a75c48bac29eb78f43c19f75c4e175974c8f1046d848d5562eae1ead2f1176` |
| `awesome-gpt-image` | 53 | MIT | Zero Lu | [https://github.com/ZeroLu/awesome-gpt-image](https://github.com/ZeroLu/awesome-gpt-image) | [`sources/awesome-gpt-image.json`](https://raw.githubusercontent.com/yukkcat/image-prompts/8df9939175e263538fa39757e23c80f37ab4da14/dist/sources/awesome-gpt-image.json) | [固定 LICENSE](https://github.com/ZeroLu/awesome-gpt-image/blob/a7ffd0d01389739d6a0907d12e49806d6ef3cc79/LICENSE) · SHA-256 `65d0863319198cb6f7a4d4bac395befd4099681bc371fd1a2a9ad9e35c02a738` |
| `awesome-gpt4o-image-prompts` | 76 | MIT | Awesome GPT4o Image Prompts | [https://github.com/ImgEdify/Awesome-GPT4o-Image-Prompts](https://github.com/ImgEdify/Awesome-GPT4o-Image-Prompts) | [`sources/awesome-gpt4o-image-prompts.json`](https://raw.githubusercontent.com/yukkcat/image-prompts/8df9939175e263538fa39757e23c80f37ab4da14/dist/sources/awesome-gpt4o-image-prompts.json) | [固定 LICENSE](https://github.com/ImgEdify/Awesome-GPT4o-Image-Prompts/blob/c4e58477d26391cb908d04c726ecea2f395a19b5/LICENSE) · SHA-256 `f8131f554150f4865eb43845e3148f3e75c6ed664a6b2326a00dfc7fc1a7b976` |
| `youmind-gpt-image-2` | 126 | CC-BY-4.0 | YouMind OpenLab | [https://github.com/YouMind-OpenLab/awesome-gpt-image-2](https://github.com/YouMind-OpenLab/awesome-gpt-image-2) | [`sources/youmind-gpt-image-2.json`](https://raw.githubusercontent.com/yukkcat/image-prompts/8df9939175e263538fa39757e23c80f37ab4da14/dist/sources/youmind-gpt-image-2.json) | [固定 LICENSE](https://github.com/YouMind-OpenLab/awesome-gpt-image-2/blob/a00a14c74f9411d864f20a26af2a342be0b0c654/LICENSE) · SHA-256 `cdf642acded97160b992064916cc1c7490ce5a8d8ccc70d8d6456e16976f9bd0` |
| `youmind-nano-banana-pro` | 129 | CC-BY-4.0 | YouMind OpenLab | [https://github.com/YouMind-OpenLab/awesome-nano-banana-pro-prompts](https://github.com/YouMind-OpenLab/awesome-nano-banana-pro-prompts) | [`sources/youmind-nano-banana-pro.json`](https://raw.githubusercontent.com/yukkcat/image-prompts/8df9939175e263538fa39757e23c80f37ab4da14/dist/sources/youmind-nano-banana-pro.json) | [固定 LICENSE](https://github.com/YouMind-OpenLab/awesome-nano-banana-pro-prompts/blob/6ee561326fb66c0d35682cefd8cc2a1ff5e100db/LICENSE) · SHA-256 `2b2ffb76ba44bf79a3f29023332fad2d8bd70ef9dd27ff9e7258bf1a213a08bb` |

合计 **1,230 条**，与六个源文件数组长度之和一致。每条记录保留上游提供的 `author`、`sourceUrl`、来源分类等字段；本次重建不编辑提示词正文。YouMind 两个 CC BY 4.0 来源的署名字段与来源链接保持原样，若后续加工需在相应分发材料中说明改动并提供许可链接。

### 当前明确排除项

- `davidwu-gpt-image2-prompts`（494 条）不在本地 `sources/`，也不在候选发布集合。固定聚合清单中存在该条目；对应原仓库在快照前可核验的 README（提交 `228edb6341d978aa2572911adf7e7e147ebf95d3`）写有 `License: MIT`，但该快照时点未发现 `LICENSE` 或 `LICENSE.md` 文件/完整许可正文。此处只判定**许可通知证据不完整、暂不纳入**，不推断内容必然无许可或作者未授权。待取得许可正文及版权通知后再重新审查。

## 上游许可与版权通知原文

以下文本取自上表固定提交中的原始 `LICENSE` 文件；保留各来源各自的版权行。聚合注册表许可仅覆盖其代码/文档；其他 MIT 与 CC BY 来源仅按各自原始许可声明处理。

### image-prompt-registry（聚合目录元数据）

来源：[`LICENSE`](https://github.com/yukkcat/image-prompts/blob/8df9939175e263538fa39757e23c80f37ab4da14/LICENSE) · SHA-256 `f7f5fe7e2c6d40d15fc5b9370daf40318ef93420c1f883b02c1b9b73531e8bb0`

```text
MIT License

Copyright (c) 2026 image-prompt-registry contributors

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

### Banana Prompt Quicker

来源：[`LICENSE`](https://github.com/glidea/banana-prompt-quicker/blob/186da165b8be690cc26278761ae12cf64acc8a0b/LICENSE) · SHA-256 `b6cf424c887549a8176ce0c522b800a9f292bdc809f4cbc311e2d649b42d9a21`

```text
MIT License

Copyright (c) 2025 glidea

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

### Freestylefly GPT Image 2

来源：[`LICENSE`](https://github.com/freestylefly/awesome-gpt-image-2/blob/c12aec800b8afba0a3154aac433f4967d21fc92e/LICENSE) · SHA-256 `27a75c48bac29eb78f43c19f75c4e175974c8f1046d848d5562eae1ead2f1176`

```text
MIT License

Copyright (c) 2026 freestylefly

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

### Awesome GPT Image

来源：[`LICENSE`](https://github.com/ZeroLu/awesome-gpt-image/blob/a7ffd0d01389739d6a0907d12e49806d6ef3cc79/LICENSE) · SHA-256 `65d0863319198cb6f7a4d4bac395befd4099681bc371fd1a2a9ad9e35c02a738`

```text
MIT License

Copyright (c) 2026 Zero Lu

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

### Awesome GPT-4o Image Prompts

来源：[`LICENSE`](https://github.com/ImgEdify/Awesome-GPT4o-Image-Prompts/blob/c4e58477d26391cb908d04c726ecea2f395a19b5/LICENSE) · SHA-256 `f8131f554150f4865eb43845e3148f3e75c6ed664a6b2326a00dfc7fc1a7b976`

```text
MIT License

Copyright (c) 2024 Awesome GPT4o Image Prompts

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

### YouMind GPT Image 2

来源：[`LICENSE`](https://github.com/YouMind-OpenLab/awesome-gpt-image-2/blob/a00a14c74f9411d864f20a26af2a342be0b0c654/LICENSE) · SHA-256 `cdf642acded97160b992064916cc1c7490ce5a8d8ccc70d8d6456e16976f9bd0`

```text
Creative Commons Attribution 4.0 International License (CC BY 4.0)

Copyright (c) 2026 YouMind OpenLab

This work is licensed under the Creative Commons Attribution 4.0 International License.

To view a copy of this license, visit:
https://creativecommons.org/licenses/by/4.0/

or send a letter to:
Creative Commons, PO Box 1866, Mountain View, CA 94042, USA.

---

You are free to:

- Share — copy and redistribute the material in any medium or format
- Adapt — remix, transform, and build upon the material for any purpose, even commercially

Under the following terms:

- Attribution — You must give appropriate credit, provide a link to the license, and indicate if changes were made. You may do so in any reasonable manner, but not in any way that suggests the licensor endorses you or your use.

No additional restrictions — You may not apply legal terms or technological measures that legally restrict others from doing anything the license permits.

---

Notices:

You do not have to comply with the license for elements of the material in the public domain or where your use is permitted by an applicable exception or limitation.

No warranties are given. The license may not give you all of the permissions necessary for your intended use. For example, other rights such as publicity, privacy, or moral rights may limit how you use the material.
```

### YouMind Nano Banana Pro

来源：[`LICENSE`](https://github.com/YouMind-OpenLab/awesome-nano-banana-pro-prompts/blob/6ee561326fb66c0d35682cefd8cc2a1ff5e100db/LICENSE) · SHA-256 `2b2ffb76ba44bf79a3f29023332fad2d8bd70ef9dd27ff9e7258bf1a213a08bb`

```text
Creative Commons Attribution 4.0 International License (CC BY 4.0)

Copyright (c) 2025 YouMind OpenLab

This work is licensed under the Creative Commons Attribution 4.0 International License.

To view a copy of this license, visit:
https://creativecommons.org/licenses/by/4.0/

or send a letter to:
Creative Commons, PO Box 1866, Mountain View, CA 94042, USA.

---

You are free to:

- Share — copy and redistribute the material in any medium or format
- Adapt — remix, transform, and build upon the material for any purpose, even commercially

Under the following terms:

- Attribution — You must give appropriate credit, provide a link to the license, and indicate if changes were made. You may do so in any reasonable manner, but not in any way that suggests the licensor endorses you or your use.

No additional restrictions — You may not apply legal terms or technological measures that legally restrict others from doing anything the license permits.

---

Notices:

You do not have to comply with the license for elements of the material in the public domain or where your use is permitted by an applicable exception or limitation.

No warranties are given. The license may not give you all of the permissions necessary for your intended use. For example, other rights such as publicity, privacy, or moral rights may limit how you use the material.
```

## 发布边界

- 本文及当前本地快照不改变根仓 `release_authorized: false`。即使组件级源字节与许可通知可核验，仍须完成 H04 独立最终审计并另行授权后，才可公开分发。
- 本 NOTICE 只覆盖 `web/prompt-registry/`；不表示全仓 SBOM、根级 NOTICE 或其他第三方组件许可证闭环。
- 任何提示词、图片、作者、主页、许可证或快照的更新，都必须重新固定上游提交，记录源/本地 SHA-256，并复核许可与 schema 兼容；不得使用浮动 `main` 哈希作为来源证明。
