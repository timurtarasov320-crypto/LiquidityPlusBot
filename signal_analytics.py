import asyncio
import sqlite3
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from config import ADMIN_ID
from signals import SIGNALS_DB_NAME


KYIV_TZ = ZoneInfo("Europe/Kyiv")
ANALYTICS_STATE_DB = "signal_analytics.db"
DAILY_REPORT_HOUR = 23
DAILY_REPORT_MINUTE = 55


def connect_signals() -> sqlite3.Connection:
    connection = sqlite3.connect(SIGNALS_DB_NAME)
    connection.row_factory = sqlite3.Row
    return connection


def connect_state() -> sqlite3.Connection:
    connection = sqlite3.connect(ANALYTICS_STATE_DB)
    connection.row_factory = sqlite3.Row
    return connection


def create_analytics_tables() -> None:
    connection = connect_state()
    cursor = connection.cursor()

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS analytics_state (
            state_key TEXT PRIMARY KEY,
            state_value TEXT NOT NULL
        )
        """
    )

    connection.commit()
    connection.close()


def get_state(key: str) -> str | None:
    connection = connect_state()
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT state_value
        FROM analytics_state
        WHERE state_key = ?
        """,
        (key,),
    )

    row = cursor.fetchone()
    connection.close()

    return row["state_value"] if row else None


def set_state(key: str, value: str) -> None:
    connection = connect_state()
    cursor = connection.cursor()

    cursor.execute(
        """
        INSERT INTO analytics_state (
            state_key,
            state_value
        )
        VALUES (?, ?)
        ON CONFLICT(state_key)
        DO UPDATE SET
            state_value = excluded.state_value
        """,
        (
            key,
            value,
        ),
    )

    connection.commit()
    connection.close()


def utc_iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)

    return value.astimezone(timezone.utc).isoformat()


def get_period_bounds(period: str) -> tuple[datetime, datetime]:
    now_local = datetime.now(KYIV_TZ)

    if period == "day":
        start_local = now_local.replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )

    elif period == "week":
        start_local = (
            now_local
            - timedelta(days=now_local.weekday())
        ).replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )

    elif period == "month":
        start_local = now_local.replace(
            day=1,
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )

    else:
        raise ValueError("Неизвестный период.")

    return (
        start_local.astimezone(timezone.utc),
        now_local.astimezone(timezone.utc),
    )


def calculate_holding_seconds(
    created_at: str | None,
    closed_at: str | None,
) -> int | None:
    if not created_at:
        return None

    try:
        opened = datetime.fromisoformat(created_at)
    except ValueError:
        return None

    if opened.tzinfo is None:
        opened = opened.replace(tzinfo=timezone.utc)

    if closed_at:
        try:
            closed = datetime.fromisoformat(closed_at)
        except ValueError:
            return None

        if closed.tzinfo is None:
            closed = closed.replace(tzinfo=timezone.utc)
    else:
        closed = datetime.now(timezone.utc)

    return max(
        0,
        int((closed - opened).total_seconds()),
    )


def format_duration(seconds: float | int | None) -> str:
    if seconds is None:
        return "—"

    seconds = int(seconds)
    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, _ = divmod(remainder, 60)

    parts = []

    if days:
        parts.append(f"{days} д.")

    if hours:
        parts.append(f"{hours} ч.")

    if minutes:
        parts.append(f"{minutes} мин.")

    return " ".join(parts) if parts else "< 1 мин."


def get_period_signals(period: str) -> list[dict]:
    start_utc, end_utc = get_period_bounds(period)

    connection = connect_signals()
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT *
        FROM signals
        WHERE created_at >= ?
          AND created_at <= ?
        ORDER BY signal_id ASC
        """,
        (
            utc_iso(start_utc),
            utc_iso(end_utc),
        ),
    )

    rows = cursor.fetchall()
    connection.close()

    return [dict(row) for row in rows]


def get_all_closed_signals() -> list[dict]:
    connection = connect_signals()
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT *
        FROM signals
        WHERE status IN ('win', 'loss')
        ORDER BY signal_id ASC
        """
    )

    rows = cursor.fetchall()
    connection.close()

    return [dict(row) for row in rows]


