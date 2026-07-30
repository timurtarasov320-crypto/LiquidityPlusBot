import hashlib
import hmac
import json
import os
import sqlite3
import time
from pathlib import Path
from urllib.parse import parse_qsl

import aiohttp
from aiohttp import web

from config import ADMIN_ID, TOKEN as BOT_TOKEN
from database import get_total_discount, get_user
from free_signals import FREE_SIGNALS_LIMIT, get_remaining_free_signals
from project_paths import data_path
from signals import get_user_signal_history, get_user_signal_statistics

BASE_DIR = Path(__file__).resolve().parent
WEBAPP_DIR = BASE_DIR / "webapp"
ASSISTANT_DB = data_path("market_assistant.db")


def validate_init_data(init_data: str) -> dict:
    if not init_data:
        if os.getenv("WEBAPP_DEMO_MODE", "0") == "1":
            return {"id": 0, "first_name": "Demo Trader", "username": "demo"}
        raise web.HTTPUnauthorized(text="Откройте Mini App через Telegram")

    values = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = values.pop("hash", "")
    auth_date = int(values.get("auth_date", "0") or 0)
    if not received_hash:
        raise web.HTTPUnauthorized(text="Missing hash")
    if not auth_date or time.time() - auth_date > 86400:
        raise web.HTTPUnauthorized(text="Expired initData")

    data_check_string = "\n".join(f"{key}={values[key]}" for key in sorted(values))
    secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    calculated_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calculated_hash, received_hash):
        raise web.HTTPUnauthorized(text="Invalid initData")

    try:
        return json.loads(values.get("user", "{}"))
    except json.JSONDecodeError as exc:
        raise web.HTTPUnauthorized(text="Invalid user data") from exc


def parse_json_list(value) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    try:
        parsed = json.loads(value)
        return [str(item) for item in parsed] if isinstance(parsed, list) else []
    except (TypeError, json.JSONDecodeError):
        return []


def _number(value):
    if value is None:
        return None
    text = str(value).replace(",", ".").strip()
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _midpoint(value):
    if value is None:
        return None
    text = str(value).replace("–", "-").replace("—", "-")
    parts = [part.strip() for part in text.split("-") if part.strip()]
    numbers = [_number(part) for part in parts]
    numbers = [number for number in numbers if number is not None]
    if not numbers:
        return None
    return sum(numbers) / len(numbers)


def serialize_signal(signal: dict) -> dict:
    item = dict(signal)
    item["confirmations"] = parse_json_list(item.pop("confirmations_json", None))
    item["warnings"] = parse_json_list(item.pop("warnings_json", None))

    entry = _midpoint(item.get("entry"))
    stop = _midpoint(item.get("stop_loss"))
    target = _midpoint(item.get("take_profit_1"))
    if entry is not None and stop is not None and entry:
        item["risk_percent"] = abs(entry - stop) / abs(entry) * 100
    else:
        item["risk_percent"] = None
    if entry is not None and stop is not None and target is not None and abs(entry - stop) > 0:
        item["planned_rr"] = abs(target - entry) / abs(entry - stop)
    else:
        item["planned_rr"] = None

    item["tp_stage"] = (
        3 if int(item.get("tp3_hit") or 0) else
        2 if int(item.get("tp2_hit") or 0) else
        1 if int(item.get("tp1_hit") or 0) else 0
    )
    return item


