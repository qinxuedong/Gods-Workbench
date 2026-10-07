# Local Vendor Assets — 本地第三方静态资源清单

本目录保存项目当前运行期自托管的第三方静态资源。本项目已采用 Apache-2.0 许可证正式发布，并在根目录提供 `LICENSE` 与分层 `THIRD_PARTY_NOTICES.md`。

## 仓库级通知边界

| 路径 | 状态 |
| --- | --- |
| 根级 `LICENSE` | 已提供（Apache License 2.0 全文） |
| 根级 `THIRD_PARTY_NOTICES.md` | 已提供（全仓第三方组件与依赖通知汇总） |
| `web/vendor/LICENSES.md` | 已提供（静态资源组件级版权与许可正文） |
| `web/prompt-registry/NOTICE.md` | 已提供（提示词快照来源与数据集许可说明） |

本清单与 `LICENSES.md` 构成前端第三方静态资源的管理基准。

## JavaScript

下表中的本地 SHA-256 均为逐字节对当前文件计算所得；“源 SHA-256”按行标明为上游原始字节或自有源码 manifest。Tailwind 是自有源码 manifest 构建的派生 bundle，输出不属于 CDN identity-copy；依赖与通知边界见 `LICENSES.md` §5.2。

| 本地文件 | 固定版本/来源 | 许可边界 | 源 SHA-256 | 本地 SHA-256 | 结论 |
| --- | --- | --- | --- | --- | --- |
| `js/lucide.js` | Lucide `1.16.0`；固定 npm 制品 `https://unpkg.com/lucide@1.16.0/dist/umd/lucide.min.js` | ISC；Feather 派生图标另含 MIT，正文见 `LICENSES.md` §1/§2 | `187A756625C5CE7499C207D1B0D1CF4E1AB95E3F666C7E0CD0FAFC3E6842D040` | `187A756625C5CE7499C207D1B0D1CF4E1AB95E3F666C7E0CD0FAFC3E6842D040` | 与固定 npm 制品逐字节一致；组件级通知已附；仍待 H04/单独授权 |
| `js/three-0.160.0.module.js` | Three.js `0.160.0`；固定 npm 制品 `https://unpkg.com/three@0.160.0/build/three.module.js` | MIT；正文见 `LICENSES.md` §3 | `76DEA8151BC9352AEF3528B4262E249B2604F62543828328DB978D060D61A495` | `76DEA8151BC9352AEF3528B4262E249B2604F62543828328DB978D060D61A495` | 与固定 npm 制品逐字节一致；组件级通知已附；仍待 H04/单独授权 |
| `js/tailwindcss-cdn.js` | Tailwind CSS `3.4.17` + forms `0.5.10` + container-queries `0.1.1`；Stage-B R09 自有浏览器 runtime 构建；源码 manifest SHA-256 见表后说明；esbuild `0.24.0` 为构建工具 | 15 个实际 runtime 包；逐组件通知原文见 `LICENSES.md` §5.2；CQ 后继同源 LICENSE 的来源与边界见该节 | `—`（派生构建，非上游 bundle identity-copy） | `D11003B2F6FBE0CA79340ECD3349DFFE61E11A3FEB224B3FDFA3D24900F70E83` | `836034 B`；有限构建/测试证据通过；公开分发仍 **BLOCKED** |
> Tailwind source manifest SHA-256：`5E5318923643B88F7B123047A975FD7BA6CBE9F78BB131DDA5E0761357FDD175`。这是自有源目录清单哈希，不是 bundle 字节哈希；R09 产物大小和本地 SHA-256 见上表。


## CSS

| 本地文件 | 用途/归属 | 源 SHA-256 | 本地 SHA-256 |
| --- | --- | --- | --- |
| `css/fonts.css` | 项目维护的 `@font-face` 加载配置，不是第三方字体制品；当前 `UNLICENSED`／保留所有权利，适用根仓未来授权边界 | 不适用（项目文件） | `F978FFFA0369F8B433EE2B1AAE442A7E29CDD01FB760B29034DE71F820814CBB` |

## Fonts — Adobe Source Han Sans CN

