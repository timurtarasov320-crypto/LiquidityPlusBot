import sqlite3
from datetime import datetime, timezone

DATABASE_NAME = "autoscan_settings.db"


def connect() -> sqlite3.Connection:
    connection = sqlite3.connect(DATABASE_NAME)
    connection.row_factory = sqlite3.Row
    return connection


def create_exclusions_table() -> None:
    connection = connect()
    cursor = connection.cursor()

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS autoscan_exclusions (
            inst_id TEXT PRIMARY KEY,
            reason TEXT,
            created_at TEXT NOT NULL
        )
        """
    )

    connection.commit()
    connection.close()


def normalize_inst_id(value: str) -> str:
    text = (
        value.upper()
        .strip()
        .replace("/", "-")
        .replace("_", "-")
    )

    if text.endswith("-USDT-SWAP"):
        return text

    if text.endswith("-SWAP"):
        return text

    if text.endswith("-USDT"):
        return f"{text}-SWAP"

    if "-" not in text:
        return f"{text}-USDT-SWAP"

    return text


def exclude_market(
    inst_id: str,
    reason: str | None = None,
) -> bool:
    normalized = normalize_inst_id(inst_id)

    connection = connect()
    cursor = connection.cursor()

    cursor.execute(
        """
        INSERT OR IGNORE INTO autoscan_exclusions (
            inst_id,
            reason,
            created_at
        )
        VALUES (?, ?, ?)
        """,
        (
            normalized,
            reason,
            datetime.now(timezone.utc).isoformat(),
        ),
    )

    added = cursor.rowcount > 0

    if not added and reason:
        cursor.execute(
            """
            UPDATE autoscan_exclusions
            SET reason = ?
            WHERE inst_id = ?
            """,
            (
                reason,
                normalized,
            ),
        )

    connection.commit()
    connection.close()

    return added


def include_market(inst_id: str) -> bool:
    normalized = normalize_inst_id(inst_id)

    connection = connect()
    cursor = connection.cursor()

    cursor.execute(
        """
        DELETE FROM autoscan_exclusions
        WHERE inst_id = ?
        """,
        (normalized,),
    )

    removed = cursor.rowcount > 0

    connection.commit()
    connection.close()

    return removed


def is_market_excluded(inst_id: str) -> bool:
    normalized = normalize_inst_id(inst_id)

    connection = connect()
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT inst_id
        FROM autoscan_exclusions
        WHERE inst_id = ?
        """,
        (normalized,),
    )

    result = cursor.fetchone()
    connection.close()

    return result is not None


def get_excluded_markets() -> list[dict]:
    connection = connect()
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT
            inst_id,
            reason,
            created_at
        FROM autoscan_exclusions
        ORDER BY created_at DESC
        """
    )

    rows = cursor.fetchall()
    connection.close()

    return [
        {
            "inst_id": row["inst_id"],
            "reason": row["reason"],
            "created_at": row["created_at"],
        }
        for row in rows
    ]


def get_excluded_market_ids() -> set[str]:
    return {
        item["inst_id"]
        for item in get_excluded_markets()
    }


def clear_excluded_markets() -> int:
    connection = connect()
    cursor = connection.cursor()

    cursor.execute(
        """
        DELETE FROM autoscan_exclusions
        """
    )

    deleted = cursor.rowcount

    connection.commit()
    connection.close()

    return deleted


def get_exclusion_info(
    inst_id: str,
) -> dict | None:
    normalized = normalize_inst_id(inst_id)

    connection = connect()
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT
            inst_id,
            reason,
            created_at
        FROM autoscan_exclusions
        WHERE inst_id = ?
        """,
        (normalized,),
    )

    row = cursor.fetchone()
    connection.close()

    if row is None:
        return None

    return {
        "inst_id": row["inst_id"],
        "reason": row["reason"],
        "created_at": row["created_at"],
    }


create_exclusions_table()