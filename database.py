import sqlite3
from datetime import datetime, timezone
from typing import Optional

DB_NAME = "users.db"


def connect() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_NAME)
    connection.row_factory = sqlite3.Row
    return connection


def create_tables() -> None:
    conn = connect()
    cursor = conn.cursor()

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            balance REAL DEFAULT 0,
            vip INTEGER DEFAULT 0,
            referrals INTEGER DEFAULT 0,
            referred_by INTEGER DEFAULT NULL,
            reg_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            vip_until TEXT DEFAULT NULL,
            subscription_plan TEXT DEFAULT NULL
        )
        """
    )

    cursor.execute("PRAGMA table_info(users)")
    columns = [column["name"] for column in cursor.fetchall()]

    migrations = {
        "username": "ALTER TABLE users ADD COLUMN username TEXT",
        "first_name": "ALTER TABLE users ADD COLUMN first_name TEXT",
        "balance": (
            "ALTER TABLE users ADD COLUMN balance REAL DEFAULT 0"
        ),
        "vip": (
            "ALTER TABLE users ADD COLUMN vip INTEGER DEFAULT 0"
        ),
        "referrals": (
            "ALTER TABLE users ADD COLUMN referrals INTEGER DEFAULT 0"
        ),
        "referred_by": (
            "ALTER TABLE users "
            "ADD COLUMN referred_by INTEGER DEFAULT NULL"
        ),
        "reg_date": (
            "ALTER TABLE users ADD COLUMN reg_date TIMESTAMP"
        ),
        "vip_until": (
            "ALTER TABLE users ADD COLUMN vip_until TEXT DEFAULT NULL"
        ),
        "subscription_plan": (
            "ALTER TABLE users "
            "ADD COLUMN subscription_plan TEXT DEFAULT NULL"
        ),
        "blocked": (
            "ALTER TABLE users ADD COLUMN blocked INTEGER DEFAULT 0"
        ),
    }

    for column_name, sql in migrations.items():
        if column_name not in columns:
            cursor.execute(sql)

    conn.commit()
    conn.close()


def add_user(
    user_id: int,
    username: Optional[str],
    first_name: Optional[str],
) -> bool:
    conn = connect()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT user_id FROM users WHERE user_id = ?",
        (user_id,),
    )

    existing_user = cursor.fetchone()

    if existing_user:
        cursor.execute(
            """
            UPDATE users
            SET username = ?, first_name = ?
            WHERE user_id = ?
            """,
            (username, first_name, user_id),
        )

        conn.commit()
        conn.close()
        return False

    cursor.execute(
        """
        INSERT INTO users (
            user_id,
            username,
            first_name
        )
        VALUES (?, ?, ?)
        """,
        (user_id, username, first_name),
    )

    conn.commit()
    conn.close()

    return True


def add_referral(user_id: int, referrer_id: int) -> bool:
    if user_id == referrer_id:
        return False

    conn = connect()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT referred_by
        FROM users
        WHERE user_id = ?
        """,
        (user_id,),
    )

    invited_user = cursor.fetchone()

    if invited_user is None:
        conn.close()
        return False

    if invited_user["referred_by"] is not None:
        conn.close()
        return False

    cursor.execute(
        """
        SELECT user_id
        FROM users
        WHERE user_id = ?
        """,
        (referrer_id,),
    )

    referrer = cursor.fetchone()

    if referrer is None:
        conn.close()
        return False

    cursor.execute(
        """
        UPDATE users
        SET referred_by = ?
        WHERE user_id = ?
        """,
        (referrer_id, user_id),
    )

    cursor.execute(
        """
        UPDATE users
        SET referrals = referrals + 1
        WHERE user_id = ?
        """,
        (referrer_id,),
    )

    cursor.execute(
        """
        SELECT referrals
        FROM users
        WHERE user_id = ?
        """,
        (referrer_id,),
    )

    referral_data = cursor.fetchone()
    referral_count = int(referral_data["referrals"] or 0)

    if referral_count >= 300:
        cursor.execute(
            """
            UPDATE users
            SET
                vip = 1,
                subscription_plan = 'referral_vip',
                vip_until = NULL
            WHERE user_id = ?
            """,
            (referrer_id,),
        )

    conn.commit()
    conn.close()

    return True


