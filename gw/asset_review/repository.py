# Copyright 2026 Gods-Workbench Authors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Phase 12 A3 资产审查与交付的真实落盘仓库。

命名空间：``asset_review``（经 ``core.storage.JsonState`` 原子落盘，重启可恢复）。

稳定 ID 口径：
- 审查会话 ``rs_NNNN``；
- 评论 ``rc_NNNN``；
- 交付 ``dlv_NNNN``；
- 分享 ``shr_NNNN``。

证据边界：本模块只保证单进程/单实例一致性；多 worker 并发写属部署方职责，
文档必须如实标注该限制。
"""

from __future__ import annotations

import hashlib
import secrets
import time
from collections.abc import Mapping
from datetime import UTC, datetime, timezone
from typing import Any, Dict, List, Optional

from gw.asset_review import service as review_service
from gw.core import storage
from gw.core.errors import CleanroomException, VersionConflictException

NS = "asset_review"
DATA_STATUS_OK = "ok"

SESSION_STATES = ("open", "delivered", "approved", "rejected", "superseded")
APPROVAL_STATES = ("approved", "changes_requested", "locked")
COMMENT_STATES = ("open", "resolved")

_SHARE_ROUNDS = 60000


def _unix_time() -> float:
    """返回可控的 Unix 时间源，供期限与限流边界使用。"""
    return time.time()


def state() -> storage.JsonState:
    """返回审查域命名空间状态容器。"""
    return storage.JsonState(NS, _blank)


def _blank() -> dict[str, Any]:
    return {
        "revision": 1,
        "sessions": {},
        "comments": {},
        "deliveries": {},
        "approvals": {},
        "shares": {},
        "rate": {},
    }


def _seq(raw: dict[str, Any], prefix: str, bucket: str) -> str:
    """按既有 id 推导下一个确定性序号 ID，禁止随机数与 uuid。"""
    taken = raw.get(bucket) or {}
    return storage.next_sequence(prefix, list(taken.keys()))


def _bump(raw: dict[str, Any]) -> int:
    raw["revision"] = int(raw.get("revision") or 1) + 1
    return raw["revision"]


def _cas(raw: dict[str, Any], expected: int | None, label: str) -> None:
    if expected is None:
        return
    if int(expected) != int(raw.get("revision") or 1):
        raise VersionConflictException(
            expected_version=int(expected),
            current_version=int(raw.get("revision") or 1),
            message="%s版本冲突，请重新读取后重试" % label,
        )


def _require(bucket: str, key: str, code: str, label: str) -> dict[str, Any]:
    item = (state().read().get(bucket) or {}).get(key)
    if item is None:
        raise CleanroomException(404, code, "%s不存在" % label)
    return item


def _is_c8(item: Mapping[str, Any] | None) -> bool:
    return review_service.is_version_bound_payload(item)


def _session_revision(item: Mapping[str, Any]) -> int:
    """对象级 CAS 版本；旧 B6 记录没有 revision 时回退到 version。"""
    return int(
        item.get("revision") or item.get("expected_version") or item.get("version") or 1
    )


def _cas_session(
    session: Mapping[str, Any], expected: Any, label: str = "审阅会话"
) -> None:
    if expected is None:
        return
    try:
        expected_int = int(expected)
    except (TypeError, ValueError):
        raise CleanroomException(
            400, "INVALID_REQUEST", "expected_version 必须是整数"
        ) from None
    current = _session_revision(session)
    if expected_int != current:
        raise VersionConflictException(
            expected_version=expected_int,
            current_version=current,
            message="%s版本冲突，请重新读取后重试" % label,
        )


def _require_expected_for_c8(
    session: Mapping[str, Any], payload: Mapping[str, Any]
) -> None:
    if _is_c8(session) and payload.get("expected_version") is None:
        raise CleanroomException(
            400, "INVALID_REQUEST", "C8 写操作必须携带 expected_version"
        )


def _assert_exact_binding(
    session: Mapping[str, Any], payload: Mapping[str, Any], *, require: bool = True
) -> None:
    """校验写入仍指向审阅会话创建时的精确资产版本。"""
    if not _is_c8(session):
        return
    if require and not review_service.is_version_bound_payload(payload):
        raise CleanroomException(
            400, "INVALID_REQUEST", "C8 写操作必须携带完整版本绑定"
        )
    binding = dict(payload)
    binding.setdefault("asset_id", session.get("asset_id"))
    version_id, version = review_service.validate_version_binding(binding)
    if (
        str(payload.get("asset_id") or session.get("asset_id"))
        != str(session.get("asset_id"))
        or version_id != session.get("version_id")
        or version != int(session.get("asset_version") or session.get("version") or 0)
    ):
        raise VersionConflictException(
            expected_version=int(
                payload.get("expected_version") or _session_revision(session)
            ),
            current_version=_session_revision(session),
            message="审阅写入的 asset_id/version_id/version 已过期，请重新读取当前版本",
        )


def _context_fields(item: Mapping[str, Any]) -> dict[str, Any]:
    """复制 C8 关联快照，防止交付记录依赖当前会话指针。"""
    fields = {
        key: item.get(key)
        for key in review_service.C8_VERSION_FIELDS
        if item.get(key) is not None
    }
    if isinstance(item.get("source"), Mapping):
        fields["source"] = dict(item["source"])
    fields["review_id"] = item.get("review_id") or item.get("session_id")
    fields["session_id"] = item.get("session_id")
    return fields


def _advance_session(session: dict[str, Any], *, c8: bool) -> int:
    if c8:
        next_revision = _session_revision(session) + 1
        session["revision"] = next_revision
        return next_revision
    session["version"] = int(session.get("version") or 1) + 1
    return int(session["version"])


def _approval_view(item: Mapping[str, Any]) -> dict[str, Any]:
    view = dict(item)
    view.setdefault("decision_id", view.get("approval_id"))
    view.setdefault("decision", view.get("status"))
    view.setdefault("review_id", view.get("session_id"))
    if "review_status" not in view:
        view["review_status"] = (
            "approved" if view.get("status") == "approved" else view.get("status")
        )
    view.setdefault(
        "next_action",
        review_service.next_action_for_decision(str(view.get("decision") or "")),
    )
    view.update(review_service.closure_projection(view, kind="review"))
    return view


def _delivery_view(item: Mapping[str, Any]) -> dict[str, Any]:
    view = dict(item)
    view.setdefault("review_id", view.get("session_id"))
    view.setdefault("delivery_status", "delivered")
    view.setdefault("updated_at", view.get("created_at"))
    view.update(review_service.closure_projection(view, kind="delivery"))
    return view


def _session_view(item: dict[str, Any]) -> dict[str, Any]:
    """对前端暴露的会话视图；字段名与 static/js/asset-review.js 对齐。"""
    view = {
        "id": item["session_id"],
        "session_id": item["session_id"],
        "review_id": item.get("review_id") or item["session_id"],
        "asset_id": item.get("asset_id") or "",
        "title": item.get("title") or "",
        "visibility": item.get("visibility") or "shared",
        "version_media_url": item.get("version_media_url"),
        "delivery_eligible": bool(item.get("delivery_eligible")),
        "status": item.get("status"),
        "version": item.get("version"),
        "version_id": item.get("version_id"),
        "asset_version": item.get("asset_version"),
        "review_status": item.get("review_status")
        or (
            "pending_review"
            if item.get("status") in ("open", "delivered")
            else item.get("status")
        ),
        "next_action": item.get("next_action")
        or review_service.next_action_for_decision(
            str(item.get("review_status") or "")
        ),
        "expected_version": _session_revision(item),
        "revision": _session_revision(item),
        "created_at": item.get("created_at"),
        "updated_at": item.get("updated_at"),
        "delivery_status": item.get("delivery_status"),
        "provider_status": item.get("provider_status"),
        "provider_message": item.get("provider_message"),
    }
    for key in review_service.C8_VERSION_FIELDS:
        if key in item and key not in view:
            view[key] = item.get(key)
    if isinstance(item.get("source"), Mapping):
        view["source"] = dict(item["source"])
    for key in ("closure_status", "blocker", "recovery"):
        if key in item:
            view[key] = item.get(key)
    view.update(review_service.closure_projection(view, kind="review"))
    return view


def _comment_view(item: dict[str, Any]) -> dict[str, Any]:
    # created_at 使用数字 epoch：前端执行 new Date(Number(created_at))。
    view = {
        "id": item["comment_id"],
        "comment_id": item["comment_id"],
        "session_id": item["session_id"],
        "review_id": item.get("review_id") or item.get("session_id"),
        "author_name": item.get("author_name") or "匿名",
        "reviewer_id": item.get("reviewer_id"),
        "created_at": item.get("created_at_epoch"),
        "occurred_at": item.get("occurred_at") or item.get("created_at"),
        "body": item.get("body") or "",
        "status": item.get("status") or "open",
        "resolved": (item.get("status") == "resolved"),
        "parent_id": item.get("parent_id"),
        "visibility": item.get("visibility") or "public",
        "timecode_ms": item.get("timecode_ms"),
        "in_ms": item.get("in_ms"),
        "out_ms": item.get("out_ms"),
        "annotations": item.get("annotations") or [],
        "version": item.get("version"),
    }
    for key in (
        "project_id",
        "entity_id",
        "episode_id",
        "scene_id",
        "shot_id",
        "canvas_id",
        "job_id",
        "asset_id",
        "version_id",
        "expected_version",
    ):
        if key in item:
            view[key] = item.get(key)
    return view


def _share_view(item: dict[str, Any], include_token: bool = False) -> dict[str, Any]:
    view = {
        "share_id": item["share_id"],
        "title": item.get("title") or "",
        "asset_ids": list(item.get("asset_ids") or []),
        "expires_at": item.get("expires_at"),
        "max_access_count": int(item.get("max_access_count") or 0),
        "access_count": int(item.get("access_count") or 0),
        "can_comment": bool(item.get("can_comment")),
        "can_download": bool(item.get("can_download")),
        "watermark_text": item.get("watermark_text") or "",
        "requires_password": bool(item.get("password_hash")),
        "created_at": item.get("created_at"),
    }
    if include_token:
        view["token"] = item.get("plaintext_token")
    return view


def list_sessions(
    asset_id: str | None = None,
    *,
    project_id: str | None = None,
    shot_id: str | None = None,
    version_id: str | None = None,
) -> dict[str, Any]:
    raw = state().read()
    items = [dict(v) for v in (raw.get("sessions") or {}).values()]
    if asset_id:
        items = [item for item in items if item.get("asset_id") == asset_id]
    if project_id:
        items = [item for item in items if item.get("project_id") == project_id]
    if shot_id:
        items = [item for item in items if item.get("shot_id") == shot_id]
    if version_id:
        items = [item for item in items if item.get("version_id") == version_id]
    items.sort(key=lambda item: item["session_id"])
    return {
        "sessions": [_session_view(item) for item in items],
        "revision": raw["revision"],
        "data_status": DATA_STATUS_OK,
        "data_gaps": [],
    }


def create_session(
    payload: dict[str, Any], expected_version: int | None
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise CleanroomException(400, "INVALID_REQUEST", "请求体必须是对象")
    c8 = review_service.is_version_bound_payload(payload)
    if c8:
        if expected_version is None:
            raise CleanroomException(
                400, "INVALID_REQUEST", "C8 创建审阅必须携带 expected_version"
            )
        version_id, asset_version = review_service.validate_version_binding(
            payload, require_source=True
        )
    else:
        version_id, asset_version = None, None

    def mutate(raw: dict[str, Any]) -> dict[str, Any]:
        _cas(raw, expected_version, "审查会话集合")
        session_id = _seq(raw, "rs-", "sessions")
        asset_id = str(payload.get("asset_id") or "").strip()
        if not asset_id:
            raise CleanroomException(400, "INVALID_REQUEST", "asset_id 不能为空")
        now = storage.now_iso()
        media_url = payload.get("version_media_url")
        record = {
            "session_id": session_id,
            "review_id": session_id,
            "asset_id": asset_id,
            "title": str(payload.get("title") or "").strip(),
            "visibility": str(payload.get("visibility") or "shared"),
            "version_media_url": str(media_url) if media_url else None,
            "delivery_eligible": bool(payload.get("delivery_eligible", True)),
            "status": "open",
            "review_status": "pending_review" if c8 else "open",
            "next_action": "review" if c8 else None,
            "version": asset_version if c8 else 1,
            "asset_version": asset_version,
            "version_id": version_id,
            "revision": 1 if c8 else None,
            "created_at": now,
            "updated_at": now,
        }
        if c8:
            provider_status = review_service.provider_state(payload)
            if provider_status:
                record["provider_status"] = provider_status
                if payload.get("provider_message") is not None:
                    record["provider_message"] = str(
                        payload.get("provider_message")
                    ).strip()
            for key in review_service.C8_VERSION_FIELDS:
                if key in payload and payload.get(key) is not None:
                    record[key] = (
                        dict(payload[key]) if key == "source" else payload[key]
                    )
            review_service.apply_closure_projection(
                record, kind="review", observed_at=now
            )
            # 新版本出现时，旧批准与交付只能作为历史读取，不能继续作为当前交付依据。
            for old_session in (raw.get("sessions") or {}).values():
                if (
                    old_session.get("asset_id") != asset_id
                    or not old_session.get("version_id")
                    or old_session.get("version_id") == version_id
                ):
                    continue
                old_session["review_status"] = "superseded"
                old_session["next_action"] = "review"
                if old_session.get("status") in (
                    "open",
                    "delivered",
                    "approved",
                    "rejected",
                ):
                    old_session["status"] = "superseded"
                old_session["closure_status"] = "blocked"
                old_session["blocker"] = review_service.blocker(
                    old_session,
                    code="APPROVAL_INVALIDATED",
                    scope="review",
                    message="当前资产已有新版本，原批准不能用于审阅或交付。",
                    retryable=False,
                    observed_at=now,
                )
                old_session["recovery"] = None
                for approval in (raw.get("approvals") or {}).values():
                    if approval.get("session_id") == old_session.get("session_id"):
                        if (
                            approval.get("decision") == "approved"
                            or approval.get("status") == "approved"
                        ):
                            approval["review_status"] = "approval_invalidated"
                            approval["status"] = "approval_invalidated"
                            approval["next_action"] = "review"
                            approval["invalidated_at"] = now
                            approval["closure_status"] = "blocked"
                            approval["blocker"] = review_service.blocker(
                                approval,
                                code="APPROVAL_INVALIDATED",
                                scope="review",
                                message="当前资产已有新版本，原批准不能用于审阅或交付。",
                                retryable=False,
                                observed_at=now,
                            )
                            approval["recovery"] = None
                for delivery in (raw.get("deliveries") or {}).values():
                    if delivery.get("session_id") == old_session.get("session_id"):
                        delivery["delivery_status"] = "superseded"
                        delivery["status"] = "superseded"
                        delivery["updated_at"] = now
                        delivery["next_action"] = "review"
                        delivery["closure_status"] = "blocked"
                        delivery["blocker"] = review_service.blocker(
                            delivery,
                            code="APPROVAL_INVALIDATED",
                            scope="delivery",
                            message="当前资产已有新版本，原批准不能用于审阅或交付。",
                            retryable=False,
                            observed_at=now,
                        )
                        delivery["recovery"] = None
        raw["sessions"][session_id] = record
        _bump(raw)
        return _session_view(record)

    session = state().mutate(mutate)
    return {"session": session, "sessions": [session]}


def get_session(session_id: str) -> dict[str, Any]:
    session = _require("sessions", session_id, "SESSION_NOT_FOUND", "审查会话")
    raw = state().read()
    comments = [
        dict(v)
        for v in (raw.get("comments") or {}).values()
        if v.get("session_id") == session_id
    ]
    comments.sort(key=lambda item: item["comment_id"])
    approvals = [
        dict(v)
        for v in (raw.get("approvals") or {}).values()
        if v.get("session_id") == session_id
    ]
    approvals.sort(key=lambda item: item["approval_id"])
    deliveries = [
        dict(v)
        for v in (raw.get("deliveries") or {}).values()
        if v.get("session_id") == session_id
    ]
    deliveries.sort(key=lambda item: item["delivery_id"])
    session_view = _session_view(session)
    delivery_views = [_delivery_view(item) for item in deliveries]
    return {
        "session": session_view,
        "comments": [_comment_view(item) for item in comments],
        "approvals": [_approval_view(item) for item in approvals],
        "delivery": (delivery_views[-1] if delivery_views else None),
        "deliveries": delivery_views,
        "delivery_records": delivery_views,
        "asset": (
            {
                "asset_id": session.get("asset_id"),
                "version_id": session.get("version_id"),
                "version": session.get("asset_version") or session.get("version"),
            }
            if session.get("asset_id")
            else None
        ),
        "revision": raw["revision"],
        "data_status": DATA_STATUS_OK,
        "data_gaps": [],
    }


# ---------------------------------------------------------------------------
# 评论
# ---------------------------------------------------------------------------


def _now_epoch() -> int:
    """数字 epoch（毫秒），前端 new Date(Number(created_at)) 依赖该口径。"""
    import time

    return int(_unix_time() * 1000)


def create_comment(session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise CleanroomException(400, "INVALID_REQUEST", "请求体必须是对象")
    body = str(payload.get("body") or "").strip()
    if not body:
        raise CleanroomException(400, "INVALID_REQUEST", "评论内容不能为空")

    def mutate(raw: dict[str, Any]) -> dict[str, Any]:
        session = raw["sessions"].get(session_id)
        if session is None:
            raise CleanroomException(404, "SESSION_NOT_FOUND", "审查会话不存在")
        c8 = _is_c8(session)
        _require_expected_for_c8(session, payload)
        _cas_session(session, payload.get("expected_version"))
        _assert_exact_binding(session, payload)
        comment_id = _seq(raw, "rc-", "comments")
        now = storage.now_iso()
        record = {
            "comment_id": comment_id,
            "session_id": session_id,
            "review_id": session_id,
            "author_name": str(payload.get("author_name") or "匿名"),
            "reviewer_id": str(
                payload.get("reviewer_id") or payload.get("author_name") or ""
            ).strip()
            or None,
            "body": body,
            "status": "open",
            "parent_id": payload.get("parent_id"),
            "visibility": str(payload.get("visibility") or "public"),
            "timecode_ms": payload.get("timecode_ms"),
            "in_ms": payload.get("in_ms"),
            "out_ms": payload.get("out_ms"),
            "annotations": list(payload.get("annotations") or []),
            "created_at": now,
            "created_at_epoch": _now_epoch(),
            "occurred_at": now,
            "version": session.get("asset_version") if c8 else 1,
        }
        if c8:
            record.update(_context_fields(session))
        raw["comments"][comment_id] = record
        session["updated_at"] = now
        new_revision = _advance_session(session, c8=c8)
        record["expected_version"] = new_revision
        _bump(raw)
        return _comment_view(record)

    comment = state().mutate(mutate)
    return {"comment": comment}


def update_comment(comment_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise CleanroomException(400, "INVALID_REQUEST", "请求体必须是对象")

    def mutate(raw: dict[str, Any]) -> dict[str, Any]:
        record = raw["comments"].get(comment_id)
        if record is None:
            raise CleanroomException(404, "COMMENT_NOT_FOUND", "评论不存在")
        session = raw["sessions"].get(record.get("session_id"))
        c8 = _is_c8(session)
        if session is not None:
            _require_expected_for_c8(session, payload)
            _cas_session(session, payload.get("expected_version"))
            _assert_exact_binding(session, payload)
        if "resolved" in payload and payload.get("resolved") is not None:
            record["status"] = "resolved" if bool(payload.get("resolved")) else "open"
        if "status" in payload and payload.get("status") in COMMENT_STATES:
            record["status"] = str(payload.get("status"))
        if payload.get("body") is not None:
            text = str(payload.get("body")).strip()
            if not text:
                raise CleanroomException(400, "INVALID_REQUEST", "评论内容不能为空")
            record["body"] = text
        if session is not None:
            session["updated_at"] = storage.now_iso()
            record["expected_version"] = _advance_session(session, c8=c8)
        else:
            record["version"] = int(record.get("version") or 1) + 1
        _bump(raw)
        return _comment_view(record)

    comment = state().mutate(mutate)
    return {"comment": comment}


# ---------------------------------------------------------------------------
# 审批
# ---------------------------------------------------------------------------


def put_approval(session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise CleanroomException(400, "INVALID_REQUEST", "请求体必须是对象")
    decision = review_service.decision_from_payload(payload)
    legacy_status = str(payload.get("status") or "").strip()
    if (
        decision not in {"approved", "changes_requested"}
        and legacy_status not in APPROVAL_STATES
    ):
        raise CleanroomException(
            400,
            "INVALID_REQUEST",
            "approval.status 只接受 %s" % "/".join(APPROVAL_STATES),
        )

    def mutate(raw: dict[str, Any]) -> dict[str, Any]:
        session = raw["sessions"].get(session_id)
        if session is None:
            raise CleanroomException(404, "SESSION_NOT_FOUND", "审查会话不存在")
        c8 = _is_c8(session)
        _require_expected_for_c8(session, payload)
        _cas_session(session, payload.get("expected_version"))
        _assert_exact_binding(session, payload)
        if c8 and review_service.provider_state(session):
            projection = review_service.closure_projection(session, kind="review")
            raise CleanroomException(
                409,
                "INVALID_STATE_TRANSITION",
                "当前 Provider 状态尚未形成可审阅结果，请按 next_action 继续",
                extra={
                    "closure_status": projection["closure_status"],
                    "blocker": projection["blocker"],
                    "next_action": projection["next_action"],
                },
                expose_extra_fields={"closure_status", "blocker", "next_action"},
            )
        if c8 and decision not in {"approved", "changes_requested"}:
            raise CleanroomException(
                400, "INVALID_REQUEST", "decision 只接受 approved/changes_requested"
            )
        next_action = str(payload.get("next_action") or "").strip()
        if c8 and decision == "changes_requested" and not next_action:
            # 契约要求退回必须能告诉用户下一步；默认动作是可执行的重新生成。
            next_action = "regenerate_version"
        current = session.get("status")
        # 旧 B6 客户端仍遵循 open -> delivered -> approved/rejected；
        # C8 的 pending_review 可以直接登记精确版本决定。
        if current == "open" and not c8:
            raise CleanroomException(
                409, "INVALID_STATE_TRANSITION", "会话尚未交付，不能审批"
            )
        if c8 and current in ("approved", "rejected", "superseded"):
            raise CleanroomException(
                409,
                "INVALID_STATE_TRANSITION",
                "当前版本已完成决定，必须创建新版本后重新审阅",
            )
        if (
            current in ("approved", "rejected", "superseded")
            and not c8
            and current != _status_for(legacy_status)
        ):
            raise CleanroomException(
                409, "INVALID_STATE_TRANSITION", "会话已终态，不能再变更审批结论"
            )
        approval_id = ("rd-%s-%04d" if c8 else "apr-%s-%04d") % (
            session_id,
            len(
                [
                    v
                    for v in raw["approvals"].values()
                    if v.get("session_id") == session_id
                ]
            )
            + 1,
        )
        now = storage.now_iso()
        status_value = decision if c8 else legacy_status
        record = {
            "approval_id": approval_id,
            "decision_id": approval_id,
            "session_id": session_id,
            "review_id": session_id,
            "status": status_value,
            "decision": decision if c8 else legacy_status,
            "review_status": decision if c8 else _status_for(legacy_status),
            "next_action": next_action
            or review_service.next_action_for_decision(decision),
            "note": str(payload.get("note") or ""),
            "author_name": str(payload.get("author_name") or "匿名"),
            "reviewer_id": str(
                payload.get("reviewer_id") or payload.get("author_name") or ""
            ).strip()
            or None,
            "created_at": now,
            "created_at_epoch": _now_epoch(),
            "occurred_at": now,
        }
        if c8:
            record.update(_context_fields(session))
        raw["approvals"][approval_id] = record
        session["status"] = "approved" if decision == "approved" else "rejected"
        session["review_status"] = decision if c8 else session.get("review_status")
        session["next_action"] = record["next_action"]
        session["updated_at"] = now
        new_revision = _advance_session(session, c8=c8)
        record["expected_version"] = new_revision
        review_service.apply_closure_projection(record, kind="review", observed_at=now)
        review_service.apply_closure_projection(session, kind="review", observed_at=now)
        _bump(raw)
        return _approval_view(record)

    approval = state().mutate(mutate)
    return {"approval": approval, "session": get_session(session_id)["session"]}


def _status_for(approval_status: str) -> str:
    """审批结论到会话状态的映射。"""
    return "approved" if approval_status == "approved" else "rejected"


# ---------------------------------------------------------------------------
# 交付与导出
# ---------------------------------------------------------------------------


def create_delivery(session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise CleanroomException(400, "INVALID_REQUEST", "请求体必须是对象")

    def mutate(raw: dict[str, Any]) -> dict[str, Any]:
        session = raw["sessions"].get(session_id)
        if session is None:
            raise CleanroomException(404, "SESSION_NOT_FOUND", "审查会话不存在")
        c8 = _is_c8(session)
        requested_id = str(
            payload.get("delivery_id") or payload.get("id") or ""
        ).strip()
        existing = (
            (raw.get("deliveries") or {}).get(requested_id) if requested_id else None
        )
        if existing is not None:
            _require_expected_for_c8(session, payload)
            _assert_exact_binding(session, payload)
            if c8:
                expected_snapshot = _context_fields(session)
                for key in ("version_id", "asset_id", "version"):
                    if existing.get(key) != expected_snapshot.get(key):
                        raise VersionConflictException(
                            expected_version=int(
                                payload.get("expected_version")
                                or _session_revision(session)
                            ),
                            current_version=_session_revision(session),
                            message="delivery_id 已绑定其他版本，不能改绑",
                        )
                requested_decision = str(payload.get("decision_id") or "").strip()
                if (
                    requested_decision
                    and existing.get("decision_id") != requested_decision
                ):
                    raise VersionConflictException(
                        expected_version=int(
                            payload.get("expected_version")
                            or _session_revision(session)
                        ),
                        current_version=_session_revision(session),
                        message="delivery_id 已绑定其他批准决定，不能改绑",
                    )
            return _delivery_view(existing)
        _require_expected_for_c8(session, payload)
        _cas_session(session, payload.get("expected_version"))
        _assert_exact_binding(session, payload)

        if c8:
            if review_service.provider_state(session):
                projection = review_service.closure_projection(session, kind="review")
                raise CleanroomException(
                    409,
                    "INVALID_STATE_TRANSITION",
                    "当前 Provider 状态尚未形成可交付结果，请按 next_action 继续",
                    extra={
                        "closure_status": projection["closure_status"],
                        "blocker": projection["blocker"],
                        "next_action": projection["next_action"],
                    },
                    expose_extra_fields={"closure_status", "blocker", "next_action"},
                )
            if (
                session.get("status") != "approved"
                or session.get("review_status") != "approved"
            ):
                raise CleanroomException(
                    409, "INVALID_STATE_TRANSITION", "只有明确批准的当前版本才能交付"
                )
            decision_id = str(payload.get("decision_id") or "").strip()
            approvals = [
                item
                for item in (raw.get("approvals") or {}).values()
                if item.get("session_id") == session_id
                and item.get("decision_id") == decision_id
                and item.get("decision") == "approved"
                and item.get("review_status") == "approved"
            ]
            if not decision_id or not approvals:
                raise CleanroomException(
                    409,
                    "INVALID_STATE_TRANSITION",
                    "交付必须引用当前版本的 approved decision_id",
                )
            for field in review_service.C8_ARTIFACT_FIELDS:
                if not str(payload.get(field) or "").strip():
                    raise CleanroomException(
                        400, "INVALID_REQUEST", "交付必须携带 %s" % field
                    )
        elif session.get("status") == "open":
            # 旧 B6 客户端保留 open -> delivered 的兼容状态机。
            session["status"] = "delivered"
        delivery_id = requested_id or _seq(raw, "dlv-", "deliveries")
        if requested_id and (
            not requested_id.isascii() or any(ch.isspace() for ch in requested_id)
        ):
            raise CleanroomException(
                400, "INVALID_REQUEST", "delivery_id 必须是无空格 ASCII 标识"
            )
        now = storage.now_iso()
        record = {
            "delivery_id": delivery_id,
            "id": delivery_id,
            "session_id": session_id,
            "review_id": session_id,
            "title": str(payload.get("title") or session.get("title") or "delivery"),
            "asset_id": session.get("asset_id"),
            "created_at": now,
            "updated_at": now,
            "delivery_status": "delivered",
            "version": session.get("asset_version") if c8 else 1,
        }
        if c8:
            record.update(_context_fields(session))
            record.update(
                {
                    field: str(payload[field]).strip()
                    for field in review_service.C8_ARTIFACT_FIELDS
                }
            )
            record["decision_id"] = str(payload.get("decision_id")).strip()
            record["approval_id"] = record["decision_id"]
            record["decision"] = "approved"
            record["expected_version"] = _session_revision(session) + 1
        raw["deliveries"][delivery_id] = record
        session["updated_at"] = now
        if c8:
            session["delivery_status"] = "delivered"
            _advance_session(session, c8=True)
            record["expected_version"] = _session_revision(session)
            review_service.apply_closure_projection(
                session, kind="review", observed_at=now
            )
            review_service.apply_closure_projection(
                record, kind="delivery", observed_at=now
            )
        else:
            _advance_session(session, c8=False)
        _bump(raw)
        return _delivery_view(record)

    delivery = state().mutate(mutate)
    return {"delivery": delivery}


def export_delivery(delivery_id: str) -> dict[str, Any]:
    """构造交付导出 PDF（纯标准库生成，%PDF- 开头、%%EOF 结尾）。"""
    raw = state().read()
    delivery = (raw.get("deliveries") or {}).get(delivery_id)
    if delivery is None:
        raise CleanroomException(404, "DELIVERY_NOT_FOUND", "交付记录不存在")
    if delivery.get("delivery_status") == "superseded":
        raise CleanroomException(
            409, "VERSION_CONFLICT", "交付记录绑定的批准版本已失效，请重新审阅当前版本"
        )
    session = (raw.get("sessions") or {}).get(delivery.get("session_id")) or {}
    view = _delivery_view(delivery)
    lines = [
        "Gods-Workbench 交付清单",
        "交付编号: %s" % delivery_id,
        "审查会话: %s" % delivery.get("session_id"),
        "素材编号: %s" % (session.get("asset_id") or "-"),
        "标题: %s" % (delivery.get("title") or "-"),
        "版本编号: %s" % (view.get("version_id") or "-"),
        "批准决定: %s" % (view.get("decision_id") or "-"),
        "生成时间: %s" % storage.now_iso(),
    ]
    return {
        "delivery": view,
        "filename": "%s.pdf" % delivery_id,
        "pdf": _build_pdf(lines),
        "data_status": DATA_STATUS_OK,
    }


def _build_pdf(lines: list[str]) -> bytes:
    """交付摘要中文排版并附同一快照UTF-8原文。"""
    from gw.core.text_pdf import build_text_pdf

    return build_text_pdf("\n".join(str(line) for line in lines))


# ---------------------------------------------------------------------------
# 分享（本地令牌：只存哈希，明文仅返回一次）
# ---------------------------------------------------------------------------

_SHARE_RATE_LIMIT = 120
_SHARE_RATE_WINDOW_SECONDS = 60
_SHARE_RATE_BUCKET_LIMIT = 2048
_SHARE_MAX_ASSETS = 100
_SHARE_MAX_TOKEN_LENGTH = 256
_SHARE_UNKNOWN_TOKEN_HASH = hashlib.sha256(
    b"asset-review-unknown-share-token"
).hexdigest()


def _resolve_assets(asset_ids):
    """回读素材注册表，只返回真实存在的素材元数据；无法解析的进 data_gaps。"""
    from gw.asset_registry import repository as asset_repo

    resolved = []
    gaps = []
    for asset_id in asset_ids:
        try:
            item = asset_repo.get_asset(str(asset_id))
        except CleanroomException:
            gaps.append(str(asset_id))
            continue
        except Exception:
            raise CleanroomException(
                503,
                "SHARE_ASSETS_UNAVAILABLE",
                "素材注册表不可用，已拒绝返回未经验证的素材",
                extra={"data_status": "not_integrated"},
                expose_extra_fields={"data_status"},
            )
        resolved.append(
            {
                "id": item.get("asset_id"),
                "asset_id": item.get("asset_id"),
                "name": item.get("name"),
                "kind": item.get("kind"),
                "metadata": dict(item.get("metadata") or {}),
                # 分享页只调用自己的同源媒体路由，不沿用受登录保护的注册表URL。
                "media_url": None,
                "comments": [],
                "approvals": [],
            }
        )
    return resolved, gaps


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _validated_token(token: Any) -> str:
    if not isinstance(token, str) or not token or len(token) > _SHARE_MAX_TOKEN_LENGTH:
        raise CleanroomException(404, "SHARE_NOT_FOUND", "分享不存在或已失效")
    return token


def _normalize_expiry(value: Any) -> str | None:
    """接受 Unix 毫秒整数或带时区 ISO-8601，统一保存为 UTC ISO-8601。"""
    if value is None:
        return None
    if isinstance(value, bool):
        raise CleanroomException(
            400, "INVALID_SHARE_EXPIRY", "expires_at 必须为毫秒时间戳或带时区 ISO-8601"
        )
    try:
        if isinstance(value, int) and value > 0:
            parsed = datetime.fromtimestamp(value / 1000, tz=UTC)
        elif isinstance(value, str) and len(value) <= 80:
            source = value.strip()
            if not source:
                raise ValueError("empty")
            parsed = datetime.fromisoformat(
                source[:-1] + "+00:00" if source.endswith(("Z", "z")) else source
            )
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                raise ValueError("timezone required")
            parsed = parsed.astimezone(UTC)
        else:
            raise ValueError("unsupported")
    except (OverflowError, OSError, ValueError):
        raise CleanroomException(
            400, "INVALID_SHARE_EXPIRY", "expires_at 必须为毫秒时间戳或带时区 ISO-8601"
        )
    return parsed.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _expiry_ms(record: dict[str, Any]) -> int | None:
    raw_expiry = record.get("expires_at")
    if raw_expiry is None:
        return None
    normalized = _normalize_expiry(raw_expiry)
    if normalized is None:
        return None
    try:
        return int(
            datetime.fromisoformat(normalized.replace("Z", "+00:00")).timestamp() * 1000
        )
    except (CleanroomException, OverflowError, OSError, ValueError):
        # 已落盘但无法解释的到期值失败关闭。
        return 0


def _ensure_not_expired(record: dict[str, Any], now_ms: int | None = None) -> None:
    expiry = _expiry_ms(record)
    if (
        expiry is not None
        and (now_ms if now_ms is not None else int(_unix_time() * 1000)) >= expiry
    ):
        raise CleanroomException(410, "SHARE_EXPIRED", "分享已过期")


def _session_for_share(
    raw: dict[str, Any], session_id: str | None, asset_ids: list[str]
) -> dict[str, Any] | None:
    if not session_id:
        return None
    session = (raw.get("sessions") or {}).get(session_id)
    if not session:
        raise CleanroomException(404, "SESSION_NOT_FOUND", "绑定的审阅会话不存在")
    if session.get("asset_id") not in asset_ids:
        raise CleanroomException(
            400, "SHARE_SESSION_ASSET_MISMATCH", "审阅会话素材不属于本次分享"
        )
    return session


def create_share(payload: dict[str, Any], context: Any = None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise CleanroomException(400, "INVALID_REQUEST", "请求体必须是对象")
    asset_ids = payload.get("asset_ids") or []
    if (
        not isinstance(asset_ids, list)
        or not asset_ids
        or len(asset_ids) > _SHARE_MAX_ASSETS
    ):
        raise CleanroomException(
            400, "INVALID_REQUEST", "asset_ids 必须包含 1 至 100 个资产"
        )
    normalized_ids = []
    for item in asset_ids:
        asset_id = str(item or "").strip()
        if not asset_id or len(asset_id) > 128:
            raise CleanroomException(
                400, "INVALID_REQUEST", "asset_ids 中包含无效资产编号"
            )
        if asset_id not in normalized_ids:
            normalized_ids.append(asset_id)
    resolved, gaps = _resolve_assets(normalized_ids)
    if gaps or len(resolved) != len(normalized_ids):
        raise CleanroomException(
            404, "SHARE_ASSET_NOT_FOUND", "分享只能包含已登记的真实资产"
        )

    title = str(payload.get("title") or "").strip() or "素材分享"
    if len(title) > 160:
        raise CleanroomException(400, "INVALID_REQUEST", "分享标题不能超过 160 个字符")
    raw_password = str(payload.get("password") or "")
    if len(raw_password) > 1024:
        raise CleanroomException(
            400, "INVALID_REQUEST", "分享口令长度不能超过 1024 个字符"
        )
    try:
        max_access_count = int(payload.get("max_access_count") or 0)
    except (TypeError, ValueError):
        raise CleanroomException(
            400, "INVALID_REQUEST", "max_access_count 必须是非负整数"
        )
    if max_access_count < 0 or max_access_count > 2_147_483_647:
        raise CleanroomException(
            400, "INVALID_REQUEST", "max_access_count 必须是非负整数"
        )
    expires_at = _normalize_expiry(payload.get("expires_at"))
    session_raw = payload.get("session_id")
    session_id = str(session_raw).strip() if session_raw is not None else None
    if session_id == "":
        session_id = None

    creator_key = None
    creator_governance = False
    if context is not None:
        from gw.projects_hub.service import owner_key_for_context

        try:
            creator_key = owner_key_for_context(context)
        except CleanroomException:
            creator_key = None
        creator_governance = str(getattr(context, "role", "") or "").lower() in {
            "admin",
            "governor",
        }

    def mutate(raw: dict[str, Any]) -> dict[str, Any]:
        _session_for_share(raw, session_id, normalized_ids)
        share_id = _seq(raw, "shr-", "shares")
        token = secrets.token_urlsafe(32)
        record = {
            "share_id": share_id,
            "token_hash": _hash_token(token),
            "title": title,
            "asset_ids": normalized_ids,
            "session_id": session_id,
            "expires_at": expires_at,
            "max_access_count": max_access_count,
            "access_count": 0,
            "can_comment": bool(payload.get("can_comment", True)),
            "can_download": bool(payload.get("can_download", True)),
            "watermark_text": str(payload.get("watermark_text") or "")[:256],
            "password_hash": (_hash_token(raw_password) if raw_password else None),
            "created_at": storage.now_iso(),
        }
        if creator_key:
            record["created_by"] = creator_key
            record["created_by_governance"] = creator_governance
        raw.setdefault("shares", {})[share_id] = record
        _bump(raw)
        view = _share_view(record)
        view["token"] = token  # 明文令牌仅此一次返回，不落盘。
        return view

    share = state().mutate(mutate)
    return {"share": share, "token": share["token"]}


def _creator_still_reads(record: dict[str, Any]) -> bool:
    """C4-AC8：分享创建者对素材所属任一项目失去读权限时，分享立即失效。

    实时判定（每次解析令牌时计算），撤销即刻生效；只影响该创建者本人创建的分享。
    无 created_by 的历史分享不参与判定（迁移默认，已在 PR 中标注）。
    """
    creator = record.get("created_by")
    if not creator:
        return True
    from gw.asset_registry import repository as asset_repo
    from gw.projects_hub.service import default_projects_service

    role = "admin" if record.get("created_by_governance") else ""
    project_ids = []
    for asset_id in record.get("asset_ids") or []:
        try:
            asset = asset_repo.get_asset(str(asset_id))
        except CleanroomException:
            continue
        for pid in [asset.get("project_id")] + list(asset.get("project_ids") or []):
            if isinstance(pid, str) and pid and pid not in project_ids:
                project_ids.append(pid)
    for pid in project_ids:
        try:
            default_projects_service.get_project(pid)
        except CleanroomException:
            continue  # 未知项目不构成读授权约束
        if not default_projects_service.can_read_project(
            pid, owner_key=creator, role=role
        ):
            return False
    return True


def _find_share(raw: dict[str, Any], token: str) -> dict[str, Any]:
    token = _validated_token(token)
    target = _hash_token(token)
    for record in (raw.get("shares") or {}).values():
        if secrets.compare_digest(str(record.get("token_hash") or ""), target):
            if not _creator_still_reads(record):
                break
            return record
    raise CleanroomException(404, "SHARE_NOT_FOUND", "分享不存在或已失效")


def _enforce_rate(token: str, operation: str) -> None:
    """在独立持久事务计数；未知令牌共用固定桶，桶和窗口记录均有上限。"""
    if operation not in {"meta", "access", "comment", "approval", "media"}:
        raise CleanroomException(
            503, "SHARE_RATE_LIMIT_UNAVAILABLE", "分享限流状态不可用"
        )
    now = int(_unix_time())
    result: dict[str, Any] = {}

    def mutate(raw: dict[str, Any]) -> None:
        rates = raw.setdefault("rate", {})
        # 先清理窗口外数据；键上限与每键时间戳上限同时保证持久状态有界。
        for key in list(rates.keys()):
            values = rates.get(key)
            if not isinstance(values, list):
                rates.pop(key, None)
                continue
            recent = values[-(_SHARE_RATE_LIMIT * 2) :]
            kept = [
                int(ts)
                for ts in recent
                if isinstance(ts, (int, float))
                and 0 <= now - int(ts) < _SHARE_RATE_WINDOW_SECONDS
            ]
            if kept:
                rates[key] = kept[-_SHARE_RATE_LIMIT:]
            else:
                rates.pop(key, None)
        try:
            known = _find_share(raw, token)
            token_hash = str(known.get("token_hash") or _hash_token(token))
        except CleanroomException:
            token_hash = _SHARE_UNKNOWN_TOKEN_HASH
        key = "%s:%s" % (token_hash, operation)
        if key not in rates and len(rates) >= _SHARE_RATE_BUCKET_LIMIT:
            result.update(persisted=False, limited=True)
            return
        bucket = list(rates.get(key) or [])
        bucket.append(now)
        limited = len(bucket) > _SHARE_RATE_LIMIT
        rates[key] = bucket[-_SHARE_RATE_LIMIT:]
        result.update(persisted=True, limited=limited)

    state().mutate(mutate)
    if not result.get("persisted"):
        raise CleanroomException(
            503, "SHARE_RATE_LIMIT_UNAVAILABLE", "分享限流状态已满，已拒绝请求"
        )
    if result.get("limited"):
        raise CleanroomException(429, "RATE_LIMITED", "请求过于频繁，请稍后重试")


def enforce_public_rate(token: str, operation: str) -> None:
    """供路由在读取公开请求体之前持久计数，超限时不再消耗正文。"""
    _enforce_rate(token, operation)


def _public_review_items(
    raw: dict[str, Any], share_id: str, asset_id: str
) -> dict[str, list[dict[str, Any]]]:
    # 只按显式 share_id + asset_id 读取，不根据旧记录的 asset_id 猜测分享归属。
    comments = [
        item
        for item in (raw.get("comments") or {}).values()
        if item.get("share_id") == share_id and item.get("asset_id") == asset_id
    ]
    comments.sort(
        key=lambda item: (
            int(item.get("created_at_epoch") or 0),
            str(item.get("comment_id") or ""),
        )
    )
    approvals = [
        item
        for item in (raw.get("approvals") or {}).values()
        if item.get("share_id") == share_id and item.get("asset_id") == asset_id
    ]
    approvals.sort(
        key=lambda item: (
            int(item.get("created_at_epoch") or 0),
            str(item.get("approval_id") or ""),
        )
    )
    comment_views = []
    for item in comments:
        view = _comment_view(item)
        view.update(share_id=share_id, asset_id=asset_id)
        comment_views.append(view)
    approval_views = [
        {
            "id": item.get("approval_id"),
            "approval_id": item.get("approval_id"),
            "share_id": share_id,
            "session_id": item.get("session_id"),
            "asset_id": asset_id,
            "status": item.get("status"),
            "note": item.get("note") or "",
            "author_name": item.get("author_name") or "访客",
            "created_at": item.get("created_at_epoch"),
        }
        for item in approvals
    ]
    return {"comments": comment_views, "approvals": approval_views}


def public_meta(token: str) -> dict[str, Any]:
    """公开元信息：不回显口令哈希与令牌哈希。"""
    _enforce_rate(token, "meta")
    raw = state().read()
    record = _find_share(raw, token)
    _ensure_not_expired(record)
    return {
        "share_id": record["share_id"],
        "title": record.get("title") or "",
        "available": True,
        "requires_password": bool(record.get("password_hash")),
        "expires_at": record.get("expires_at"),
        "can_comment": bool(record.get("can_comment")),
        "can_download": bool(record.get("can_download")),
    }


def public_access(
    token: str, payload: dict[str, Any], *, rate_checked: bool = False
) -> dict[str, Any]:
    """校验口令并返回可展示的分享载荷；仅返回真实登记的 asset_id。"""
    payload = payload if isinstance(payload, dict) else {}
    if not rate_checked:
        _enforce_rate(token, "access")

    def mutate(state_raw: dict[str, Any]) -> dict[str, Any]:
        record = _find_share(state_raw, token)
        _ensure_not_expired(record)
        stored = record.get("password_hash")
        if stored:
            supplied = _hash_token(str(payload.get("password") or ""))
            if not secrets.compare_digest(stored, supplied):
                raise CleanroomException(
                    401, "SHARE_PASSWORD_REQUIRED", "分享口令不正确"
                )
        max_count = int(record.get("max_access_count") or 0)
        if max_count and int(record.get("access_count") or 0) >= max_count:
            raise CleanroomException(410, "SHARE_EXPIRED", "分享访问次数已用尽")
        resolved, gaps = _resolve_assets(list(record.get("asset_ids") or []))
        if gaps:
            raise CleanroomException(
                404, "SHARE_ASSET_NOT_FOUND", "分享中的素材已不可用"
            )
        for asset in resolved:
            asset_id = str(asset.get("asset_id") or "")
            asset["media_url"] = "/api/public/shares/%s/assets/%s/media" % (
                token,
                asset_id,
            )
            asset["review"] = _public_review_items(
                state_raw, record["share_id"], asset_id
            )
        record["access_count"] = int(record.get("access_count") or 0) + 1
        ticket = secrets.token_urlsafe(24)
        record["ticket_hash"] = _hash_token(ticket)
        _bump(state_raw)
        return {
            "share": {
                "share_id": record["share_id"],
                "title": record.get("title") or "",
                "expires_at": record.get("expires_at"),
                "watermark_text": record.get("watermark_text") or "",
                "permissions": {
                    "comment": bool(record.get("can_comment")),
                    "download": bool(record.get("can_download")),
                },
            },
            "assets": resolved,
            "data_gaps": [],
            "data_status": "ok",
            "ticket": ticket,
            "expires_at": record.get("expires_at"),
        }

    return state().mutate(mutate)


def _require_ticket(record: dict[str, Any], ticket: Any) -> None:
    stored = record.get("ticket_hash")
    if not stored:
        raise CleanroomException(
            401, "SHARE_TICKET_REQUIRED", "请先通过访问校验获取票据"
        )
    if (
        not isinstance(ticket, str)
        or not ticket
        or len(ticket) > 256
        or not secrets.compare_digest(str(stored), _hash_token(ticket))
    ):
        raise CleanroomException(
            401, "SHARE_TICKET_REQUIRED", "访问票据无效，请重新校验"
        )


def _share_asset(record: dict[str, Any], asset_id: Any) -> str:
    value = str(asset_id or "").strip()
    if not value or len(value) > 128 or value not in (record.get("asset_ids") or []):
        raise CleanroomException(404, "SHARE_ASSET_NOT_FOUND", "该素材不属于此分享")
    return value


def _linked_session_for_write(
    raw: dict[str, Any], record: dict[str, Any], asset_id: str
) -> dict[str, Any] | None:
    session_id = record.get("session_id")
    if not session_id:
        return None
    session = (raw.get("sessions") or {}).get(session_id)
    if not session or session.get("asset_id") not in (record.get("asset_ids") or []):
        raise CleanroomException(
            409, "SHARE_SESSION_CONFLICT", "绑定的审阅会话已失效或素材不匹配"
        )
    # 多资产分享中，绑定会话只关联其自身素材；其他资产的访客记录仍只属于分享。
    return session if session.get("asset_id") == asset_id else None


def public_comment(
    token: str, payload: dict[str, Any], *, rate_checked: bool = False
) -> dict[str, Any]:
    payload = payload if isinstance(payload, dict) else {}
    if not rate_checked:
        _enforce_rate(token, "comment")
    _ensure_not_expired(_find_share(state().read(), token))
    body = str(payload.get("body") or "").strip()
    if not body or len(body) > 10000:
        raise CleanroomException(
            400, "INVALID_REQUEST", "评论内容必须为 1 至 10000 个字符"
        )
    guest_name = str(payload.get("guest_name") or "访客").strip() or "访客"
    if len(guest_name) > 100:
        raise CleanroomException(400, "INVALID_REQUEST", "访客称呼不能超过 100 个字符")
    annotations = payload.get("annotations") or []
    if not isinstance(annotations, list) or len(annotations) > 100:
        raise CleanroomException(
            400, "INVALID_REQUEST", "annotations 必须是最多 100 项的数组"
        )

    def mutate(state_raw: dict[str, Any]) -> dict[str, Any]:
        target = _find_share(state_raw, token)
        _ensure_not_expired(target)
        asset_id = _share_asset(target, payload.get("asset_id"))
        _require_ticket(target, payload.get("ticket"))
        if not target.get("can_comment"):
            raise CleanroomException(403, "SHARE_COMMENT_FORBIDDEN", "该分享未开放评论")
        session = _linked_session_for_write(state_raw, target, asset_id)
        share_id = target["share_id"]
        related = [
            v
            for v in (state_raw.get("comments") or {}).values()
            if v.get("share_id") == share_id
        ]
        comment_id = "rc-%s-%04d" % (share_id, len(related) + 1)
        item = {
            "comment_id": comment_id,
            "share_id": share_id,
            "session_id": session.get("session_id") if session else None,
            "asset_id": asset_id,
            "author_name": guest_name,
            "body": body,
            "status": "open",
            "visibility": "public",
            "timecode_ms": payload.get("timecode_ms"),
            "annotations": annotations,
            "created_at": storage.now_iso(),
            "created_at_epoch": _now_epoch(),
            "version": 1,
        }
        state_raw.setdefault("comments", {})[comment_id] = item
        if session:
            session["updated_at"] = item["created_at"]
            session["version"] = int(session.get("version") or 1) + 1
        _bump(state_raw)
        return {
            "comment": _comment_view(item)
            | {"share_id": share_id, "asset_id": asset_id}
        }

    return state().mutate(mutate)


def public_approval(
    token: str, payload: dict[str, Any], *, rate_checked: bool = False
) -> dict[str, Any]:
    payload = payload if isinstance(payload, dict) else {}
    if not rate_checked:
        _enforce_rate(token, "approval")
    # 保持未知/篡改令牌与到期分享的优先级；下面的 mutate 会在写入锁内再重验。
    _ensure_not_expired(_find_share(state().read(), token))
    status = str(payload.get("status") or "").strip()
    if status not in ("approved", "changes_requested"):
        raise CleanroomException(
            400, "INVALID_REQUEST", "status 只接受 approved/changes_requested"
        )
    note = str(payload.get("note") or "")
    guest_name = str(payload.get("guest_name") or "访客").strip() or "访客"
    if len(note) > 4000 or len(guest_name) > 100:
        raise CleanroomException(
            400, "INVALID_REQUEST", "审批说明或访客称呼超过长度上限"
        )

    def mutate(state_raw: dict[str, Any]) -> dict[str, Any]:
        target = _find_share(state_raw, token)
        _ensure_not_expired(target)
        asset_id = _share_asset(target, payload.get("asset_id"))
        _require_ticket(target, payload.get("ticket"))
        if not target.get("can_comment"):
            raise CleanroomException(
                403, "SHARE_APPROVAL_FORBIDDEN", "该分享未开放审批"
            )
        session = _linked_session_for_write(state_raw, target, asset_id)
        share_id = target["share_id"]
        items = [
            v
            for v in (state_raw.get("approvals") or {}).values()
            if v.get("share_id") == share_id
        ]
        approval_id = "apr-%s-%04d" % (share_id, len(items) + 1)
        item = {
            "approval_id": approval_id,
            "share_id": share_id,
            "session_id": session.get("session_id") if session else None,
            "asset_id": asset_id,
            "status": status,
            "note": note,
            "author_name": guest_name,
            "created_at": storage.now_iso(),
            "created_at_epoch": _now_epoch(),
        }
        state_raw.setdefault("approvals", {})[approval_id] = item
        if session:
            # 只记录访客结论及版本，不替私有会话状态机推进 approved/rejected。
            session["updated_at"] = item["created_at"]
            session["version"] = int(session.get("version") or 1) + 1
        _bump(state_raw)
        return {"approval": dict(item)}

    return state().mutate(mutate)


_PUBLIC_MEDIA_TYPES = {
    ".png": ("image/png", True),
    ".jpg": ("image/jpeg", True),
    ".jpeg": ("image/jpeg", True),
    ".gif": ("image/gif", True),
    ".webp": ("image/webp", True),
    ".bmp": ("image/bmp", True),
    ".mp4": ("video/mp4", True),
    ".webm": ("video/webm", True),
    ".mov": ("video/quicktime", True),
    ".mp3": ("audio/mpeg", True),
    ".wav": ("audio/wav", True),
    ".ogg": ("audio/ogg", True),
    ".m4a": ("audio/mp4", True),
    # PDF 等非预览主动内容只允许有下载权限时作为 attachment 返回。
    ".pdf": ("application/pdf", False),
    ".txt": ("text/plain; charset=utf-8", False),
}


def public_media(
    token: str,
    asset_id: str,
    ticket: str | None,
    download: bool = False,
    *,
    rate_checked: bool = False,
):
    """验证分享票据并返回注册表准入的本地媒体路径与安全响应元数据。"""
    if not rate_checked:
        _enforce_rate(token, "media")
    from gw.asset_registry import repository as asset_repo

    def read_authorization(raw: dict[str, Any]):
        record = _find_share(raw, token)
        _ensure_not_expired(record)
        canonical_asset_id = _share_asset(record, asset_id)
        _require_ticket(record, ticket)
        try:
            asset = asset_repo.get_asset(canonical_asset_id)
        except CleanroomException:
            raise CleanroomException(404, "SHARE_ASSET_NOT_FOUND", "该素材不属于此分享")
        except Exception:
            raise CleanroomException(
                503, "SHARE_ASSETS_UNAVAILABLE", "素材注册表暂时不可用"
            )
        path = asset_repo.media_file(canonical_asset_id)
        if path is None or not path.is_file():
            raise CleanroomException(404, "SHARE_MEDIA_NOT_FOUND", "分享媒体不可用")
        media_type, inline_allowed = _PUBLIC_MEDIA_TYPES.get(
            path.suffix.lower(), (None, False)
        )
        if media_type is None:
            raise CleanroomException(
                415, "SHARE_MEDIA_TYPE_NOT_ALLOWED", "该媒体类型不允许通过公开分享读取"
            )
        if download and not record.get("can_download"):
            raise CleanroomException(
                403, "SHARE_DOWNLOAD_FORBIDDEN", "该分享未开放下载"
            )
        if not inline_allowed and not record.get("can_download"):
            raise CleanroomException(
                403, "SHARE_DOWNLOAD_FORBIDDEN", "该文件只能通过授权下载"
            )
        if not inline_allowed and not download:
            raise CleanroomException(
                415, "SHARE_MEDIA_INLINE_FORBIDDEN", "该媒体类型只允许以附件方式下载"
            )
        filename = str(asset.get("name") or path.name)
        filename = (
            filename.replace("\\", "_")
            .replace("/", "_")
            .replace("\r", "_")
            .replace("\n", "_")[:180]
        )
        if not filename:
            filename = path.name
        return (
            path,
            media_type,
            filename,
            ("inline" if inline_allowed and not download else "attachment"),
        )

    # 使用当前快照授权，票据、到期和分享归属均在一个命名空间锁内重新读取。
    return state().mutate(read_authorization)
