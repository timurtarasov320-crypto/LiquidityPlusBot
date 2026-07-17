import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DB_PATH = Path(__file__).resolve().with_name('admin_audit.db')


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def create_audit_table() -> None:
    with connect() as conn:
        conn.execute(
            '''
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                actor_id INTEGER,
                action TEXT NOT NULL,
                target_id INTEGER,
                details TEXT
            )
            '''
        )
        conn.execute(
            'CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_log(created_at DESC)'
        )
        conn.commit()


def log_event(action: str, actor_id: int | None = None, target_id: int | None = None, **details: Any) -> None:
    create_audit_table()
    payload = json.dumps(details, ensure_ascii=False, default=str) if details else None
    with connect() as conn:
        conn.execute(
            'INSERT INTO audit_log(created_at, actor_id, action, target_id, details) VALUES (?, ?, ?, ?, ?)',
            (datetime.now(timezone.utc).isoformat(), actor_id, action, target_id, payload),
        )
        conn.commit()


def get_recent_events(limit: int = 20):
    create_audit_table()
    with connect() as conn:
        return conn.execute(
            'SELECT id, created_at, actor_id, action, target_id, details FROM audit_log ORDER BY id DESC LIMIT ?',
            (max(1, min(int(limit), 100)),),
        ).fetchall()
