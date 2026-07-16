import asyncio
import sqlite3
from datetime import datetime, timezone
from typing import Optional

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from config import ADMIN_ID
from database import get_all_users
from free_signals import (
    FREE_SIGNALS_LIMIT,
    can_receive_free_signal,
    get_remaining_free_signals,
    register_free_signal,
)

router = Router()

SIGNALS_DB_NAME = "signals.db"


def is_admin(user_id: int) -> bool:
    return int(user_id) == int(ADMIN_ID)


def connect_signals_db() -> sqlite3.Connection:
    connection = sqlite3.connect(SIGNALS_DB_NAME)
    connection.row_factory = sqlite3.Row
    return connection


def create_signal_tables() -> None:
    connection = connect_signals_db()
    cursor = connection.cursor()

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS signals (
            signal_id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            direction TEXT NOT NULL,
            entry TEXT NOT NULL,
            stop_loss TEXT NOT NULL,
            take_profit_1 TEXT,
            take_profit_2 TEXT,
            take_profit_3 TEXT,
            risk TEXT,
            comment TEXT,
            status TEXT NOT NULL DEFAULT 'active',
            result_percent REAL DEFAULT NULL,
            created_at TEXT NOT NULL,
            closed_at TEXT DEFAULT NULL
        )
        """
    )

    cursor.execute("PRAGMA table_info(signals)")
    signal_columns = {
        row["name"]
        for row in cursor.fetchall()
    }

    if "score" not in signal_columns:
        cursor.execute(
            "ALTER TABLE signals "
            "ADD COLUMN score INTEGER DEFAULT NULL"
        )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS signal_recipients (
            signal_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            access_type TEXT NOT NULL,
            sent_at TEXT NOT NULL,
            PRIMARY KEY (signal_id, user_id)
        )
        """
    )

    connection.commit()
    connection.close()


def create_signal(
    symbol: str,
    direction: str,
    entry: str,
    stop_loss: str,
    take_profit_1: str,
    take_profit_2: Optional[str],
    take_profit_3: Optional[str],
    risk: Optional[str],
    comment: Optional[str],
    score: Optional[int] = None,
) -> int:
    connection = connect_signals_db()
    cursor = connection.cursor()

    cursor.execute(
        """
        INSERT INTO signals (
            symbol,
            direction,
            entry,
            stop_loss,
            take_profit_1,
            take_profit_2,
            take_profit_3,
            risk,
            comment,
            score,
            status,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?)
        """,
        (
            symbol.upper(),
            direction.upper(),
            entry,
            stop_loss,
            take_profit_1,
            take_profit_2,
            take_profit_3,
            risk,
            comment,
            (
                max(0, min(int(score), 100))
                if score is not None
                else None
            ),
            datetime.now(timezone.utc).isoformat(),
        ),
    )

    signal_id = int(cursor.lastrowid)

    connection.commit()
    connection.close()

    return signal_id


def get_signal(signal_id: int):
    connection = connect_signals_db()
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT *
        FROM signals
        WHERE signal_id = ?
        """,
        (signal_id,),
    )

    signal = cursor.fetchone()
    connection.close()

    if signal is None:
        return None

    return dict(signal)


def get_recent_signals(limit: int = 10):
    connection = connect_signals_db()
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT *
        FROM signals
        ORDER BY signal_id DESC
        LIMIT ?
        """,
        (limit,),
    )

    signals = cursor.fetchall()
    connection.close()

    return [dict(signal) for signal in signals]


def save_signal_recipient(
    signal_id: int,
    user_id: int,
    access_type: str,
) -> None:
    connection = connect_signals_db()
    cursor = connection.cursor()

    cursor.execute(
        """
        INSERT OR IGNORE INTO signal_recipients (
            signal_id,
            user_id,
            access_type,
            sent_at
        )
        VALUES (?, ?, ?, ?)
        """,
        (
            signal_id,
            user_id,
            access_type,
            datetime.now(timezone.utc).isoformat(),
        ),
    )

    connection.commit()
    connection.close()


