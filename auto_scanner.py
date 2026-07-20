import asyncio
import os
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from aiogram import Bot

from autoscan_logs import fail_scan_log, finish_scan_log, start_scan_log
from autoscan_settings import (
    get_autoscan_interval_minutes,
    get_minimum_autoscan_score,
    is_autoscan_enabled,
)
from config import ADMIN_ID
from market_assistant import (
    MAX_RESULTS_TO_SHOW,
    format_setup,
    scan_markets,
    setup_keyboard,
)

PROJECT_DIR = Path(__file__).resolve().parent
ANTIDUPLICATE_DB_PATH = PROJECT_DIR / "autoscan_settings.db"

MAX_AUTO_SETUPS = max(1, int(os.getenv("AUTOSCAN_MAX_SETUPS", "5")))
DUPLICATE_COOLDOWN_MINUTES = max(
    1,
    int(os.getenv("AUTOSCAN_DUPLICATE_MINUTES", "1440")),
)

# instrument: блокировать повтор той же монеты независимо от LONG/SHORT.
# instrument_direction: LONG и SHORT считать разными сигналами.
DUPLICATE_MODE = os.getenv(
    "AUTOSCAN_DUPLICATE_MODE",
    "instrument",
).strip().lower()

if DUPLICATE_MODE not in {"instrument", "instrument_direction"}:
    DUPLICATE_MODE = "instrument"

_scan_lock = asyncio.Lock()
_stop_event = asyncio.Event()

_last_scan_started_at: Optional[float] = None
_last_scan_finished_at: Optional[float] = None
_last_scan_error: Optional[str] = None
_last_scan_result: dict[str, int] = {
    "analysed": 0,
    "found": 0,
    "sent": 0,
    "duplicates": 0,
}


@dataclass(frozen=True)
class Reservation:
    row_id: int
    setup_id: str
    inst_id: str
    direction: str


def normalize_direction(direction: str) -> str:
    value = str(direction or "").upper().strip()
    aliases = {
        "BUY": "LONG",
        "SELL": "SHORT",
        "ЛОНГ": "LONG",
        "ШОРТ": "SHORT",
    }
    return aliases.get(value, value)


def normalize_instrument(inst_id: str) -> str:
    value = str(inst_id or "").upper().strip()
    value = value.replace("/", "-").replace("_", "-").replace(" ", "")

    while "--" in value:
        value = value.replace("--", "-")

    while value.endswith("-SWAP-SWAP"):
        value = value[:-5]

    if value.endswith("USDT") and not value.endswith("-USDT"):
        value = value[:-4].rstrip("-") + "-USDT"

    if value.endswith("-USDT"):
        value += "-SWAP"

    return value


def duplicate_key(inst_id: str, direction: str) -> tuple[str, str]:
    instrument = normalize_instrument(inst_id)
    normalized_direction = normalize_direction(direction)

    if DUPLICATE_MODE == "instrument":
        return instrument, "*"

    return instrument, normalized_direction


def connect_antiduplicate_db() -> sqlite3.Connection:
    connection = sqlite3.connect(
        ANTIDUPLICATE_DB_PATH,
        timeout=30,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 30000")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA synchronous = NORMAL")
    return connection


def create_antiduplicate_table() -> None:
    connection = connect_antiduplicate_db()

    try:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS autoscan_sent_setups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                setup_id TEXT NOT NULL DEFAULT '',
                inst_id TEXT NOT NULL,
                direction TEXT NOT NULL,
                duplicate_direction TEXT NOT NULL DEFAULT '*',
                score INTEGER NOT NULL DEFAULT 0,
                sent_at TEXT NOT NULL,
                sent_at_unix INTEGER NOT NULL,
                delivery_status TEXT NOT NULL DEFAULT 'reserved'
            )
            """
        )

        columns = {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(autoscan_sent_setups)"
            ).fetchall()
        }

        migrations = {
            "duplicate_direction": (
                "ALTER TABLE autoscan_sent_setups "
                "ADD COLUMN duplicate_direction TEXT NOT NULL DEFAULT '*'"
            ),
            "delivery_status": (
                "ALTER TABLE autoscan_sent_setups "
                "ADD COLUMN delivery_status TEXT NOT NULL DEFAULT 'sent'"
            ),
        }

        for column_name, sql in migrations.items():
            if column_name not in columns:
                connection.execute(sql)

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_autoscan_sent_duplicate_lookup
            ON autoscan_sent_setups(inst_id, duplicate_direction, sent_at_unix)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_autoscan_sent_time
            ON autoscan_sent_setups(sent_at_unix)
            """
        )
        connection.commit()
    finally:
        connection.close()


