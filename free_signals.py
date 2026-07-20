import sqlite3

from project_paths import data_path
from datetime import datetime, timezone
from typing import Optional

FREE_SIGNALS_LIMIT = 10
DATABASE_NAME = data_path("free_signals.db")


def connect() -> sqlite3.Connection:
    connection = sqlite3.connect(DATABASE_NAME)
    connection.row_factory = sqlite3.Row
    return connection


def create_free_signals_tables() -> None:
    connection = connect()
    cursor = connection.cursor()

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS free_signal_usage (
            user_id INTEGER NOT NULL,
            period TEXT NOT NULL,
            signals_received INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (user_id, period)
        )
        """
    )

    connection.commit()
    connection.close()


def get_current_period() -> str:
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m")


def get_used_free_signals(user_id: int) -> int:
    period = get_current_period()

    connection = connect()
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT signals_received
        FROM free_signal_usage
        WHERE user_id = ? AND period = ?
        """,
        (user_id, period),
    )

    result = cursor.fetchone()
    connection.close()

    if result is None:
        return 0

    return int(result["signals_received"] or 0)


def get_remaining_free_signals(user_id: int) -> int:
    used = get_used_free_signals(user_id)

    return max(
        0,
        FREE_SIGNALS_LIMIT - used,
    )


def can_receive_free_signal(user_id: int) -> bool:
    return get_remaining_free_signals(user_id) > 0


def register_free_signal(user_id: int) -> bool:
    period = get_current_period()
    now = datetime.now(timezone.utc).isoformat()

    connection = connect()
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT signals_received
        FROM free_signal_usage
        WHERE user_id = ? AND period = ?
        """,
        (user_id, period),
    )

    result = cursor.fetchone()

    if result is None:
        cursor.execute(
            """
            INSERT INTO free_signal_usage (
                user_id,
                period,
                signals_received,
                updated_at
            )
            VALUES (?, ?, 1, ?)
            """,
            (
                user_id,
                period,
                now,
            ),
        )

        connection.commit()
        connection.close()
        return True

    used = int(result["signals_received"] or 0)

    if used >= FREE_SIGNALS_LIMIT:
        connection.close()
        return False

    cursor.execute(
        """
        UPDATE free_signal_usage
        SET
            signals_received = signals_received + 1,
            updated_at = ?
        WHERE user_id = ? AND period = ?
        """,
        (
            now,
            user_id,
            period,
        ),
    )

    connection.commit()
    connection.close()

    return True


def reset_user_current_month(user_id: int) -> None:
    period = get_current_period()

    connection = connect()
    cursor = connection.cursor()

    cursor.execute(
        """
        DELETE FROM free_signal_usage
        WHERE user_id = ? AND period = ?
        """,
        (
            user_id,
            period,
        ),
    )

    connection.commit()
    connection.close()


def set_used_free_signals(
    user_id: int,
    amount: int,
) -> None:
    period = get_current_period()
    safe_amount = max(
        0,
        min(int(amount), FREE_SIGNALS_LIMIT),
    )

    now = datetime.now(timezone.utc).isoformat()

    connection = connect()
    cursor = connection.cursor()

    cursor.execute(
        """
        INSERT INTO free_signal_usage (
            user_id,
            period,
            signals_received,
            updated_at
        )
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id, period)
        DO UPDATE SET
            signals_received = excluded.signals_received,
            updated_at = excluded.updated_at
        """,
        (
            user_id,
            period,
            safe_amount,
            now,
        ),
    )

    connection.commit()
    connection.close()


def get_usage_information(
    user_id: int,
) -> dict[str, int | str]:
    used = get_used_free_signals(user_id)
    remaining = max(
        0,
        FREE_SIGNALS_LIMIT - used,
    )

    return {
        "period": get_current_period(),
        "limit": FREE_SIGNALS_LIMIT,
        "used": used,
        "remaining": remaining,
    }


create_free_signals_tables()