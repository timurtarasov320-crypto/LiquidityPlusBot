import asyncio
import re
import sqlite3
from datetime import datetime, timezone
from typing import Optional

import aiohttp

from database import (
    add_signal_event_by_source,
    close_trading_signal_by_source,
)
from signals import (
    SIGNALS_DB_NAME,
    get_signal_recipients,
)


OKX_API_URL = "https://www.okx.com"
CHECK_INTERVAL_SECONDS = 20


def connect_signals_db() -> sqlite3.Connection:
    connection = sqlite3.connect(SIGNALS_DB_NAME)
    connection.row_factory = sqlite3.Row
    return connection


def create_monitor_columns() -> None:
    with connect_signals_db() as connection:
        cursor = connection.cursor()
        cursor.execute("PRAGMA table_info(signals)")
        columns = {row["name"] for row in cursor.fetchall()}

        migrations = {
            "tp1_hit": (
                "ALTER TABLE signals "
                "ADD COLUMN tp1_hit INTEGER DEFAULT 0"
            ),
            "tp2_hit": (
                "ALTER TABLE signals "
                "ADD COLUMN tp2_hit INTEGER DEFAULT 0"
            ),
            "tp3_hit": (
                "ALTER TABLE signals "
                "ADD COLUMN tp3_hit INTEGER DEFAULT 0"
            ),
            "entry_price_numeric": (
                "ALTER TABLE signals "
                "ADD COLUMN entry_price_numeric REAL DEFAULT NULL"
            ),
            "last_checked_price": (
                "ALTER TABLE signals "
                "ADD COLUMN last_checked_price REAL DEFAULT NULL"
            ),
            "breakeven_active": (
                "ALTER TABLE signals "
                "ADD COLUMN breakeven_active INTEGER DEFAULT 0"
            ),
        }

        for column_name, sql in migrations.items():
            if column_name not in columns:
                cursor.execute(sql)

        connection.commit()


def parse_number(value: object) -> Optional[float]:
    if value is None:
        return None

    text = str(value).strip()
    text = text.replace("\u00a0", "")
    text = text.replace(" ", "")
    text = text.replace(",", ".")

    match = re.search(r"[-+]?\d+(?:\.\d+)?", text)

    if not match:
        return None

    try:
        return float(match.group(0))
    except ValueError:
        return None


def parse_entry_price(value: object) -> Optional[float]:
    if value is None:
        return None

    text = str(value)
    text = text.replace("\u00a0", " ")
    text = re.sub(r"[–—−]", "-", text)

    numbers = [
        float(item.replace(" ", "").replace(",", "."))
        for item in re.findall(
            r"\d[\d\s]*(?:[.,]\d+)?",
            text,
        )
    ]

    if not numbers:
        return None

    if len(numbers) == 1:
        return numbers[0]

    return sum(numbers[:2]) / 2


def symbol_to_okx_inst_id(symbol: str) -> str:
    normalized = (
        symbol.upper()
        .strip()
        .replace("/", "-")
        .replace("_", "-")
        .replace(" ", "")
    )

    if normalized.endswith("-SWAP"):
        return normalized

    if normalized.endswith("-USDT"):
        return f"{normalized}-SWAP"

    if "-" not in normalized:
        return f"{normalized}-USDT-SWAP"

    return normalized


async def get_okx_price(
    session: aiohttp.ClientSession,
    inst_id: str,
) -> Optional[float]:
    url = f"{OKX_API_URL}/api/v5/market/ticker"

    try:
        async with session.get(
            url,
            params={"instId": inst_id},
        ) as response:
            response.raise_for_status()
            payload = await response.json()

    except (
        aiohttp.ClientError,
        asyncio.TimeoutError,
        ValueError,
    ) as error:
        print(f"Ошибка цены {inst_id}: {error}")
        return None

    if str(payload.get("code")) != "0":
        print(
            f"OKX ошибка {inst_id}: "
            f"{payload.get('msg')}"
        )
        return None

    data = payload.get("data", [])

    if not data:
        return None

    try:
        price = float(data[0].get("last") or 0)
    except (TypeError, ValueError):
        return None

    return price if price > 0 else None


