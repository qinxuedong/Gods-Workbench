"""前端端到端冒烟检查的最小、可注入实现。

本模块只提供浏览器上下文工厂和可选检查编排，不保存认证状态、不写入仓库目录，
也不把页面访问结果自动解释为业务验收通过。
"""
from __future__ import annotations

from typing import Any, Callable, Iterable

ContextFactory = Callable[..., Any]
ContextBuilder = Callable[[], Any]


def _new_context(browser: Any, context_factory: ContextFactory | None = None, **kwargs: Any) -> Any:
    """通过显式工厂创建上下文；未注入工厂时保留浏览器默认行为。"""
    if context_factory is not None:
        return context_factory(browser, **kwargs)
    return browser.new_context(**kwargs)


def _new_page(context: Any) -> Any:
    """创建页面，供真实 Playwright 运行或测试替身使用。"""
    return context.new_page()


def _visit(page: Any, url: str) -> dict[str, Any]:
    """访问单个地址，仅记录状态，不读取或保存响应正文。"""
    try:
        response = page.goto(url, wait_until="domcontentloaded")
        status = getattr(response, "status", None)
        return {"url": url, "status": status, "ok": isinstance(status, int) and 200 <= status < 400}
    except Exception as exc:  # pragma: no cover - 由真实浏览器运行时决定
        return {"url": url, "status": None, "ok": False, "error_type": type(exc).__name__}


def _close(context: Any) -> None:
    close = getattr(context, "close", None)
    if callable(close):
        close()


def _run_pages(
    browser: Any,
    base_url: str,
    pages: Iterable[str],
    context_factory: ContextFactory | None,
    *,
    viewport: dict[str, int] | None = None,
    context_builder: ContextBuilder | None = None,
) -> dict[str, Any]:
    """运行页面检查；上下文构建可由调用方显式注入，便于认证门禁审计。"""
    context = (
        context_builder()
        if context_builder is not None
        else _new_context(browser, context_factory, viewport=viewport or {"width": 1440, "height": 900})
    )
    results: list[dict[str, Any]] = []
    try:
        for path in pages:
            page = _new_page(context)
            results.append(_visit(page, base_url.rstrip("/") + "/" + str(path).lstrip("/")))
            page_errors = getattr(page, "page_errors", None)
            if callable(page_errors):
                results[-1]["page_error_count"] = len(page_errors())
    finally:
        _close(context)
    return {"pages": results, "failures": [item for item in results if not item.get("ok")]}


def run_interactions(browser: Any, base_url: str, context_factory: ContextFactory | None = None, **_: Any) -> dict[str, Any]:
    """运行最小页面访问检查。"""
    def build_context() -> Any:
        return _new_context(browser, context_factory, viewport={"width": 1440, "height": 900})

    return _run_pages(browser, base_url, ("projects.html", "agents.html"), context_factory, context_builder=build_context)


def run_topbar_accessibility(browser: Any, base_url: str, context_factory: ContextFactory | None = None, **_: Any) -> dict[str, Any]:
    """检查顶栏页面是否可打开；详细无障碍断言由专门测试负责。"""
    def build_context() -> Any:
        return _new_context(browser, context_factory, viewport={"width": 1440, "height": 900})

    return _run_pages(browser, base_url, ("projects.html",), context_factory, context_builder=build_context)


def run_shell_route_consistency(browser: Any, base_url: str, context_factory: ContextFactory | None = None, **_: Any) -> dict[str, Any]:
    """检查壳路由的首屏访问状态。"""
    def build_context() -> Any:
        return _new_context(browser, context_factory, viewport={"width": 1440, "height": 900})

    return _run_pages(browser, base_url, ("projects.html", "canvas.html", "agents.html"), context_factory, context_builder=build_context)


def run_shell_route_repeat_nav(browser: Any, base_url: str, context_factory: ContextFactory | None = None, **_: Any) -> dict[str, Any]:
    """检查重复导航不改变上下文创建策略。"""
    def build_context() -> Any:
        return _new_context(browser, context_factory, viewport={"width": 1440, "height": 900})

    return _run_pages(browser, base_url, ("projects.html", "projects.html"), context_factory, context_builder=build_context)


def run_checks(browser: Any, base_url: str, context_factory: ContextFactory | None = None, **kwargs: Any) -> dict[str, Any]:
    """汇总冒烟检查；结果仅是观测，不构成发布验收。"""
    def delegated_context(got_browser: Any, **options: Any) -> Any:
        return _new_context(got_browser, context_factory, **options)

    return {
        "interactions": run_interactions(browser, base_url, delegated_context, **kwargs),
        "topbar_accessibility": run_topbar_accessibility(browser, base_url, delegated_context, **kwargs),
        "shell_route_consistency": run_shell_route_consistency(browser, base_url, delegated_context, **kwargs),
        "shell_route_repeat_nav": run_shell_route_repeat_nav(browser, base_url, delegated_context, **kwargs),
    }
