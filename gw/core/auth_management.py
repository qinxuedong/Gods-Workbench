# -*- coding: utf-8 -*-
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

"""B1 认证管理与持久团队消息边界。

团队身份引用、团队/成员/bootstrap 状态与消息幂等记录由同一个 SQLite 文件承载；审批与临时令牌仍是本进程状态。部署边界为单实例、单应用进程。
所有身份匹配必须是受信身份域与 subject 的精确匹配，不使用创建者字段冒认成员。
"""
from __future__ import annotations

import base64
import hashlib
import os
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Iterable, Optional

from gw.core import audit as audit_log
from gw.core.auth import GOVERNANCE_ROLES, KNOWN_ROLES
from gw.core.errors import CleanroomException
from gw.core.team_messages import TeamMessageRepository, TeamMessageStorageUnavailable


BOOTSTRAP_ENABLED_ENV = "GW_AUTH_BOOTSTRAP_ENABLED"
BOOTSTRAP_WINDOW_ENV = "GW_AUTH_BOOTSTRAP_WINDOW"
BOOTSTRAP_UNTIL_ENV = "GW_AUTH_BOOTSTRAP_UNTIL"
_TRUE_VALUES = frozenset({"1", "true", "yes", "on", "open", "enabled"})


class AuthManagementNotFound(CleanroomException):
    def __init__(self, message: str = "目标认证资源不存在"):
        super().__init__(404, "AUTH_RESOURCE_NOT_FOUND", message)


class AuthManagementConflict(CleanroomException):
    def __init__(self, expected: int, current: int, message: str = "认证资源版本冲突，请刷新后重试"):
        super().__init__(409, "AUTH_VERSION_CONFLICT", message, {"expected_version": expected, "current_version": current})


class AuthManagementStateConflict(CleanroomException):
    def __init__(self, message: str = "认证资源状态冲突"):
        super().__init__(409, "AUTH_STATE_CONFLICT", message)


class AuthManagementInvalidCursor(CleanroomException):
    def __init__(self):
        super().__init__(422, "INVALID_CURSOR", "分页游标无效")


class AuthBootstrapUnavailable(CleanroomException):
    def __init__(self):
        super().__init__(403, "AUTH_BOOTSTRAP_DISABLED", "认证初始化窗口未显式开启")


def _parse_window_deadline(raw: str) -> Optional[float]:
    """解析初始化窗口截止时间；格式错误按失败关闭处理。"""
    value = raw.strip()
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        pass
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except ValueError:
        return 0.0


def bootstrap_window_open(now: Optional[float] = None) -> bool:
    """判断是否处于显式初始化窗口。

    默认关闭；只有 ``GW_AUTH_BOOTSTRAP_ENABLED`` 或兼容的
    ``GW_AUTH_BOOTSTRAP_WINDOW`` 被明确设置为真值才打开。若配置了截止时间，
    截止时间必须可解析且尚未到期，否则失败关闭。
    """
    enabled = (
        os.getenv(BOOTSTRAP_ENABLED_ENV, "").strip().lower() in _TRUE_VALUES
        or os.getenv(BOOTSTRAP_WINDOW_ENV, "").strip().lower() in _TRUE_VALUES
    )
    if not enabled:
        return False
    deadline_raw = os.getenv(BOOTSTRAP_UNTIL_ENV)
    if deadline_raw:
        deadline = _parse_window_deadline(deadline_raw)
        if deadline is None or deadline <= (now if now is not None else __import__("time").time()):
            return False
    return True


@dataclass
class _User:
    user_id: str
    display_name: str
    role: str
    version: int = 1
    external_subject: str | None = None
    owner_subject: str | None = None
    identity_domain: str | None = None
    owner_domain: str | None = None

    def public(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "display_name": self.display_name,
            "role": self.role,
            "version": self.version,
            # 保持现有本地契约字段；OIDC 路由不会采用客户端传入值。
            "external_subject": self.external_subject,
        }


