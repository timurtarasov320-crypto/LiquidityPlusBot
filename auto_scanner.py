import asyncio
import sqlite3
import time
from datetime import datetime, timezone
from typing import Optional

from aiogram import Bot

from autoscan_logs import (
    fail_scan_log,
    finish_scan_log,
    start_scan_log,
)
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


MAX_AUTO_SETUPS = 5
DUPLICATE_COOLDOWN_MINUTES = 60
ANTIDUPLICATE_DB_NAME = "autoscan_settings.db"

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


def connect_antiduplicate_db() -> sqlite3.Connection:
    connection = sqlite3.connect(ANTIDUPLICATE_DB_NAME)
    connection.row_factory = sqlite3.Row
    return connection


def create_antiduplicate_table() -> None:
    connection = connect_antiduplicate_db()
    cursor = connection.cursor()

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS autoscan_sent_setups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            setup_id TEXT,
            inst_id TEXT NOT NULL,
            direction TEXT NOT NULL,
            score INTEGER NOT NULL,
            sent_at TEXT NOT NULL,
            sent_at_unix INTEGER NOT NULL
        )
        """
    )

    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_autoscan_sent_market_direction
        ON autoscan_sent_setups(inst_id, direction, sent_at_unix)
        """
    )

    connection.commit()
    connection.close()


def normalize_direction(direction: str) -> str:
    return str(direction).upper().strip()


def cleanup_old_antiduplicates(days: int = 30) -> int:
    threshold = int(time.time()) - max(1, int(days)) * 86400

    connection = connect_antiduplicate_db()
    cursor = connection.cursor()

    cursor.execute(
        """
        DELETE FROM autoscan_sent_setups
        WHERE sent_at_unix < ?
        """,
        (threshold,),
    )

    deleted = cursor.rowcount

    connection.commit()
    connection.close()

    return deleted


def is_duplicate_setup(
    inst_id: str,
    direction: str,
    cooldown_minutes: int = DUPLICATE_COOLDOWN_MINUTES,
) -> bool:
    threshold = int(time.time()) - max(1, int(cooldown_minutes)) * 60

    connection = connect_antiduplicate_db()
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT id
        FROM autoscan_sent_setups
        WHERE inst_id = ?
          AND direction = ?
          AND sent_at_unix >= ?
        ORDER BY sent_at_unix DESC
        LIMIT 1
        """,
        (
            str(inst_id).upper().strip(),
            normalize_direction(direction),
            threshold,
        ),
    )

    result = cursor.fetchone()
    connection.close()

    return result is not None


def register_sent_setup(setup) -> None:
    connection = connect_antiduplicate_db()
    cursor = connection.cursor()

    cursor.execute(
        """
        INSERT INTO autoscan_sent_setups (
            setup_id,
            inst_id,
            direction,
            score,
            sent_at,
            sent_at_unix
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            str(getattr(setup, "setup_id", "")),
            str(setup.inst_id).upper().strip(),
            normalize_direction(setup.direction),
            int(setup.score),
            datetime.now(timezone.utc).isoformat(),
            int(time.time()),
        ),
    )

    connection.commit()
    connection.close()


