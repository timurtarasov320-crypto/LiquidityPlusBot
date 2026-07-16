import sqlite3
from dataclasses import dataclass

PREFERENCES_DB = "user_preferences.db"


@dataclass
class UserPreferences:
    user_id: int
    language: str = "ru"
    new_signals: bool = True
    tp_updates: bool = True
    sl_updates: bool = True
    ai_ideas: bool = True
    daily_analytics: bool = True


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(PREFERENCES_DB)
    conn.row_factory = sqlite3.Row
    return conn


def create_tables() -> None:
    with connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_preferences (
                user_id INTEGER PRIMARY KEY,
                language TEXT NOT NULL DEFAULT 'ru',
                new_signals INTEGER NOT NULL DEFAULT 1,
                tp_updates INTEGER NOT NULL DEFAULT 1,
                sl_updates INTEGER NOT NULL DEFAULT 1,
                ai_ideas INTEGER NOT NULL DEFAULT 1,
                daily_analytics INTEGER NOT NULL DEFAULT 1
            )
            """
        )
        conn.commit()


def ensure_user(user_id: int) -> None:
    create_tables()
    with connect() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO user_preferences (user_id)
            VALUES (?)
            """,
            (user_id,),
        )
        conn.commit()


def get_preferences(user_id: int) -> UserPreferences:
    ensure_user(user_id)
    with connect() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM user_preferences
            WHERE user_id = ?
            """,
            (user_id,),
        ).fetchone()

    return UserPreferences(
        user_id=int(row["user_id"]),
        language=str(row["language"]),
        new_signals=bool(row["new_signals"]),
        tp_updates=bool(row["tp_updates"]),
        sl_updates=bool(row["sl_updates"]),
        ai_ideas=bool(row["ai_ideas"]),
        daily_analytics=bool(row["daily_analytics"]),
    )


def set_language(user_id: int, language: str) -> None:
    if language not in {"ru", "uk", "en"}:
        raise ValueError("Unsupported language")

    ensure_user(user_id)
    with connect() as conn:
        conn.execute(
            """
            UPDATE user_preferences
            SET language = ?
            WHERE user_id = ?
            """,
            (language, user_id),
        )
        conn.commit()


def toggle_preference(user_id: int, field_name: str) -> bool:
    allowed = {
        "new_signals",
        "tp_updates",
        "sl_updates",
        "ai_ideas",
        "daily_analytics",
    }
    if field_name not in allowed:
        raise ValueError("Unsupported preference")

    ensure_user(user_id)

    with connect() as conn:
        current = conn.execute(
            f"""
            SELECT {field_name}
            FROM user_preferences
            WHERE user_id = ?
            """,
            (user_id,),
        ).fetchone()[0]

        new_value = 0 if int(current) else 1

        conn.execute(
            f"""
            UPDATE user_preferences
            SET {field_name} = ?
            WHERE user_id = ?
            """,
            (new_value, user_id),
        )
        conn.commit()

    return bool(new_value)


create_tables()
