import os

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "5681851735"))

CRYPTO_PAY_TOKEN = os.getenv("CRYPTO_PAY_TOKEN")
CRYPTO_PAY_API_URL = os.getenv(
    "CRYPTO_PAY_API_URL",
    "https://pay.crypt.bot/api"
)

CHANNELS = [
    "@liquidityplus",
    "@liquiditypluschat",
    "@skytraded",
]

WEBAPP_URL = os.getenv(
    "WEBAPP_URL",
    "https://liquidityplusbot.onrender.com"
)