def get_signal_recipients(signal_id: int):
    connection = connect_signals_db()
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT
            user_id,
            access_type
        FROM signal_recipients
        WHERE signal_id = ?
        """,
        (signal_id,),
    )

    recipients = cursor.fetchall()
    connection.close()

    return [
        (
            int(recipient["user_id"]),
            str(recipient["access_type"]),
        )
        for recipient in recipients
    ]


def close_signal(
    signal_id: int,
    status: str,
    result_percent: float,
) -> bool:
    connection = connect_signals_db()
    cursor = connection.cursor()

    cursor.execute(
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
    connection.close()

    return updated


def get_signal_statistics() -> dict:
    connection = connect_signals_db()
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT
            COUNT(*) AS total,
            SUM(
                CASE
                    WHEN status = 'win' THEN 1
                    ELSE 0
                END
            ) AS wins,
            SUM(
                CASE
                    WHEN status = 'loss' THEN 1
                    ELSE 0
                END
            ) AS losses,
            SUM(
                CASE
                    WHEN status = 'active' THEN 1
                    ELSE 0
                END
            ) AS active,
            SUM(
                CASE
                    WHEN status = 'breakeven' THEN 1
                    ELSE 0
                END
            ) AS breakeven,
            COALESCE(SUM(result_percent), 0) AS total_result
        FROM signals
        """
    )

    result = cursor.fetchone()
    connection.close()

    total = int(result["total"] or 0)
    wins = int(result["wins"] or 0)
    losses = int(result["losses"] or 0)
    active = int(result["active"] or 0)
    breakeven = int(result["breakeven"] or 0)
    total_result = float(result["total_result"] or 0)

    closed_signals = wins + losses

    winrate = (
        wins / closed_signals * 100
        if closed_signals > 0
        else 0
    )

    return {
        "total": total,
        "wins": wins,
        "losses": losses,
        "active": active,
        "breakeven": breakeven,
        "winrate": winrate,
        "total_result": total_result,
    }



def normalize_symbol_query(value: str) -> str:
    normalized = (
        value.upper()
        .strip()
        .replace("-", "/")
        .replace("_", "/")
    )

    if "/" not in normalized:
        normalized = f"{normalized}/USDT"

    return normalized


def get_signal_history(
    limit: int = 20,
    symbol: Optional[str] = None,
) -> list[dict]:
    safe_limit = max(1, min(int(limit), 100))

    connection = connect_signals_db()
    cursor = connection.cursor()

    if symbol:
        normalized_symbol = normalize_symbol_query(symbol)

        cursor.execute(
            """
            SELECT *
            FROM signals
            WHERE UPPER(symbol) = UPPER(?)
            ORDER BY signal_id DESC
            LIMIT ?
            """,
            (
                normalized_symbol,
                safe_limit,
            ),
        )
    else:
        cursor.execute(
            """
            SELECT *
            FROM signals
            ORDER BY signal_id DESC
            LIMIT ?
            """,
            (safe_limit,),
        )

    rows = cursor.fetchall()
    connection.close()

    return [dict(row) for row in rows]


def parse_iso_datetime(
    value: Optional[str],
) -> Optional[datetime]:
    if not value:
        return None

    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def format_signal_datetime(
    value: Optional[str],
) -> str:
    parsed = parse_iso_datetime(value)

    if parsed is None:
        return "—"

    return parsed.strftime("%d.%m.%Y %H:%M:%S UTC")


def calculate_holding_seconds(signal: dict) -> Optional[int]:
    created_at = parse_iso_datetime(
        signal.get("created_at")
    )

    if created_at is None:
        return None

    closed_at = parse_iso_datetime(
        signal.get("closed_at")
    )

    end_time = closed_at or datetime.now(timezone.utc)

    if created_at.tzinfo is None:
        created_at = created_at.replace(
            tzinfo=timezone.utc
        )

    if end_time.tzinfo is None:
        end_time = end_time.replace(
            tzinfo=timezone.utc
        )

    return max(
        0,
        int((end_time - created_at).total_seconds()),
    )


def format_duration(seconds: Optional[int]) -> str:
    if seconds is None:
        return "—"

    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds_left = divmod(remainder, 60)

    parts = []

    if days:
        parts.append(f"{days} д.")

    if hours:
        parts.append(f"{hours} ч.")

    if minutes:
        parts.append(f"{minutes} мин.")

    if not parts:
        parts.append(f"{seconds_left} сек.")

    return " ".join(parts)