def calculate_statistics(signals: list[dict]) -> dict:
    total = len(signals)

    wins = sum(
        1
        for signal in signals
        if signal.get("status") == "win"
    )

    losses = sum(
        1
        for signal in signals
        if signal.get("status") == "loss"
    )

    active = sum(
        1
        for signal in signals
        if signal.get("status") == "active"
    )

    closed = wins + losses

    results = [
        float(signal["result_percent"])
        for signal in signals
        if signal.get("result_percent") is not None
    ]

    scores = [
        float(signal["score"])
        for signal in signals
        if signal.get("score") is not None
    ]

    holding_times = []

    for signal in signals:
        if signal.get("status") not in ("win", "loss"):
            continue

        duration = calculate_holding_seconds(
            signal.get("created_at"),
            signal.get("closed_at"),
        )

        if duration is not None:
            holding_times.append(duration)

    long_count = sum(
        1
        for signal in signals
        if str(signal.get("direction", "")).upper() == "LONG"
    )

    short_count = sum(
        1
        for signal in signals
        if str(signal.get("direction", "")).upper() == "SHORT"
    )

    symbol_data: dict[str, dict] = {}

    for signal in signals:
        symbol = str(signal.get("symbol", "UNKNOWN"))

        data = symbol_data.setdefault(
            symbol,
            {
                "symbol": symbol,
                "total": 0,
                "wins": 0,
                "losses": 0,
                "result": 0.0,
            },
        )

        data["total"] += 1

        if signal.get("status") == "win":
            data["wins"] += 1

        if signal.get("status") == "loss":
            data["losses"] += 1

        if signal.get("result_percent") is not None:
            data["result"] += float(
                signal["result_percent"]
            )

    ranked_symbols = []

    for data in symbol_data.values():
        closed_symbol = data["wins"] + data["losses"]

        data["winrate"] = (
            data["wins"] / closed_symbol * 100
            if closed_symbol > 0
            else 0.0
        )

        ranked_symbols.append(data)

    ranked_symbols.sort(
        key=lambda item: (
            item["result"],
            item["winrate"],
        ),
        reverse=True,
    )

    best_symbol = (
        ranked_symbols[0]
        if ranked_symbols
        else None
    )

    worst_symbol = (
        ranked_symbols[-1]
        if ranked_symbols
        else None
    )

    return {
        "total": total,
        "wins": wins,
        "losses": losses,
        "active": active,
        "closed": closed,
        "winrate": (
            wins / closed * 100
            if closed > 0
            else 0.0
        ),
        "total_result": sum(results),
        "average_result": (
            sum(results) / len(results)
            if results
            else 0.0
        ),
        "average_score": (
            sum(scores) / len(scores)
            if scores
            else 0.0
        ),
        "average_holding_seconds": (
            sum(holding_times) / len(holding_times)
            if holding_times
            else None
        ),
        "long_count": long_count,
        "short_count": short_count,
        "best_symbol": best_symbol,
        "worst_symbol": worst_symbol,
        "symbols": ranked_symbols,
    }


def format_statistics(
    statistics: dict,
    title: str,
) -> str:
    best = statistics["best_symbol"]
    worst = statistics["worst_symbol"]

    best_text = (
        f"{best['symbol']} | "
        f"{best['result']:+.2f}% | "
        f"WR {best['winrate']:.1f}%"
        if best
        else "—"
    )

    worst_text = (
        f"{worst['symbol']} | "
        f"{worst['result']:+.2f}% | "
        f"WR {worst['winrate']:.1f}%"
        if worst
        else "—"
    )

    return (
        f"📊 {title}\n\n"
        f"Всего сигналов: {statistics['total']}\n"
        f"Активных: {statistics['active']}\n"
        f"Закрытых: {statistics['closed']}\n"
        f"Прибыльных: {statistics['wins']}\n"
        f"Убыточных: {statistics['losses']}\n"
        f"WinRate: {statistics['winrate']:.2f}%\n\n"
        f"Общий результат: "
        f"{statistics['total_result']:+.2f}%\n"
        f"Средний результат: "
        f"{statistics['average_result']:+.2f}%\n"
        f"Средний Score: "
        f"{statistics['average_score']:.1f}/100\n"
        f"Среднее время сделки: "
        f"{format_duration(statistics['average_holding_seconds'])}\n\n"
        f"LONG: {statistics['long_count']}\n"
        f"SHORT: {statistics['short_count']}\n\n"
        f"Лучшая монета:\n{best_text}\n\n"
        f"Худшая монета:\n{worst_text}"
    )


def get_statistics_for_period(period: str) -> dict:
    return calculate_statistics(
        get_period_signals(period)
    )


def get_best_coins(limit: int = 10) -> list[dict]:
    statistics = calculate_statistics(
        get_all_closed_signals()
    )

    safe_limit = max(1, min(int(limit), 50))

    return statistics["symbols"][:safe_limit]


async def daily_analytics_report(bot) -> None:
    create_analytics_tables()

    while True:
        now = datetime.now(KYIV_TZ)

        scheduled = now.replace(
            hour=DAILY_REPORT_HOUR,
            minute=DAILY_REPORT_MINUTE,
            second=0,
            microsecond=0,
        )

        if scheduled <= now:
            scheduled += timedelta(days=1)

        await asyncio.sleep(
            max(
                1,
                (scheduled - now).total_seconds(),
            )
        )

        report_date = datetime.now(KYIV_TZ).strftime(
            "%Y-%m-%d"
        )

        if get_state("last_daily_report") == report_date:
            await asyncio.sleep(60)
            continue

        try:
            statistics = get_statistics_for_period(
                "day"
            )

            await bot.send_message(
                chat_id=ADMIN_ID,
                text=format_statistics(
                    statistics,
                    "Итоги дня",
                ),
            )

            set_state(
                "last_daily_report",
                report_date,
            )

        except asyncio.CancelledError:
            raise

        except Exception as error:
            print(
                "Ошибка ежедневной аналитики:",
                error,
            )

        await asyncio.sleep(60)


create_analytics_tables()