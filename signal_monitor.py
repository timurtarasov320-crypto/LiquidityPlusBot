from __future__ import annotations

import asyncio
import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import aiohttp
from aiogram import Bot

from database import add_signal_event_by_source, close_trading_signal_by_source
from signals import SIGNALS_DB_NAME, get_signal_recipients
from signal_message_editor import update_signal_messages

OKX_API_URL = "https://www.okx.com"
CHECK_INTERVAL_SECONDS = max(5, int(os.getenv("SIGNAL_CHECK_INTERVAL_SECONDS", "20")))
REQUEST_TIMEOUT_SECONDS = max(5, int(os.getenv("SIGNAL_PRICE_TIMEOUT_SECONDS", "15")))
SEND_DELAY_SECONDS = max(0.0, float(os.getenv("SIGNAL_NOTIFY_DELAY_SECONDS", "0.04")))
MAX_ACTIVE_HOURS = max(24, int(os.getenv("SIGNAL_MAX_ACTIVE_HOURS", "72")))


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect_signals_db() -> sqlite3.Connection:
    connection = sqlite3.connect(SIGNALS_DB_NAME, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.execute("PRAGMA busy_timeout=30000")
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def create_monitor_columns() -> None:
    migrations = {
        "tp1_hit": "ALTER TABLE signals ADD COLUMN tp1_hit INTEGER NOT NULL DEFAULT 0",
        "tp2_hit": "ALTER TABLE signals ADD COLUMN tp2_hit INTEGER NOT NULL DEFAULT 0",
        "tp3_hit": "ALTER TABLE signals ADD COLUMN tp3_hit INTEGER NOT NULL DEFAULT 0",
        "entry_price_numeric": "ALTER TABLE signals ADD COLUMN entry_price_numeric REAL DEFAULT NULL",
        "entry_reached": "ALTER TABLE signals ADD COLUMN entry_reached INTEGER NOT NULL DEFAULT 0",
        "entry_reached_at": "ALTER TABLE signals ADD COLUMN entry_reached_at TEXT DEFAULT NULL",
        "last_checked_price": "ALTER TABLE signals ADD COLUMN last_checked_price REAL DEFAULT NULL",
        "breakeven_active": "ALTER TABLE signals ADD COLUMN breakeven_active INTEGER NOT NULL DEFAULT 0",
        "monitor_enabled": "ALTER TABLE signals ADD COLUMN monitor_enabled INTEGER NOT NULL DEFAULT 1",
        "close_reason": "ALTER TABLE signals ADD COLUMN close_reason TEXT DEFAULT NULL",
        "last_event": "ALTER TABLE signals ADD COLUMN last_event TEXT DEFAULT NULL",
        "last_event_at": "ALTER TABLE signals ADD COLUMN last_event_at TEXT DEFAULT NULL",
        "monitor_updated_at": "ALTER TABLE signals ADD COLUMN monitor_updated_at TEXT DEFAULT NULL",
    }
    with connect_signals_db() as connection:
        existing = {str(row["name"]) for row in connection.execute("PRAGMA table_info(signals)")}
        for column_name, sql in migrations.items():
            if column_name not in existing:
                connection.execute(sql)
        connection.execute("CREATE INDEX IF NOT EXISTS idx_signals_monitor ON signals(status, monitor_enabled, closed_at)")
        # Closed signals can never be monitored again.
        connection.execute("""
            UPDATE signals
               SET monitor_enabled = 0
             WHERE status <> 'active' OR closed_at IS NOT NULL
        """)
        connection.commit()


def parse_number(value: object) -> Optional[float]:
    if value is None:
        return None
    text = str(value).replace("\u00a0", "").replace(" ", "").replace(",", ".")
    match = re.search(r"[-+]?\d+(?:\.\d+)?", text)
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def parse_entry_range(value: object) -> tuple[Optional[float], Optional[float]]:
    if value is None:
        return None, None
    text = re.sub(r"[–—−]", "-", str(value).replace("\u00a0", " "))
    numbers = [float(item.replace(" ", "").replace(",", ".")) for item in re.findall(r"\d[\d\s]*(?:[.,]\d+)?", text)]
    if not numbers:
        return None, None
    if len(numbers) == 1:
        return numbers[0], numbers[0]
    return min(numbers[0], numbers[1]), max(numbers[0], numbers[1])


def parse_entry_price(value: object) -> Optional[float]:
    low, high = parse_entry_range(value)
    if low is None or high is None:
        return None
    return (low + high) / 2


def format_price(value: float) -> str:
    if value >= 1000:
        return f"{value:,.4f}".rstrip("0").rstrip(".").replace(",", " ")
    if value >= 1:
        return f"{value:.6f}".rstrip("0").rstrip(".")
    return f"{value:.10f}".rstrip("0").rstrip(".")


def symbol_to_okx_inst_id(symbol: str) -> str:
    value = str(symbol).upper().strip().replace("/", "-").replace("_", "-").replace(" ", "")
    value = value.replace("USDT-SWAP-SWAP", "USDT-SWAP")
    if value.endswith("-SWAP"):
        return value
    if value.endswith("-USDT"):
        return f"{value}-SWAP"
    if value.endswith("USDT") and "-" not in value:
        return f"{value[:-4]}-USDT-SWAP"
    if "-" not in value:
        return f"{value}-USDT-SWAP"
    return value


async def get_okx_price(session: aiohttp.ClientSession, inst_id: str) -> Optional[float]:
    try:
        async with session.get(f"{OKX_API_URL}/api/v5/market/ticker", params={"instId": inst_id}) as response:
            response.raise_for_status()
            payload = await response.json()
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as error:
        print(f"Ошибка получения цены {inst_id}: {error}")
        return None
    if str(payload.get("code")) != "0":
        print(f"OKX ошибка {inst_id}: {payload.get('msg', 'неизвестная ошибка')}")
        return None
    data = payload.get("data") or []
    try:
        price = float(data[0].get("last") or 0) if data else 0
    except (TypeError, ValueError):
        return None
    return price if price > 0 else None


def expire_stale_signals() -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=MAX_ACTIVE_HOURS)).isoformat()
    with connect_signals_db() as connection:
        cursor = connection.execute("""
            UPDATE signals
               SET status='expired', monitor_enabled=0, close_reason='expired',
                   closed_at=COALESCE(closed_at, ?), monitor_updated_at=?
             WHERE status='active' AND COALESCE(monitor_enabled,1)=1
               AND created_at < ?
        """, (utc_now(), utc_now(), cutoff))
        connection.commit()
        return cursor.rowcount


