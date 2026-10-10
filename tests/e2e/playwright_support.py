"""Playwright 启动辅助。

环境不能创建浏览器驱动管道时，按失败关闭原则显式跳过，
不把环境限制记成产品通过，也不把产品断言失败改写成跳过。
"""

import pytest


class PlaywrightSession:
    """保持 Playwright 原有上下文管理，只拦截驱动管道权限错误。"""

    def __init__(self, browser_api):
        self._browser_api = browser_api
        self._manager = None

    def __enter__(self):
        self._manager = self._browser_api.sync_playwright()
        try:
            playwright = self._manager.__enter__()
            self._patch_chromium_launch(playwright.chromium)
            return playwright
        except PermissionError as exc:
            pytest.skip(f"Playwright 驱动管道不可用，浏览器证据未取得：{exc}")
        except OSError as exc:
            if getattr(exc, "winerror", None) == 5:
                pytest.skip(f"Playwright 驱动管道不可用，浏览器证据未取得：{exc}")
            raise

    @staticmethod
    def _patch_chromium_launch(chromium):
        orig_launch = chromium.launch
        orig_launch_persistent = chromium.launch_persistent_context

        def _wrap_args(kwargs):
            disable_flag = "--disable-features=AutofillAiWalletPrivatePasses,AutofillAmbientAutofill"
            args = list(kwargs.get("args") or [])
            if not any(disable_flag in arg for arg in args):
                args.append(disable_flag)
            kwargs["args"] = args
            return kwargs

        def _safe_launch(*args, **kwargs):
            return orig_launch(*args, **_wrap_args(kwargs))

        def _safe_launch_persistent(*args, **kwargs):
            return orig_launch_persistent(*args, **_wrap_args(kwargs))

        chromium.launch = _safe_launch
        chromium.launch_persistent_context = _safe_launch_persistent

    def __exit__(self, exc_type, exc, traceback):
        if self._manager is None:
            return False
        return self._manager.__exit__(exc_type, exc, traceback)


def open_playwright(browser_api):
    """返回可直接用于 with 的 Playwright 会话。"""
    return PlaywrightSession(browser_api)