def latest_scanner_setups(limit: int = 8) -> list[dict]:
    try:
        connection = sqlite3.connect(ASSISTANT_DB)
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT setup_id, inst_id, direction, score, setup_json, status, created_at
            FROM assistant_setups
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (max(1, min(limit, 20)),),
        ).fetchall()
        connection.close()
    except sqlite3.Error:
        return []

    result = []
    for row in rows:
        try:
            setup = json.loads(row["setup_json"])
        except (TypeError, json.JSONDecodeError):
            setup = {}
        result.append({
            "setup_id": row["setup_id"],
            "symbol": row["inst_id"],
            "direction": row["direction"],
            "score": row["score"],
            "status": row["status"],
            "created_at": row["created_at"],
            "entry_low": setup.get("entry_low"),
            "entry_high": setup.get("entry_high"),
            "stop_loss": setup.get("stop_loss"),
            "take_profit_1": setup.get("take_profit_1"),
            "take_profit_2": setup.get("take_profit_2"),
            "risk_reward": setup.get("risk_reward"),
            "confirmations": setup.get("reasons", [])[:12],
            "warnings": setup.get("warnings", [])[:6],
            "order_flow_score": setup.get("order_flow_score", 0),
            "rsi_15m": setup.get("rsi_15m"),
            "rsi_1h": setup.get("rsi_1h"),
            "rsi_4h": setup.get("rsi_4h"),
            "funding_rate": setup.get("funding_rate"),
            "volume_ratio": setup.get("volume_ratio"),
        })
    return result


async def market_snapshot() -> list[dict]:
    symbols = ["BTC-USDT", "ETH-USDT", "SOL-USDT"]
    url = "https://www.okx.com/api/v5/market/ticker"
    timeout = aiohttp.ClientTimeout(total=8)
    result = []
    async with aiohttp.ClientSession(timeout=timeout) as session:
        for inst_id in symbols:
            try:
                async with session.get(url, params={"instId": inst_id}) as response:
                    payload = await response.json()
                item = payload.get("data", [{}])[0]
                last = float(item.get("last") or 0)
                open_24h = float(item.get("open24h") or last or 1)
                change = (last - open_24h) / open_24h * 100
            except Exception:
                last, change = 0.0, 0.0
            result.append({"symbol": inst_id.split("-")[0], "price": last, "change": change})
    return result


def request_user(request: web.Request) -> dict:
    return validate_init_data(request.headers.get("X-Telegram-Init-Data", ""))


async def dashboard(request: web.Request) -> web.Response:
    telegram_user = request_user(request)
    user_id = int(telegram_user.get("id") or 0)
    db_user = get_user(user_id) if user_id else None
    stats = get_user_signal_statistics(user_id) if user_id else {
        "total": 0, "wins": 0, "losses": 0, "breakeven": 0, "active": 0,
        "tp1": 0, "tp2": 0, "tp3": 0, "winrate": 0.0,
        "total_result": 0.0, "average_result": 0.0, "average_rr": 0.0,
    }
    history = [serialize_signal(x) for x in get_user_signal_history(user_id, limit=30)] if user_id else []
    vip = bool(db_user[4]) if db_user else False
    referrals = int(db_user[5] or 0) if db_user else 0

    return web.json_response({
        "user": {
            "id": user_id,
            "first_name": telegram_user.get("first_name", "Trader"),
            "username": telegram_user.get("username"),
            "vip": vip,
            "referrals": referrals,
            "discount": get_total_discount(user_id) if user_id else 0,
            "free_remaining": get_remaining_free_signals(user_id) if user_id else FREE_SIGNALS_LIMIT,
            "free_limit": FREE_SIGNALS_LIMIT,
            "is_admin": user_id == int(ADMIN_ID),
        },
        "stats": stats,
        "history": history,
        "market": await market_snapshot(),
        "system": {
            "api": "online",
            "signal_monitor": "active",
            "scanner_setups": len(latest_scanner_setups(20)),
            "updated_at": int(time.time()),
        },
    })


async def scanner(request: web.Request) -> web.Response:
    telegram_user = request_user(request)
    user_id = int(telegram_user.get("id") or 0)
    is_admin = user_id == int(ADMIN_ID)
    setups = latest_scanner_setups(12 if is_admin else 5)
    if not is_admin:
        setups = [item for item in setups if item["status"] == "published" and item["score"] >= 72]
    return web.json_response({
        "is_admin": is_admin,
        "setups": setups,
        "message": (
            "Показаны реальные последние результаты AI Scanner."
            if setups else "Подходящих сетапов в базе пока нет."
        ),
    })