@dataclass
class _Team:
    team_id: str
    name: str
    description: str | None = None
    version: int = 1
    members: dict[str, str] = field(default_factory=dict)
    owner_subject: str | None = None
    owner_user_id: str | None = None
    owner_domain: str | None = None

    def public(self) -> dict[str, Any]:
        return {
            "team_id": self.team_id,
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "member_count": len(self.members),
        }


@dataclass
class _Approval:
    approval_id: str
    status: str = "pending"
    version: int = 1
    reason: str | None = None
    owner_subject: str | None = None
    owner_domain: str | None = None
    team_id: str | None = None

    def public(self) -> dict[str, Any]:
        return {
            "approval_id": self.approval_id,
            "status": self.status,
            "version": self.version,
            "reason": self.reason,
        }


@dataclass
class _Token:
    token_id: str
    name: str
    scopes: list[str]
    token_digest: str
    expires_at: str | None
    owner_subject: str | None = None

    def public(self) -> dict[str, Any]:
        return {
            "token_id": self.token_id,
            "name": self.name,
            "scopes": list(self.scopes),
            "expires_at": self.expires_at,
        }


class AuthManagementStore:
    """B1 治理状态与消息存储；团队权限检查和消息追加共享锁及 SQLite。"""

    def __init__(self) -> None:
        self._lock = RLock()
        self.repository = TeamMessageRepository()
        self._loaded_path: Path | None = None
        self.users: dict[str, _User] = {}
        self.teams: dict[str, _Team] = {}
        self.approvals: dict[str, _Approval] = {}
        self.tokens: dict[str, _Token] = {}
        self.bootstrapped = False

    def reset_for_tests(self) -> None:
        """仅测试可显式调用；生产构造与启动路径绝不重置持久状态。"""
        with self._lock:
            self.repository.reset_for_tests()
            self._loaded_path = None
            self.users = {}
            self.teams = {}
            self.approvals = {}
            self.tokens = {}
            self.bootstrapped = False

    def _ensure_loaded(self) -> None:
        path = self.repository.database_path().resolve()
        if self._loaded_path == path:
            return
        state = self.repository.load_governance_state()
        self._hydrate_state(state)
        self._loaded_path = path

    def _hydrate_state(self, state: dict[str, Any]) -> None:
        users: dict[str, _User] = {}
        for value in state["users"]:
            user = _User(
                user_id=value["user_id"],
                display_name=value["display_name"],
                role=value["role"],
                version=value["version"],
                external_subject=value["external_subject"],
                owner_subject=value["owner_subject"],
                identity_domain=value["identity_domain"],
                owner_domain=value["owner_domain"],
            )
            users[user.user_id] = user
        teams: dict[str, _Team] = {}
        for value in state["teams"]:
            team = _Team(
                team_id=value["team_id"],
                name=value["name"],
                description=value["description"],
                version=value["version"],
                members=dict(state["members"].get(value["team_id"], {})),
                owner_user_id=value["owner_user_id"],
            )
            teams[team.team_id] = team
        self.users = users
        self.teams = teams
        self.bootstrapped = bool(state["bootstrapped"])

    @staticmethod
    def _user_record(user: _User) -> dict[str, Any]:
        return {
            "user_id": user.user_id,
            "display_name": user.display_name,
            "role": user.role,
            "version": user.version,
            "external_subject": user.external_subject,
            "identity_domain": user.identity_domain,
            "owner_subject": user.owner_subject,
            "owner_domain": user.owner_domain,
        }

    @staticmethod
    def _team_record(team: _Team) -> dict[str, Any]:
        return {
            "team_id": team.team_id,
            "name": team.name,
            "description": team.description,
            "version": team.version,
            "owner_user_id": team.owner_user_id,
            "members": dict(team.members),
        }

    def _persist_core_state(self) -> None:
        self.bootstrapped = self.bootstrapped or bool(self.users)
        try:
            self.repository.save_governance_state(
                (self._user_record(user) for user in self.users.values()),
                (self._team_record(team) for team in self.teams.values()),
                self.bootstrapped,
            )
        except Exception as exc:
            # SQLite 事务失败时恢复已提交快照；不能让内存先成功、磁盘失败。
            try:
                state = self.repository.load_governance_state()
                self._hydrate_state(state)
                self._loaded_path = self.repository.database_path().resolve()
            except Exception:
                self.users = {}
                self.teams = {}
                self.bootstrapped = True
                self._loaded_path = None
            if isinstance(exc, TeamMessageStorageUnavailable):
                raise
            raise TeamMessageStorageUnavailable() from exc

    @staticmethod
    def _new_id(prefix: str) -> str:
        return f"{prefix}-{secrets.token_hex(8)}"

    @staticmethod
    def _check_role(role: str) -> str:
        value = str(role or "").strip().lower()
        if value not in KNOWN_ROLES:
            raise CleanroomException(422, "INVALID_ROLE", "用户角色不合法")
        return value

    @staticmethod
    def _check_decision(decision: str) -> str:
        value = str(decision or "").strip().lower()
        if value not in {"approved", "rejected", "cancelled"}:
            raise CleanroomException(422, "INVALID_DECISION", "审批决定不合法")
        return value

    @staticmethod
    def _cas(expected: int, current: int) -> None:
        if expected != current:
            raise AuthManagementConflict(expected, current)

    @staticmethod
    def _subject_user(users: Iterable[_User], subject: str | None, identity_domain: str | None = None) -> Optional[_User]:
        """只按受信身份域和 subject 的唯一绑定查找，不使用 owner_subject 回退。"""
        if not subject or not identity_domain:
            return None
        matches = [u for u in users if u.identity_domain == identity_domain and u.external_subject == subject]
        return matches[0] if len(matches) == 1 else None

    @staticmethod
    def _is_governor(actor_role: str | None) -> bool:
        return str(actor_role or "").strip().lower() in GOVERNANCE_ROLES

    @staticmethod
    def _audit(
        event: str,
        *,
        actor_subject: str | None,
        actor_role: str | None,
        resource_id: str | None,
        auth_mode: str | None,
        reason: str = "",
    ) -> None:
        audit_log.record_management_event(
            event,
            outcome=audit_log.OUTCOME_SUCCEEDED,
            subject=actor_subject,
            role=actor_role,
            resource_id=resource_id,
            auth_mode=auth_mode,
            reason=reason,
        )

    def _visible_team(self, team: _Team, actor_subject: str | None, actor_role: str | None, identity_domain: str | None = None) -> bool:
        actor = self._subject_user(self.users.values(), actor_subject, identity_domain)
        if self._is_governor(actor_role):
            return True
        if actor and team.owner_user_id == actor.user_id:
            return True
        return bool(actor and actor.user_id in team.members)

    def _visible_user(self, user: _User, actor_subject: str | None, actor_role: str | None, identity_domain: str | None = None) -> bool:
        if self._is_governor(actor_role):
            return True
        actor = self._subject_user(self.users.values(), actor_subject, identity_domain)
        if actor and (user.user_id == actor.user_id or (user.owner_domain == identity_domain and user.owner_subject == actor_subject)):
            return True
        if not actor:
            return False
        return any(actor.user_id in team.members and user.user_id in team.members for team in self.teams.values())

    def bootstrap(
        self,
        display_name: str,
        role: str,
        *,
        owner_subject: str | None = None,
        identity_domain: str | None = None,
        identity_subject: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            self._ensure_loaded()
            if not bootstrap_window_open():
                raise AuthBootstrapUnavailable()
            if self.bootstrapped or self.users:
                raise AuthManagementStateConflict("认证初始化已完成，不得重复初始化")
            role = self._check_role(role)
            user = _User(
                self._new_id("usr"),
                display_name.strip(),
                role,
                external_subject=identity_subject,
                owner_subject=identity_subject or owner_subject,
                identity_domain=identity_domain if identity_subject else None,
                owner_domain=identity_domain if identity_subject else None,
            )
            self.users[user.user_id] = user
            self.bootstrapped = True
            self._persist_core_state()
            self._audit(
                audit_log.EVENT_BOOTSTRAP_COMPLETED,
                actor_subject=identity_subject or owner_subject,
                actor_role=role,
                resource_id=user.user_id,
                auth_mode="bootstrap",
            )
            return user.public()

    def list_users(self, actor_subject: str | None = None, actor_role: str | None = None, identity_domain: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            self._ensure_loaded()
            return [u.public() for u in self.users.values() if self._visible_user(u, actor_subject, actor_role, identity_domain)]

    def create_user(
        self,
        display_name: str,
        role: str,
        external_subject: str | None,
        *,
        actor_subject: str | None = None,
        actor_role: str | None = None,
        auth_mode: str | None = None,
        actor_domain: str | None = None,
        trusted_role: str | None = None,
        trusted_external_subject: str | None = None,
        trusted_identity_domain: str | None = None,
        allow_client_identity: bool = True,
    ) -> dict[str, Any]:
        with self._lock:
            self._ensure_loaded()
            if not allow_client_identity:
                role = self._check_role(trusted_role or "readonly")
                external_subject = trusted_external_subject
            else:
                role = self._check_role(trusted_role if trusted_role is not None else role)
                external_subject = trusted_external_subject if trusted_external_subject is not None else external_subject
            if trusted_external_subject and any(
                u.external_subject == trusted_external_subject and u.identity_domain == trusted_identity_domain
                for u in self.users.values()
            ):
                raise AuthManagementStateConflict("外部身份已绑定其他用户")
            user = _User(
                self._new_id("usr"),
                display_name.strip(),
                role,
                external_subject=external_subject,
                owner_subject=actor_subject,
                identity_domain=trusted_identity_domain if trusted_external_subject else None,
                owner_domain=actor_domain,
            )
            self.users[user.user_id] = user
            self._persist_core_state()
            self._audit(
                audit_log.EVENT_USER_CREATED,
                actor_subject=actor_subject,
                actor_role=actor_role,
                resource_id=user.user_id,
                auth_mode=auth_mode,
            )
            return user.public()


    def update_user(
        self,
        user_id: str,
        display_name: str | None,
        role: str | None,
        expected: int,
        *,
        actor_subject: str | None = None,
        actor_role: str | None = None,
        auth_mode: str | None = None,
        allow_role_update: bool = True,
    ) -> dict[str, Any]:
        with self._lock:
            self._ensure_loaded()
            user = self.users.get(user_id)
            if not user:
                raise AuthManagementNotFound("用户不存在")
            self._cas(expected, user.version)
            if display_name is not None:
                user.display_name = display_name.strip()
            if allow_role_update and role is not None:
                user.role = self._check_role(role)
            user.version += 1
            self._persist_core_state()
            self._audit(
                audit_log.EVENT_USER_UPDATED,
                actor_subject=actor_subject,
                actor_role=actor_role,
                resource_id=user.user_id,
                auth_mode=auth_mode,
            )
            return user.public()


    def delete_user(
        self,
        user_id: str,
        expected: int,
        *,
        actor_subject: str | None = None,
        actor_role: str | None = None,
        auth_mode: str | None = None,
    ) -> None:
        with self._lock:
            self._ensure_loaded()
            user = self.users.get(user_id)
            if not user:
                raise AuthManagementNotFound("用户不存在")
            self._cas(expected, user.version)
            if user.role in {"admin", "governor"} and sum(u.role in {"admin", "governor"} for u in self.users.values()) <= 1:
                raise AuthManagementStateConflict("不得删除最后一个治理用户")
            self.users.pop(user_id)
            for team in self.teams.values():
                if user_id in team.members:
                    team.members.pop(user_id, None)
                    team.version += 1
                if team.owner_user_id == user_id:
                    team.owner_user_id = None
            self._persist_core_state()
            self._audit(
                audit_log.EVENT_USER_DELETED,
                actor_subject=actor_subject,
                actor_role=actor_role,
                resource_id=user_id,
                auth_mode=auth_mode,
            )


    def list_teams(self, actor_subject: str | None = None, actor_role: str | None = None, identity_domain: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            self._ensure_loaded()
            return [t.public() for t in self.teams.values() if self._visible_team(t, actor_subject, actor_role, identity_domain)]


    def create_team(
        self,
        name: str,
        description: str | None,
        *,
        actor_subject: str | None = None,
        actor_role: str | None = None,
        auth_mode: str | None = None,
        actor_domain: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            self._ensure_loaded()
            if any(t.name == name.strip() for t in self.teams.values()):
                raise AuthManagementStateConflict("团队名称已存在")
            owner = self._subject_user(self.users.values(), actor_subject, actor_domain)
            team = _Team(
                self._new_id("team"), name.strip(), description,
                owner_subject=actor_subject,
                owner_user_id=owner.user_id if owner else None,
                owner_domain=actor_domain if owner else None,
            )
            if owner:
                team.members[owner.user_id] = "admin"
            self.teams[team.team_id] = team
            self._persist_core_state()
            self._audit(
                audit_log.EVENT_TEAM_CREATED,
                actor_subject=actor_subject,
                actor_role=actor_role,
                resource_id=team.team_id,
                auth_mode=auth_mode,
            )
            return team.public()


    def delete_team(
        self,
        team_id: str,
        expected: int,
        *,
        actor_subject: str | None = None,
        actor_role: str | None = None,
        auth_mode: str | None = None,
    ) -> None:
        with self._lock:
            self._ensure_loaded()
            team = self.teams.get(team_id)
            if not team:
                raise AuthManagementNotFound("团队不存在")
            self._cas(expected, team.version)
            if team.members:
                raise AuthManagementStateConflict("团队仍有成员，不能直接删除")
            self.teams.pop(team_id)
            self._persist_core_state()
            self._audit(
                audit_log.EVENT_TEAM_DELETED,
                actor_subject=actor_subject,
                actor_role=actor_role,
                resource_id=team_id,
                auth_mode=auth_mode,
            )

    def update_member(
        self,
        team_id: str,
        user_id: str,
        role: str,
        expected: int,
        *,
        actor_subject: str | None = None,
        actor_role: str | None = None,
        auth_mode: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            self._ensure_loaded()
            team = self.teams.get(team_id)
            if not team:
                raise AuthManagementNotFound("团队不存在")
            if user_id not in self.users:
                raise AuthManagementNotFound("用户不存在")
            role = self._check_role(role)
            current_role = team.members.get(user_id)
            # 相同角色的重复请求不增加版本，允许使用首次请求的旧版本重放。
            if current_role == role and expected in {team.version, team.version - 1}:
                return {"team_id": team_id, "user_id": user_id, "role": role, "version": team.version}
            self._cas(expected, team.version)
            team.members[user_id] = role
            team.version += 1
            self._persist_core_state()
            self._audit(
                audit_log.EVENT_MEMBER_UPDATED,
                actor_subject=actor_subject,
                actor_role=actor_role,
                resource_id=f"{team_id}:{user_id}",
                auth_mode=auth_mode,
            )
            return {"team_id": team_id, "user_id": user_id, "role": role, "version": team.version}

    def delete_member(
        self,
        team_id: str,
        user_id: str,
        expected: int,
        *,
        actor_subject: str | None = None,
        actor_role: str | None = None,
        auth_mode: str | None = None,
    ) -> None:
        with self._lock:
            self._ensure_loaded()
            team = self.teams.get(team_id)
            if not team:
                raise AuthManagementNotFound("团队不存在")
            if user_id not in team.members:
                raise AuthManagementNotFound("团队成员不存在")
            # 团队创建者是唯一可识别的 owner；未建立替代 owner 前不得删除，
            # 避免团队进入无 owner 状态。
            if team.owner_user_id == user_id:
                raise AuthManagementStateConflict("不得删除团队最后一个 owner")
            self._cas(expected, team.version)
            team.members.pop(user_id)
            team.version += 1
            self._persist_core_state()
            self._audit(
                audit_log.EVENT_MEMBER_DELETED,
                actor_subject=actor_subject,
                actor_role=actor_role,
                resource_id=f"{team_id}:{user_id}",
                auth_mode=auth_mode,
            )

    def get_identity_binding(self, identity_domain: str | None, identity_subject: str | None) -> dict[str, Any]:
        """仅查询当前认证主体自己的治理身份绑定状态。"""
        with self._lock:
            self._ensure_loaded()
            user = self._subject_user(self.users.values(), identity_subject, identity_domain)
            return {"bound": user is not None, "user_id": user.user_id if user else None}

    def bind_authenticated_identity(self, context: Any) -> tuple[dict[str, Any], bool]:
        """由可信认证上下文显式绑定身份；请求体不能选择身份或角色。"""
        identity_domain = getattr(context, "identity_domain", None)
        identity_subject = getattr(context, "subject", None)
        if not identity_domain or not identity_subject:
            raise CleanroomException(403, "TEAM_IDENTITY_UNAVAILABLE", "当前认证上下文没有可绑定的唯一身份")
        with self._lock:
            self._ensure_loaded()
            existing = self._subject_user(self.users.values(), identity_subject, identity_domain)
            if existing:
                return existing.public(), False
            if identity_domain == "local_account":
                user_id = identity_subject
                prior = self.users.get(user_id)
                if prior:
                    raise AuthManagementStateConflict("本地账户 user_id 已绑定到不同治理身份")
            else:
                user_id = self._new_id("usr")
            role = self._check_role(getattr(context, "role", ""))
            display_name = str(getattr(context, "display_name", None) or identity_subject).strip()[:120]
            user = _User(
                user_id=user_id,
                display_name=display_name or user_id,
                role=role,
                external_subject=identity_subject,
                owner_subject=identity_subject,
                identity_domain=identity_domain,
                owner_domain=identity_domain,
            )
            self.users[user.user_id] = user
            self._persist_core_state()
            self._audit(
                audit_log.EVENT_USER_CREATED,
                actor_subject=identity_subject,
                actor_role=role,
                resource_id=user.user_id,
                auth_mode=getattr(context, "mode", None),
                reason="authenticated_identity_binding",
            )
            return user.public(), True

    def list_messages(
        self,
        team_id: str,
        *,
        identity_domain: str | None,
        identity_subject: str | None,
        global_role: str,
        limit: int,
        after_sequence: int | None,
        cursor: str | None,
    ) -> dict[str, Any]:
        with self._lock:
            self._ensure_loaded()
            return self.repository.list_messages(
                team_id, identity_domain=identity_domain, identity_subject=identity_subject,
                global_role=global_role, limit=limit, after_sequence=after_sequence, cursor=cursor,
            )

    def create_message(
        self,
        team_id: str,
        *,
        identity_domain: str | None,
        identity_subject: str | None,
        global_role: str,
        text: str,
        client_request_id: str,
    ) -> tuple[dict[str, Any], bool]:
        with self._lock:
            self._ensure_loaded()
            return self.repository.create_message(
                team_id, identity_domain=identity_domain, identity_subject=identity_subject,
                global_role=global_role, text=text, client_request_id=client_request_id,
            )
    def list_approvals(
        self,
        status_filter: str | None,
        actor_subject: str | None = None,
        actor_role: str | None = None,
        identity_domain: str | None = None,
    ) -> list[dict[str, Any]]:
        with self._lock:
            self._ensure_loaded()
            values = [a for a in self.approvals.values() if not status_filter or a.status == status_filter]
            visible: list[_Approval] = []
            actor = self._subject_user(self.users.values(), actor_subject, identity_domain)
            for approval in values:
                owns_approval = bool(
                    actor_subject and identity_domain
                    and approval.owner_subject == actor_subject
                    and approval.owner_domain == identity_domain
                )
                if self._is_governor(actor_role) or owns_approval:
                    visible.append(approval)
                elif actor and approval.team_id and approval.team_id in self.teams and actor.user_id in self.teams[approval.team_id].members:
                    visible.append(approval)
            return [a.public() for a in sorted(visible, key=lambda item: item.approval_id)]

    @staticmethod
    def _decode_cursor(cursor: str | None) -> int:
        if not cursor:
            return 0
        try:
            raw = base64.urlsafe_b64decode(cursor.encode("ascii") + b"=" * (-len(cursor) % 4)).decode("ascii")
            offset = int(raw)
        except (ValueError, UnicodeError, TypeError, base64.binascii.Error):
            raise AuthManagementInvalidCursor()
        if offset < 0:
            raise AuthManagementInvalidCursor()
        return offset

    @staticmethod
    def _encode_cursor(offset: int) -> str:
        return base64.urlsafe_b64encode(str(offset).encode("ascii")).decode("ascii").rstrip("=")

    def list_approvals_page(
        self,
        status_filter: str | None,
        *,
        limit: int,
        cursor: str | None,
        actor_subject: str | None = None,
        actor_role: str | None = None,
        identity_domain: str | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        values = self.list_approvals(status_filter, actor_subject, actor_role, identity_domain)
        start = self._decode_cursor(cursor)
        if start > len(values):
            raise AuthManagementInvalidCursor()
        page = values[start : start + limit]
        next_cursor = self._encode_cursor(start + limit) if start + limit < len(values) else None
        return page, next_cursor

    def update_approval(
        self,
        approval_id: str,
        decision: str,
        expected: int,
        reason: str | None,
        *,
        actor_subject: str | None = None,
        actor_role: str | None = None,
        auth_mode: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            approval = self.approvals.get(approval_id)
            if not approval:
                raise AuthManagementNotFound("审批不存在")
            decision = self._check_decision(decision)
            self._cas(expected, approval.version)
            if approval.status == decision:
                return approval.public()
            if approval.status != "pending":
                raise AuthManagementStateConflict("审批已完成，不能再次修改")
            approval.status = decision
            approval.reason = reason
            approval.version += 1
            self._audit(
                audit_log.EVENT_APPROVAL_UPDATED,
                actor_subject=actor_subject,
                actor_role=actor_role,
                resource_id=approval_id,
                auth_mode=auth_mode,
                reason=decision,
            )
            return approval.public()

    def create_token(
        self,
        name: str,
        scopes: list[str],
        expires_at: str | None,
        *,
        actor_subject: str | None = None,
        actor_role: str | None = None,
        auth_mode: str | None = None,
    ) -> tuple[dict[str, Any], str]:
        with self._lock:
            token = secrets.token_urlsafe(32)
            record = _Token(
                self._new_id("tok"),
                name.strip(),
                list(scopes),
                hashlib.sha256(token.encode()).hexdigest(),
                expires_at,
                owner_subject=actor_subject,
            )
            self.tokens[record.token_id] = record
            self._audit(
                audit_log.EVENT_TOKEN_CREATED,
                actor_subject=actor_subject,
                actor_role=actor_role,
                resource_id=record.token_id,
                auth_mode=auth_mode,
            )
            public = record.public()
            public["token"] = token
            return public, token


store = AuthManagementStore()
