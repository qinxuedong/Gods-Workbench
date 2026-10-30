"""QIN-491：#496 顶栏上层机架的几何门禁（真实浏览器布局量测）。

源码正则测试只能证明「不再绝对定位」，证明不了「右端组没被挤出顶栏后被裁掉」：
顶栏设置了 overflow 裁切，元素越界时既看不到横向滚动条，也测不出页面级 scrollWidth。
这里用真实 Chrome 量测三页在关键宽度下的矩形关系：

1. 中央状态位右边界不得越过右端组左边界（否则同一位置叠层）；
2. 右端组与排期入口的右边缘不得越过顶栏可见右边界（否则被裁切）；
3. 播控按键在 >1450px 时须完整可见（窄屏收起规则另有口径）。
"""

from __future__ import annotations

import pytest
from workflow_support import goto_ready, settings_browser

PAGES = ("index.html", "agents.html", "settings.html")
# 1451–1460 是「RAM/功率表不再收起」的断点边界，历史上正是在此被挤出顶栏。
WIDTHS = (1280, 1366, 1440, 1451, 1460, 1680, 1920)

MEASURE = """() => {
  const header = document.querySelector('header');
  if (!header) return {error: 'no-header'};
  const deck = header.querySelector('.master-deck-upper');
  if (!deck) return {error: 'no-upper-deck'};
  const kids = [...deck.children];
  const right = kids[kids.length - 1];
  const center = kids.length > 2 ? kids[1] : null;
  const hr = header.getBoundingClientRect();
  const rr = right.getBoundingClientRect();
  const date = document.getElementById('masterDeckDate');
  const clock = document.getElementById('masterDeckClock');
  const plays = [...right.querySelectorAll('.hw-round-btn')];
  return {
    headerRight: hr.right,
    rightRight: rr.right,
    rightLeft: rr.left,
    centerRight: center ? center.getBoundingClientRect().right : 0,
    centerWidth: center ? center.getBoundingClientRect().width : 0,
    dateRight: date ? date.getBoundingClientRect().right : null,
    dateWidth: date ? date.getBoundingClientRect().width : 0,
    clockWidth: clock ? clock.getBoundingClientRect().width : 0,
    playsVisible: plays.filter((el) => el.getBoundingClientRect().width > 0).length,
    playsTotal: plays.length,
  };
}"""


@pytest.mark.parametrize("page_name", PAGES)
def test_topbar_right_group_is_not_pushed_out_of_the_deck(
    page_name, monkeypatch, tmp_path
):
    with settings_browser(monkeypatch, tmp_path) as (page, base, errors, _):
        goto_ready(page, f"{base}/static/pages/{page_name}")
        failures = []
        for width in WIDTHS:
            page.set_viewport_size({"width": width, "height": 900})
            page.evaluate(
                "() => new Promise((done) => requestAnimationFrame(() => "
                "requestAnimationFrame(done)))"
            )
            m = page.evaluate(MEASURE)
            assert "error" not in m, f"{page_name} 缺少顶栏结构：{m.get('error')}"
            label = f"{page_name}@{width}px"
            # 1) 不叠层：中央状态位右边界不得越过右端组左边界。
            assert m["centerRight"] <= m["rightLeft"] + 1, (
                f"{label} 中央状态位压在右端组下方："
                f"centerRight={m['centerRight']:.1f} rightLeft={m['rightLeft']:.1f}"
            )
            # 2) 不裁切：右端组与排期入口都必须在顶栏可见右边界内。
            if m["rightRight"] > m["headerRight"] + 1:
                failures.append(
                    f"{label} 右端组越界 {m['rightRight'] - m['headerRight']:.1f}px"
                )
            if m["dateRight"] is not None and m["dateRight"] > m["headerRight"] + 1:
                failures.append(
                    f"{label} 排期入口越界 {m['dateRight'] - m['headerRight']:.1f}px"
                )
            # 3) 必达控件仍在：时钟与排期入口有尺寸；>1450px 时播控按键同样可见。
            assert m["dateWidth"] > 0 and m["clockWidth"] > 0, (
                f"{label} 时钟或排期入口丢失"
            )
            if width > 1450:
                assert m["playsVisible"] == m["playsTotal"] > 0, (
                    f"{label} 播控组未完整可见：{m['playsVisible']}/{m['playsTotal']}"
                )
        assert not failures, "顶栏右端内容被挤出可见区域：" + "；".join(failures)
        assert not errors, f"{page_name} 出现页面脚本错误：{errors}"
