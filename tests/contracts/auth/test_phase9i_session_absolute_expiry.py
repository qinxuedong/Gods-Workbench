# -*- coding: utf-8 -*-
"""R6-7 回归守卫：服务端会话必须有**绝对**过期上限，滑动续期不得无限延长会话。

缺陷背景（独立复核 R6-7）：``core/session.py`` 原先只有滑动过期，
活跃会话每次访问都会续期 8 小时，因此**永不失效**；对已泄露的不透明会话标识
缺少最终失效边界。修复后新增 ``SESSION_ABSOLUTE_MAX_SECONDS``。

本文件用**行为级**断言（可控时钟），不用静态字符串：
证据边界：本地复算；不代表已接入生产 IdP 或生产验收。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SRC_PATH = REPO_ROOT
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

from gw.core import session as session_store  # noqa: E402


class _FakeClock:
    """可注入的假时钟，避免用例依赖真实等待。"""

    def __init__(self, now: float = 1_000_000.0):
        self.now = now

    def time(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture()
def clock(monkeypatch):
    """把 ``session_store`` 看到的 time.time 换成可控假时钟。"""
    fake = _FakeClock()
    monkeypatch.setattr(session_store, "time", fake)
    session_store.reset_stores()
    yield fake
    session_store.reset_stores()


def test_absolute_cap_terminates_actively_renewed_session(clock):
    """会话被反复访问（持续滑动续期）也必须越过绝对上限后失效。"""
    session_id = session_store.create_session({"username": "u", "role": "editor"})

    step = session_store.SESSION_TTL_SECONDS // 2
    absolute = session_store.SESSION_ABSOLUTE_MAX_SECONDS
    elapsed = 0.0
    # 每步都远小于滑动窗口，保证「活跃会话」不会因空闲而先过期。
    while elapsed < absolute:
        assert session_store.get_session(session_id) is not None, "绝对上限内不应失效"
        clock.advance(step)
        elapsed += step

    assert session_store.get_session(session_id) is None, "活跃会话越过绝对上限后仍可用（R6-7 未修复）"


def test_sliding_renewal_never_exceeds_absolute_cap(clock):
    """续期后的 expires_at 不得越过登录时刻 + 绝对上限。"""
    session_id = session_store.create_session({"username": "u", "role": "editor"})
    record = session_store._SESSIONS[session_id]
    cap = record.absolute_expires_at
    assert cap == pytest.approx(record.created_at + session_store.SESSION_ABSOLUTE_MAX_SECONDS)

    # 持续活跃直至逼近绝对上限：若续期不做封顶，expires_at 会被推到 cap 之后。
    step = session_store.SESSION_TTL_SECONDS // 2
    while clock.now < cap - step:
        assert session_store.get_session(session_id) is not None, "绝对上限内不应失效"
        clock.advance(step)

    # 此时仍在绝对上限内，续期必须被封顶在 cap。
    assert session_store.get_session(session_id) is not None
    assert session_store._SESSIONS[session_id].expires_at <= cap, "滑动续期越过绝对上限"


def test_absolute_cap_does_not_break_normal_session(clock):
    """绝对上限不得误伤正常会话：窗口内仍可用，且滑动续期照常生效。"""
    session_id = session_store.create_session({"username": "u", "role": "editor"})
    clock.advance(session_store.SESSION_TTL_SECONDS // 2)
    assert session_store.get_session(session_id) is not None

    record = session_store._SESSIONS[session_id]
    # 滑动续期仍在绝对上限内时，应确实把到期时间往后推。
    assert record.expires_at > record.created_at + session_store.SESSION_TTL_SECONDS
    assert record.expires_at <= record.absolute_expires_at


def test_prune_removes_sessions_past_absolute_cap(clock):
    """``_prune`` 必须同时按滑动窗口与绝对上限清理，避免内存与信任残留。"""
    session_id = session_store.create_session({"username": "u", "role": "editor"})
    clock.advance(session_store.SESSION_ABSOLUTE_MAX_SECONDS + 1)
    # 触发 _prune 的写入路径。
    session_store.create_session({"username": "other", "role": "editor"})
    assert session_id not in session_store._SESSIONS, "越过绝对上限的会话未被 _prune 清理"


def test_session_record_requires_absolute_expiry_field(clock):
    """记录结构必须显式携带绝对到期时间，防止将来被静默移除。"""
    session_id = session_store.create_session({"username": "u", "role": "editor"})
    record = session_store._SESSIONS[session_id]
    assert hasattr(record, "absolute_expires_at")
    assert record.absolute_expires_at > record.created_at


def test_absolute_cap_is_finite_and_sane(clock):
    """绝对上限必须是有限正数，且不短于单次滑动窗口（否则登录即失效）。"""
    assert session_store.SESSION_ABSOLUTE_MAX_SECONDS > 0
    assert session_store.SESSION_ABSOLUTE_MAX_SECONDS >= session_store.SESSION_TTL_SECONDS