def get_active_signals() -> list[dict[str, Any]]:
    with connect_signals_db() as connection:
        rows = connection.execute("""
            SELECT * FROM signals
             WHERE status='active' AND COALESCE(monitor_enabled,1)=1 AND closed_at IS NULL
             ORDER BY signal_id ASC
        """).fetchall()
    return [dict(row) for row in rows]


def update_last_price(signal_id: int, price: float, entry_price: Optional[float]) -> None:
    with connect_signals_db() as connection:
        connection.execute("""
            UPDATE signals SET last_checked_price=?, entry_price_numeric=COALESCE(entry_price_numeric,?), monitor_updated_at=?
             WHERE signal_id=? AND status='active' AND COALESCE(monitor_enabled,1)=1
        """, (price, entry_price, utc_now(), signal_id))
        connection.commit()


def mark_entry_reached(signal_id: int, entry_price: float) -> bool:
    with connect_signals_db() as connection:
        cursor = connection.execute("""
            UPDATE signals
               SET entry_reached=1, entry_reached_at=COALESCE(entry_reached_at,?),
                   entry_price_numeric=COALESCE(entry_price_numeric,?), last_event='entry', last_event_at=?, monitor_updated_at=?
             WHERE signal_id=? AND status='active' AND COALESCE(monitor_enabled,1)=1 AND COALESCE(entry_reached,0)=0
        """, (utc_now(), entry_price, utc_now(), utc_now(), signal_id))
        connection.commit()
        return cursor.rowcount > 0


def claim_target(signal_id: int, target_number: int) -> bool:
    if target_number not in (1,2,3): return False
    column=f"tp{target_number}_hit"; event=f"tp{target_number}"
    with connect_signals_db() as connection:
        cursor=connection.execute(f"""
            UPDATE signals SET {column}=1,last_event=?,last_event_at=?,monitor_updated_at=?
             WHERE signal_id=? AND status='active' AND COALESCE(monitor_enabled,1)=1
               AND COALESCE(entry_reached,0)=1 AND COALESCE({column},0)=0
        """, (event,utc_now(),utc_now(),signal_id))
        connection.commit(); return cursor.rowcount>0


def activate_breakeven(signal_id: int) -> bool:
    with connect_signals_db() as connection:
        cursor=connection.execute("""
            UPDATE signals SET breakeven_active=1,last_event='breakeven_enabled',last_event_at=?,monitor_updated_at=?
             WHERE signal_id=? AND status='active' AND COALESCE(monitor_enabled,1)=1
               AND COALESCE(entry_reached,0)=1 AND COALESCE(breakeven_active,0)=0
        """, (utc_now(),utc_now(),signal_id))
        connection.commit(); return cursor.rowcount>0


