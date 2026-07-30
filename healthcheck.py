from __future__ import annotations

import compileall
import importlib.util
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REQUIRED_MODULES = ("aiogram", "aiohttp")
DATABASES = sorted(ROOT.glob("*.db"))


def check_python() -> bool:
    ok = sys.version_info >= (3, 11)
    print(f"[{'OK' if ok else 'FAIL'}] Python {sys.version.split()[0]} (нужен 3.11+)")
    return ok


def check_dependencies() -> bool:
    ok = True
    for name in REQUIRED_MODULES:
        found = importlib.util.find_spec(name) is not None
        print(f"[{'OK' if found else 'FAIL'}] Модуль {name}")
        ok &= found
    return ok


def check_env() -> bool:
    try:
        from config import ADMIN_ID, TOKEN, WEBAPP_URL
    except Exception as exc:
        print(f"[FAIL] Конфигурация: {exc}")
        return False
    token_ok = ":" in TOKEN and len(TOKEN) >= 20
    print(f"[{'OK' if token_ok else 'FAIL'}] BOT_TOKEN")
    print(f"[OK] ADMIN_ID={ADMIN_ID}")
    print(f"[OK] WEBAPP_URL={WEBAPP_URL}")
    return token_ok


def check_syntax() -> bool:
    ok = compileall.compile_dir(ROOT, quiet=1, force=True)
    print(f"[{'OK' if ok else 'FAIL'}] Синтаксис Python-файлов")
    return ok


def check_databases() -> bool:
    ok = True
    for path in DATABASES:
        try:
            with sqlite3.connect(path) as conn:
                result = conn.execute("PRAGMA integrity_check").fetchone()[0]
            valid = result == "ok"
            print(f"[{'OK' if valid else 'FAIL'}] {path.name}: {result}")
            ok &= valid
        except sqlite3.Error as exc:
            print(f"[FAIL] {path.name}: {exc}")
            ok = False
    return ok


def main() -> int:
    print("=== Liquidity+ Bot Healthcheck ===")
    checks = (
        check_python(),
        check_dependencies(),
        check_env(),
        check_syntax(),
        check_databases(),
    )
    print("=================================")
    if all(checks):
        print("Проект готов к запуску: py bot.py")
        return 0
    print("Есть ошибки. Исправьте пункты [FAIL] выше.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