def get_user(user_id: int):
    refresh_user_vip(user_id)

    conn = connect()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT
            user_id,
            username,
            first_name,
            balance,
            vip,
            referrals,
            referred_by,
            reg_date,
            vip_until,
            subscription_plan,
            blocked
        FROM users
        WHERE user_id = ?
        """,
        (user_id,),
    )

    user = cursor.fetchone()
    conn.close()

    if user is None:
        return None

    return tuple(user)


def get_all_users():
    refresh_all_expired_vip()

    conn = connect()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT
            user_id,
            username,
            first_name,
            balance,
            vip,
            referrals,
            referred_by,
            reg_date,
            vip_until,
            subscription_plan,
            blocked
        FROM users
        ORDER BY reg_date DESC
        """
    )

    users = cursor.fetchall()
    conn.close()

    return [tuple(user) for user in users]


def set_vip(user_id: int, value: int) -> bool:
    vip_value = 1 if value else 0

    conn = connect()
    cursor = conn.cursor()

    if vip_value:
        cursor.execute(
            """
            UPDATE users
            SET
                vip = 1,
                vip_until = NULL,
                subscription_plan = 'manual'
            WHERE user_id = ?
            """,
            (user_id,),
        )
    else:
        cursor.execute(
            """
            UPDATE users
            SET
                vip = 0,
                vip_until = NULL,
                subscription_plan = NULL
            WHERE user_id = ?
            """,
            (user_id,),
        )

    updated = cursor.rowcount > 0

    conn.commit()
    conn.close()

    return updated


def activate_subscription(
    user_id: int,
    plan_code: str,
    vip_until: datetime,
) -> bool:
    if vip_until.tzinfo is None:
        vip_until = vip_until.replace(tzinfo=timezone.utc)

    vip_until_text = vip_until.astimezone(
        timezone.utc
    ).isoformat()

    conn = connect()
    cursor = conn.cursor()

    cursor.execute(
        """
        UPDATE users
        SET
            vip = 1,
            vip_until = ?,
            subscription_plan = ?
        WHERE user_id = ?
        """,
        (
            vip_until_text,
            plan_code,
            user_id,
        ),
    )

    updated = cursor.rowcount > 0

    conn.commit()
    conn.close()

    return updated


def get_vip_until(user_id: int) -> Optional[datetime]:
    conn = connect()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT vip_until
        FROM users
        WHERE user_id = ?
        """,
        (user_id,),
    )

    result = cursor.fetchone()
    conn.close()

    if result is None or not result["vip_until"]:
        return None

    try:
        vip_until = datetime.fromisoformat(
            result["vip_until"]
        )
    except ValueError:
        return None

    if vip_until.tzinfo is None:
        vip_until = vip_until.replace(
            tzinfo=timezone.utc
        )

    return vip_until


def refresh_user_vip(user_id: int) -> None:
    conn = connect()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT
            vip,
            vip_until,
            subscription_plan,
            referrals
        FROM users
        WHERE user_id = ?
        """,
        (user_id,),
    )

    user = cursor.fetchone()

    if user is None:
        conn.close()
        return

    referrals = int(user["referrals"] or 0)
    plan = user["subscription_plan"]
    vip_until_text = user["vip_until"]

    if referrals >= 300:
        if not user["vip"]:
            cursor.execute(
                """
                UPDATE users
                SET
                    vip = 1,
                    vip_until = NULL,
                    subscription_plan = 'referral_vip'
                WHERE user_id = ?
                """,
                (user_id,),
            )

        conn.commit()
        conn.close()
        return

    if plan in ("manual", "referral_vip"):
        conn.close()
        return

    if not vip_until_text:
        conn.close()
        return

    try:
        vip_until = datetime.fromisoformat(
            vip_until_text
        )
    except ValueError:
        conn.close()
        return

    if vip_until.tzinfo is None:
        vip_until = vip_until.replace(
            tzinfo=timezone.utc
        )

    now = datetime.now(timezone.utc)

    if vip_until <= now:
        cursor.execute(
            """
            UPDATE users
            SET
                vip = 0,
                vip_until = NULL,
                subscription_plan = NULL
            WHERE user_id = ?
            """,
            (user_id,),
        )

    conn.commit()
    conn.close()