def close_monitored_signal(signal_id:int,status:str,result_percent:float,reason:str)->bool:
    with connect_signals_db() as connection:
        cursor=connection.execute("""
            UPDATE signals SET status=?,result_percent=?,closed_at=?,monitor_enabled=0,close_reason=?,
                               last_event=?,last_event_at=?,monitor_updated_at=?
             WHERE signal_id=? AND status='active' AND COALESCE(monitor_enabled,1)=1 AND closed_at IS NULL
        """, (status,result_percent,utc_now(),reason,reason,utc_now(),utc_now(),signal_id))
        connection.commit(); return cursor.rowcount>0


def calculate_result_percent(direction:str,entry_price:float,exit_price:float)->float:
    if entry_price<=0:return 0.0
    return ((exit_price-entry_price)/entry_price*100) if direction.upper()=="LONG" else ((entry_price-exit_price)/entry_price*100)


def target_reached(direction:str,current_price:float,target_price:float)->bool:
    return current_price>=target_price if direction.upper()=="LONG" else current_price<=target_price


def stop_reached(direction:str,current_price:float,stop_price:float)->bool:
    return current_price<=stop_price if direction.upper()=="LONG" else current_price>=stop_price


def entry_was_reached(current:float,last:Optional[float],low:float,high:float)->bool:
    if low<=current<=high:return True
    if last is None:return False
    return min(last,current)<=high and max(last,current)>=low


async def notify_signal_recipients(bot:Bot,signal_id:int,text:str)->tuple[int,int]:
    sent=failed=0
    for user_id,_ in get_signal_recipients(signal_id):
        try: await bot.send_message(chat_id=user_id,text=text); sent+=1
        except Exception as error: failed+=1; print(f"Ошибка уведомления пользователя {user_id}: {error}")
        if SEND_DELAY_SECONDS: await asyncio.sleep(SEND_DELAY_SECONDS)
    return sent,failed


def header(title:str,signal_id:int)->str:return f"━━━━━━━━━━━━━━━━━━━━\n{title} • #{signal_id}\n━━━━━━━━━━━━━━━━━━━━"


async def handle_stop(bot:Bot,signal:dict[str,Any],entry_price:float,original_stop:float,current_price:float)->bool:
    sid=int(signal['signal_id']); direction=str(signal['direction']).upper(); be=bool(signal.get('breakeven_active',0)); effective=entry_price if be else original_stop
    if not stop_reached(direction,current_price,effective): return False
    if be:
        if not close_monitored_signal(sid,'breakeven',0.0,'breakeven'): return True
        close_trading_signal_by_source(sid,'be'); add_signal_event_by_source(sid,'be_closed',str(entry_price)); signal['status']='breakeven'; signal['monitor_enabled']=0
        await update_signal_messages(bot,signal,'breakeven')
        await notify_signal_recipients(bot,sid,f"{header('🛡 БЕЗУБЫТОК',sid)}\n\nМонета: {signal['symbol']}\nНаправление: {direction}\nЦена закрытия: {format_price(entry_price)}\n\nПозиция закрыта без убытка: 0.00%.")
        return True
    result=calculate_result_percent(direction,entry_price,original_stop)
    if not close_monitored_signal(sid,'loss',result,'stop_loss'): return True
    close_trading_signal_by_source(sid,'sl'); add_signal_event_by_source(sid,'sl',str(original_stop)); signal['status']='loss'; signal['monitor_enabled']=0
    await update_signal_messages(bot,signal,'loss')
    await notify_signal_recipients(bot,sid,f"{header('❌ STOP LOSS',sid)}\n\nМонета: {signal['symbol']}\nНаправление: {direction}\nЦена стопа: {format_price(original_stop)}\nРезультат: {result:+.2f}%")
    return True


async def handle_tp1(bot:Bot,signal:dict[str,Any],entry:float,tp1:float)->bool:
    sid=int(signal['signal_id'])
    if not claim_target(sid,1):return False
    activate_breakeven(sid); add_signal_event_by_source(sid,'tp1',str(tp1)); add_signal_event_by_source(sid,'be',str(entry)); result=calculate_result_percent(str(signal['direction']),entry,tp1)
    signal['tp1_hit']=1; signal['breakeven_active']=1
    await update_signal_messages(bot,signal,'tp1')
    await notify_signal_recipients(bot,sid,f"{header('🎯 TP1 ДОСТИГНУТ',sid)}\n\nМонета: {signal['symbol']}\nНаправление: {str(signal['direction']).upper()}\nЦена TP1: {format_price(tp1)}\nРезультат: {result:+.2f}%\n\nСтоп отмечен в точке входа.")
    return True