def get_symbol_statistics(symbol: str) -> dict:
    normalized_symbol = normalize_symbol_query(symbol)

    connection = connect_signals_db()
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT
            COUNT(*) AS total,
            SUM(
                CASE WHEN status = 'win'
                THEN 1 ELSE 0 END
            ) AS wins,
            SUM(
                CASE WHEN status = 'loss'
                THEN 1 ELSE 0 END
            ) AS losses,
            SUM(
                CASE WHEN status = 'active'
                THEN 1 ELSE 0 END
            ) AS active,
            COALESCE(SUM(result_percent), 0) AS total_result,
            COALESCE(AVG(
                CASE
                    WHEN status IN ('win', 'loss')
                    THEN result_percent
                END
            ), 0) AS average_result,
            COALESCE(AVG(score), 0) AS average_score
        FROM signals
        WHERE UPPER(symbol) = UPPER(?)
        """,
        (normalized_symbol,),
    )

    row = cursor.fetchone()
    connection.close()

    wins = int(row["wins"] or 0)
    losses = int(row["losses"] or 0)
    closed = wins + losses

    return {
        "symbol": normalized_symbol,
        "total": int(row["total"] or 0),
        "wins": wins,
        "losses": losses,
        "active": int(row["active"] or 0),
        "winrate": (
            wins / closed * 100
            if closed > 0
            else 0.0
        ),
        "total_result": float(
            row["total_result"] or 0
        ),
        "average_result": float(
            row["average_result"] or 0
        ),
        "average_score": float(
            row["average_score"] or 0
        ),
    }

def format_signal(signal: dict) -> str:
    direction = signal["direction"].upper()

    direction_text = (
        "🟢 LONG"
        if direction == "LONG"
        else "🔴 SHORT"
    )

    lines = [
        f"📈 ТОРГОВЫЙ СИГНАЛ #{signal['signal_id']}",
        "",
        f"Монета: {signal['symbol']}",
        f"Направление: {direction_text}",
    ]

    if signal.get("score") is not None:
        lines.append(
            f"Оценка сетапа: {int(signal['score'])}/100"
        )

    lines.extend(
        [
            "",
            f"🎯 Вход: {signal['entry']}",
        f"🛑 Стоп: {signal['stop_loss']}",
            f"✅ TP1: {signal['take_profit_1']}",
        ]
    )

    if signal.get("take_profit_2"):
        lines.append(
            f"✅ TP2: {signal['take_profit_2']}"
        )

    if signal.get("take_profit_3"):
        lines.append(
            f"✅ TP3: {signal['take_profit_3']}"
        )

    if signal.get("risk"):
        lines.extend(
            [
                "",
                f"⚠️ Риск: {signal['risk']}",
            ]
        )

    if signal.get("comment"):
        lines.extend(
            [
                "",
                f"📝 Комментарий:\n{signal['comment']}",
            ]
        )

    lines.extend(
        [
            "",
            "Не превышайте допустимый риск.",
        ]
    )

    return "\n".join(lines)


def signal_admin_keyboard(
    signal_id: int,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Закрыть в плюс",
                    callback_data=f"signal_win:{signal_id}",
                ),
                InlineKeyboardButton(
                    text="❌ Закрыть в минус",
                    callback_data=f"signal_loss:{signal_id}",
                ),
            ]
        ]
    )


def signal_user_keyboard(symbol: str) -> InlineKeyboardMarkup:
    normalized = symbol.upper().replace("/", "-")
    base = normalized.split("-", 1)[0]

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📈 Открыть OKX",
                    url=f"https://www.okx.com/trade-swap/{base}-USDT-SWAP",
                ),
                InlineKeyboardButton(
                    text="📊 TradingView",
                    url=f"https://www.tradingview.com/chart/?symbol=OKX:{base}USDT.P",
                ),
            ]
        ]
    )


async def send_signal_to_users(
    bot,
    signal: dict,
    audience: str = "all",
) -> dict[str, int]:
    users = get_all_users()
    audience = audience.lower().strip()

    if audience not in {"all", "vip", "free"}:
        raise ValueError("audience must be: all, vip or free")

    vip_sent = 0
    free_sent = 0
    limit_exhausted = 0
    failed = 0

    signal_text = format_signal(signal)
    signal_id = int(signal["signal_id"])

    for user in users:
        user_id = int(user[0])
        vip_status = bool(user[4])
        blocked = bool(len(user) > 10 and user[10])

        if blocked:
            continue

        if audience == "vip" and not vip_status:
            continue

        if audience == "free" and vip_status:
            continue

        if vip_status:
            try:
                await bot.send_message(
                    chat_id=user_id,
                    text=(
                        "💎 VIP-ДОСТУП\n\n"
                        f"{signal_text}"
                    ),
                    reply_markup=signal_user_keyboard(signal["symbol"]),
                )

                save_signal_recipient(
                    signal_id=signal_id,
                    user_id=user_id,
                    access_type="vip",
                )

                vip_sent += 1

            except Exception:
                failed += 1

            await asyncio.sleep(0.05)
            continue

        if not can_receive_free_signal(user_id):
            limit_exhausted += 1
            continue

        try:
            remaining_before = get_remaining_free_signals(
                user_id
            )

            await bot.send_message(
                chat_id=user_id,
                text=(
                    "🎁 БЕСПЛАТНЫЙ СИГНАЛ\n\n"
                    f"{signal_text}\n\n"
                    f"Осталось бесплатных сигналов "
                    f"после этого: "
                    f"{max(0, remaining_before - 1)} "
                    f"из {FREE_SIGNALS_LIMIT}.\n\n"
                    "Оформите VIP, чтобы получать "
                    "все сигналы без ограничений."
                ),
                reply_markup=signal_user_keyboard(signal["symbol"]),
            )

            registered = register_free_signal(user_id)

            if registered:
                save_signal_recipient(
                    signal_id=signal_id,
                    user_id=user_id,
                    access_type="free",
                )

                free_sent += 1
            else:
                failed += 1

        except Exception:
            failed += 1

        await asyncio.sleep(0.05)

    return {
        "vip_sent": vip_sent,
        "free_sent": free_sent,
        "limit_exhausted": limit_exhausted,
        "failed": failed,
    }


async def send_signal_result(
    bot,
    signal_id: int,
    signal: dict,
    result_percent: float,
    status: str,
) -> tuple[int, int]:
    recipients = get_signal_recipients(signal_id)

    sent = 0
    failed = 0

    result_icon = (
        "✅"
        if status == "win"
        else "❌"
    )

    result_name = (
        "закрыт в плюс"
        if status == "win"
        else "закрыт в минус"
    )

    for user_id, access_type in recipients:
        access_text = (
            "VIP-сигнал"
            if access_type == "vip"
            else "Бесплатный сигнал"
        )

        try:
            await bot.send_message(
                chat_id=user_id,
                text=(
                    f"{result_icon} {access_text} "
                    f"#{signal_id} {result_name}\n\n"
                    f"Монета: {signal['symbol']}\n"
                    f"Результат: {result_percent:+.2f}%"
                ),
            )

            sent += 1

        except Exception:
            failed += 1

        await asyncio.sleep(0.05)

    return sent, failed


@router.message(Command("newsignal"))
async def new_signal(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("Нет доступа.")
        return

    raw_text = message.text.partition(" ")[2].strip()

    if not raw_text:
        await message.answer(
            "Использование:\n\n"
            "/newsignal МОНЕТА | LONG/SHORT | ВХОД | СТОП | "
            "TP1 | TP2 | TP3 | РИСК | КОММЕНТАРИЙ | РЕЙТИНГ\n\n"
            "Пример:\n"
            "/newsignal BTC/USDT | LONG | 65000-65500 | "
            "64200 | 66500 | 67500 | 69000 | 1% | "
            "Вход после подтверждения"
        )
        return

    parts = [
        part.strip()
        for part in raw_text.split("|")
    ]

    if len(parts) < 5:
        await message.answer(
            "Недостаточно данных.\n\n"
            "Минимальный формат:\n"
            "/newsignal МОНЕТА | LONG/SHORT | "
            "ВХОД | СТОП | TP1"
        )
        return

    symbol = parts[0]
    direction = parts[1].upper()
    entry = parts[2]
    stop_loss = parts[3]
    take_profit_1 = parts[4]

    take_profit_2 = (
        parts[5]
        if len(parts) > 5 and parts[5]
        else None
    )

    take_profit_3 = (
        parts[6]
        if len(parts) > 6 and parts[6]
        else None
    )

    risk = (
        parts[7]
        if len(parts) > 7 and parts[7]
        else None
    )

    comment = (
        parts[8]
        if len(parts) > 8 and parts[8]
        else None
    )

    score = None

    if len(parts) > 9 and parts[9]:
        try:
            score = int(parts[9])
        except ValueError:
            await message.answer(
                "Рейтинг должен быть целым числом от 0 до 100."
            )
            return

        if not 0 <= score <= 100:
            await message.answer(
                "Рейтинг должен быть от 0 до 100."
            )
            return

    if direction not in ("LONG", "SHORT"):
        await message.answer(
            "Направление должно быть LONG или SHORT."
        )
        return

    signal_id = create_signal(
        symbol=symbol,
        direction=direction,
        entry=entry,
        stop_loss=stop_loss,
        take_profit_1=take_profit_1,
        take_profit_2=take_profit_2,
        take_profit_3=take_profit_3,
        risk=risk,
        comment=comment,
        score=score,
    )

    signal = get_signal(signal_id)

    if signal is None:
        await message.answer(
            "Не удалось создать сигнал."
        )
        return

    await message.answer(
        "Сигнал создан. Начинаю рассылку."
    )

    result = await send_signal_to_users(
        message.bot,
        signal,
    )

    await message.answer(
        format_signal(signal),
        reply_markup=signal_admin_keyboard(signal_id),
    )

    await message.answer(
        "✅ Рассылка сигнала завершена\n\n"
        f"Отправлено VIP: {result['vip_sent']}\n"
        f"Отправлено бесплатно: {result['free_sent']}\n"
        f"Лимит закончился: {result['limit_exhausted']}\n"
        f"Ошибок доставки: {result['failed']}"
    )



@router.message(Command("history"))
async def detailed_signal_history(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("Нет доступа.")
        return

    argument = message.text.partition(" ")[2].strip()
    limit = 20
    symbol = None

    if argument:
        if argument.isdigit():
            limit = max(1, min(int(argument), 100))
        else:
            symbol = argument

    signals = get_signal_history(
        limit=limit,
        symbol=symbol,
    )

    if not signals:
        await message.answer(
            "История сигналов не найдена."
        )
        return

    title = (
        f"📈 История {normalize_symbol_query(symbol)}"
        if symbol
        else "📈 История сигналов"
    )

    lines = [title, ""]

    for signal in signals:
        status_names = {
            "active": "⏳ Активен",
            "win": "✅ Плюс",
            "loss": "❌ Минус",
        }

        result = signal.get("result_percent")
        result_text = (
            f"{float(result):+.2f}%"
            if result is not None
            else "—"
        )

        score = signal.get("score")
        score_text = (
            f"{int(score)}/100"
            if score is not None
            else "—"
        )

        holding = format_duration(
            calculate_holding_seconds(signal)
        )

        lines.append(
            f"#{signal['signal_id']} | "
            f"{signal['symbol']} | "
            f"{signal['direction']}\n"
            f"{status_names.get(signal['status'], signal['status'])} | "
            f"Результат: {result_text} | "
            f"Score: {score_text}\n"
            f"Открыт: "
            f"{format_signal_datetime(signal.get('created_at'))}\n"
            f"Длительность: {holding}\n"
        )

    await message.answer("\n".join(lines))


@router.message(Command("signalinfo"))
async def signal_information(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("Нет доступа.")
        return

    parts = message.text.split()

    if len(parts) != 2:
        await message.answer(
            "Использование:\n"
            "/signalinfo ID"
        )
        return

    try:
        signal_id = int(parts[1])
    except ValueError:
        await message.answer(
            "ID сигнала должен быть числом."
        )
        return

    signal = get_signal(signal_id)

    if signal is None:
        await message.answer(
            "Сигнал не найден."
        )
        return

    status_names = {
        "active": "⏳ Активен",
        "win": "✅ Закрыт в плюс",
        "loss": "❌ Закрыт в минус",
    }

    result = signal.get("result_percent")
    result_text = (
        f"{float(result):+.2f}%"
        if result is not None
        else "—"
    )

    score = signal.get("score")
    score_text = (
        f"{int(score)}/100"
        if score is not None
        else "—"
    )

    recipients = get_signal_recipients(signal_id)
    vip_count = sum(
        1
        for _, access_type in recipients
        if access_type == "vip"
    )
    free_count = sum(
        1
        for _, access_type in recipients
        if access_type == "free"
    )

    await message.answer(
        "📋 Информация о сигнале\n\n"
        f"ID: #{signal['signal_id']}\n"
        f"Монета: {signal['symbol']}\n"
        f"Направление: {signal['direction']}\n"
        f"Статус: "
        f"{status_names.get(signal['status'], signal['status'])}\n"
        f"Рейтинг: {score_text}\n\n"
        f"Вход: {signal['entry']}\n"
        f"Стоп: {signal['stop_loss']}\n"
        f"TP1: {signal['take_profit_1'] or '—'}\n"
        f"TP2: {signal['take_profit_2'] or '—'}\n"
        f"TP3: {signal['take_profit_3'] or '—'}\n"
        f"Риск: {signal['risk'] or '—'}\n\n"
        f"Результат: {result_text}\n"
        f"Открыт: "
        f"{format_signal_datetime(signal.get('created_at'))}\n"
        f"Закрыт: "
        f"{format_signal_datetime(signal.get('closed_at'))}\n"
        f"Время сделки: "
        f"{format_duration(calculate_holding_seconds(signal))}\n\n"
        f"Получили VIP: {vip_count}\n"
        f"Получили бесплатно: {free_count}\n\n"
        f"Комментарий:\n"
        f"{signal['comment'] or '—'}"
    )


@router.message(Command("coinstat"))
async def coin_statistics(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("Нет доступа.")
        return

    argument = message.text.partition(" ")[2].strip()

    if not argument:
        await message.answer(
            "Использование:\n"
            "/coinstat BTC"
        )
        return

    statistics = get_symbol_statistics(argument)

    if statistics["total"] == 0:
        await message.answer(
            "По этой монете сигналов пока нет."
        )
        return

    await message.answer(
        "📊 Статистика монеты\n\n"
        f"Монета: {statistics['symbol']}\n"
        f"Всего сигналов: {statistics['total']}\n"
        f"Активных: {statistics['active']}\n"
        f"Прибыльных: {statistics['wins']}\n"
        f"Убыточных: {statistics['losses']}\n"
        f"WinRate: {statistics['winrate']:.2f}%\n"
        f"Общий результат: "
        f"{statistics['total_result']:+.2f}%\n"
        f"Средний результат: "
        f"{statistics['average_result']:+.2f}%\n"
        f"Средний рейтинг: "
        f"{statistics['average_score']:.1f}/100"
    )

@router.message(Command("signals"))
async def signals_history(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("Нет доступа.")
        return

    signals = get_recent_signals(10)

    if not signals:
        await message.answer(
            "Сигналов пока нет."
        )
        return

    lines = ["📋 Последние сигналы\n"]

    for signal in signals:
        status = signal["status"]

        if status == "active":
            status_text = "⏳ Активен"
        elif status == "win":
            status_text = "✅ Плюс"
        else:
            status_text = "❌ Минус"

        result = signal["result_percent"]

        result_text = (
            f"{float(result):+.2f}%"
            if result is not None
            else "—"
        )

        lines.append(
            f"#{signal['signal_id']} | "
            f"{signal['symbol']} | "
            f"{signal['direction']} | "
            f"{status_text} | {result_text}"
        )

    await message.answer("\n".join(lines))


@router.message(Command("signalstat"))
async def signal_statistics(message: Message):
    statistics = get_signal_statistics()

    await message.answer(
        "📊 Статистика сигналов\n\n"
        f"Всего сигналов: {statistics['total']}\n"
        f"Активных: {statistics['active']}\n"
        f"Прибыльных: {statistics['wins']}\n"
        f"Убыточных: {statistics['losses']}\n"
        f"Винрейт: {statistics['winrate']:.2f}%\n"
        f"Общий результат: "
        f"{statistics['total_result']:+.2f}%"
    )


@router.message(Command("closesignal"))
async def close_signal_command(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("Нет доступа.")
        return

    parts = message.text.split()

    if len(parts) != 4:
        await message.answer(
            "Использование:\n"
            "/closesignal ID win 3.5\n"
            "/closesignal ID loss -1.0"
        )
        return

    try:
        signal_id = int(parts[1])
        status = parts[2].lower()
        result_percent = float(
            parts[3].replace(",", ".")
        )
    except ValueError:
        await message.answer(
            "Неправильный формат данных."
        )
        return

    if status not in ("win", "loss"):
        await message.answer(
            "Статус должен быть win или loss."
        )
        return

    signal = get_signal(signal_id)

    if signal is None:
        await message.answer(
            "Сигнал не найден."
        )
        return

    if signal["status"] != "active":
        await message.answer(
            "Этот сигнал уже закрыт."
        )
        return

    updated = close_signal(
        signal_id=signal_id,
        status=status,
        result_percent=result_percent,
    )

    if not updated:
        await message.answer(
            "Не удалось закрыть сигнал."
        )
        return

    sent, failed = await send_signal_result(
        bot=message.bot,
        signal_id=signal_id,
        signal=signal,
        result_percent=result_percent,
        status=status,
    )

    await message.answer(
        "✅ Сигнал закрыт\n\n"
        f"Результат: {result_percent:+.2f}%\n"
        f"Уведомлено пользователей: {sent}\n"
        f"Ошибок доставки: {failed}"
    )


@router.callback_query(
    F.data.startswith("signal_win:")
)
async def signal_win_callback(
    callback: CallbackQuery,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(
            "Нет доступа.",
            show_alert=True,
        )
        return

    signal_id = int(
        callback.data.split(":")[1]
    )

    await callback.answer()

    await callback.message.answer(
        "Чтобы закрыть сигнал в плюс, отправьте:\n\n"
        f"/closesignal {signal_id} win 3.5\n\n"
        "Вместо 3.5 укажите реальный результат."
    )


@router.callback_query(
    F.data.startswith("signal_loss:")
)
async def signal_loss_callback(
    callback: CallbackQuery,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(
            "Нет доступа.",
            show_alert=True,
        )
        return

    signal_id = int(
        callback.data.split(":")[1]
    )

    await callback.answer()

    await callback.message.answer(
        "Чтобы закрыть сигнал в минус, отправьте:\n\n"
        f"/closesignal {signal_id} loss -1\n\n"
        "Вместо -1 укажите реальный результат."
    )


create_signal_tables()

def update_signal_field(
    signal_id: int,
    field_name: str,
    value: str | None,
) -> bool:
    allowed_fields = {
        "entry",
        "stop_loss",
        "take_profit_1",
        "take_profit_2",
        "take_profit_3",
        "risk",
        "comment",
    }

    if field_name not in allowed_fields:
        raise ValueError("Недопустимое поле сигнала.")

    with connect() as connection:
        cursor = connection.execute(
            f"""
            UPDATE signals
            SET {field_name} = ?
            WHERE signal_id = ?
              AND status = 'active'
            """,
            (value, signal_id),
        )
        connection.commit()

    return cursor.rowcount > 0


def get_signal_recipient_ids(signal_id: int) -> list[int]:
    return [
        int(user_id)
        for user_id, _access_type
        in get_signal_recipients(signal_id)
    ]


def get_user_signal_statistics(user_id: int) -> dict:
    connection = connect_signals_db()
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN s.status = 'win' THEN 1 ELSE 0 END) AS wins,
            SUM(CASE WHEN s.status = 'loss' THEN 1 ELSE 0 END) AS losses,
            SUM(CASE WHEN s.status = 'breakeven' THEN 1 ELSE 0 END) AS breakeven,
            SUM(CASE WHEN s.status = 'active' THEN 1 ELSE 0 END) AS active,
            COALESCE(SUM(s.result_percent), 0) AS total_result
        FROM signal_recipients r
        JOIN signals s ON s.signal_id = r.signal_id
        WHERE r.user_id = ?
        """,
        (user_id,),
    )

    row = cursor.fetchone()
    connection.close()

    total = int(row["total"] or 0)
    wins = int(row["wins"] or 0)
    losses = int(row["losses"] or 0)
    breakeven = int(row["breakeven"] or 0)
    active = int(row["active"] or 0)
    total_result = float(row["total_result"] or 0)

    resolved = wins + losses
    winrate = wins / resolved * 100 if resolved else 0.0

    return {
        "total": total,
        "wins": wins,
        "losses": losses,
        "breakeven": breakeven,
        "active": active,
        "winrate": winrate,
        "total_result": total_result,
    }


def get_user_signal_history(
    user_id: int,
    limit: int = 10,
) -> list[dict]:
    safe_limit = max(1, min(int(limit), 50))

    connection = connect_signals_db()
    cursor = connection.cursor()
    cursor.execute(
        """
        SELECT s.*
        FROM signal_recipients r
        JOIN signals s ON s.signal_id = r.signal_id
        WHERE r.user_id = ?
        ORDER BY s.signal_id DESC
        LIMIT ?
        """,
        (user_id, safe_limit),
    )

    rows = cursor.fetchall()
    connection.close()
    return [dict(row) for row in rows]
