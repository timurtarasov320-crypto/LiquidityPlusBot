import os


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(
            f"Не указана переменная {name}. Добавьте её в Render Environment "
            "или задайте перед локальным запуском."
        )
    return value


TOKEN = _required_env("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "5681851735"))
CRYPTO_PAY_TOKEN = os.getenv("CRYPTO_PAY_TOKEN", "").strip()
CRYPTO_PAY_API_URL = os.getenv("CRYPTO_PAY_API_URL", "https://pay.crypt.bot/api")

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