三个精确白名单路径现已重建为 [Adobe 官方 `2.005R` 发布树](https://github.com/adobe-fonts/source-han-sans/tree/6c709ca72d3d7c46ab42ebecc1a26e7d69595a37/SubsetOTF/CN)中的原始 Simplified Chinese OTF。上游固定提交为 `6c709ca72d3d7c46ab42ebecc1a26e7d69595a37`（标签 `2.005R`）；每个下载字节均用 Git blob SHA-1 与官方 `git/trees` API 中同路径 blob ID 复核，并记录独立 SHA-256。源与本地哈希相同，转换为 `identity-copy`，未修改字体二进制。

| 本地文件 / CSS 字重 | 固定上游路径 | 大小 | 官方 Git blob SHA-1 | 源 SHA-256 | 本地 SHA-256 |
| --- | --- | ---: | --- | --- | --- |
| `fonts/SourceHanSansCN-Normal.otf`（400） | `SubsetOTF/CN/SourceHanSansCN-Normal.otf` | 8,434,332 B | `80fa1125374a81e214c6184fb2f10e5602c3c7b3` | `9FC9AD5B64F086A2B342087865623923C216E346F879142A84DF37C7FFD323C5` | `9FC9AD5B64F086A2B342087865623923C216E346F879142A84DF37C7FFD323C5` |
| `fonts/SourceHanSansCN-Medium.otf`（500） | `SubsetOTF/CN/SourceHanSansCN-Medium.otf` | 8,406,556 B | `d870b3549d2cbd61ab24ec4f496cacb2cf1963fc` | `A94E558A2FE972BEE4F46BCE0843ABFF37063FD68C33F1E7D9058F6F09432B01` | `A94E558A2FE972BEE4F46BCE0843ABFF37063FD68C33F1E7D9058F6F09432B01` |
| `fonts/SourceHanSansCN-Bold.otf`（700） | `SubsetOTF/CN/SourceHanSansCN-Bold.otf` | 8,569,308 B | `49a623f360e4dfca8e96df4e0681150d76dcaf89` | `62383707C086A32F3AFD5E293F34C7EFF64C7FEA31F579FDC6CBE34D920519A6` | `62383707C086A32F3AFD5E293F34C7EFF64C7FEA31F579FDC6CBE34D920519A6` |

版本、name 表与许可证核验：内嵌 name ID 5 为 `Version 2.005;addfeatures 5.0.0b21`；字体 family/style 与字重见 `LICENSES.md` §4。官方 `LICENSE.txt` 包含 OFL-1.1 及 Reserved Font Name `Source`；完整 OFL 正文和 Adobe 版权行随 `LICENSES.md` 提供。此证据只关闭这三个字体的组件级字节/许可证来源核验，不代表根仓获得发布授权。

## 来源、转换与剩余门禁

- **源哈希**表示所记固定上游原始字节；对于明确标注的 Tailwind 行，则表示自有源码 manifest，而非源 bundle 字节。**本地哈希**表示仓库当前文件。Lucide、Three.js 与三份字体仍为上游字节直接副本；Tailwind 为自有源码清单构建的派生 runtime，源 manifest SHA 与输出 SHA 分别记录，不称为 CDN 响应或 `identity-copy`。
- `web/vendor/LICENSES.md` 载有现有 vendor 组件级通知；Tailwind 15 个 runtime 包的版本/来源/通知见 §5.2。esbuild `0.24.0` 是构建工具而非运行期 bundle 成员；CQ 后继同源 LICENSE 的来源及证据边界在 §5.2 说明。
- 全仓第三方资源、根级 LICENSE/NOTICE/SBOM、H04 独立审核及公开发布授权仍在本文件范围之外或未完成；因此本仓没有当前可公开分发的二进制包。

## 更新规则

- 禁止用 `latest`、浮动分支或无哈希 URL 覆盖文件；升级先固定 tag/commit/version，再记录源字节 SHA-256、文件大小、Git blob（若可用）、本地字节 SHA-256 和转换说明。
- 新增或替换 `web/vendor/` 文件时，同步更新 `MANIFEST.md`、`LICENSES.md`、组件级准入证据及防污染卫生检查。
- 本清单不是完整 SBOM、全仓 NOTICE 或许可证意见；不得用于推导 `release_authorized: true`。