def get_active_signals() -> list[dict]:
    with connect_signals_db() as connection:
        rows = connection.execute(
            """
            SELECT
                signal_id,
                symbol,
                direction,
                entry,
                stop_loss,
                take_profit_1,
                take_profit_2,
                take_profit_3,
                status,
                result_percent,
                created_at,
                tp1_hit,
                tp2_hit,
                tp3_hit,
                entry_price_numeric,
                last_checked_price,
                breakeven_active
            FROM signals
            WHERE status = 'active'
            ORDER BY signal_id ASC
            """
        ).fetchall()

    return [dict(row) for row in rows]


def update_last_price(
    signal_id: int,
    price: float,
    entry_price: Optional[float],
) -> None:
    with connect_signals_db() as connection:
        connection.execute(
            """
            UPDATE signals
            SET
                last_checked_price = ?,
                entry_price_numeric = COALESCE(
                    entry_price_numeric,
                    ?
                )
            WHERE signal_id = ?
            """,
            (price, entry_price, signal_id),
        )
        connection.commit()


def mark_target_hit(
    signal_id: int,
    target_number: int,
) -> None:
    if target_number not in (1, 2, 3):
        return

    column_name = f"tp{target_number}_hit"

    with connect_signals_db() as connection:
        connection.execute(
            f"""
            UPDATE signals
            SET {column_name} = 1
            WHERE signal_id = ?
            """,
            (signal_id,),
        )
        connection.commit()


def activate_breakeven(signal_id: int) -> None:
    with connect_signals_db() as connection:
        connection.execute(
            """
            UPDATE signals
            SET breakeven_active = 1
            WHERE signal_id = ?
            """,
            (signal_id,),
        )
        connection.commit()


def close_monitored_signal(
    signal_id: int,
    status: str,
    result_percent: float,
) -> bool:
    with connect_signals_db() as connection:
        cursor = connection.execute(
            """
            UPDATE signals
            SET
                status = ?,
                result_percent = ?,
                closed_at = ?
            WHERE signal_id = ?
              AND status = 'active'
            """,
            (
                status,
                result_percent,
                datetime.now(timezone.utc).isoformat(),
                signal_id,
            ),
        )
        updated = cursor.rowcount > 0
        connection.commit()

    return updated


def calculate_result_percent(
    direction: str,
    entry_price: float,
    exit_price: float,
) -> float:
    if entry_price <= 0:
        return 0.0

    if direction.upper() == "LONG":
        return (
            (exit_price - entry_price)
            / entry_price
            * 100
        )

    return (
        (entry_price - exit_price)
        / entry_price
        * 100
    )


async def notify_signal_recipients(
    bot,
    signal_id: int,
    text: str,
) -> tuple[int, int]:
    recipients = get_signal_recipients(signal_id)
    sent = 0
    failed = 0

    for user_id, _access_type in recipients:
        try:
            await bot.send_message(
                chat_id=user_id,
                text=text,
            )
            sent += 1
        except Exception as error:
            failed += 1
            print(
                f"Ошибка уведомления {user_id}: {error}"
            )

        await asyncio.sleep(0.04)

    return sent, failed


def target_reached(
    direction: str,
    current_price: float,
    target_price: float,
) -> bool:
    if direction.upper() == "LONG":
        return current_price >= target_price

    return current_price <= target_price


def stop_reached(
    direction: str,
    current_price: float,
    stop_price: float,
) -> bool:
    if direction.upper() == "LONG":
        return current_price <= stop_price

    return current_price >= stop_price


