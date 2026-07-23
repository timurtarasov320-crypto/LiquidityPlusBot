LIQUIDITYPLUS — ПОЛНАЯ ЗАМЕНА ПАПКИ, VERSION 3.0

ВАЖНО ПЕРЕД ЗАМЕНОЙ:
1. Остановите bot.py и webapp_server.py.
2. Скопируйте из текущей рабочей папки все файлы *.db в отдельную резервную папку.
3. Не используйте старые Telegram и Crypto Pay токены: они были засвечены. Создайте новые.

ЛОКАЛЬНЫЙ ЗАПУСК POWERSHELL:
$env:BOT_TOKEN="НОВЫЙ_ТОКЕН"
$env:ADMIN_ID="5681851735"
$env:CRYPTO_PAY_TOKEN="НОВЫЙ_CRYPTO_PAY_TOKEN"
$env:WEBAPP_URL="https://liquidityplusbot.onrender.com/?v=3.0"
py bot.py

Для локальной Mini App во втором окне:
$env:BOT_TOKEN="НОВЫЙ_ТОКЕН"
py webapp_server.py

RENDER ENVIRONMENT:
BOT_TOKEN=новый токен
ADMIN_ID=5681851735
CRYPTO_PAY_TOKEN=новый токен Crypto Pay
WEBAPP_URL=https://liquidityplusbot.onrender.com/?v=3.0
WEBAPP_DEMO_MODE=0

Render Start Command:
python webapp_server.py

Проверка после деплоя:
https://liquidityplusbot.onrender.com/health

Правильный ответ содержит:
"version": "3.0"

После успешного запуска верните актуальные *.db из резервной копии, если заменяли папку целиком.
