import asyncio
from datetime import datetime

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from auto_scanner import (
    DUPLICATE_COOLDOWN_MINUTES,
    clear_antiduplicates,
    get_last_scan_error,
    get_last_scan_finished_at,
    get_last_scan_result,
    get_last_scan_started_at,
    get_recent_sent_setups,
    is_auto_scan_running,
    run_single_auto_scan,
    wake_auto_scanner,
)
from autoscan_logs import (
    clear_autoscan_logs,
    get_autoscan_log,
    get_autoscan_logs,
    get_autoscan_statistics,
)
from autoscan_settings import (
    get_all_autoscan_settings,
    set_autoscan_enabled,
    set_autoscan_interval_minutes,
    set_minimum_autoscan_score,
)
from config import ADMIN_ID


router = Router()


async def show_scanner_progress(message: Message) -> Message:
    steps = [
        ("🔍 Поиск ликвидных монет...", 15),
        ("📊 Анализ Funding и OI...", 32),
        ("📈 Проверка RSI и объёмов...", 48),
        ("💧 Поиск ликвидности...", 62),
        ("📚 Анализ FVG и Order Blocks...", 76),
        ("🧠 Расчёт AI Score...", 92),
        ("✅ Анализ завершён", 100),
    ]

    progress_message = await message.answer(
        "🤖 Запуск AI Scanner..."
    )

    for title, percent in steps:
        filled = round(percent / 10)
        bar = "█" * filled + "░" * (10 - filled)

        try:
            await progress_message.edit_text(
                "━━━━━━━━━━━━━━━━━━━━\n\n"
                "🤖 LIQUIDITY AI SCANNER\n\n"
                f"{title}\n\n"
                f"{bar} {percent}%\n\n"
                "━━━━━━━━━━━━━━━━━━━━"
            )
        except Exception:
            pass

        await asyncio.sleep(0.30)

    return progress_message


def is_admin(user_id: int) -> bool:
    return int(user_id) == int(ADMIN_ID)


def format_timestamp(value: float | None) -> str:
    if value is None:
        return "ещё не было"

    return datetime.fromtimestamp(value).strftime(
        "%d.%m.%Y %H:%M:%S"
    )


def format_iso_datetime(value: str | None) -> str:
    if not value:
        return "—"

    try:
        parsed = datetime.fromisoformat(value)
        return parsed.strftime("%d.%m.%Y %H:%M:%S")
    except ValueError:
        return str(value)


