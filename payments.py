import asyncio
import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Optional

import aiohttp
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from config import (
    ADMIN_ID,
    CRYPTO_PAY_API_URL,
    CRYPTO_PAY_TOKEN,
)
from database import (
    activate_subscription,
    get_user,
    get_vip_until,
    set_vip,
)
from subscriptions import (
    calculate_discounted_price,
    calculate_subscription_end,
    get_plan,
)

router = Router()

PAYMENTS_DB_NAME = "payments.db"


def is_admin(user_id: int) -> bool:
    return int(user_id) == int(ADMIN_ID)


def connect_payments_db() -> sqlite3.Connection:
    connection = sqlite3.connect(PAYMENTS_DB_NAME)
    connection.row_factory = sqlite3.Row
    return connection


def create_payment_tables() -> None:
    conn = connect_payments_db()
    cursor = conn.cursor()

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS payments (
            invoice_id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL,
            plan_code TEXT NOT NULL,
            amount REAL NOT NULL,
            asset TEXT NOT NULL DEFAULT 'USDT',
            status TEXT NOT NULL DEFAULT 'active',
            payload TEXT,
            created_at TEXT NOT NULL,
            paid_at TEXT,
            activated INTEGER NOT NULL DEFAULT 0
        )
        """
    )

    conn.commit()
    conn.close()


def save_invoice(
    invoice_id: int,
    user_id: int,
    plan_code: str,
    amount: float,
    asset: str,
    payload: str,
) -> None:
    conn = connect_payments_db()
    cursor = conn.cursor()

    cursor.execute(
        """
        INSERT OR REPLACE INTO payments (
            invoice_id,
            user_id,
            plan_code,
            amount,
            asset,
            status,
            payload,
            created_at,
            paid_at,
            activated
        )
        VALUES (?, ?, ?, ?, ?, 'active', ?, ?, NULL, 0)
        """,
        (
            invoice_id,
            user_id,
            plan_code,
            amount,
            asset,
            payload,
            datetime.now(timezone.utc).isoformat(),
        ),
    )

    conn.commit()
    conn.close()


def get_payment(invoice_id: int):
    conn = connect_payments_db()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT
            invoice_id,
            user_id,
            plan_code,
            amount,
            asset,
            status,
            payload,
            created_at,
            paid_at,
            activated
        FROM payments
        WHERE invoice_id = ?
        """,
        (invoice_id,),
    )

    payment = cursor.fetchone()
    conn.close()

    if payment is None:
        return None

    return tuple(payment)


def update_payment_status(
    invoice_id: int,
    status: str,
    paid_at: Optional[str] = None,
) -> None:
    conn = connect_payments_db()
    cursor = conn.cursor()

    cursor.execute(
        """
        UPDATE payments
        SET status = ?, paid_at = COALESCE(?, paid_at)
        WHERE invoice_id = ?
        """,
        (
            status,
            paid_at,
            invoice_id,
        ),
    )

    conn.commit()
    conn.close()


def mark_payment_activated(invoice_id: int) -> None:
    conn = connect_payments_db()
    cursor = conn.cursor()

    cursor.execute(
        """
        UPDATE payments
        SET activated = 1
        WHERE invoice_id = ?
        """,
        (invoice_id,),
    )

    conn.commit()
    conn.close()


