import asyncio
import json
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
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

from admin_audit import log_event
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
    """Создаёт и безопасно обновляет таблицу платежей."""
    with connect_payments_db() as conn:
        conn.execute(
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
                activated INTEGER NOT NULL DEFAULT 0,
                original_amount REAL,
                discount_percent INTEGER NOT NULL DEFAULT 0,
                activated_at TEXT,
                cancelled_at TEXT
            )
            """
        )

        columns = {
            row[1]
            for row in conn.execute(
                "PRAGMA table_info(payments)"
            ).fetchall()
        }

        migrations = {
            "original_amount": (
                "ALTER TABLE payments "
                "ADD COLUMN original_amount REAL"
            ),
            "discount_percent": (
                "ALTER TABLE payments "
                "ADD COLUMN discount_percent INTEGER NOT NULL DEFAULT 0"
            ),
            "activated_at": (
                "ALTER TABLE payments "
                "ADD COLUMN activated_at TEXT"
            ),
            "cancelled_at": (
                "ALTER TABLE payments "
                "ADD COLUMN cancelled_at TEXT"
            ),
        }

        for column_name, sql in migrations.items():
            if column_name not in columns:
                conn.execute(sql)

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_payments_user
            ON payments(user_id, created_at DESC)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_payments_pending
            ON payments(activated, status)
            """
        )
        conn.commit()


def save_invoice(
    invoice_id: int,
    user_id: int,
    plan_code: str,
    amount: float,
    asset: str,
    payload: str,
    original_amount: float,
    discount_percent: int,
) -> None:
    with connect_payments_db() as conn:
        conn.execute(
            """
            INSERT INTO payments (
                invoice_id,
                user_id,
                plan_code,
                amount,
                asset,
                status,
                payload,
                created_at,
                paid_at,
                activated,
                original_amount,
                discount_percent,
                activated_at,
                cancelled_at
            )
            VALUES (?, ?, ?, ?, ?, 'active', ?, ?, NULL, 0, ?, ?, NULL, NULL)
            ON CONFLICT(invoice_id) DO NOTHING
            """,
            (
                invoice_id,
                user_id,
                plan_code,
                amount,
                asset,
                payload,
                datetime.now(timezone.utc).isoformat(),
                original_amount,
                max(0, min(int(discount_percent), 55)),
            ),
        )
        conn.commit()


