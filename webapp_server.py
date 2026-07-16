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

from config import BOT_TOKEN
from database import (
    get_total_discount,
    get_user,
)
from free_signals import (
    FREE_SIGNALS_LIMIT,
    get_remaining_free_signals,
)
from signals import (
    get_user_signal_history,
    get_user_signal_statistics,
)

BASE_DIR = Path(__file__).resolve().parent
WEBAPP_DIR = BASE_DIR / "webapp"


def validate_init_data(init_data: str) -> dict:
    if not init_data:
        if os.getenv("WEBAPP_DEMO_MODE", "1") == "1":
            return {
                "id": 0,
                "first_name": "Demo Trader",
                "username": "demo",
            }
        raise web.HTTPUnauthorized(text="Missing Telegram initData")

    values = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = values.pop("hash", "")
    auth_date = int(values.get("auth_date", "0") or 0)

    if not received_hash:
        raise web.HTTPUnauthorized(text="Missing hash")

    if time.time() - auth_date > 86400:
        raise web.HTTPUnauthorized(text="Expired initData")

    data_check_string = "\n".join(
        f"{key}={values[key]}"
        for key in sorted(values)
    )

    secret_key = hmac.new(
        b"WebAppData",
        BOT_TOKEN.encode(),
        hashlib.sha256,
    ).digest()

    calculated_hash = hmac.new(
        secret_key,
        data_check_string.encode(),
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(calculated_hash, received_hash):
        raise web.HTTPUnauthorized(text="Invalid initData")

    try:
        return json.loads(values.get("user", "{}"))
    except json.JSONDecodeError:
        raise web.HTTPUnauthorized(text="Invalid user data")


async def market_snapshot() -> list[dict]:
    symbols = ["BTC-USDT", "ETH-USDT", "SOL-USDT"]
    url = "https://www.okx.com/api/v5/market/ticker"
    timeout = aiohttp.ClientTimeout(total=8)
    result = []

    async with aiohttp.ClientSession(timeout=timeout) as session:
        for inst_id in symbols:
            try:
                async with session.get(
                    url,
                    params={"instId": inst_id},
                ) as response:
                    payload = await response.json()
                item = payload.get("data", [{}])[0]
                last = float(item.get("last") or 0)
                open_24h = float(item.get("open24h") or last or 1)
                change = (last - open_24h) / open_24h * 100
            except Exception:
                last, change = 0.0, 0.0

            result.append({
                "symbol": inst_id.split("-")[0],
                "price": last,
                "change": change,
            })

    return result


async def dashboard(request: web.Request) -> web.Response:
    telegram_user = validate_init_data(
        request.headers.get("X-Telegram-Init-Data", "")
    )
    user_id = int(telegram_user.get("id") or 0)

    db_user = get_user(user_id) if user_id else None
    stats = (
        get_user_signal_statistics(user_id)
        if user_id
        else {
            "total": 0,
            "wins": 0,
            "losses": 0,
            "breakeven": 0,
            "active": 0,
            "winrate": 0.0,
            "total_result": 0.0,
        }
    )
    history = (
        get_user_signal_history(user_id, limit=12)
        if user_id
        else []
    )

    vip = bool(db_user[4]) if db_user else False
    referrals = int(db_user[5] or 0) if db_user else 0

    response = {
        "user": {
            "id": user_id,
            "first_name": telegram_user.get("first_name", "Trader"),
            "username": telegram_user.get("username"),
            "vip": vip,
            "referrals": referrals,
            "discount": get_total_discount(user_id) if user_id else 0,
            "free_remaining": (
                get_remaining_free_signals(user_id)
                if user_id
                else FREE_SIGNALS_LIMIT
            ),
            "free_limit": FREE_SIGNALS_LIMIT,
        },
        "stats": stats,
        "history": history,
        "market": await market_snapshot(),
    }
    return web.json_response(response)


async def index(_: web.Request) -> web.FileResponse:
    return web.FileResponse(WEBAPP_DIR / "index.html")


def create_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/", index)
    app.router.add_get("/api/dashboard", dashboard)
    app.router.add_static(
        "/static/",
        WEBAPP_DIR,
        show_index=False,
    )
    return app


if __name__ == "__main__":
    port = int(os.getenv("WEBAPP_PORT", "8080"))
    web.run_app(
        create_app(),
        host="0.0.0.0",
        port=port,
    )