def cleanup_old_antiduplicates(days: int = 30) -> int:
    threshold = int(time.time()) - max(1, int(days)) * 86400
    connection = connect_antiduplicate_db()

    try:
        cursor = connection.execute(
            "DELETE FROM autoscan_sent_setups WHERE sent_at_unix < ?",
            (threshold,),
        )
        connection.commit()
        return max(0, cursor.rowcount)
    finally:
        connection.close()


def is_duplicate_setup(
    inst_id: str,
    direction: str,
    cooldown_minutes: int = DUPLICATE_COOLDOWN_MINUTES,
) -> bool:
    threshold = int(time.time()) - max(1, int(cooldown_minutes)) * 60
    normalized_inst_id, duplicate_direction = duplicate_key(inst_id, direction)
    connection = connect_antiduplicate_db()

    try:
        row = connection.execute(
            """
            SELECT id
            FROM autoscan_sent_setups
            WHERE inst_id = ?
              AND duplicate_direction = ?
              AND sent_at_unix >= ?
            ORDER BY sent_at_unix DESC, id DESC
            LIMIT 1
            """,
            (normalized_inst_id, duplicate_direction, threshold),
        ).fetchone()
        return row is not None
    finally:
        connection.close()


def reserve_setup(
    setup,
    cooldown_minutes: int = DUPLICATE_COOLDOWN_MINUTES,
) -> Optional[Reservation]:
    now = int(time.time())
    threshold = now - max(1, int(cooldown_minutes)) * 60
    normalized_inst_id, duplicate_direction = duplicate_key(
        setup.inst_id,
        setup.direction,
    )
    normalized_direction = normalize_direction(setup.direction)
    setup_id = str(getattr(setup, "setup_id", "") or "")
    connection = connect_antiduplicate_db()

    try:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            """
            SELECT id
            FROM autoscan_sent_setups
            WHERE inst_id = ?
              AND duplicate_direction = ?
              AND sent_at_unix >= ?
            ORDER BY sent_at_unix DESC, id DESC
            LIMIT 1
            """,
            (normalized_inst_id, duplicate_direction, threshold),
        ).fetchone()

        if row is not None:
            connection.rollback()
            return None

        cursor = connection.execute(
            """
            INSERT INTO autoscan_sent_setups (
                setup_id,
                inst_id,
                direction,
                duplicate_direction,
                score,
                sent_at,
                sent_at_unix,
                delivery_status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 'reserved')
            """,
            (
                setup_id,
                normalized_inst_id,
                normalized_direction,
                duplicate_direction,
                int(getattr(setup, "score", 0) or 0),
                datetime.now(timezone.utc).isoformat(),
                now,
            ),
        )
        row_id = int(cursor.lastrowid)
        connection.commit()

        return Reservation(
            row_id=row_id,
            setup_id=setup_id,
            inst_id=normalized_inst_id,
            direction=normalized_direction,
        )
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def mark_reservation_sent(reservation: Reservation) -> None:
    connection = connect_antiduplicate_db()

    try:
        connection.execute(
            "UPDATE autoscan_sent_setups SET delivery_status = 'sent' WHERE id = ?",
            (reservation.row_id,),
        )
        connection.commit()
    finally:
        connection.close()


def release_reserved_setup(reservation: Reservation) -> None:
    connection = connect_antiduplicate_db()

    try:
        connection.execute(
            """
            DELETE FROM autoscan_sent_setups
            WHERE id = ? AND delivery_status = 'reserved'
            """,
            (reservation.row_id,),
        )
        connection.commit()
    finally:
        connection.close()


