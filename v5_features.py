from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from aiogram import Bot, Router
from aiogram.filters import Command
from aiogram.types import Message

from database import activate_subscription, get_vip_until
from role_manager import has_role

router = Router()
DB_PATH = Path('v5_features.db')


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def create_v5_tables() -> None:
    conn = _connect()
    conn.executescript(
        '''
        CREATE TABLE IF NOT EXISTS promo_codes (
            code TEXT PRIMARY KEY,
            vip_days INTEGER NOT NULL,
            max_uses INTEGER NOT NULL,
            uses INTEGER NOT NULL DEFAULT 0,
            active INTEGER NOT NULL DEFAULT 1,
            created_by INTEGER NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS promo_redemptions (
            code TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            redeemed_at TEXT NOT NULL,
            PRIMARY KEY(code, user_id)
        );
        CREATE TABLE IF NOT EXISTS trading_journal (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            symbol TEXT NOT NULL,
            direction TEXT NOT NULL,
            result_percent REAL NOT NULL,
            note TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS report_runs (
            report_key TEXT PRIMARY KEY,
            sent_at TEXT NOT NULL
        );
        '''
    )
    conn.commit()
    conn.close()


def _extend_vip(user_id: int, days: int, plan: str) -> datetime:
    now = datetime.now(timezone.utc)
    current = get_vip_until(user_id)
    base = current if current and current > now else now
    until = base + timedelta(days=days)
    if not activate_subscription(user_id, plan, until):
        raise ValueError('Пользователь не найден. Он должен сначала нажать /start.')
    return until