def refresh_all_expired_vip() -> None:
    conn = connect()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT
            user_id,
            vip_until,
            subscription_plan,
            referrals
        FROM users
        WHERE vip = 1
        """
    )

    users = cursor.fetchall()
    now = datetime.now(timezone.utc)

    for user in users:
        user_id = int(user["user_id"])
        referrals = int(user["referrals"] or 0)
        plan = user["subscription_plan"]
        vip_until_text = user["vip_until"]

        if referrals >= 300:
            continue

        if plan in ("manual", "referral_vip"):
            continue

        if not vip_until_text:
            continue

        try:
            vip_until = datetime.fromisoformat(
                vip_until_text
            )
        except ValueError:
            continue

        if vip_until.tzinfo is None:
            vip_until = vip_until.replace(
                tzinfo=timezone.utc
            )

        if vip_until <= now:
            cursor.execute(
                """
                UPDATE users
                SET
                    vip = 0,
                    vip_until = NULL,
                    subscription_plan = NULL
                WHERE user_id = ?
                """,
                (user_id,),
            )

    conn.commit()
    conn.close()


def add_balance(user_id: int, amount: float) -> bool:
    conn = connect()
    cursor = conn.cursor()

    cursor.execute(
        """
        UPDATE users
        SET balance = balance + ?
        WHERE user_id = ?
        """,
        (amount, user_id),
    )

    updated = cursor.rowcount > 0

    conn.commit()
    conn.close()

    return updated


def set_balance(user_id: int, amount: float) -> bool:
    conn = connect()
    cursor = conn.cursor()

    cursor.execute(
        """
        UPDATE users
        SET balance = ?
        WHERE user_id = ?
        """,
        (amount, user_id),
    )

    updated = cursor.rowcount > 0

    conn.commit()
    conn.close()

    return updated


def get_referral_count(user_id: int) -> int:
    conn = connect()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT referrals
        FROM users
        WHERE user_id = ?
        """,
        (user_id,),
    )

    result = cursor.fetchone()
    conn.close()

    if result is None:
        return 0

    return int(result["referrals"] or 0)


def get_referral_discount(referral_count: int) -> int:
    if referral_count >= 500:
        return 50

    if referral_count >= 400:
        return 45

    if referral_count >= 300:
        return 40

    if referral_count >= 200:
        return 35

    if referral_count >= 100:
        return 30

    if referral_count >= 50:
        return 25

    if referral_count >= 40:
        return 20

    if referral_count >= 30:
        return 15

    if referral_count >= 20:
        return 10

    if referral_count >= 10:
        return 5

    return 0


def get_total_discount(user_id: int) -> int:
    user = get_user(user_id)

    if user is None:
        return 0

    vip_status = bool(user[4])
    referral_count = int(user[5] or 0)

    discount = get_referral_discount(
        referral_count
    )

    if vip_status:
        discount += 5

    return min(discount, 55)


def get_next_referral_level(referral_count: int):
    levels = [
        (10, 5),
        (20, 10),
        (30, 15),
        (40, 20),
        (50, 25),
        (100, 30),
        (200, 35),
        (300, 40),
        (400, 45),
        (500, 50),
    ]

    for required_referrals, discount in levels:
        if referral_count < required_referrals:
            return required_referrals, discount

    return None