def get_pending_invoice_ids() -> list[int]:
    conn = connect_payments_db()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT invoice_id
        FROM payments
        WHERE activated = 0
          AND status IN ('active', 'paid')
        ORDER BY created_at ASC
        """
    )

    invoice_ids = [int(row[0]) for row in cursor.fetchall()]
    conn.close()
    return invoice_ids


async def automatic_payment_monitor(bot) -> None:
    """Автоматически проверяет счета и выдаёт VIP после оплаты."""
    while True:
        try:
            for invoice_id in get_pending_invoice_ids():
                payment = get_payment(invoice_id)

                if payment is None or bool(payment[9]):
                    continue

                user_id = int(payment[1])

                try:
                    success, result_text = await activate_paid_invoice(
                        invoice_id
                    )
                except RuntimeError as error:
                    print(
                        f"Ошибка автопроверки счёта {invoice_id}: {error}"
                    )
                    continue

                if success:
                    try:
                        await bot.send_message(
                            user_id,
                            result_text
                            + "\n\nVIP активирован автоматически.",
                        )
                    except Exception as error:
                        print(
                            "VIP активирован, но уведомление "
                            f"пользователю {user_id} не отправлено: {error}"
                        )

        except Exception as error:
            print(f"Ошибка автоматической проверки платежей: {error}")

        await asyncio.sleep(30)


async def crypto_pay_request(
    method: str,
    data: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    api_url = CRYPTO_PAY_API_URL.rstrip("/")
    url = f"{api_url}/{method}"

    headers = {
        "Crypto-Pay-API-Token": CRYPTO_PAY_TOKEN,
        "Content-Type": "application/json",
    }

    timeout = aiohttp.ClientTimeout(total=20)

    async with aiohttp.ClientSession(
        timeout=timeout
    ) as session:
        try:
            async with session.post(
                url,
                headers=headers,
                json=data or {},
            ) as response:
                response_text = await response.text()

                try:
                    result = json.loads(response_text)
                except json.JSONDecodeError as error:
                    raise RuntimeError(
                        "Crypto Pay вернул неправильный ответ."
                    ) from error

                if response.status != 200:
                    raise RuntimeError(
                        f"Crypto Pay HTTP {response.status}: "
                        f"{response_text}"
                    )

                if not result.get("ok"):
                    error_message = result.get(
                        "error",
                        "Неизвестная ошибка Crypto Pay",
                    )

                    raise RuntimeError(str(error_message))

                return result["result"]

        except aiohttp.ClientError as error:
            raise RuntimeError(
                f"Ошибка подключения к Crypto Pay: {error}"
            ) from error


async def create_crypto_invoice(
    user_id: int,
    plan_code: str,
    amount: float,
) -> dict[str, Any]:
    plan = get_plan(plan_code)

    if plan is None:
        raise ValueError("Тариф не найден.")

    payload = json.dumps(
        {
            "user_id": user_id,
            "plan_code": plan_code,
        },
        ensure_ascii=False,
    )

    invoice = await crypto_pay_request(
        "createInvoice",
        {
            "currency_type": "crypto",
            "asset": "USDT",
            "amount": f"{amount:.2f}",
            "description": (
                f"VIP LiquidityPlus на {plan.title}"
            ),
            "hidden_message": (
                "Оплата получена. Вернитесь в бот "
                "и нажмите «Проверить оплату»."
            ),
            "payload": payload,
            "allow_comments": False,
            "allow_anonymous": False,
            "expires_in": 3600,
        },
    )

    invoice_id = int(invoice["invoice_id"])

    save_invoice(
        invoice_id=invoice_id,
        user_id=user_id,
        plan_code=plan_code,
        amount=amount,
        asset="USDT",
        payload=payload,
    )

    return invoice


async def get_crypto_invoice(
    invoice_id: int,
) -> Optional[dict[str, Any]]:
    result = await crypto_pay_request(
        "getInvoices",
        {
            "invoice_ids": str(invoice_id),
        },
    )

    items = result.get("items", [])

    if not items:
        return None

    return items[0]


async def activate_paid_invoice(
    invoice_id: int,
) -> tuple[bool, str]:
    payment = get_payment(invoice_id)

    if payment is None:
        return False, "Платёж не найден в базе."

    payment_user_id = int(payment[1])
    plan_code = str(payment[2])
    already_activated = bool(payment[9])

    if already_activated:
        return True, "Подписка по этому счёту уже активирована."

    invoice = await get_crypto_invoice(invoice_id)

    if invoice is None:
        return False, "Счёт не найден в Crypto Pay."

    status = invoice.get("status", "unknown")
    paid_at = invoice.get("paid_at")

    update_payment_status(
        invoice_id=invoice_id,
        status=status,
        paid_at=paid_at,
    )

    if status == "active":
        return False, "Оплата ещё не поступила."

    if status == "expired":
        return False, "Срок оплаты счёта истёк."

    if status != "paid":
        return False, f"Статус счёта: {status}"

    current_end = get_vip_until(payment_user_id)

    new_end = calculate_subscription_end(
        plan_code=plan_code,
        current_end=current_end,
    )

    if new_end is None:
        return False, "Не удалось рассчитать срок VIP."

    activated = activate_subscription(
        user_id=payment_user_id,
        plan_code=plan_code,
        vip_until=new_end,
    )

    if not activated:
        return False, "Не удалось активировать VIP."

    mark_payment_activated(invoice_id)

    plan = get_plan(plan_code)
    plan_title = plan.title if plan else plan_code

    end_text = new_end.strftime(
        "%d.%m.%Y %H:%M UTC"
    )

    return (
        True,
        "✅ Оплата подтверждена!\n\n"
        f"Тариф: {plan_title}\n"
        f"VIP действует до: {end_text}",
    )


async def update_payment_screen(
    callback: CallbackQuery,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    """Обновляет платёжный экран независимо от типа сообщения."""
    if callback.message.photo:
        await callback.message.edit_caption(
            caption=text,
            reply_markup=reply_markup,
        )
    else:
        await callback.message.edit_text(
            text=text,
            reply_markup=reply_markup,
        )


def invoice_keyboard(
    invoice_id: int,
    payment_url: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="💳 Оплатить через CryptoBot",
                    url=payment_url,
                )
            ],
            [
                InlineKeyboardButton(
                    text="✅ Проверить оплату",
                    callback_data=(
                        f"check_payment:{invoice_id}"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Назад к тарифам",
                    callback_data="vip_plans",
                )
            ],
        ]
    )


@router.callback_query(
    F.data.startswith("pay_plan:")
)
async def create_payment(
    callback: CallbackQuery,
):
    plan_code = callback.data.split(
        ":",
        maxsplit=1,
    )[1]

    plan = get_plan(plan_code)

    if plan is None:
        await callback.answer(
            "Тариф не найден.",
            show_alert=True,
        )
        return

    user = get_user(callback.from_user.id)

    if user is None:
        await callback.answer(
            "Сначала отправьте команду /start.",
            show_alert=True,
        )
        return

    from database import get_total_discount

    discount = get_total_discount(
        callback.from_user.id
    )

    final_price = calculate_discounted_price(
        plan_code,
        discount,
    )

    if final_price is None or final_price <= 0:
        await callback.answer(
            "Неправильная цена тарифа.",
            show_alert=True,
        )
        return

    await callback.answer(
        "Создаю счёт...",
    )

    try:
        invoice = await create_crypto_invoice(
            user_id=callback.from_user.id,
            plan_code=plan_code,
            amount=final_price,
        )
    except (RuntimeError, ValueError) as error:
        await callback.message.answer(
            "❌ Не удалось создать счёт.\n\n"
            f"{error}"
        )
        return

    invoice_id = int(invoice["invoice_id"])

    payment_url = (
        invoice.get("bot_invoice_url")
        or invoice.get("mini_app_invoice_url")
        or invoice.get("web_app_invoice_url")
        or invoice.get("pay_url")
    )

    if not payment_url:
        await callback.message.answer(
            "❌ Crypto Pay не вернул ссылку оплаты."
        )
        return

    await update_payment_screen(
        callback,
        "💳 Счёт создан\n\n"
        f"Тариф: {plan.title}\n"
        f"Скидка: {discount}%\n"
        f"К оплате: {final_price:.2f} USDT\n"
        "Счёт действует 1 час.\n\n"
        "После оплаты вернитесь сюда и нажмите "
        "«✅ Проверить оплату».",
        reply_markup=invoice_keyboard(
            invoice_id=invoice_id,
            payment_url=payment_url,
        ),
    )


@router.callback_query(
    F.data.startswith("check_payment:")
)
async def check_payment(
    callback: CallbackQuery,
):
    invoice_id_text = callback.data.split(
        ":",
        maxsplit=1,
    )[1]

    try:
        invoice_id = int(invoice_id_text)
    except ValueError:
        await callback.answer(
            "Неправильный номер счёта.",
            show_alert=True,
        )
        return

    payment = get_payment(invoice_id)

    if payment is None:
        await callback.answer(
            "Платёж не найден.",
            show_alert=True,
        )
        return

    payment_user_id = int(payment[1])

    if payment_user_id != callback.from_user.id:
        await callback.answer(
            "Этот счёт принадлежит другому пользователю.",
            show_alert=True,
        )
        return

    await callback.answer(
        "Проверяю оплату...",
    )

    try:
        success, result_text = await activate_paid_invoice(
            invoice_id
        )
    except RuntimeError as error:
        await callback.message.answer(
            "❌ Ошибка проверки платежа.\n\n"
            f"{error}"
        )
        return

    if not success:
        await callback.answer(
            result_text,
            show_alert=True,
        )
        return

    await update_payment_screen(callback, result_text)


@router.message(Command("cryptotest"))
async def crypto_test(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("Нет доступа.")
        return

    try:
        app = await crypto_pay_request("getMe")
    except RuntimeError as error:
        await message.answer(
            "❌ Crypto Pay не подключён.\n\n"
            f"{error}"
        )
        return

    await message.answer(
        "✅ Crypto Pay подключён\n\n"
        f"Приложение: {app.get('name', 'не указано')}\n"
        f"ID: {app.get('app_id', 'не указан')}"
    )


@router.message(Command("payment"))
async def payment_help(message: Message):
    if not is_admin(message.from_user.id):
        return

    await message.answer(
        "💳 Управление подписками\n\n"
        "/cryptotest — проверить Crypto Pay\n"
        "/activate ID week — VIP на 7 дней\n"
        "/activate ID month — VIP на 30 дней\n"
        "/activate ID year — VIP на 365 дней\n"
        "/deactivate ID — отключить VIP\n"
        "/subinfo ID — информация о подписке"
    )


@router.message(Command("activate"))
async def activate_vip(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("Нет доступа.")
        return

    parts = message.text.split()

    if len(parts) != 3:
        await message.answer(
            "Использование:\n"
            "/activate ID week\n"
            "/activate ID month\n"
            "/activate ID year"
        )
        return

    try:
        user_id = int(parts[1])
    except ValueError:
        await message.answer("Неправильный ID.")
        return

    plan_code = parts[2].lower().strip()
    plan = get_plan(plan_code)

    if plan is None:
        await message.answer("Тариф не найден.")
        return

    user = get_user(user_id)

    if user is None:
        await message.answer(
            "Пользователь не найден."
        )
        return

    current_end = get_vip_until(user_id)

    new_end = calculate_subscription_end(
        plan_code=plan_code,
        current_end=current_end,
    )

    if new_end is None:
        await message.answer(
            "Не удалось рассчитать срок."
        )
        return

    activate_subscription(
        user_id=user_id,
        plan_code=plan_code,
        vip_until=new_end,
    )

    await message.answer(
        "✅ VIP активирован.\n\n"
        f"Пользователь: {user_id}\n"
        f"Тариф: {plan.title}\n"
        f"До: {new_end.strftime('%d.%m.%Y %H:%M UTC')}"
    )


@router.message(Command("deactivate"))
async def deactivate_vip(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("Нет доступа.")
        return

    parts = message.text.split()

    if len(parts) != 2:
        await message.answer(
            "Использование: /deactivate ID"
        )
        return

    try:
        user_id = int(parts[1])
    except ValueError:
        await message.answer("Неправильный ID.")
        return

    if not set_vip(user_id, 0):
        await message.answer(
            "Пользователь не найден."
        )
        return

    await message.answer(
        f"✅ VIP отключён у пользователя {user_id}."
    )


@router.message(Command("subinfo"))
async def subscription_info(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("Нет доступа.")
        return

    parts = message.text.split()

    if len(parts) != 2:
        await message.answer(
            "Использование: /subinfo ID"
        )
        return

    try:
        user_id = int(parts[1])
    except ValueError:
        await message.answer("Неправильный ID.")
        return

    user = get_user(user_id)

    if user is None:
        await message.answer(
            "Пользователь не найден."
        )
        return

    vip_until = get_vip_until(user_id)

    if vip_until is None:
        vip_until_text = (
            "Без ограничения"
            if user[4]
            else "—"
        )
    else:
        vip_until_text = vip_until.strftime(
            "%d.%m.%Y %H:%M UTC"
        )

    await message.answer(
        "👤 Подписка\n\n"
        f"ID: {user[0]}\n"
        f"VIP: {'Активен' if user[4] else 'Не активен'}\n"
        f"Тариф: {user[9] or 'нет'}\n"
        f"До: {vip_until_text}"
    )


create_payment_tables()