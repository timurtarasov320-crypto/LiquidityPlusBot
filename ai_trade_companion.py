from __future__ import annotations

import asyncio
import os
import sqlite3
from datetime import datetime, timezone

import aiohttp
from aiogram import Bot

from signals import SIGNALS_DB_NAME, get_signal_recipients
from signal_monitor import symbol_to_okx_inst_id
from user_preferences import get_preferences

OKX = "https://www.okx.com"
INTERVAL = max(120, int(os.getenv("AI_COMPANION_INTERVAL_SECONDS", "300")))
COOLDOWN = max(600, int(os.getenv("AI_COMPANION_COOLDOWN_SECONDS", "1800")))
DB = "ai_companion.db"


def conn(path: str = DB):
    c = sqlite3.connect(path, timeout=30)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    with conn() as c:
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS snapshots(
                signal_id INTEGER PRIMARY KEY,
                funding REAL,
                oi REAL,
                volume REAL,
                price REAL DEFAULT 0,
                last_notice_at REAL DEFAULT 0,
                updated_at TEXT
            )
            """
        )
        cols = {r[1] for r in c.execute("PRAGMA table_info(snapshots)")}
        if "price" not in cols:
            c.execute("ALTER TABLE snapshots ADD COLUMN price REAL DEFAULT 0")
        c.commit()


def active_signals():
    with conn(SIGNALS_DB_NAME) as c:
        return [dict(r) for r in c.execute("SELECT * FROM signals WHERE status='active'").fetchall()]


async def fetch_json(session, path, params):
    try:
        async with session.get(OKX + path, params=params) as r:
            data = await r.json()
            return (data.get("data") or []) if str(data.get("code")) == "0" else []
    except Exception:
        return []


async def metrics(session, inst):
    funding_d, oi_d, candles, ticker = await asyncio.gather(
        fetch_json(session, "/api/v5/public/funding-rate", {"instId": inst}),
        fetch_json(session, "/api/v5/public/open-interest", {"instType": "SWAP", "instId": inst}),
        fetch_json(session, "/api/v5/market/candles", {"instId": inst, "bar": "5m", "limit": "13"}),
        fetch_json(session, "/api/v5/market/ticker", {"instId": inst}),
    )
    funding = float((funding_d[0].get("fundingRate") if funding_d else 0) or 0)
    oi = float(((oi_d[0].get("oiUsd") or oi_d[0].get("oi")) if oi_d else 0) or 0)
    price = float((ticker[0].get("last") if ticker else 0) or 0)
    vols = [float(x[5] or 0) for x in candles if len(x) > 5]
    volume_ratio = (vols[0] / (sum(vols[1:]) / len(vols[1:]))) if len(vols) > 1 and sum(vols[1:]) > 0 else 1.0
    momentum = 0.0
    if len(candles) >= 7:
        latest = float(candles[0][4] or 0)
        older = float(candles[6][4] or 0)
        if older:
            momentum = (latest - older) / older * 100
    return funding, oi, volume_ratio, price, momentum


async def notify(bot, signal, text):
    for uid, _ in get_signal_recipients(int(signal["signal_id"])):
        try:
            if not get_preferences(uid).ai_ideas:
                continue
            await bot.send_message(uid, text)
        except Exception as exc:
            print(f"AI companion notify {uid}: {exc}")
        await asyncio.sleep(0.04)


def probability(direction: str, oi_change: float, funding: float, volume_ratio: float, momentum: float) -> tuple[int, str]:
    score = 58
    aligned_momentum = momentum if direction == "LONG" else -momentum
    score += max(-18, min(18, aligned_momentum * 8))
    score += 8 if oi_change > 2 else (-8 if oi_change < -2 else 0)
    score += 8 if volume_ratio >= 1.8 and aligned_momentum > 0 else 0
    crowd_risk = (direction == "LONG" and funding > 0.001) or (direction == "SHORT" and funding < -0.001)
    if crowd_risk:
        score -= 15
    score = int(max(15, min(92, score)))
    if score >= 72:
        verdict = "структура пока поддерживает продолжение движения"
    elif score >= 50:
        verdict = "сценарий нейтральный — удерживайте риск под контролем"
    else:
        verdict = "риск ослабления движения вырос"
    return score, verdict


async def process(bot, session, signal):
    sid = int(signal["signal_id"])
    inst = symbol_to_okx_inst_id(signal["symbol"])
    funding, oi, vr, price, momentum = await metrics(session, inst)
    now = datetime.now(timezone.utc).timestamp()
    with conn() as c:
        old = c.execute("SELECT * FROM snapshots WHERE signal_id=?", (sid,)).fetchone()
        if not old:
            c.execute(
                "INSERT OR REPLACE INTO snapshots(signal_id,funding,oi,volume,price,last_notice_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                (sid, funding, oi, vr, price, 0, datetime.now(timezone.utc).isoformat()),
            )
            c.commit()
            return
        old_oi = float(old["oi"] or 0)
        oi_change = ((oi - old_oi) / old_oi * 100) if old_oi > 0 else 0
        funding_change = (funding - float(old["funding"] or 0)) * 100
        direction = str(signal["direction"]).upper()
        reasons = []
        if abs(oi_change) >= 4:
            reasons.append(f"Open Interest {'вырос' if oi_change > 0 else 'снизился'} на {abs(oi_change):.1f}%")
        if abs(funding_change) >= 0.02:
            reasons.append(f"Funding изменился до {funding * 100:+.4f}%")
        if vr >= 1.8:
            reasons.append(f"объём 5m выше среднего в {vr:.1f} раза")
        aligned = momentum if direction == "LONG" else -momentum
        if abs(momentum) >= 0.6:
            reasons.append(f"30m-импульс {'поддерживает' if aligned > 0 else 'ослабляет'} сценарий ({momentum:+.2f}%)")
        crowd_risk = (direction == "LONG" and funding > 0.001) or (direction == "SHORT" and funding < -0.001)
        if crowd_risk:
            reasons.append("позиционирование толпы повышает риск резкого отката")
        can_notice = now - float(old["last_notice_at"] or 0) >= COOLDOWN
        if reasons and can_notice:
            score, verdict = probability(direction, oi_change, funding, vr, momentum)
            await notify(
                bot,
                signal,
                "━━━━━━━━━━━━━━━━━━━━\n🤖 AI-СОПРОВОЖДЕНИЕ\n━━━━━━━━━━━━━━━━━━━━\n\n"
                f"Сигнал #{sid} • {signal['symbol']} • {direction}\n"
                f"Текущая цена: {price:g}\n\n"
                + "\n".join(f"• {r}" for r in reasons)
                + f"\n\nВероятность продолжения: {score}%\n"
                f"Вывод: {verdict}.\n\n"
                "Это аналитическое обновление, а не команда на вход или выход.",
            )
            c.execute("UPDATE snapshots SET last_notice_at=? WHERE signal_id=?", (now, sid))
        c.execute(
            "UPDATE snapshots SET funding=?,oi=?,volume=?,price=?,updated_at=? WHERE signal_id=?",
            (funding, oi, vr, price, datetime.now(timezone.utc).isoformat(), sid),
        )
        c.commit()


async def ai_trade_companion(bot: Bot):
    init_db()
    print(f"AI-сопровождение запущено (каждые {INTERVAL} сек.)")
    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        while True:
            try:
                for signal in active_signals():
                    await process(bot, session, signal)
                    await asyncio.sleep(0.2)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(f"Ошибка AI-сопровождения: {exc}")
            await asyncio.sleep(INTERVAL)