async def process_signal(
    bot,
    session: aiohttp.ClientSession,
    signal: dict,
) -> None:
    signal_id = int(signal["signal_id"])
    symbol = str(signal["symbol"])
    direction = str(signal["direction"]).upper()

    current_price = await get_okx_price(
        session,
        symbol_to_okx_inst_id(symbol),
    )

    if current_price is None:
        return

    entry_price = (
        parse_number(signal["entry_price_numeric"])
        or parse_entry_price(signal["entry"])
    )
    original_stop = parse_number(signal["stop_loss"])
    tp1 = parse_number(signal["take_profit_1"])
    tp2 = parse_number(signal["take_profit_2"])
    tp3 = parse_number(signal["take_profit_3"])

    update_last_price(
        signal_id,
        current_price,
        entry_price,
    )

    if entry_price is None or original_stop is None:
        print(
            f"Сигнал #{signal_id}: "
            "не удалось разобрать вход или стоп"
        )
        return

    breakeven_active = bool(signal["breakeven_active"])
    effective_stop = (
        entry_price
        if breakeven_active
        else original_stop
    )

    if stop_reached(
        direction,
        current_price,
        effective_stop,
    ):
        if breakeven_active:
            closed = close_monitored_signal(
                signal_id,
                "breakeven",
                0.0,
            )

            if closed:
                close_trading_signal_by_source(
                    signal_id,
                    "be",
                )
                await notify_signal_recipients(
                    bot,
                    signal_id,
                    (
                        "━━━━━━━━━━━━━━━━━━━━\n"
                        f"  BREAKEVEN • #{signal_id}\n"
                        "━━━━━━━━━━━━━━━━━━━━\n\n"
                        f"Монета: {symbol}\n"
                        f"Направление: {direction}\n"
                        f"Цена выхода: {entry_price}\n"
                        "Результат: 0.00%"
                    ),
                )
        else:
            result_percent = calculate_result_percent(
                direction,
                entry_price,
                original_stop,
            )
            closed = close_monitored_signal(
                signal_id,
                "loss",
                result_percent,
            )

            if closed:
                close_trading_signal_by_source(
                    signal_id,
                    "sl",
                )
                await notify_signal_recipients(
                    bot,
                    signal_id,
                    (
                        "━━━━━━━━━━━━━━━━━━━━\n"
                        f"  STOP LOSS • #{signal_id}\n"
                        "━━━━━━━━━━━━━━━━━━━━\n\n"
                        f"Монета: {symbol}\n"
                        f"Направление: {direction}\n"
                        f"Цена стопа: {original_stop}\n"
                        f"Результат: "
                        f"{result_percent:+.2f}%"
                    ),
                )

        return

    tp1_hit = bool(signal["tp1_hit"])
    tp2_hit = bool(signal["tp2_hit"])
    tp3_hit = bool(signal["tp3_hit"])

    if (
        tp1 is not None
        and not tp1_hit
        and target_reached(
            direction,
            current_price,
            tp1,
        )
    ):
        mark_target_hit(signal_id, 1)
        activate_breakeven(signal_id)
        add_signal_event_by_source(
            signal_id,
            "tp1",
            str(tp1),
        )
        add_signal_event_by_source(
            signal_id,
            "be",
            str(entry_price),
        )

        result = calculate_result_percent(
            direction,
            entry_price,
            tp1,
        )

        await notify_signal_recipients(
            bot,
            signal_id,
            (
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"  TP1 REACHED • #{signal_id}\n"
                "━━━━━━━━━━━━━━━━━━━━\n\n"
                f"Монета: {symbol}\n"
                f"Цена TP1: {tp1}\n"
                f"Результат от входа: "
                f"{result:+.2f}%\n\n"
                "✅ Часть позиции можно зафиксировать.\n"
                "🛡 Stop Loss автоматически "
                "перенесён в безубыток."
            ),
        )

        tp1_hit = True
        breakeven_active = True

    if (
        tp2 is not None
        and not tp2_hit
        and target_reached(
            direction,
            current_price,
            tp2,
        )
    ):
        mark_target_hit(signal_id, 2)
        add_signal_event_by_source(
            signal_id,
            "tp2",
            str(tp2),
        )

        result = calculate_result_percent(
            direction,
            entry_price,
            tp2,
        )

        await notify_signal_recipients(
            bot,
            signal_id,
            (
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"  TP2 REACHED • #{signal_id}\n"
                "━━━━━━━━━━━━━━━━━━━━\n\n"
                f"Монета: {symbol}\n"
                f"Цена TP2: {tp2}\n"
                f"Результат от входа: "
                f"{result:+.2f}%"
            ),
        )

        tp2_hit = True

    if (
        tp3 is not None
        and not tp3_hit
        and target_reached(
            direction,
            current_price,
            tp3,
        )
    ):
        result_percent = calculate_result_percent(
            direction,
            entry_price,
            tp3,
        )
        mark_target_hit(signal_id, 3)
        add_signal_event_by_source(
            signal_id,
            "tp3",
            str(tp3),
        )

        closed = close_monitored_signal(
            signal_id,
            "win",
            result_percent,
        )

        if closed:
            close_trading_signal_by_source(
                signal_id,
                "tp3",
            )
            await notify_signal_recipients(
                bot,
                signal_id,
                (
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    f"  TARGET COMPLETE • #{signal_id}\n"
                    "━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"Монета: {symbol}\n"
                    f"TP3 достигнут: {tp3}\n"
                    f"Итоговый результат: "
                    f"{result_percent:+.2f}%"
                ),
            )

        return

    if (
        tp3 is None
        and tp2 is not None
        and tp2_hit
    ):
        result_percent = calculate_result_percent(
            direction,
            entry_price,
            tp2,
        )
        closed = close_monitored_signal(
            signal_id,
            "win",
            result_percent,
        )

        if closed:
            close_trading_signal_by_source(
                signal_id,
                "tp2",
            )
            await notify_signal_recipients(
                bot,
                signal_id,
                (
                    f"🏆 Сигнал #{signal_id} завершён\n\n"
                    f"Монета: {symbol}\n"
                    f"Последняя цель достигнута: {tp2}\n"
                    f"Итоговый результат: "
                    f"{result_percent:+.2f}%"
                ),
            )

        return

    if (
        tp2 is None
        and tp3 is None
        and tp1 is not None
        and tp1_hit
    ):
        result_percent = calculate_result_percent(
            direction,
            entry_price,
            tp1,
        )
        closed = close_monitored_signal(
            signal_id,
            "win",
            result_percent,
        )

        if closed:
            close_trading_signal_by_source(
                signal_id,
                "tp1",
            )
            await notify_signal_recipients(
                bot,
                signal_id,
                (
                    f"🏆 Сигнал #{signal_id} завершён\n\n"
                    f"Монета: {symbol}\n"
                    f"Цель достигнута: {tp1}\n"
                    f"Результат: "
                    f"{result_percent:+.2f}%"
                ),
            )


async def monitor_signals(bot) -> None:
    create_monitor_columns()

    timeout = aiohttp.ClientTimeout(total=15)

    print(
        "Автоматическое сопровождение сигналов "
        f"запущено (проверка каждые "
        f"{CHECK_INTERVAL_SECONDS} сек.)"
    )

    async with aiohttp.ClientSession(
        timeout=timeout
    ) as session:
        while True:
            try:
                for signal in get_active_signals():
                    try:
                        await process_signal(
                            bot,
                            session,
                            signal,
                        )
                    except Exception as error:
                        print(
                            "Ошибка сопровождения сигнала "
                            f"#{signal['signal_id']}: "
                            f"{error}"
                        )

                    await asyncio.sleep(0.15)

            except Exception as error:
                print(
                    f"Ошибка цикла мониторинга: {error}"
                )

            await asyncio.sleep(
                CHECK_INTERVAL_SECONDS
            )
