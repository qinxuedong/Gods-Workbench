"""QIN-497：#507 素材页 / 工作流页 1451–1500px 顶栏排期按钮被裁切。

这两页的顶栏上层机架长期缺 `.master-deck-upper` 语义类，QIN-491 的行内可收缩
修复只对带该语义类的页面生效；旧版页面中央状态位是 `shrink-0` 且不省略，
项目名稍长就把右端组（播控 / 时钟 / 排期）顶出 `header{overflow:hidden}`，
既不产生横向滚动条也不改变页面 scrollWidth，只能靠真实浏览器量测矩形发现。

本用例用真实 Chrome 在 6 档宽度量测矩形并逐档截图：
1440 / 1451 / 1470 / 1500 / 1680 / 1920。
截图证据由环境变量 `GW_QIN497_SHOTS` 指定目录；未设置时只落 tmp_path。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from workflow_support import goto_ready, settings_browser

PAGES = ("assets.html", "workflow.html")
WIDTHS = (1440, 1451, 1470, 1500, 1680, 1920)
LONG_CENTER_TEXT = (
    "《神谕之地：第七封印与失落的机械神殿》· 影视工坊流水线 · "
    "当前资产库：项目素材库（共 1284 项）"
)

MEASURE = """() => {
  const header = document.querySelector('header');
  if (!header) return {error: 'no-header'};
  let deck = header.querySelector('.master-deck-upper');
  if (!deck) {
    // 旧版页面没有语义类：上层机架即包含排期入口的直属行。
    deck = [...header.children].find(
      (el) => el.contains(document.getElementById('masterDeckDate'))
    );
  }
  if (!deck) return {error: 'no-upper-deck'};
  const kids = [...deck.children];
  const center = kids.length > 2 ? kids[1] : null;
  const hr = header.getBoundingClientRect();
  const right = kids[kids.length - 1];
  const rr = right.getBoundingClientRect();
  const date = document.getElementById('masterDeckDate');
  const clock = document.getElementById('masterDeckClock');
  const dr = date && date.getBoundingClientRect();
  const cr = clock && clock.getBoundingClientRect();
  const plays = [...deck.querySelectorAll('.hw-round-btn')];
  const centerOverflow = center
    ? center.getBoundingClientRect().right - rr.left
    : 0;
  const hit = dr
    ? document.elementFromPoint(dr.left + dr.width / 2, dr.top + dr.height / 2)
    : null;
  return {
    hasUpperClass: deck.classList.contains('master-deck-upper'),
    headerRight: hr.right,
    rightLeft: rr.left,
    rightRight: rr.right,
    dateLeft: dr ? dr.left : null,
    dateRight: dr ? dr.right : null,
    dateWidth: dr ? dr.width : 0,
    clockWidth: cr ? cr.width : 0,
    centerRight: center ? center.getBoundingClientRect().right : 0,
    centerWidth: center ? center.getBoundingClientRect().width : 0,
    centerOverflow: centerOverflow,
    playsVisible: plays.filter((el) => el.getBoundingClientRect().width > 0).length,
    playsTotal: plays.length,
    dateHitSelf: hit ? Boolean(hit.closest('#masterDeckDate')) : false,
    docScrollWidth: document.documentElement.scrollWidth,
  };
}"""


INJECT_LONG = """(longText) => {
  const deck =
    document.querySelector('.master-deck-upper') ||
    [...document.querySelector('header').children].find(
      (el) => el.contains(document.getElementById('masterDeckDate'))
    );
  const center = deck && deck.children.length > 2 ? deck.children[1] : null;
  if (!center) return false;
  // 中央状态位的可见文案（素材页是 ASSET VAULT / 状态文案，工作流页是项目名）。
  const label = [...center.querySelectorAll('span, p')].find(
    (el) => el.textContent.trim().length >= 3
  );
  if (!label) return false;
  label.textContent = longText;
  return true;
}"""


def _shots_dir(tmp_path: Path) -> Path:
    configured = os.environ.get("GW_QIN497_SHOTS")
    target = Path(configured) if configured else tmp_path / "topbar-shots"
    target.mkdir(parents=True, exist_ok=True)
    return target


def _measure(page, page_name, width, suffix, shots, long_text=False):
    """切到指定宽度（可选注入长文案）后截图并量测矩形，截图必须先于量测。"""
    page.set_viewport_size({"width": width, "height": 900})
    page.evaluate(
        "() => new Promise((done) => requestAnimationFrame(() => "
        "requestAnimationFrame(done)))"
    )
    if long_text:
        assert page.evaluate(INJECT_LONG, LONG_CENTER_TEXT), "未找到中央状态位文案"
        page.evaluate(
            "() => new Promise((done) => requestAnimationFrame(() => "
            "requestAnimationFrame(done)))"
        )
    page.screenshot(
        path=str(shots / f"{page_name.removesuffix('.html')}-{width}{suffix}.png"),
        clip={"x": 0, "y": 0, "width": width, "height": 150},
    )
    return page.evaluate(MEASURE)


@pytest.mark.parametrize("page_name", PAGES)
def test_topbar_schedule_button_is_not_clipped(page_name, monkeypatch, tmp_path):
    """AC1：6 档宽度下右端组与排期入口完整可见，无溢出、裁切、叠层。"""
    shots = _shots_dir(tmp_path)
    with settings_browser(monkeypatch, tmp_path) as (page, base, errors, _):
        goto_ready(page, f"{base}/static/pages/{page_name}")
        failures = []
        for width in WIDTHS:
            m = _measure(page, page_name, width, "", shots)
            assert "error" not in m, f"{page_name} 缺少顶栏结构：{m.get('error')}"
            label = f"{page_name}@{width}px"
            assert m["hasUpperClass"], f"{label} 上层机架缺少 master-deck-upper 语义类"
            if m["rightRight"] > m["headerRight"] + 1:
                failures.append(
                    f"{label} 右端组越界 {m['rightRight'] - m['headerRight']:.1f}px"
                )
            if m["dateRight"] > m["headerRight"] + 1:
                failures.append(
                    f"{label} 排期入口越界 {m['dateRight'] - m['headerRight']:.1f}px"
                )
            assert m["centerRight"] <= m["rightLeft"] + 1, (
                f"{label} 中央状态位压在右端组下方："
                f"centerRight={m['centerRight']:.1f} rightLeft={m['rightLeft']:.1f}"
            )
            assert m["dateWidth"] >= 76, f"{label} 排期按钮被压缩：{m['dateWidth']:.1f}px"
            assert m["clockWidth"] > 0, f"{label} 顶栏时钟丢失"
            assert m["dateHitSelf"], f"{label} 排期按钮被其它元素遮挡"
            assert m["playsVisible"] == m["playsTotal"] > 0, (
                f"{label} 播控组未完整可见：{m['playsVisible']}/{m['playsTotal']}"
            )
            assert m["docScrollWidth"] <= width, f"{label} 出现横向滚动"
        assert not failures, "顶栏右端内容被裁切：" + "；".join(failures)
        assert not errors, f"{page_name} 出现页面脚本错误：{errors}"


@pytest.mark.parametrize("page_name", PAGES)
def test_topbar_schedule_button_survives_long_center_text(
    page_name, monkeypatch, tmp_path
):
    """中央状态位文案变长时须自己省略，不得把右端组顶出顶栏（本单真实成因）。"""
    shots = _shots_dir(tmp_path)
    with settings_browser(monkeypatch, tmp_path) as (page, base, errors, _):
        goto_ready(page, f"{base}/static/pages/{page_name}")
        failures = []
        for width in WIDTHS:
            m = _measure(page, page_name, width, "-long", shots, long_text=True)
            label = f"{page_name}@{width}px 长文案"
            assert m["centerOverflow"] <= 0, (
                f"{label} 中央状态位压进右端组 {m['centerOverflow']:.1f}px"
            )
            if m["rightRight"] > m["headerRight"] + 1:
                failures.append(
                    f"{label} 右端组越界 {m['rightRight'] - m['headerRight']:.1f}px"
                )
            assert m["dateWidth"] >= 76, f"{label} 排期按钮被压缩：{m['dateWidth']:.1f}px"
            assert m["dateHitSelf"], f"{label} 排期按钮被其它元素遮挡"
            assert m["playsVisible"] == m["playsTotal"] > 0, (
                f"{label} 播控组未完整可见：{m['playsVisible']}/{m['playsTotal']}"
            )
        assert not failures, "长文案下顶栏右端被裁切：" + "；".join(failures)
        assert not errors, f"{page_name} 出现页面脚本错误：{errors}"
