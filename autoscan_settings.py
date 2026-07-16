import sqlite3
from typing import Any

DATABASE_NAME = "autoscan_settings.db"

DEFAULT_SETTINGS = {
    "enabled": "1",
    "interval_minutes": "5",
    "minimum_score": "84",
}


def connect() -> sqlite3.Connection:
    connection = sqlite3.connect(DATABASE_NAME)
    connection.row_factory = sqlite3.Row
    return connection


def create_autoscan_tables() -> None:
    connection = connect()
    cursor = connection.cursor()

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS autoscan_settings (
            setting_key TEXT PRIMARY KEY,
            setting_value TEXT NOT NULL
        )
        """
    )

    for key, value in DEFAULT_SETTINGS.items():
        cursor.execute(
            """
            INSERT OR IGNORE INTO autoscan_settings (
                setting_key,
                setting_value
            )
            VALUES (?, ?)
            """,
            (key, value),
        )

    connection.commit()
    connection.close()


def get_setting(
    setting_key: str,
    default: Any = None,
) -> str | Any:
    connection = connect()
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT setting_value
        FROM autoscan_settings
        WHERE setting_key = ?
        """,
        (setting_key,),
    )

    result = cursor.fetchone()
    connection.close()

    if result is None:
        return default

    return result["setting_value"]


def set_setting(
    setting_key: str,
    setting_value: Any,
) -> None:
    connection = connect()
    cursor = connection.cursor()

    cursor.execute(
        """
        INSERT INTO autoscan_settings (
            setting_key,
            setting_value
        )
        VALUES (?, ?)
        ON CONFLICT(setting_key)
        DO UPDATE SET
            setting_value = excluded.setting_value
        """,
        (
            setting_key,
            str(setting_value),
        ),
    )

    connection.commit()
    connection.close()


def is_autoscan_enabled() -> bool:
    value = str(
        get_setting(
            "enabled",
            DEFAULT_SETTINGS["enabled"],
        )
    ).strip()

    return value == "1"


def set_autoscan_enabled(enabled: bool) -> None:
    set_setting(
        "enabled",
        "1" if enabled else "0",
    )


def get_autoscan_interval_minutes() -> int:
    raw_value = get_setting(
        "interval_minutes",
        DEFAULT_SETTINGS["interval_minutes"],
    )

    try:
        interval = int(raw_value)
    except (TypeError, ValueError):
        interval = int(
            DEFAULT_SETTINGS["interval_minutes"]
        )

    return max(3, min(interval, 1440))


def set_autoscan_interval_minutes(
    minutes: int,
) -> int:
    safe_minutes = max(
        3,
        min(int(minutes), 1440),
    )

    set_setting(
        "interval_minutes",
        safe_minutes,
    )

    return safe_minutes


def get_minimum_autoscan_score() -> int:
    raw_value = get_setting(
        "minimum_score",
        DEFAULT_SETTINGS["minimum_score"],
    )

    try:
        score = int(raw_value)
    except (TypeError, ValueError):
        score = int(
            DEFAULT_SETTINGS["minimum_score"]
        )

    return max(60, min(score, 100))


def set_minimum_autoscan_score(
    score: int,
) -> int:
    safe_score = max(
        60,
        min(int(score), 100),
    )

    set_setting(
        "minimum_score",
        safe_score,
    )

    return safe_score


def get_all_autoscan_settings() -> dict[str, int | bool]:
    return {
        "enabled": is_autoscan_enabled(),
        "interval_minutes": (
            get_autoscan_interval_minutes()
        ),
        "minimum_score": (
            get_minimum_autoscan_score()
        ),
    }


create_autoscan_tables()