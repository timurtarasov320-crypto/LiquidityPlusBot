from __future__ import annotations

import html
import os
import sqlite3
from aiohttp import web

HOST = os.getenv("ADMIN_WEB_HOST", "127.0.0.1")
PORT = int(os.getenv("ADMIN_WEB_PORT", "8081"))
TOKEN = os.getenv("ADMIN_WEB_TOKEN", "").strip()


def _authorized(request: web.Request) -> bool:
    if not TOKEN:
        return False
    supplied = request.query.get("token", "") or request.headers.get("X-Admin-Token", "")
    return supplied == TOKEN


def _count(db: str, query: str) -> int:
    try:
        conn = sqlite3.connect(db)
        value = conn.execute(query).fetchone()[0]
        conn.close()
        return int(value or 0)
    except Exception:
        return 0


async def dashboard(request: web.Request) -> web.Response:
    if not _authorized(request):
        raise web.HTTPUnauthorized(text="Unauthorized")
    users = _count("users.db", "SELECT COUNT(*) FROM users")
    vip = _count("users.db", "SELECT COUNT(*) FROM users WHERE vip=1")
    active = _count("signals.db", "SELECT COUNT(*) FROM signals WHERE status='active'")
    paid = _count("payments.db", "SELECT COUNT(*) FROM payments WHERE status='paid'")
    body = f"""
    <!doctype html><html><head><meta charset='utf-8'><title>LiquidityPlus Admin</title>
    <style>body{{font-family:Arial;background:#0f1117;color:#fff;padding:30px}}.grid{{display:grid;grid-template-columns:repeat(4,minmax(160px,1fr));gap:16px}}.card{{background:#191d27;border:1px solid #2a3140;border-radius:14px;padding:22px}}h1{{margin-top:0}}.n{{font-size:34px;font-weight:700}}</style></head>
    <body><h1>LiquidityPlus Admin</h1><div class='grid'>
    <div class='card'><div>Пользователи</div><div class='n'>{users}</div></div>
    <div class='card'><div>VIP</div><div class='n'>{vip}</div></div>
    <div class='card'><div>Активные сигналы</div><div class='n'>{active}</div></div>
    <div class='card'><div>Оплаченные счета</div><div class='n'>{paid}</div></div>
    </div><p>Панель работает в режиме безопасного просмотра. Управление остаётся в Telegram.</p></body></html>
    """
    return web.Response(text=body, content_type="text/html")


async def health(_: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


async def start_admin_web_server() -> web.AppRunner | None:
    if not TOKEN:
        return None
    app = web.Application()
    app.router.add_get("/", dashboard)
    app.router.add_get("/health", health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, HOST, PORT)
    await site.start()
    return runner