@router.message(Command('promocreate'))
async def promo_create(message: Message) -> None:
    if not has_role(message.from_user.id, 'admin'):
        await message.answer('Недостаточно прав.')
        return
    parts = (message.text or '').split()
    if len(parts) != 4:
        await message.answer('Формат: /promocreate CODE VIP_DAYS MAX_USES')
        return
    code = parts[1].upper().strip()
    try:
        days, max_uses = int(parts[2]), int(parts[3])
        if days < 1 or max_uses < 1:
            raise ValueError
    except ValueError:
        await message.answer('VIP_DAYS и MAX_USES должны быть положительными числами.')
        return
    conn = _connect()
    try:
        conn.execute(
            'INSERT INTO promo_codes(code,vip_days,max_uses,created_by,created_at) VALUES(?,?,?,?,?)',
            (code, days, max_uses, message.from_user.id, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        await message.answer('Такой промокод уже существует.')
        return
    finally:
        conn.close()
    await message.answer(f'Промокод {code} создан: {days} дней VIP, максимум {max_uses} активаций.')


@router.message(Command('promo'))
async def promo_redeem(message: Message) -> None:
    parts = (message.text or '').split(maxsplit=1)
    if len(parts) != 2:
        await message.answer('Формат: /promo CODE')
        return
    code = parts[1].upper().strip()
    conn = _connect()
    try:
        conn.execute('BEGIN IMMEDIATE')
        promo = conn.execute('SELECT * FROM promo_codes WHERE code=? AND active=1', (code,)).fetchone()
        if not promo:
            await message.answer('Промокод не найден или отключён.')
            conn.rollback()
            return
        if promo['uses'] >= promo['max_uses']:
            await message.answer('Лимит активаций этого промокода исчерпан.')
            conn.rollback()
            return
        used = conn.execute('SELECT 1 FROM promo_redemptions WHERE code=? AND user_id=?', (code, message.from_user.id)).fetchone()
        if used:
            await message.answer('Вы уже использовали этот промокод.')
            conn.rollback()
            return
        until = _extend_vip(message.from_user.id, int(promo['vip_days']), f'promo:{code}')
        conn.execute('INSERT INTO promo_redemptions(code,user_id,redeemed_at) VALUES(?,?,?)', (code, message.from_user.id, datetime.now(timezone.utc).isoformat()))
        conn.execute('UPDATE promo_codes SET uses=uses+1 WHERE code=?', (code,))
        conn.commit()
    except Exception as exc:
        conn.rollback()
        await message.answer(f'Не удалось активировать промокод: {exc}')
        return
    finally:
        conn.close()
    await message.answer(f'VIP активирован до {until.strftime("%d.%m.%Y %H:%M UTC")}.')


@router.message(Command('giftvip'))
async def gift_vip(message: Message, bot: Bot) -> None:
    if not has_role(message.from_user.id, 'admin'):
        await message.answer('Недостаточно прав.')
        return
    parts = (message.text or '').split()
    if len(parts) != 3:
        await message.answer('Формат: /giftvip USER_ID DAYS')
        return
    try:
        user_id, days = int(parts[1]), int(parts[2])
        if days < 1:
            raise ValueError
        until = _extend_vip(user_id, days, 'gift')
    except Exception as exc:
        await message.answer(f'Ошибка: {exc}')
        return
    await message.answer(f'Пользователю {user_id} выдан VIP до {until.strftime("%d.%m.%Y")}.')
    try:
        await bot.send_message(user_id, f'Вам подарен VIP на {days} дней. Доступ активен до {until.strftime("%d.%m.%Y")}.')
    except Exception:
        pass


@router.message(Command('journaladd'))
async def journal_add(message: Message) -> None:
    parts = (message.text or '').split(maxsplit=5)
    if len(parts) < 5:
        await message.answer('Формат: /journaladd BTCUSDT LONG 2.5 комментарий')
        return
    symbol, direction = parts[1].upper(), parts[2].upper()
    if direction not in {'LONG', 'SHORT'}:
        await message.answer('Направление должно быть LONG или SHORT.')
        return
    try:
        result = float(parts[3].replace(',', '.'))
    except ValueError:
        await message.answer('Результат укажите числом, например 2.5 или -1.2.')
        return
    note = parts[4] if len(parts) == 5 else parts[4] + ' ' + parts[5]
    conn = _connect()
    conn.execute('INSERT INTO trading_journal(user_id,symbol,direction,result_percent,note,created_at) VALUES(?,?,?,?,?,?)', (message.from_user.id, symbol, direction, result, note, datetime.now(timezone.utc).isoformat()))
    conn.commit(); conn.close()
    await message.answer('Сделка добавлена в личный дневник.')


@router.message(Command('journal'))
async def journal(message: Message) -> None:
    conn = _connect()
    rows = conn.execute('SELECT * FROM trading_journal WHERE user_id=? ORDER BY id DESC LIMIT 10', (message.from_user.id,)).fetchall()
    stats = conn.execute('SELECT COUNT(*) n, SUM(CASE WHEN result_percent>0 THEN 1 ELSE 0 END) wins, AVG(result_percent) avg_r, SUM(result_percent) total FROM trading_journal WHERE user_id=?', (message.from_user.id,)).fetchone()
    conn.close()
    if not rows:
        await message.answer('Дневник пока пуст. Добавление: /journaladd BTCUSDT LONG 2.5 комментарий')
        return
    n = int(stats['n'] or 0); wins = int(stats['wins'] or 0)
    text = [f'Личный торговый дневник\n\nСделок: {n}\nWin Rate: {(wins/n*100 if n else 0):.1f}%\nСредний результат: {float(stats["avg_r"] or 0):+.2f}%\nИтого: {float(stats["total"] or 0):+.2f}%\n', 'Последние сделки:']
    for r in rows:
        text.append(f'{r["symbol"]} {r["direction"]}: {float(r["result_percent"]):+.2f}% — {r["note"] or "без комментария"}')
    await message.answer('\n'.join(text))


def _signal_report(period_days: int) -> str:
    since = (datetime.now(timezone.utc) - timedelta(days=period_days)).isoformat()
    conn = sqlite3.connect('signals.db'); conn.row_factory = sqlite3.Row
    rows = conn.execute('SELECT status,result_percent,tp1_hit,tp2_hit,tp3_hit FROM signals WHERE created_at>=?', (since,)).fetchall(); conn.close()
    total = len(rows); closed = [r for r in rows if str(r['status']).lower() != 'active']
    wins = sum(1 for r in closed if float(r['result_percent'] or 0) > 0 or int(r['tp3_hit'] or 0) == 1)
    losses = sum(1 for r in closed if float(r['result_percent'] or 0) < 0)
    be = max(0, len(closed)-wins-losses)
    return (f'Отчёт за {period_days} дн.\n\nСигналов: {total}\nЗакрыто: {len(closed)}\nПобед: {wins}\nУбытков: {losses}\nBE: {be}\nWin Rate: {(wins/len(closed)*100 if closed else 0):.1f}%\nTP1: {sum(int(r["tp1_hit"] or 0) for r in rows)}\nTP2: {sum(int(r["tp2_hit"] or 0) for r in rows)}\nTP3: {sum(int(r["tp3_hit"] or 0) for r in rows)}\nСуммарный результат: {sum(float(r["result_percent"] or 0) for r in closed):+.2f}%')


@router.message(Command('report'))
async def report_command(message: Message) -> None:
    if not has_role(message.from_user.id, 'analyst'):
        await message.answer('Недостаточно прав.')
        return
    parts = (message.text or '').split()
    days = {'day': 1, 'week': 7, 'month': 30}.get(parts[1].lower() if len(parts) > 1 else 'day', 1)
    await message.answer(_signal_report(days))


async def automatic_reports(bot: Bot, admin_id: int) -> None:
    create_v5_tables()
    while True:
        now = datetime.now(timezone.utc)
        schedules = []
        if now.hour == 20:
            schedules.append(('daily:' + now.strftime('%Y-%m-%d'), 1))
        if now.weekday() == 6 and now.hour == 20:
            schedules.append(('weekly:' + now.strftime('%G-%V'), 7))
        if now.day == 1 and now.hour == 20:
            schedules.append(('monthly:' + now.strftime('%Y-%m'), 30))
        for key, days in schedules:
            conn = _connect()
            exists = conn.execute('SELECT 1 FROM report_runs WHERE report_key=?', (key,)).fetchone()
            if not exists:
                try:
                    await bot.send_message(admin_id, _signal_report(days))
                    conn.execute('INSERT INTO report_runs(report_key,sent_at) VALUES(?,?)', (key, now.isoformat()))
                    conn.commit()
                except Exception:
                    pass
            conn.close()
        await asyncio.sleep(900)
