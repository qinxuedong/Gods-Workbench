# 文本 PDF 导出契约

- **状态**：CURRENT / VERIFIED
- **依据**：独立设计审查 D1–D4，已通过完整实现与契约测试验收。范围为注册表资产清单、素材正文、审查交付摘要；不是新增通用富文本/图片排版引擎。

## 内容与排版
- 自研标准库生成PDF1.7。A4纵向595×842；左右48；11点字格；17点行距；每行45格、每页42物理行，完整分页不截断。
- Type0/STSong-Light、CIDFontType0 Adobe-GB1 Supplement4、UniGB-UTF16-H；DW1000配合一字一格，ASCII也占一格。ToUnicode按实际发出的UTF16BE字符码映射，不按CID臆造。
- 可直接显示可打印ASCII和可用GB2312字符；其他字符显式转为\uXXXX或\UXXXXXXXX。制表符显示四空格；换行统一后排版。非嵌入字体依赖阅读器替代，不承诺所有阅读器字体完全一致。
- 原文转换前全文UTF8字节以固定安全名source.txt作为EmbeddedFiles附件。附件无需外部路径、不执行JavaScript或启动动作；Chrome是否提供附件面板不在承诺内。
- 注册表以一次状态快照按首次出现顺序去重资产ID；任一未知ID整请求404；响应X-PDF-Exported是实际唯一记录数、Skipped为0。源文每项为asset_id | name | kind，空清单为明确中文空态。
- 正文附件为已保存正文；无内容时为明确中文空态。交付摘要附件为同一会话/交付快照组装的编号、标题等文本，包含导出时刻。

## 上限与错误
- UTF8原文最多1MiB；显示字符最多180000；最多100页/4200物理行；ToUnicode映射最多8192字符。
- 超限413 PDF_EXPORT_LIMIT，非法孤立代理Unicode字符400 INVALID_PDF_TEXT；必须在响应字节开始前失败，不能问号替换或静默截断。
- 沿用原路由认证授权：注册表导出和素材正文GET是认证读取，readonly可读；交付POST导出沿用编辑权限，readonly403。未认证401。
- 注册表及正文提供RFC5987 UTF8文件名；交付文件名为服务器稳定ASCII delivery_id，不使用用户标题直接拼响应头。

## 验收
- tests/contracts/assets/test_phase12_pdf.py覆盖1/41/42/43/100物理行、长行、中文/ASCII/特殊符号、UTF8附件精确比较、页内bbox、非法Unicode/资源上限及三个HTTP导出面。
- tools/phase12_pdf_verification.py在仓库外临时目录保留三接口PDF、PyMuPDF与Chrome原生PDF阅读器第一页/尾页截图及SHA256。截图须人工/代理目视，不以非白像素替代正文核实。
- PyMuPDF/Playwright仅验收工具依赖，不新增生产运行依赖。不增加仓库字体或任何其他二进制。
