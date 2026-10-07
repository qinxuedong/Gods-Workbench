# -*- coding: utf-8 -*-
"""洁净室禁用二进制扩展名与白名单的**唯一事实来源**。

背景：同一份清单此前在三个位置各写一遍（卫生用例、Phase 6 深度审计用例、CI 工作流），
已实际发生漂移（39 / 39 / 27 项）。任何检查清单被复制粘贴到多处，都迟早分叉；
故本模块作为唯一来源，Python 侧一律 `import`，CI 侧由回归用例比对，禁止各自再写字面量。

分类依据：根 `AGENTS.md` 1.2 条。
"""

# 禁用扩展名（AGENTS.md 1.2 条 6 大类）。
# 说明：`.pdf` 超出 1.2 条字面枚举，属本仓**更严格**的本地策略（文档类二进制同样不应入库），
# 因此本集合是三个历史清单的**并集**，严格强于其中任何一个，不含任何放宽。
BANNED_EXTENSIONS = {
    # 图片 / 截图
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico", ".svg",
    # 字体（仅 3 条思源黑体白名单例外）
    ".ttf", ".otf", ".woff", ".woff2", ".eot",
    # 音频 / 视频
    ".mp3", ".wav", ".ogg", ".mp4", ".mov", ".avi", ".mkv",
    # 压缩包
    ".zip", ".gz", ".7z", ".rar", ".tar", ".tgz", ".bz2", ".xz",
    # 可执行文件 / 动态库 / 安装包 / 脚本二进制
    ".exe", ".dll", ".so", ".dylib", ".bin", ".msi", ".app",
    ".bat", ".cmd", ".com", ".scr",
    # 文档类二进制（本地策略超集）
    ".pdf",
}

# AGENTS.md 1.2 条要求覆盖的**最小必需集合**；反查 BANNED_EXTENSIONS 是否被静默删减。
REQUIRED_BANNED_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico", ".svg",
    ".ttf", ".otf", ".woff", ".woff2", ".eot",
    ".mp3", ".wav", ".ogg", ".mp4", ".mov", ".avi", ".mkv",
    ".zip", ".gz", ".7z", ".rar", ".tar", ".tgz", ".bz2", ".xz",
    ".exe", ".dll", ".so", ".dylib", ".bin", ".msi", ".app",
    ".bat", ".cmd", ".com", ".scr",
    ".pdf",
}

# 唯一二进制白名单：根启动脚本与用户 2026-09-18 指示的 3 个本地字体，逐条精确路径。
ALLOWED_BINARY_ALLOWLIST = {
    "start.bat",
    "web/vendor/fonts/SourceHanSansCN-Bold.otf",
    "web/vendor/fonts/SourceHanSansCN-Medium.otf",
    "web/vendor/fonts/SourceHanSansCN-Normal.otf",
}
