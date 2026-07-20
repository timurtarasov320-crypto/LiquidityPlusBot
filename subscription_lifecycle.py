from __future__ import annotations

import asyncio
import sqlite3

from project_paths import data_path
from datetime import datetime, timezone

from aiogram import Bot

DB_NAME = data_path("users.db")
STATE_DB = "subscription_notifications.db"
CHECK_INTERVAL_SECONDS = 900


def _connect(path: str = DB_NAME) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def _init_state() -> None:
    with _connect(STATE_DB) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS notifications (
                user_id INTEGER NOT NULL,
                vip_until TEXT NOT NULL,
                notice_type TEXT NOT NULL,
                sent_at TEXT NOT NULL,
                PRIMARY KEY (user_id, vip_until, notice_type)
            )
            """
        )
        conn.commit()


def _was_sent(user_id: int, vip_until: str, notice_type: str) -> bool:
    with _connect(STATE_DB) as conn:
        row = conn.execute(
            "SELECT 1 FROM notifications WHERE user_id=? AND vip_until=? AND notice_type=?",
            (user_id, vip_until, notice_type),
        ).fetchone()
        return row is not None


def _mark_sent(user_id: int, vip_until: str, notice_type: str) -> None:
    with _connect(STATE_DB) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO notifications(user_id,vip_until,notice_type,sent_at) VALUES(?,?,?,?)",
            (user_id, vip_until, notice_type, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()


def _paid_vip_users() -> list[sqlite3.Row]:
    with _connect() as conn:
        return conn.execute(
            """
            SELECT user_id, vip, vip_until, subscription_plan, referrals
            FROM users
            WHERE vip_until IS NOT NULL
              AND subscription_plan NOT IN ('manual', 'referral_vip')
            """
        ).fetchall()


def _expire_user(user_id: int, expected_until: str) -> bool:
    with _connect() as conn:
        cur = conn.execute(
            """
            UPDATE users
            SET vip=0, vip_until=NULL, subscription_plan=NULL
            WHERE user_id=? AND vip_until=? AND referrals < 300
            """,
            (user_id, expected_until),
        )
        conn.commit()
        return cur.rowcount > 0


async def _safe_send(bot: Bot, user_id: int, text: str) -> None:
    try:
        await bot.send_message(user_id, text)
    except Exception as exc:
        print(f"VIP lifecycle notify {user_id}: {exc}")


async def subscription_lifecycle_monitor(bot: Bot) -> None:
    _init_state()
    print("Монитор VIP-подписок запущен")

    while True:
        try:
            now = datetime.now(timezone.utc)
            for row in _paid_vip_users():
                user_id = int(row["user_id"])
                until_text = str(row["vip_until"])
                try:
                    until = datetime.fromisoformat(until_text)
                except ValueError:
                    continue
                if until.tzinfo is None:
                    until = until.replace(tzinfo=timezone.utc)

                seconds_left = (until - now).total_seconds()
                notice_type = None
                text = None

                if seconds_left <= 0:
                    if _expire_user(user_id, until_text):
                        notice_type = "expired"
                        text = (
                            "❌ VIP-подписка закончилась\n\n"
                            "Доступ к VIP-сигналам отключён автоматически. "
                            "Оформите новый тариф в разделе «💎 VIP»."
                        )
                elif seconds_left <= 24 * 3600:
                    notice_type = "1d"
                    text = (
                        "⚠️ VIP заканчивается менее чем через 24 часа\n\n"
                        f"Дата окончания: {until.strftime('%d.%m.%Y %H:%M UTC')}\n"
                        "Продлите подписку заранее — новый срок добавится к текущему."
                    )
                elif seconds_left <= 3 * 24 * 3600:
                    notice_type = "3d"
                    text = (
                        "⏰ До окончания VIP осталось меньше 3 дней\n\n"
                        f"Дата окончания: {until.strftime('%d.%m.%Y %H:%M UTC')}"
                    )

                if notice_type and text and not _was_sent(user_id, until_text, notice_type):
                    await _safe_send(bot, user_id, text)
                    _mark_sent(user_id, until_text, notice_type)
                    await asyncio.sleep(0.05)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"Ошибка монитора VIP: {exc}")

        await asyncio.sleep(CHECK_INTERVAL_SECONDS)