def analytics_period_days(period: str) -> int | None:
    return {"day": 1, "week": 7, "month": 30, "all": None}.get(period, None)


def build_analytics(user_id: int, period: str = "all") -> dict:
    days = analytics_period_days(period)
    connection = sqlite3.connect(data_path("signals.db"))
    connection.row_factory = sqlite3.Row
    params: list[object] = [user_id]
    where = "r.user_id = ?"
    if days is not None:
        where += " AND datetime(COALESCE(s.closed_at, s.created_at)) >= datetime('now', ?)"
        params.append(f"-{days} days")
    rows = connection.execute(f"""
        SELECT s.* FROM signal_recipients r
        JOIN signals s ON s.signal_id = r.signal_id
        WHERE {where}
        ORDER BY datetime(COALESCE(s.closed_at, s.created_at)) ASC, s.signal_id ASC
    """, params).fetchall()
    connection.close()
    items = [dict(row) for row in rows]
    closed = [x for x in items if x.get("status") in {"win", "loss", "breakeven"}]
    results = [float(x.get("result_percent") or 0) for x in closed]
    wins = sum(1 for x in closed if x.get("status") == "win")
    losses = sum(1 for x in closed if x.get("status") == "loss")
    resolved = wins + losses
    cumulative = 0.0
    curve = []
    for x in closed:
        cumulative += float(x.get("result_percent") or 0)
        curve.append({"label": (x.get("closed_at") or x.get("created_at") or "")[:10], "value": round(cumulative, 4)})
    best = max(closed, key=lambda x: float(x.get("result_percent") or 0), default=None)
    worst = min(closed, key=lambda x: float(x.get("result_percent") or 0), default=None)
    long_items = [x for x in closed if str(x.get("direction") or "").upper() == "LONG"]
    short_items = [x for x in closed if str(x.get("direction") or "").upper() == "SHORT"]
    def side_summary(side):
        side_wins = sum(1 for x in side if x.get("status") == "win")
        side_losses = sum(1 for x in side if x.get("status") == "loss")
        denom = side_wins + side_losses
        return {"count": len(side), "winrate": side_wins / denom * 100 if denom else 0.0, "result": sum(float(x.get("result_percent") or 0) for x in side)}
    return {
        "period": period, "total": len(items), "closed": len(closed), "active": sum(1 for x in items if x.get("status") == "active"),
        "wins": wins, "losses": losses, "breakeven": sum(1 for x in closed if x.get("status") == "breakeven"),
        "winrate": wins / resolved * 100 if resolved else 0.0,
        "total_result": sum(results), "average_result": sum(results) / len(results) if results else 0.0,
        "best": serialize_signal(best) if best else None, "worst": serialize_signal(worst) if worst else None,
        "long": side_summary(long_items), "short": side_summary(short_items), "curve": curve[-60:],
        "recent_closed": [serialize_signal(x) for x in reversed(closed[-20:])],
    }


async def analytics(request: web.Request) -> web.Response:
    telegram_user = request_user(request)
    user_id = int(telegram_user.get("id") or 0)
    period = request.query.get("period", "all")
    if period not in {"day", "week", "month", "all"}:
        period = "all"
    return web.json_response(build_analytics(user_id, period) if user_id else build_analytics(0, period))


async def health(_: web.Request) -> web.Response:
    return web.json_response({"ok": True, "service": "LiquidityPlus Mini App API", "version": "3.2", "time": int(time.time())})


async def index(_: web.Request) -> web.FileResponse:
    return web.FileResponse(WEBAPP_DIR / "index.html")


def create_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/", index)
    app.router.add_get("/health", health)
    app.router.add_get("/api/dashboard", dashboard)
    app.router.add_get("/api/scanner", scanner)
    app.router.add_get("/api/analytics", analytics)
    app.router.add_static("/static/", WEBAPP_DIR, show_index=False)
    return app


if __name__ == "__main__":
    port = int(os.getenv("PORT") or os.getenv("WEBAPP_PORT", "8080"))
    web.run_app(create_app(), host="0.0.0.0", port=port)