async def handle_tp2(bot:Bot,signal:dict[str,Any],entry:float,tp2:float)->bool:
    sid=int(signal['signal_id'])
    if not claim_target(sid,2):return False
    add_signal_event_by_source(sid,'tp2',str(tp2)); result=calculate_result_percent(str(signal['direction']),entry,tp2); signal['tp2_hit']=1
    await update_signal_messages(bot,signal,'tp2')
    await notify_signal_recipients(bot,sid,f"{header('🚀 TP2 ДОСТИГНУТ',sid)}\n\nМонета: {signal['symbol']}\nНаправление: {str(signal['direction']).upper()}\nЦена TP2: {format_price(tp2)}\nРезультат: {result:+.2f}%")
    return True


async def handle_final_target(bot:Bot,signal:dict[str,Any],entry:float,n:int,target:float,already_claimed:bool=False)->bool:
    sid=int(signal['signal_id'])
    if not already_claimed and not claim_target(sid,n):return False
    result=calculate_result_percent(str(signal['direction']),entry,target); add_signal_event_by_source(sid,f'tp{n}',str(target))
    if not close_monitored_signal(sid,'win',result,f'tp{n}'):return True
    close_trading_signal_by_source(sid,f'tp{n}'); signal[f'tp{n}_hit']=1; signal['status']='win'; signal['monitor_enabled']=0
    await update_signal_messages(bot,signal,'win')
    await notify_signal_recipients(bot,sid,f"{header(f'🏆 TP{n} ДОСТИГНУТ',sid)}\n\nМонета: {signal['symbol']}\nНаправление: {str(signal['direction']).upper()}\nЦена цели: {format_price(target)}\nИтог: {result:+.2f}%")
    return True


async def process_signal(bot:Bot,session:aiohttp.ClientSession,signal:dict[str,Any])->None:
    sid=int(signal['signal_id']); direction=str(signal['direction']).upper(); current=await get_okx_price(session,symbol_to_okx_inst_id(str(signal['symbol'])))
    if current is None:return
    low,high=parse_entry_range(signal.get('entry')); entry=parse_number(signal.get('entry_price_numeric')) or parse_entry_price(signal.get('entry')); stop=parse_number(signal.get('stop_loss')); tp1=parse_number(signal.get('take_profit_1')); tp2=parse_number(signal.get('take_profit_2')); tp3=parse_number(signal.get('take_profit_3')); last=parse_number(signal.get('last_checked_price'))
    update_last_price(sid,current,entry)
    if entry is None or low is None or high is None or stop is None:
        print(f"Сигнал #{sid}: не удалось разобрать уровни"); return
    if not bool(signal.get('entry_reached',0)):
        if not entry_was_reached(current,last,low,high): return
        if not mark_entry_reached(sid,entry): return
        signal['entry_reached']=1
        add_signal_event_by_source(sid,'entry',str(entry))
    # Only an entered and still-active position may receive TP/SL events.
    if await handle_stop(bot,signal,entry,stop,current):return
    tp1_hit=bool(signal.get('tp1_hit',0)); tp2_hit=bool(signal.get('tp2_hit',0))
    if tp1 is not None and not tp1_hit and target_reached(direction,current,tp1):
        await handle_tp1(bot,signal,entry,tp1); tp1_hit=True; signal['breakeven_active']=1
    if tp2 is not None and not tp2_hit and target_reached(direction,current,tp2):
        await handle_tp2(bot,signal,entry,tp2); tp2_hit=True
    if tp3 is not None and target_reached(direction,current,tp3): await handle_final_target(bot,signal,entry,3,tp3); return
    if tp3 is None and tp2 is not None and tp2_hit: await handle_final_target(bot,signal,entry,2,tp2,True); return
    if tp2 is None and tp3 is None and tp1 is not None and tp1_hit: await handle_final_target(bot,signal,entry,1,tp1,True)


async def monitor_signals(bot:Bot)->None:
    create_monitor_columns(); expired=expire_stale_signals()
    if expired: print(f"Мониторинг отключён для просроченных сигналов: {expired}")
    timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS)
    print(f"Отслеживание позиций запущено: {CHECK_INTERVAL_SECONDS} сек., максимум {MAX_ACTIVE_HOURS} ч.")
    async with aiohttp.ClientSession(timeout=timeout) as session:
        while True:
            try:
                expire_stale_signals()
                for signal in get_active_signals():
                    try: await process_signal(bot,session,signal)
                    except asyncio.CancelledError: raise
                    except Exception as error: print(f"Ошибка отслеживания сигнала #{signal.get('signal_id')}: {error}")
                    await asyncio.sleep(0.15)
            except asyncio.CancelledError: raise
            except Exception as error: print(f"Ошибка цикла мониторинга: {error}")
            await asyncio.sleep(CHECK_INTERVAL_SECONDS)
