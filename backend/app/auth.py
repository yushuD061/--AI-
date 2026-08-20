from __future__ import annotations

import contextvars
import hashlib
import hmac
import json
import os
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException

from . import state


ROLES = {"admin", "regional_manager", "store_manager", "readonly"}
PERMISSIONS = {
    "admin": {"view", "ai", "report_create", "report_export", "config", "conversation_delete", "audit"},
    "regional_manager": {"view", "ai", "report_create", "report_export", "conversation_delete"},
    "store_manager": {"view", "ai", "report_create", "report_export", "conversation_delete"},
    "readonly": {"view"},
}


@dataclass(frozen=True)
class Principal:
    user_id: str
    username: str
    display_name: str
    role: str
    store_ids: tuple[str, ...] | None

    def public(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "username": self.username,
            "display_name": self.display_name,
            "role": self.role,
            "store_ids": list(self.store_ids) if self.store_ids is not None else None,
            "permissions": sorted(PERMISSIONS[self.role]),
        }


_principal: contextvars.ContextVar[Principal | None] = contextvars.ContextVar("principal", default=None)
_request_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)


def set_context(principal: Principal, request_id: str) -> tuple[contextvars.Token, contextvars.Token]:
    return _principal.set(principal), _request_id.set(request_id)


def reset_context(tokens: tuple[contextvars.Token, contextvars.Token]) -> None:
    _principal.reset(tokens[0]); _request_id.reset(tokens[1])


def current_user() -> Principal:
    principal = _principal.get()
    if principal is None:
        raise HTTPException(status_code=401, detail="需要登录")
    return principal


def state_user_id() -> str:
    principal = _principal.get()
    return principal.user_id if principal else "legacy"


def request_id() -> str:
    return _request_id.get() or f"req_{uuid.uuid4().hex}"


def require(permission: str) -> Principal:
    principal = current_user()
    if permission not in PERMISSIONS[principal.role]:
        raise HTTPException(status_code=403, detail="当前角色无权执行此操作")
    return principal


def ensure_store(store_id: str | None) -> Principal:
    principal = current_user()
    if store_id and principal.store_ids is not None and store_id not in principal.store_ids:
        raise HTTPException(status_code=403, detail="门店不在当前用户授权范围内")
    return principal


def password_hash(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 200_000)
    return f"pbkdf2_sha256$200000${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        _, rounds, salt, expected = encoded.split("$", 3)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), int(rounds))
        return hmac.compare_digest(actual.hex(), expected)
    except (ValueError, TypeError):
        return False


def seed_users() -> None:
    db = state.connect()
    try:
        if db.execute("SELECT 1 FROM users LIMIT 1").fetchone():
            return
        defaults = [
            ("u_admin", "admin", "系统管理员", "admin", os.getenv("MONEKI_ADMIN_PASSWORD", "admin-demo"), []),
            ("u_region", "region", "区域经理", "regional_manager", os.getenv("MONEKI_REGION_PASSWORD", "region-demo"), ["S01", "S02", "S03"]),
            ("u_manager", "manager", "门店店长", "store_manager", os.getenv("MONEKI_MANAGER_PASSWORD", "manager-demo"), ["S01"]),
            ("u_readonly", "readonly", "只读访客", "readonly", os.getenv("MONEKI_READONLY_PASSWORD", "readonly-demo"), ["S01", "S02"]),
        ]
        for user_id, username, name, role, password, stores in defaults:
            db.execute("INSERT INTO users VALUES (?, ?, ?, ?, ?, 1, ?)",
                       (user_id, username, password_hash(password), role, name, datetime.now(timezone.utc).isoformat()))
            db.executemany("INSERT INTO user_store_scope VALUES (?, ?)", [(user_id, store) for store in stores])
        db.commit()
    finally:
        db.close()


def _principal_from_row(db, row) -> Principal:
    stores = tuple(item[0] for item in db.execute(
        "SELECT store_id FROM user_store_scope WHERE user_id = ? ORDER BY store_id", (row["user_id"],)))
    return Principal(row["user_id"], row["username"], row["display_name"], row["role"], None if row["role"] == "admin" else stores)


def login(username: str, password: str) -> tuple[str, Principal]:
    seed_users(); db = state.connect()
    try:
        row = db.execute("SELECT * FROM users WHERE username = ? AND active = 1", (username,)).fetchone()
        if row is None or not verify_password(password, row["password_hash"]):
            raise HTTPException(status_code=401, detail="用户名或密码错误")
        token = secrets.token_urlsafe(32); now = datetime.now(timezone.utc)
        db.execute("INSERT INTO auth_sessions VALUES (?, ?, ?, ?, NULL)",
                   (hashlib.sha256(token.encode()).hexdigest(), row["user_id"], now.isoformat(), (now + timedelta(hours=12)).isoformat()))
        db.commit(); return token, _principal_from_row(db, row)
    finally:
        db.close()


def authenticate(token: str) -> Principal | None:
    if not token:
        return None
    db = state.connect()
    try:
        row = db.execute("""SELECT u.* FROM auth_sessions s JOIN users u ON u.user_id=s.user_id
          WHERE s.token_hash=? AND s.revoked_at IS NULL AND s.expires_at>? AND u.active=1""",
          (hashlib.sha256(token.encode()).hexdigest(), datetime.now(timezone.utc).isoformat())).fetchone()
        return _principal_from_row(db, row) if row else None
    finally:
        db.close()


def logout(token: str) -> None:
    db = state.connect()
    try:
        db.execute("UPDATE auth_sessions SET revoked_at=? WHERE token_hash=?",
                   (datetime.now(timezone.utc).isoformat(), hashlib.sha256(token.encode()).hexdigest()))
        db.commit()
    finally:
        db.close()


def audit(action: str, resource: str, filters: dict[str, Any] | None = None, result: str = "success",
          principal: Principal | None = None) -> None:
    user = principal or _principal.get()
    safe_filters = filters or {}
    state.record_audit({
        "event_id": f"evt_{uuid.uuid4().hex}", "user_id": user.user_id if user else None,
        "role": user.role if user else "anonymous", "action": action, "resource": resource,
        "filters": safe_filters, "request_id": request_id(),
        "created_at": datetime.now(timezone.utc).isoformat(), "result": result,
    })


def report_allowed(report: dict[str, Any]) -> bool:
    principal = current_user()
    if principal.store_ids is None:
        return True
    scope = report.get("scope_store_ids")
    if scope is None:
        scope = [] if report.get("store_id") == "ALL" else [report.get("store_id")]
    return bool(scope) and set(scope).issubset(principal.store_ids)