@router.message(Command("autoscan"))
@router.message(Command("autoscanstatus"))
async def autoscan_status(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("Нет доступа.")
        return

    settings = get_all_autoscan_settings()
    result = get_last_scan_result()
    last_error = get_last_scan_error()

    await message.answer(
        "━━━━━━━━━━━━━━━━━━━━\n"
        "   LIQUIDITY AI SCANNER\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Статус: "
        f"{'включён' if settings['enabled'] else 'выключен'}\n"
        f"Сейчас выполняется: "
        f"{'да' if is_auto_scan_running() else 'нет'}\n"
        f"Интервал: {settings['interval_minutes']} минут\n"
        f"Минимальный рейтинг: "
        f"{settings['minimum_score']}/100\n"
        f"Антидубли: {DUPLICATE_COOLDOWN_MINUTES} минут\n\n"
        f"Последний запуск:\n"
        f"{format_timestamp(get_last_scan_started_at())}\n\n"
        f"Последнее завершение:\n"
        f"{format_timestamp(get_last_scan_finished_at())}\n\n"
        f"Последний результат:\n"
        f"Проверено: {result.get('analysed', 0)}\n"
        f"Найдено: {result.get('found', 0)}\n"
        f"Отправлено: {result.get('sent', 0)}\n"
        f"Антидубли: {result.get('duplicates', 0)}\n\n"
        f"Последняя ошибка: {last_error or 'нет'}\n\n"
        "ENGINE\n"
        "Funding • OI • RSI • Volume • CVD\n"
        "Order Book • FVG • Order Blocks\n"
        "Liquidity • Fear & Greed • Altseason\n\n"
"Команды:\n"
        "/autoscanon\n"
        "/autoscanoff\n"
        "/autoscaninterval 10\n"
        "/autoscanscore 88\n"
        "/autoscanrun\n"
        "/autoscanlogs\n"
        "/autoscanlog 20\n"
        "/autoscanstats\n"
        "/antiduplicates"
    )


@router.message(Command("autoscanon"))
async def enable_autoscan(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("Нет доступа.")
        return

    set_autoscan_enabled(True)
    wake_auto_scanner()

    await message.answer(
        "✅ Автоматический сканер включён.\n\n"
        "Настройка сохранена после перезапуска."
    )


@router.message(Command("autoscanoff"))
async def disable_autoscan(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("Нет доступа.")
        return

    set_autoscan_enabled(False)
    wake_auto_scanner()

    await message.answer(
        "⛔ Автоматический сканер выключен.\n\n"
        "Настройка сохранена."
    )


@router.message(Command("autoscaninterval"))
async def set_autoscan_interval(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("Нет доступа.")
        return

    parts = message.text.split()

    if len(parts) != 2:
        await message.answer(
            "Использование:\n"
            "/autoscaninterval 10\n\n"
            "Минимум: 3 минуты\n"
            "Максимум: 1440 минут"
        )
        return

    try:
        requested_minutes = int(parts[1])
    except ValueError:
        await message.answer(
            "Интервал должен быть целым числом."
        )
        return

    saved_minutes = set_autoscan_interval_minutes(
        requested_minutes
    )

    wake_auto_scanner()

    await message.answer(
        "✅ Интервал изменён.\n\n"
        f"Новый интервал: {saved_minutes} минут"
    )


@router.message(Command("autoscanscore"))
async def set_autoscan_score(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("Нет доступа.")
        return

    parts = message.text.split()

    if len(parts) != 2:
        await message.answer(
            "Использование:\n"
            "/autoscanscore 88\n\n"
            "Минимум: 60\n"
            "Максимум: 100"
        )
        return

    try:
        requested_score = int(parts[1])
    except ValueError:
        await message.answer(
            "Рейтинг должен быть целым числом."
        )
        return

    saved_score = set_minimum_autoscan_score(
        requested_score
    )

    wake_auto_scanner()

    await message.answer(
        "✅ Минимальный рейтинг изменён.\n\n"
        f"Новый порог: {saved_score}/100"
    )


@router.message(Command("autoscanrun"))
async def run_autoscan_now(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("Нет доступа.")
        return

    if is_auto_scan_running():
        await message.answer(
            "Сканирование уже выполняется."
        )
        return

    await message.answer(
        "🔎 Запускаю ручное сканирование."
    )

    result = await run_single_auto_scan(
        bot=message.bot,
        notify_admin=False,
    )

    error = get_last_scan_error()

    if error:
        await message.answer(
            "❌ Сканирование завершилось с ошибкой.\n\n"
            f"{error}"
        )
        return

    await message.answer(
        "✅ Ручное сканирование завершено\n\n"
        f"Проанализировано: {result['analysed']}\n"
        f"Сильных сетапов: {result['found']}\n"
        f"Антидубли: {result['duplicates']}\n"
        f"Отправлено: {result['sent']}"
    )


@router.message(Command("autoscanlogs"))
async def autoscan_logs_command(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("Нет доступа.")
        return

    logs = get_autoscan_logs(20)

    if not logs:
        await message.answer(
            "Журнал автосканов пока пуст."
        )
        return

    lines = ["📋 Последние автосканы", ""]

    for log in logs:
        icon = "✅" if log["status"] == "success" else "❌"

        lines.append(
            f"{icon} #{log['log_id']} | "
            f"{format_iso_datetime(log['started_at'])}\n"
            f"Проверено: {log['analysed']} | "
            f"Найдено: {log['found']} | "
            f"Отправлено: {log['sent']} | "
            f"Дубли: {log['duplicates']}\n"
            f"Время: {float(log['duration_seconds'] or 0):.1f} сек."
        )

        if log["error_text"]:
            lines.append(
                f"Ошибка: {str(log['error_text'])[:150]}"
            )

        lines.append("")

    await message.answer("\n".join(lines))


@router.message(Command("autoscanlog"))
async def autoscan_log_command(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("Нет доступа.")
        return

    parts = message.text.split()

    if len(parts) != 2:
        await message.answer(
            "Использование:\n"
            "/autoscanlog 20\n\n"
            "Число — ID записи из /autoscanlogs."
        )
        return

    try:
        log_id = int(parts[1])
    except ValueError:
        await message.answer(
            "ID журнала должен быть числом."
        )
        return

    log = get_autoscan_log(log_id)

    if not log:
        await message.answer(
            "Запись журнала не найдена."
        )
        return

    await message.answer(
        "📋 Автоскан\n\n"
        f"ID: {log['log_id']}\n"
        f"Статус: {log['status']}\n"
        f"Начало: {format_iso_datetime(log['started_at'])}\n"
        f"Завершение: "
        f"{format_iso_datetime(log['finished_at'])}\n"
        f"Длительность: "
        f"{float(log['duration_seconds'] or 0):.1f} сек.\n"
        f"Минимальный рейтинг: "
        f"{log['minimum_score']}/100\n\n"
        f"Проверено: {log['analysed']}\n"
        f"Найдено: {log['found']}\n"
        f"Отправлено: {log['sent']}\n"
        f"Антидубли: {log['duplicates']}\n\n"
        f"Ошибка: {log['error_text'] or 'нет'}"
    )


@router.message(Command("autoscanstats"))
async def autoscan_stats_command(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("Нет доступа.")
        return

    stats = get_autoscan_statistics()

    await message.answer(
        "📊 Статистика автосканера\n\n"
        f"Всего запусков: {stats['total_scans']}\n"
        f"Успешных: {stats['successful']}\n"
        f"С ошибкой: {stats['failed']}\n"
        f"Средняя длительность: "
        f"{stats['average_duration']:.1f} сек.\n\n"
        f"Всего проверено: {stats['analysed']}\n"
        f"Всего найдено: {stats['found']}\n"
        f"Всего отправлено: {stats['sent']}\n"
        f"Всего дублей: {stats['duplicates']}\n\n"
        f"Сегодня запусков: {stats['today_scans']}\n"
        f"Сегодня проверено: {stats['today_analysed']}\n"
        f"Сегодня найдено: {stats['today_found']}\n"
        f"Сегодня отправлено: {stats['today_sent']}\n"
        f"Сегодня дублей: {stats['today_duplicates']}"
    )


@router.message(Command("autoscanlogsclear"))
async def clear_logs_command(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("Нет доступа.")
        return

    deleted = clear_autoscan_logs()

    await message.answer(
        f"✅ Журнал очищен.\nУдалено записей: {deleted}"
    )


@router.message(Command("antiduplicates"))
async def antiduplicates_command(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("Нет доступа.")
        return

    rows = get_recent_sent_setups(20)

    if not rows:
        await message.answer(
            "История антидублей пока пустая."
        )
        return

    lines = [
        "🚫 Последние отправленные сетапы",
        f"Cooldown: {DUPLICATE_COOLDOWN_MINUTES} минут",
        "",
    ]

    for row in rows:
        lines.append(
            f"{row['inst_id']} | "
            f"{row['direction']} | "
            f"{row['score']}/100\n"
            f"{format_iso_datetime(row['sent_at'])}"
        )

    await message.answer("\n\n".join(lines))


@router.message(Command("antiduplicatesclear"))
async def clear_antiduplicates_command(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("Нет доступа.")
        return

    deleted = clear_antiduplicates()

    await message.answer(
        "✅ История антидублей очищена.\n"
        f"Удалено записей: {deleted}"
    )