def get_recent_sent_setups(limit: int = 20) -> list[dict]:
    safe_limit = max(1, min(int(limit), 100))
    connection = connect_antiduplicate_db()

    try:
        rows = connection.execute(
            """
            SELECT setup_id, inst_id, direction, score, sent_at,
                   sent_at_unix, delivery_status
            FROM autoscan_sent_setups
            ORDER BY sent_at_unix DESC, id DESC
            LIMIT ?
            """,
            (safe_limit,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()


def clear_antiduplicates() -> int:
    connection = connect_antiduplicate_db()

    try:
        cursor = connection.execute("DELETE FROM autoscan_sent_setups")
        connection.commit()
        return max(0, cursor.rowcount)
    finally:
        connection.close()


def is_auto_scan_running() -> bool:
    return _scan_lock.locked()


def get_last_scan_started_at() -> Optional[float]:
    return _last_scan_started_at


def get_last_scan_finished_at() -> Optional[float]:
    return _last_scan_finished_at


def get_last_scan_error() -> Optional[str]:
    return _last_scan_error


def get_last_scan_result() -> dict[str, int]:
    return dict(_last_scan_result)


def wake_auto_scanner() -> None:
    _stop_event.set()


async def send_setup_to_admin(bot: Bot, setup) -> bool:
    try:
        await bot.send_message(
            chat_id=ADMIN_ID,
            text=(
                "🚨 АВТОМАТИЧЕСКИЙ СКАНЕР\n\n"
                f"{format_setup(setup)}"
            ),
            reply_markup=setup_keyboard(setup.setup_id),
        )
        return True
    except Exception as error:
        print(f"Ошибка отправки автосетапа {setup.inst_id}: {error}")
        return False


async def notify_admin_about_scan_error(bot: Bot, error_text: str) -> None:
    try:
        await bot.send_message(
            chat_id=ADMIN_ID,
            text=(
                "❌ Ошибка автоматического сканирования\n\n"
                f"{error_text}"
            ),
        )
    except Exception as error:
        print(f"Не удалось отправить администратору ошибку сканера: {error}")


async def run_single_auto_scan(
    bot: Bot,
    notify_admin: bool = True,
) -> dict[str, int]:
    global _last_scan_started_at
    global _last_scan_finished_at
    global _last_scan_error
    global _last_scan_result

    if _scan_lock.locked():
        print("Автоскан уже выполняется — повторный запуск пропущен")
        return {
            "analysed": 0,
            "found": 0,
            "sent": 0,
            "duplicates": 0,
        }

    async with _scan_lock:
        started_monotonic = time.monotonic()
        _last_scan_started_at = time.time()
        _last_scan_finished_at = None
        _last_scan_error = None

        minimum_score = get_minimum_autoscan_score()
        log_id = start_scan_log(minimum_score)

        print(
            "Автоматическое сканирование запущено. "
            f"Минимальный рейтинг: {minimum_score}. "
            f"Антидубли: {DUPLICATE_COOLDOWN_MINUTES} мин. "
            f"Режим: {DUPLICATE_MODE}."
        )

        try:
            setups, analysed_count = await scan_markets()
        except asyncio.CancelledError:
            duration = time.monotonic() - started_monotonic
            fail_scan_log(
                log_id=log_id,
                duration_seconds=duration,
                error_text="Сканирование отменено",
            )
            raise
        except Exception as error:
            error_text = str(error)
            duration = time.monotonic() - started_monotonic
            _last_scan_error = error_text
            _last_scan_finished_at = time.time()
            _last_scan_result = {
                "analysed": 0,
                "found": 0,
                "sent": 0,
                "duplicates": 0,
            }
            fail_scan_log(
                log_id=log_id,
                duration_seconds=duration,
                error_text=error_text,
            )
            print("Ошибка автоматического сканирования:", error_text)

            if notify_admin:
                await notify_admin_about_scan_error(bot, error_text)

            return dict(_last_scan_result)

        strong_setups = [
            setup
            for setup in setups
            if int(getattr(setup, "score", 0) or 0) >= minimum_score
        ]
        strong_setups.sort(
            key=lambda setup: (
                int(getattr(setup, "score", 0) or 0),
                float(getattr(setup, "risk_reward", 0) or 0),
            ),
            reverse=True,
        )

        candidate_limit = min(
            max(MAX_AUTO_SETUPS * 4, MAX_AUTO_SETUPS),
            max(MAX_RESULTS_TO_SHOW, MAX_AUTO_SETUPS),
        )
        raw_candidates = strong_setups[:candidate_limit]

        candidate_setups = []
        seen_in_current_scan: set[tuple[str, str]] = set()
        in_scan_duplicates = 0

        for setup in raw_candidates:
            key = duplicate_key(setup.inst_id, setup.direction)

            if key in seen_in_current_scan:
                in_scan_duplicates += 1
                print("Повтор внутри текущего скана пропущен:", key[0], key[1])
                continue

            seen_in_current_scan.add(key)
            candidate_setups.append(setup)

        reserved_setups: list[tuple[object, Reservation]] = []
        database_duplicates = 0

        for setup in candidate_setups:
            reservation = reserve_setup(setup)

            if reservation is None:
                database_duplicates += 1
                print(
                    "Антидубль БД пропустил:",
                    normalize_instrument(setup.inst_id),
                    normalize_direction(setup.direction),
                    f"score={getattr(setup, 'score', 0)}",
                )
                continue

            reserved_setups.append((setup, reservation))

            if len(reserved_setups) >= MAX_AUTO_SETUPS:
                break

        sent = 0

        for setup, reservation in reserved_setups:
            delivered = await send_setup_to_admin(bot, setup)

            if delivered:
                mark_reservation_sent(reservation)
                sent += 1
            else:
                release_reserved_setup(reservation)

            await asyncio.sleep(0.3)

        duplicates = in_scan_duplicates + database_duplicates
        duration = time.monotonic() - started_monotonic
        _last_scan_finished_at = time.time()
        _last_scan_result = {
            "analysed": int(analysed_count),
            "found": len(strong_setups),
            "sent": sent,
            "duplicates": duplicates,
        }

        finish_scan_log(
            log_id=log_id,
            duration_seconds=duration,
            analysed=int(analysed_count),
            found=len(strong_setups),
            sent=sent,
            duplicates=duplicates,
        )

        print(
            "Автоскан завершён:",
            f"проверено={analysed_count},",
            f"найдено={len(strong_setups)},",
            f"антидубли={duplicates},",
            f"отправлено={sent},",
            f"время={duration:.1f}с",
        )

        cleanup_old_antiduplicates(days=30)
        return dict(_last_scan_result)


async def wait_for_next_scan(interval_seconds: int) -> None:
    _stop_event.clear()

    try:
        await asyncio.wait_for(
            _stop_event.wait(),
            timeout=max(1, int(interval_seconds)),
        )
    except asyncio.TimeoutError:
        pass
    finally:
        _stop_event.clear()


async def automatic_market_scanner(bot: Bot) -> None:
    global _last_scan_error

    await asyncio.sleep(30)
    print("Цикл автоматического сканера запущен")

    while True:
        try:
            if not is_autoscan_enabled():
                print("Автосканер выключен. Ожидаю включения.")
                await wait_for_next_scan(60)
                continue

            await run_single_auto_scan(
                bot=bot,
                notify_admin=True,
            )

            interval_minutes = max(
                1,
                int(get_autoscan_interval_minutes()),
            )
            print(f"Следующий автоскан через {interval_minutes} мин.")
            await wait_for_next_scan(interval_minutes * 60)

        except asyncio.CancelledError:
            print("Цикл автоматического сканера остановлен")
            raise
        except Exception as error:
            _last_scan_error = str(error)
            print("Критическая ошибка цикла автосканера:", error)

            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                raise


create_antiduplicate_table()