def get_top_referrers(limit: int = 10):
    conn = connect()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT
            user_id,
            username,
            first_name,
            referrals
        FROM users
        ORDER BY referrals DESC, reg_date ASC
        LIMIT ?
        """,
        (limit,),
    )

    users = cursor.fetchall()
    conn.close()

    return [tuple(user) for user in users]

def set_blocked(user_id: int, blocked: bool) -> bool:
    conn = connect()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE users SET blocked = ? WHERE user_id = ?",
        (1 if blocked else 0, user_id),
    )
    updated = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return updated


def is_user_blocked(user_id: int) -> bool:
    conn = connect()
    cursor = conn.cursor()
    cursor.execute("SELECT blocked FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    return bool(row and row["blocked"])


def search_users(query: str, limit: int = 10):
    conn = connect()
    cursor = conn.cursor()
    clean = query.strip().lstrip("@").lower()
    if clean.isdigit():
        cursor.execute(
            """
            SELECT user_id, username, first_name, balance, vip, referrals,
                   referred_by, reg_date, vip_until, subscription_plan, blocked
            FROM users WHERE user_id = ? LIMIT ?
            """,
            (int(clean), limit),
        )
    else:
        like = f"%{clean}%"
        cursor.execute(
            """
            SELECT user_id, username, first_name, balance, vip, referrals,
                   referred_by, reg_date, vip_until, subscription_plan, blocked
            FROM users
            WHERE LOWER(COALESCE(username, '')) LIKE ?
               OR LOWER(COALESCE(first_name, '')) LIKE ?
            ORDER BY reg_date DESC LIMIT ?
            """,
            (like, like, limit),
        )
    rows = cursor.fetchall()
    conn.close()
    return [tuple(row) for row in rows]


# =========================
# SIGNAL TRACKING
# =========================

def init_signal_tracking_tables() -> None:
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS trading_signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_signal_id INTEGER,
                symbol TEXT NOT NULL,
                direction TEXT NOT NULL,
                entry_zone TEXT NOT NULL,
                stop_loss TEXT NOT NULL,
                tp1 TEXT NOT NULL,
                tp2 TEXT,
                tp3 TEXT,
                risk TEXT,
                comment TEXT,
                setup_score INTEGER DEFAULT 0,
                audience TEXT DEFAULT 'all',
                status TEXT DEFAULT 'active',
                result TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                closed_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS signal_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_id INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                event_value TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.commit()


def create_trading_signal(
    source_signal_id: int | None,
    symbol: str,
    direction: str,
    entry_zone: str,
    stop_loss: str,
    tp1: str,
    tp2: str | None = None,
    tp3: str | None = None,
    risk: str | None = None,
    comment: str | None = None,
    setup_score: int = 0,
    audience: str = "all",
) -> int:
    init_signal_tracking_tables()

    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.execute(
            """
            INSERT INTO trading_signals (
                source_signal_id, symbol, direction, entry_zone,
                stop_loss, tp1, tp2, tp3, risk, comment,
                setup_score, audience, status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active')
            """,
            (
                source_signal_id, symbol, direction, entry_zone,
                stop_loss, tp1, tp2, tp3, risk, comment,
                setup_score, audience,
            ),
        )
        conn.commit()
        return int(cursor.lastrowid)


def add_signal_event(
    signal_id: int,
    event_type: str,
    event_value: str | None = None,
) -> None:
    init_signal_tracking_tables()

    with sqlite3.connect(DB_NAME) as conn:
        conn.execute(
            """
            INSERT INTO signal_events (
                signal_id, event_type, event_value
            )
            VALUES (?, ?, ?)
            """,
            (signal_id, event_type, event_value),
        )
        conn.commit()


def close_trading_signal(signal_id: int, result: str) -> None:
    init_signal_tracking_tables()

    with sqlite3.connect(DB_NAME) as conn:
        conn.execute(
            """
            UPDATE trading_signals
            SET status = 'closed',
                result = ?,
                closed_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (result, signal_id),
        )
        conn.commit()

    add_signal_event(signal_id, "closed", result)


def get_trading_signal(signal_id: int):
    init_signal_tracking_tables()

    with sqlite3.connect(DB_NAME) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM trading_signals WHERE id = ?",
            (signal_id,),
        ).fetchone()

    return dict(row) if row else None


def get_active_trading_signals(limit: int = 30):
    init_signal_tracking_tables()

    with sqlite3.connect(DB_NAME) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT *
            FROM trading_signals
            WHERE status = 'active'
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    return [dict(row) for row in rows]


