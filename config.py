from __future__ import annotations

import os
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent


def _load_local_env() -> None:
    """Load PROJECT_DIR/.env without an external dependency.

    Existing system/Render variables always have priority.
    """
    env_path = PROJECT_DIR / ".env"
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if value and value[0:1] == value[-1:] and value.startswith(("'", '"')):
            value = value[1:-1]
        if key:
            os.environ.setdefault(key, value)


_load_local_env()


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(
            f"Не указана переменная {name}. Создайте файл .env рядом с bot.py "
            f"по примеру .env.example или добавьте {name} в Render Environment."
        )
    return value


def _integer_env(name: str, default: str) -> int:
    raw = os.getenv(name, default).strip()
    try:
        return int(raw)
    except ValueError as exc:
        raise RuntimeError(f"Переменная {name} должна быть целым числом, получено: {raw!r}") from exc


TOKEN = _required_env("BOT_TOKEN")
ADMIN_ID = _integer_env("ADMIN_ID", "5681851735")
CRYPTO_PAY_TOKEN = os.getenv("CRYPTO_PAY_TOKEN", "").strip()
CRYPTO_PAY_API_URL = os.getenv("CRYPTO_PAY_API_URL", "https://pay.crypt.bot/api").strip()

CHANNELS = [
    item.strip()
    for item in os.getenv(
        "REQUIRED_CHANNELS",
        "@liquidityplus,@liquiditypluschat,@skytraded",
    ).split(",")
    if item.strip()
]

WEBAPP_URL = os.getenv(
    "WEBAPP_URL",
    "https://liquidityplusbot.onrender.com/?v=3.0",
).strip()