def get_recent_sent_setups(limit: int = 20) -> list[dict]:
    safe_limit = max(1, min(int(limit), 100))

    connection = connect_antiduplicate_db()
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT
            setup_id,
            inst_id,
            direction,
            score,
            sent_at,
            sent_at_unix
        FROM autoscan_sent_setups
        ORDER BY sent_at_unix DESC
        LIMIT ?
        """,
        (safe_limit,),
    )

    rows = cursor.fetchall()
    connection.close()

    return [dict(row) for row in rows]


def clear_antiduplicates() -> int:
    connection = connect_antiduplicate_db()
    cursor = connection.cursor()

    cursor.execute("DELETE FROM autoscan_sent_setups")
    deleted = cursor.rowcount

    connection.commit()
    connection.close()

    return deleted


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


async def send_setup_to_admin(
    bot: Bot,
    setup,
) -> bool:
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
        print(
            "Ошибка отправки автосетапа "
            f"{setup.inst_id}: {error}"
        )
        return False


async def notify_admin_about_scan_error(
    bot: Bot,
    error_text: str,
) -> None:
    try:
        await bot.send_message(
            chat_id=ADMIN_ID,
            text=(
                "❌ Ошибка автоматического сканирования\n\n"
                f"{error_text}"
            ),
        )
    except Exception as error:
        print(
            "Не удалось отправить администратору "
            f"ошибку сканера: {error}"
        )


async def run_single_auto_scan(
    bot: Bot,
    notify_admin: bool = True,
) -> dict[str, int]:
    global _last_scan_started_at
    global _last_scan_finished_at
    global _last_scan_error
    global _last_scan_result

    if _scan_lock.locked():
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
            f"Антидубли: {DUPLICATE_COOLDOWN_MINUTES} мин."
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

            print(
                "Ошибка автоматического сканирования:",
                error_text,
            )

            if notify_admin:
                await notify_admin_about_scan_error(
                    bot,
                    error_text,
                )

            return dict(_last_scan_result)

        strong_setups = [
            setup
            for setup in setups
            if setup.score >= minimum_score
        ]

        strong_setups.sort(
            key=lambda setup: (
                setup.score,
                setup.risk_reward,
            ),
            reverse=True,
        )

        candidate_limit = min(
            max(MAX_AUTO_SETUPS * 3, MAX_AUTO_SETUPS),
            max(MAX_RESULTS_TO_SHOW, MAX_AUTO_SETUPS),
        )

        candidate_setups = strong_setups[:candidate_limit]

        unique_setups = []
        duplicates = 0

        for setup in candidate_setups:
            if is_duplicate_setup(
                inst_id=setup.inst_id,
                direction=setup.direction,
            ):
                duplicates += 1

                print(
                    "Антидубль пропустил:",
                    setup.inst_id,
                    setup.direction,
                    f"score={setup.score}",
                )

                continue

            unique_setups.append(setup)

            if len(unique_setups) >= MAX_AUTO_SETUPS:
                break

        sent = 0

        for setup in unique_setups:
            delivered = await send_setup_to_admin(
                bot,
                setup,
            )

            if delivered:
                register_sent_setup(setup)
                sent += 1

            await asyncio.sleep(0.3)

        duration = time.monotonic() - started_monotonic
        _last_scan_finished_at = time.time()

        _last_scan_result = {
            "analysed": analysed_count,
            "found": len(strong_setups),
            "sent": sent,
            "duplicates": duplicates,
        }

        finish_scan_log(
            log_id=log_id,
            duration_seconds=duration,
            analysed=analysed_count,
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


async def wait_for_next_scan(
    interval_seconds: int,
) -> None:
    _stop_event.clear()

    try:
        await asyncio.wait_for(
            _stop_event.wait(),
            timeout=interval_seconds,
        )

    except asyncio.TimeoutError:
        pass

    finally:
        _stop_event.clear()


async def automatic_market_scanner(
    bot: Bot,
) -> None:
    await asyncio.sleep(30)

    print("Цикл автоматического сканера запущен")

    while True:
        try:
            if not is_autoscan_enabled():
                print(
                    "Автосканер выключен. "
                    "Ожидаю включения."
                )

                await wait_for_next_scan(60)
                continue

            await run_single_auto_scan(
                bot=bot,
                notify_admin=True,
            )

            interval_minutes = get_autoscan_interval_minutes()

            print(
                "Следующий автоскан через "
                f"{interval_minutes} мин."
            )

            await wait_for_next_scan(
                interval_minutes * 60
            )

        except asyncio.CancelledError:
            print(
                "Цикл автоматического сканера остановлен"
            )
            raise

        except Exception as error:
            print(
                "Критическая ошибка цикла автосканера:",
                error,
            )

            _last_scan_error = str(error)

            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                raise


create_antiduplicate_table()
