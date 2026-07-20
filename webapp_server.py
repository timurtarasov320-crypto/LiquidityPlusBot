import hashlib
import hmac
import json
import logging
import os
import time
from pathlib import Path
from urllib.parse import parse_qsl

import aiohttp
from aiohttp import web

from config import TOKEN as BOT_TOKEN
from database import create_tables, get_total_discount, get_user
from free_signals import FREE_SIGNALS_LIMIT, get_remaining_free_signals
from signals import create_signal_tables, get_user_signal_history, get_user_signal_statistics

BASE_DIR = Path(__file__).resolve().parent
WEBAPP_DIR = BASE_DIR / "webapp"
LOGGER = logging.getLogger("liquidityplus.webapp")


def validate_init_data(init_data: str) -> dict:
    if not init_data:
        if os.getenv("WEBAPP_DEMO_MODE", "0") == "1":
            return {"id": 0, "first_name": "Demo Trader", "username": "demo"}
        raise web.HTTPUnauthorized(text="Откройте Mini App только через Telegram")

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
        user = json.loads(values.get("user", "{}"))
    except json.JSONDecodeError as exc:
        raise web.HTTPUnauthorized(text="Invalid user data") from exc
    if not user.get("id"):
        raise web.HTTPUnauthorized(text="Telegram user missing")
    return user


def request_user(request: web.Request) -> dict:
    return validate_init_data(request.headers.get("X-Telegram-Init-Data", ""))


def user_payload(telegram_user: dict) -> dict:
    user_id = int(telegram_user.get("id") or 0)
    db_user = get_user(user_id) if user_id else None
    return {
        "id": user_id,
        "first_name": telegram_user.get("first_name") or (db_user[2] if db_user else "Trader"),
        "username": telegram_user.get("username") or (db_user[1] if db_user else None),
        "balance": float(db_user[3] or 0) if db_user else 0.0,
        "vip": bool(db_user[4]) if db_user else False,
        "referrals": int(db_user[5] or 0) if db_user else 0,
        "discount": get_total_discount(user_id) if user_id else 0,
        "free_remaining": get_remaining_free_signals(user_id) if user_id else FREE_SIGNALS_LIMIT,
        "free_limit": FREE_SIGNALS_LIMIT,
        "vip_until": db_user[8] if db_user else None,
        "plan": db_user[9] if db_user else None,
    }


def normalize_signal(signal: dict) -> dict:
    item = dict(signal)
    raw = item.get("confirmations_json")
    try:
        item["confirmations"] = json.loads(raw) if raw else []
    except (TypeError, json.JSONDecodeError):
        item["confirmations"] = []
    item.pop("confirmations_json", None)
    score = item.get("score")
    item["quality_label"] = (
        "PREMIUM" if score is not None and int(score) >= 85
        else "STRONG" if score is not None and int(score) >= 75
        else "STANDARD"
    )
    return item


async def market_snapshot() -> list[dict]:
    symbols = ["BTC-USDT", "ETH-USDT", "SOL-USDT"]
    url = "https://www.okx.com/api/v5/market/ticker"
    timeout = aiohttp.ClientTimeout(total=5)
    result = []
    async with aiohttp.ClientSession(timeout=timeout) as session:
        for inst_id in symbols:
            try:
                async with session.get(url, params={"instId": inst_id}) as response:
                    response.raise_for_status()
                    payload = await response.json()
                item = payload.get("data", [{}])[0]
                last = float(item.get("last") or 0)
                open_24h = float(item.get("open24h") or last or 1)
                change = (last - open_24h) / open_24h * 100
            except Exception as exc:
                LOGGER.warning("Market request failed for %s: %s", inst_id, exc)
                last, change = 0.0, 0.0
            result.append({"symbol": inst_id.split("-")[0], "price": last, "change": change})
    return result


async def api_profile(request: web.Request) -> web.Response:
    return web.json_response({"user": user_payload(request_user(request))})


async def api_statistics(request: web.Request) -> web.Response:
    user_id = int(request_user(request)["id"])
    return web.json_response({"stats": get_user_signal_statistics(user_id)})


async def api_signals(request: web.Request) -> web.Response:
    user_id = int(request_user(request)["id"])
    limit = max(1, min(int(request.query.get("limit", "30")), 50))
    items = [normalize_signal(x) for x in get_user_signal_history(user_id, limit=limit)]
    return web.json_response({"signals": items})


async def api_market(_: web.Request) -> web.Response:
    return web.json_response({"market": await market_snapshot()})


async def dashboard(request: web.Request) -> web.Response:
    telegram_user = request_user(request)
    user_id = int(telegram_user["id"])
    return web.json_response({
        "user": user_payload(telegram_user),
        "stats": get_user_signal_statistics(user_id),
        "history": [normalize_signal(x) for x in get_user_signal_history(user_id, limit=30)],
        "market": await market_snapshot(),
        "server": {"status": "online", "version": "2.0"},
    })


async def health(_: web.Request) -> web.Response:
    return web.json_response({"status": "ok", "service": "LiquidityPlus Mini App API", "version": "2.0"})


async def index(_: web.Request) -> web.FileResponse:
    return web.FileResponse(WEBAPP_DIR / "index.html")


@web.middleware
async def error_middleware(request: web.Request, handler):
    try:
        return await handler(request)
    except web.HTTPException:
        raise
    except Exception as exc:
        LOGGER.exception("Mini App API error on %s", request.path)
        return web.json_response({"error": "internal_error", "message": str(exc)}, status=500)


def create_app() -> web.Application:
    create_tables()
    create_signal_tables()
    app = web.Application(middlewares=[error_middleware])
    app.router.add_get("/", index)
    app.router.add_get("/health", health)
    app.router.add_get("/api/dashboard", dashboard)
    app.router.add_get("/api/profile", api_profile)
    app.router.add_get("/api/statistics", api_statistics)
    app.router.add_get("/api/signals", api_signals)
    app.router.add_get("/api/market", api_market)
    app.router.add_static("/static/", WEBAPP_DIR, show_index=False)
    return app


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    port = int(os.getenv("PORT", os.getenv("WEBAPP_PORT", "8080")))
    web.run_app(create_app(), host="0.0.0.0", port=port)
