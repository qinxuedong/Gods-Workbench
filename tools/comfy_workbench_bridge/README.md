# ComfyUI 工作流浏览器桥

将本目录复制到**已获准的** ComfyUI `custom_nodes/comfy_workbench_bridge/` 后重启该宿主。
实现采用 ComfyUI 官方 `WEB_DIRECTORY` 和 `app.registerExtension` 扩展入口，无新增 Python 依赖或生成节点。

从神工坊“打开 ComfyUI”进入，扩展加载原生图并确认结果；“回存神工坊”按钮按打开时的文档版本保存。
并发编辑返回版本冲突，保留 ComfyUI 中的图；重新从神工坊打开最新版本后再处理。
30秒未收到回执时解除按钮锁并显示“回存未确认”；先核对神工坊文档版本再决定重试。不会自动重发，旧请求的迟到回执不会解锁新请求。
窗口来源、ComfyUI origin 和单次随机 nonce 必须匹配。扩展不接收账户 Cookie、令牌或密钥，回存由原神工坊页的受保护接口执行。

关闭原神工坊页或登出会结束会话；宿主未安装扩展时显示未确认，不能据写盘或打开网页宣称原生加载成功。
仓库测试覆盖消息合同和隔离浏览器宿主；实际 ComfyUI 安装、前端版本与缺失自定义节点仍须现场验收。

参考：[ComfyUI 官方 JavaScript 扩展](https://docs.comfy.org/custom-nodes/js/javascript_overview)。
