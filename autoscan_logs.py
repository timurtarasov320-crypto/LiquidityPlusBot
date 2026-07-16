import sqlite3
from datetime import datetime, timezone
from typing import Optional

DATABASE_NAME = "autoscan_logs.db"


def connect() -> sqlite3.Connection:
    connection = sqlite3.connect(DATABASE_NAME)
    connection.row_factory = sqlite3.Row
    return connection


def create_autoscan_logs_table() -> None:
    connection = connect()
    cursor = connection.cursor()

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS autoscan_logs (
            log_id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            duration_seconds REAL DEFAULT 0,
            analysed INTEGER DEFAULT 0,
            found INTEGER DEFAULT 0,
            sent INTEGER DEFAULT 0,
            duplicates INTEGER DEFAULT 0,
            minimum_score INTEGER DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'running',
            error_text TEXT
        )
        """
    )

    connection.commit()
    connection.close()


def start_scan_log(
    minimum_score: int,
) -> int:
    connection = connect()
    cursor = connection.cursor()

    started_at = datetime.now(timezone.utc).isoformat()

    cursor.execute(
        """
        INSERT INTO autoscan_logs (
            started_at,
            minimum_score,
            status
        )
        VALUES (?, ?, 'running')
        """,
        (
            started_at,
            int(minimum_score),
        ),
    )

    log_id = int(cursor.lastrowid)

    connection.commit()
    connection.close()

    return log_id


def finish_scan_log(
    log_id: int,
    duration_seconds: float,
    analysed: int,
    found: int,
    sent: int,
    duplicates: int,
) -> None:
    connection = connect()
    cursor = connection.cursor()

    cursor.execute(
        """
        UPDATE autoscan_logs
        SET
            finished_at = ?,
            duration_seconds = ?,
            analysed = ?,
            found = ?,
            sent = ?,
            duplicates = ?,
            status = 'success',
            error_text = NULL
        WHERE log_id = ?
        """,
        (
            datetime.now(timezone.utc).isoformat(),
            float(duration_seconds),
            int(analysed),
            int(found),
            int(sent),
            int(duplicates),
            int(log_id),
        ),
    )

    connection.commit()
    connection.close()


def fail_scan_log(
    log_id: int,
    duration_seconds: float,
    error_text: str,
) -> None:
    connection = connect()
    cursor = connection.cursor()

    cursor.execute(
        """
        UPDATE autoscan_logs
        SET
            finished_at = ?,
            duration_seconds = ?,
            status = 'error',
            error_text = ?
        WHERE log_id = ?
        """,
        (
            datetime.now(timezone.utc).isoformat(),
            float(duration_seconds),
            str(error_text)[:3000],
            int(log_id),
        ),
    )

    connection.commit()
    connection.close()


def get_autoscan_logs(
    limit: int = 20,
) -> list[dict]:
    safe_limit = max(1, min(int(limit), 100))

    connection = connect()
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT
            log_id,
            started_at,
            finished_at,
            duration_seconds,
            analysed,
            found,
            sent,
            duplicates,
            minimum_score,
            status,
            error_text
        FROM autoscan_logs
        ORDER BY log_id DESC
        LIMIT ?
        """,
        (safe_limit,),
    )

    rows = cursor.fetchall()
    connection.close()

    return [dict(row) for row in rows]


def get_autoscan_log(
    log_id: int,
) -> Optional[dict]:
    connection = connect()
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT
            log_id,
            started_at,
            finished_at,
            duration_seconds,
            analysed,
            found,
            sent,
            duplicates,
            minimum_score,
            status,
            error_text
        FROM autoscan_logs
        WHERE log_id = ?
        """,
        (int(log_id),),
    )

    row = cursor.fetchone()
    connection.close()

    return dict(row) if row else None


def get_autoscan_statistics() -> dict:
    connection = connect()
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT
            COUNT(*) AS total_scans,
            SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS successful,
            SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) AS failed,
            COALESCE(SUM(analysed), 0) AS analysed,
            COALESCE(SUM(found), 0) AS found,
            COALESCE(SUM(sent), 0) AS sent,
            COALESCE(SUM(duplicates), 0) AS duplicates,
            COALESCE(AVG(duration_seconds), 0) AS average_duration
        FROM autoscan_logs
        """
    )

    row = cursor.fetchone()

    cursor.execute(
        """
        SELECT
            COUNT(*) AS today_scans,
            COALESCE(SUM(analysed), 0) AS today_analysed,
            COALESCE(SUM(found), 0) AS today_found,
            COALESCE(SUM(sent), 0) AS today_sent,
            COALESCE(SUM(duplicates), 0) AS today_duplicates
        FROM autoscan_logs
        WHERE DATE(started_at) = DATE('now')
        """
    )

    today = cursor.fetchone()
    connection.close()

    return {
        "total_scans": int(row["total_scans"] or 0),
        "successful": int(row["successful"] or 0),
        "failed": int(row["failed"] or 0),
        "analysed": int(row["analysed"] or 0),
        "found": int(row["found"] or 0),
        "sent": int(row["sent"] or 0),
        "duplicates": int(row["duplicates"] or 0),
        "average_duration": float(row["average_duration"] or 0),
        "today_scans": int(today["today_scans"] or 0),
        "today_analysed": int(today["today_analysed"] or 0),
        "today_found": int(today["today_found"] or 0),
        "today_sent": int(today["today_sent"] or 0),
        "today_duplicates": int(today["today_duplicates"] or 0),
    }


def clear_autoscan_logs() -> int:
    connection = connect()
    cursor = connection.cursor()

    cursor.execute("DELETE FROM autoscan_logs")
    deleted = cursor.rowcount

    connection.commit()
    connection.close()

    return deleted


create_autoscan_logs_table()