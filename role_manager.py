from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from config import ADMIN_ID

DB_PATH = Path("roles.db")
VALID_ROLES = {"owner", "admin", "moderator", "analyst"}
ROLE_LEVELS = {"analyst": 10, "moderator": 20, "admin": 30, "owner": 40}


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def create_role_tables() -> None:
    conn = _connect()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS staff_roles (
            user_id INTEGER PRIMARY KEY,
            role TEXT NOT NULL,
            granted_by INTEGER,
            granted_at TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS role_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            actor_id INTEGER NOT NULL,
            target_id INTEGER NOT NULL,
            action TEXT NOT NULL,
            role TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    now = datetime.now(timezone.utc).isoformat()
    cur.execute(
        """
        INSERT INTO staff_roles(user_id, role, granted_by, granted_at, active)
        VALUES (?, 'owner', ?, ?, 1)
        ON CONFLICT(user_id) DO UPDATE SET role='owner', active=1
        """,
        (int(ADMIN_ID), int(ADMIN_ID), now),
    )
    conn.commit()
    conn.close()


def get_role(user_id: int) -> str | None:
    if int(user_id) == int(ADMIN_ID):
        return "owner"
    conn = _connect()
    row = conn.execute(
        "SELECT role FROM staff_roles WHERE user_id=? AND active=1",
        (int(user_id),),
    ).fetchone()
    conn.close()
    return str(row["role"]) if row else None


def has_role(user_id: int, minimum: str = "analyst") -> bool:
    role = get_role(user_id)
    if role is None or minimum not in ROLE_LEVELS:
        return False
    return ROLE_LEVELS.get(role, 0) >= ROLE_LEVELS[minimum]


def set_role(actor_id: int, target_id: int, role: str) -> bool:
    role = role.lower().strip()
    if role not in VALID_ROLES or not has_role(actor_id, "owner"):
        return False
    if int(target_id) == int(ADMIN_ID) and role != "owner":
        return False
    now = datetime.now(timezone.utc).isoformat()
    conn = _connect()
    conn.execute(
        """
        INSERT INTO staff_roles(user_id, role, granted_by, granted_at, active)
        VALUES (?, ?, ?, ?, 1)
        ON CONFLICT(user_id) DO UPDATE SET
            role=excluded.role,
            granted_by=excluded.granted_by,
            granted_at=excluded.granted_at,
            active=1
        """,
        (int(target_id), role, int(actor_id), now),
    )
    conn.execute(
        "INSERT INTO role_audit(actor_id,target_id,action,role,created_at) VALUES(?,?,?,?,?)",
        (int(actor_id), int(target_id), "set", role, now),
    )
    conn.commit()
    conn.close()
    return True


def remove_role(actor_id: int, target_id: int) -> bool:
    if not has_role(actor_id, "owner") or int(target_id) == int(ADMIN_ID):
        return False
    now = datetime.now(timezone.utc).isoformat()
    conn = _connect()
    cur = conn.execute(
        "UPDATE staff_roles SET active=0 WHERE user_id=?",
        (int(target_id),),
    )
    conn.execute(
        "INSERT INTO role_audit(actor_id,target_id,action,role,created_at) VALUES(?,?,?,?,?)",
        (int(actor_id), int(target_id), "remove", None, now),
    )
    conn.commit()
    changed = cur.rowcount > 0
    conn.close()
    return changed


def list_staff() -> list[tuple[int, str, str]]:
    conn = _connect()
    rows = conn.execute(
        "SELECT user_id, role, granted_at FROM staff_roles WHERE active=1 ORDER BY CASE role WHEN 'owner' THEN 1 WHEN 'admin' THEN 2 WHEN 'moderator' THEN 3 ELSE 4 END"
    ).fetchall()
    conn.close()
    return [(int(r["user_id"]), str(r["role"]), str(r["granted_at"])) for r in rows]