def get_payment(invoice_id: int):
    with connect_payments_db() as conn:
        return conn.execute(
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
                activated,
                original_amount,
                discount_percent,
                activated_at,
                cancelled_at
            FROM payments
            WHERE invoice_id = ?
            """,
            (invoice_id,),
        ).fetchone()


def update_payment_status(
    invoice_id: int,
    status: str,
    paid_at: Optional[str] = None,
) -> None:
    with connect_payments_db() as conn:
        conn.execute(
            """
            UPDATE payments
            SET status = ?, paid_at = COALESCE(?, paid_at)
            WHERE invoice_id = ?
            """,
            (status, paid_at, invoice_id),
        )
        conn.commit()


def claim_payment_activation(invoice_id: int) -> bool:
    """Атомарно резервирует счёт для активации VIP.

    activated: 0 = не обработан, 2 = обрабатывается, 1 = активирован.
    """
    with connect_payments_db() as conn:
        cursor = conn.execute(
            """
            UPDATE payments
            SET activated = 2
            WHERE invoice_id = ?
              AND activated = 0
              AND status = 'paid'
            """,
            (invoice_id,),
        )
        conn.commit()
        return cursor.rowcount == 1


def release_payment_activation(invoice_id: int) -> None:
    with connect_payments_db() as conn:
        conn.execute(
            """
            UPDATE payments
            SET activated = 0
            WHERE invoice_id = ? AND activated = 2
            """,
            (invoice_id,),
        )
        conn.commit()


def mark_payment_activated(invoice_id: int) -> None:
    with connect_payments_db() as conn:
        conn.execute(
            """
            UPDATE payments
            SET activated = 1,
                activated_at = ?
            WHERE invoice_id = ? AND activated = 2
            """,
            (
                datetime.now(timezone.utc).isoformat(),
                invoice_id,
            ),
        )
        conn.commit()


def cancel_local_payment(invoice_id: int, user_id: int) -> bool:
    with connect_payments_db() as conn:
        cursor = conn.execute(
            """
            UPDATE payments
            SET status = 'cancelled',
                cancelled_at = ?
            WHERE invoice_id = ?
              AND user_id = ?
              AND activated = 0
              AND status = 'active'
            """,
            (
                datetime.now(timezone.utc).isoformat(),
                invoice_id,
                user_id,
            ),
        )
        conn.commit()
        return cursor.rowcount == 1


def get_pending_invoice_ids() -> list[int]:
    with connect_payments_db() as conn:
        rows = conn.execute(
            """
            SELECT invoice_id
            FROM payments
            WHERE activated = 0
              AND status IN ('active', 'paid')
            ORDER BY created_at ASC
            """
        ).fetchall()
    return [int(row[0]) for row in rows]


def get_recent_payments(limit: int = 20):
    safe_limit = max(1, min(int(limit), 100))
    with connect_payments_db() as conn:
        return conn.execute(
            """
            SELECT *
            FROM payments
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (safe_limit,),
        ).fetchall()


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
    if not CRYPTO_PAY_TOKEN:
        raise RuntimeError(
            "CRYPTO_PAY_TOKEN не задан в config.py или Environment."
        )

    if not CRYPTO_PAY_API_URL:
        raise RuntimeError(
            "CRYPTO_PAY_API_URL не задан."
        )

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
    discount_percent: int,
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
        original_amount=plan.price_usd,
        discount_percent=discount_percent,
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
    local_amount = Decimal(str(payment[3])).quantize(Decimal("0.01"))
    local_asset = str(payment[4]).upper()
    local_payload = str(payment[6] or "")
    activation_state = int(payment[9] or 0)

    if activation_state == 1:
        return True, "Подписка по этому счёту уже активирована."

    if activation_state == 2:
        return False, "Платёж уже обрабатывается. Повторите через несколько секунд."

    invoice = await get_crypto_invoice(invoice_id)

    if invoice is None:
        return False, "Счёт не найден в Crypto Pay."

    status = str(invoice.get("status", "unknown"))
    paid_at = invoice.get("paid_at")

    update_payment_status(
        invoice_id=invoice_id,
        status=status,
        paid_at=paid_at,
    )

    if status == "active":
        return False, "Оплата ещё не поступила."

    if status == "expired":
        return False, "Срок оплаты счёта истёк. Создайте новый счёт."

    if status != "paid":
        return False, f"Статус счёта: {status}"

    remote_asset = str(invoice.get("asset") or "").upper()
    remote_payload = str(invoice.get("payload") or "")

    try:
        remote_amount = Decimal(
            str(invoice.get("amount"))
        ).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError):
        return False, "Crypto Pay вернул неправильную сумму счёта."

    if remote_asset != local_asset:
        return False, "Валюта оплаченного счёта не совпадает."

    if remote_amount != local_amount:
        return False, "Сумма оплаченного счёта не совпадает."

    if remote_payload != local_payload:
        return False, "Данные оплаченного счёта не совпадают."

    try:
        payload_data = json.loads(local_payload)
    except json.JSONDecodeError:
        return False, "Повреждены данные платежа."

    if (
        int(payload_data.get("user_id", 0)) != payment_user_id
        or str(payload_data.get("plan_code")) != plan_code
    ):
        return False, "Счёт не соответствует выбранному тарифу."

    if not claim_payment_activation(invoice_id):
        current = get_payment(invoice_id)
        if current and int(current[9] or 0) == 1:
            return True, "Подписка по этому счёту уже активирована."
        return False, "Платёж уже обрабатывается."

    try:
        current_end = get_vip_until(payment_user_id)
        new_end = calculate_subscription_end(
            plan_code=plan_code,
            current_end=current_end,
        )

        if new_end is None:
            raise RuntimeError("Не удалось рассчитать срок VIP.")

        activated = activate_subscription(
            user_id=payment_user_id,
            plan_code=plan_code,
            vip_until=new_end,
        )

        if not activated:
            raise RuntimeError("Не удалось активировать VIP.")

        mark_payment_activated(invoice_id)
        log_event("payment_activated", None, user_id, invoice_id=invoice_id, plan=plan_code, amount=float(local_amount), asset=local_asset)

    except Exception:
        release_payment_activation(invoice_id)
        raise

    plan = get_plan(plan_code)
    plan_title = plan.title if plan else plan_code
    end_text = new_end.strftime("%d.%m.%Y %H:%M UTC")

    return (
        True,
        "━━━━━━━━━━━━━━━━━━━━\n"
        "✅ ОПЛАТА ПОДТВЕРЖДЕНА\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Тариф: {plan_title}\n"
        f"Оплачено: {local_amount:.2f} {local_asset}\n"
        f"VIP действует до: {end_text}\n\n"
        "Доступ активирован автоматически.",
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
                    text="❌ Отменить счёт",
                    callback_data=f"cancel_payment:{invoice_id}",
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
            discount_percent=discount,
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
    F.data.startswith("cancel_payment:")
)
async def cancel_payment(callback: CallbackQuery):
    try:
        invoice_id = int(callback.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await callback.answer("Неправильный счёт.", show_alert=True)
        return

    payment = get_payment(invoice_id)
    if payment is None or int(payment[1]) != callback.from_user.id:
        await callback.answer("Платёж не найден.", show_alert=True)
        return

    if int(payment[9] or 0) == 1:
        await callback.answer(
            "Оплаченный счёт нельзя отменить.",
            show_alert=True,
        )
        return

    cancelled = cancel_local_payment(
        invoice_id,
        callback.from_user.id,
    )

    if not cancelled:
        await callback.answer(
            "Счёт уже оплачен, истёк или отменён.",
            show_alert=True,
        )
        return

    await update_payment_screen(
        callback,
        "❌ Счёт отменён.\n\nМожно выбрать тариф и создать новый счёт.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="💎 Выбрать тариф",
                        callback_data="vip_plans",
                    )
                ]
            ]
        ),
    )
    await callback.answer("Счёт отменён.")


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


@router.message(Command("payments"))
async def payments_history(message: Message):
    if not is_admin(message.from_user.id):
        return

    rows = get_recent_payments(15)
    if not rows:
        await message.answer("Платежей пока нет.")
        return

    lines = ["💳 ПОСЛЕДНИЕ ПЛАТЕЖИ", ""]
    for row in rows:
        activation = int(row["activated"] or 0)
        state = "✅ VIP" if activation == 1 else str(row["status"]).upper()
        lines.append(
            f"#{row['invoice_id']} | {row['user_id']} | "
            f"{row['plan_code']} | {row['amount']:.2f} {row['asset']} | {state}"
        )

    await message.answer("\n".join(lines))


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