def get_signal_statistics() -> dict:
    init_signal_tracking_tables()

    with sqlite3.connect(DB_NAME) as conn:
        total = conn.execute(
            "SELECT COUNT(*) FROM trading_signals"
        ).fetchone()[0]
        active = conn.execute(
            "SELECT COUNT(*) FROM trading_signals WHERE status = 'active'"
        ).fetchone()[0]
        closed = conn.execute(
            "SELECT COUNT(*) FROM trading_signals WHERE status = 'closed'"
        ).fetchone()[0]
        wins = conn.execute(
            """
            SELECT COUNT(*) FROM trading_signals
            WHERE result IN ('tp1', 'tp2', 'tp3', 'win')
            """
        ).fetchone()[0]
        losses = conn.execute(
            """
            SELECT COUNT(*) FROM trading_signals
            WHERE result IN ('sl', 'loss')
            """
        ).fetchone()[0]
        breakeven = conn.execute(
            """
            SELECT COUNT(*) FROM trading_signals
            WHERE result IN ('be', 'breakeven')
            """
        ).fetchone()[0]

    resolved = wins + losses
    winrate = wins / resolved * 100 if resolved else 0.0

    return {
        "total": total,
        "active": active,
        "closed": closed,
        "wins": wins,
        "losses": losses,
        "breakeven": breakeven,
        "winrate": winrate,
    }


def get_tracking_signal_id_by_source(
    source_signal_id: int,
) -> int | None:
    init_signal_tracking_tables()

    with sqlite3.connect(DB_NAME) as conn:
        row = conn.execute(
            """
            SELECT id
            FROM trading_signals
            WHERE source_signal_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (source_signal_id,),
        ).fetchone()

    return int(row[0]) if row else None


def add_signal_event_by_source(
    source_signal_id: int,
    event_type: str,
    event_value: str | None = None,
) -> None:
    tracking_id = get_tracking_signal_id_by_source(
        source_signal_id
    )

    if tracking_id is not None:
        add_signal_event(
            tracking_id,
            event_type,
            event_value,
        )


def close_trading_signal_by_source(
    source_signal_id: int,
    result: str,
) -> None:
    tracking_id = get_tracking_signal_id_by_source(
        source_signal_id
    )

    if tracking_id is not None:
        close_trading_signal(
            tracking_id,
            result,
        )


init_signal_tracking_tables()


def get_referral_rank(user_id: int) -> int:
    conn = connect()
    cursor = conn.cursor()
    cursor.execute("SELECT referrals FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    if row is None:
        conn.close()
        return 0
    referrals = int(row["referrals"] or 0)
    cursor.execute(
        "SELECT COUNT(*) FROM users WHERE referrals > ?",
        (referrals,),
    )
    rank = int(cursor.fetchone()[0]) + 1
    conn.close()
    return rank


def get_project_growth_stats() -> dict[str, int]:
    conn = connect()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN datetime(reg_date) >= datetime('now', '-1 day') THEN 1 ELSE 0 END) AS day,
            SUM(CASE WHEN datetime(reg_date) >= datetime('now', '-7 day') THEN 1 ELSE 0 END) AS week,
            SUM(CASE WHEN datetime(reg_date) >= datetime('now', '-30 day') THEN 1 ELSE 0 END) AS month,
            SUM(CASE WHEN vip = 1 THEN 1 ELSE 0 END) AS vip,
            SUM(CASE WHEN blocked = 1 THEN 1 ELSE 0 END) AS blocked,
            COALESCE(SUM(referrals), 0) AS referrals
        FROM users
        """
    )
    row = cursor.fetchone()
    conn.close()
    return {
        "total": int(row["total"] or 0),
        "day": int(row["day"] or 0),
        "week": int(row["week"] or 0),
        "month": int(row["month"] or 0),
        "vip": int(row["vip"] or 0),
        "blocked": int(row["blocked"] or 0),
        "referrals": int(row["referrals"] or 0),
    }
