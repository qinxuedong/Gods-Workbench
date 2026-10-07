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

"""本地持久化审计收据；不代表外部投递、签名或防管理员篡改。"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from gw.core import storage
from gw.core.errors import CleanroomException

NAMESPACE = "asset_audit_receipts"
EVENT_TYPES = {"asset.deleted", "asset.recycle_restored", "asset.unarchived"}
EVENT_FIELDS = {"event_id", "schema_version", "event_type", "asset_id", "occurred_at", "actor_key"}


def state():
    return storage.JsonState(NAMESPACE, lambda: {"schema_version": 1, "receipts": {}})


def digest_event(event: dict[str, Any]) -> str:
    """只接纳有限、不可变事件；不持久化请求正文或用户凭据。"""
    if (not isinstance(event, dict) or set(event) != EVENT_FIELDS
            or event.get("schema_version") != 1 or event.get("event_type") not in EVENT_TYPES
            or not re.fullmatch(r"evt_\d{4,}", str(event.get("event_id", "")))
            or not re.fullmatch(r"[a-f0-9]{64}|unknown", str(event.get("actor_key", "")))
            or any(not isinstance(event.get(key), str) or not 1 <= len(event[key]) <= 160
                   for key in ("asset_id", "occurred_at"))):
        raise CleanroomException(409, "AUDIT_EVENT_INVALID", "审计事件结构或字段无效")
    return hashlib.sha256(json.dumps(event, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":")).encode("utf-8")).hexdigest()


def actor_key(context) -> str:
    """主体只保留不可逆摘要；组件调用无上下文时明确标为 unknown。"""
    if context is None:
        return "unknown"
    domain, subject = getattr(context, "identity_domain", ""), getattr(context, "subject", "")
    if not domain or not subject:
        raise CleanroomException(401, "IDENTITY_REQUIRED", "审计操作缺少稳定身份")
    return hashlib.sha256((domain + "\0" + subject).encode("utf-8")).hexdigest()


def append_event(raw, event_type: str, asset_id: str, context=None):
    """由源操作在同一注册表事务中调用；失败操作不留下成功事件。"""
    outbox = raw.setdefault("outbox", {})
    event_id = storage.next_sequence("evt_", iter(outbox))
    event = {"event_id": event_id, "schema_version": 1, "event_type": event_type,
             "asset_id": asset_id, "occurred_at": storage.now_iso(), "actor_key": actor_key(context)}
    outbox[event_id] = {"event": event, "digest": digest_event(event), "state": "pending", "attempts": 0}
    return event_id


def deliver(event, digest):
    """幂等写入后重新读取收据，只有 ID 和摘要一致才返回 ACK。"""
    expected = digest_event(event)
    if digest != expected:
        raise CleanroomException(409, "AUDIT_DIGEST_CONFLICT", "审计事件摘要不一致")
    event_id = event["event_id"]

    def mutate(raw):
        receipts = raw["receipts"]
        existing = receipts.get(event_id)
        if existing:
            if existing.get("digest") != expected or existing.get("event") != event:
                raise CleanroomException(409, "AUDIT_RECEIPT_CONFLICT", "同一审计事件已有不同收据")
            return
        receipts[event_id] = {"event_id": event_id, "digest": expected,
                              "event": dict(event), "received_at": storage.now_iso()}

    state().mutate(mutate)
    receipt = state().read()["receipts"].get(event_id)
    if not receipt or receipt.get("digest") != expected or receipt.get("event") != event:
        raise CleanroomException(503, "AUDIT_ACK_UNVERIFIED", "审计收据读回未验证")
    return {"event_id": event_id, "digest": expected, "received_at": receipt["received_at"]}


def overview():
    """治理概览最多返回最近50个脱敏收据，不返回原始主体或请求。"""
    receipts = list(state().read()["receipts"].values())
    receipts.sort(key=lambda item: (item.get("received_at", ""), item["event_id"]), reverse=True)
    return {"scope": "local_persistent_receipt", "count": len(receipts), "limit": 50,
            "items": [{"event_id": item["event_id"], "digest": item["digest"],
                       "received_at": item["received_at"], "event_type": item["event"]["event_type"],
                       "asset_id": item["event"]["asset_id"]} for item in receipts[:50]]}
