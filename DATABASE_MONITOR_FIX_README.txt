LIQUIDITYPLUS — FIX DATABASE + POSITION MONITOR

Что исправлено:
1. Все основные SQLite-базы используют абсолютный стабильный путь.
2. Можно задать LIQUIDITYPLUS_DATA_DIR для постоянного диска на хостинге.
3. Закрытая позиция получает monitor_enabled=0 и больше никогда не отслеживается.
4. TP/SL/безубыток работают только после фактического касания зоны входа.
5. События TP1/TP2/TP3 атомарные и не отправляются повторно.
6. Старые ACTIVE-сигналы автоматически переводятся в expired после 72 часов.
7. SQLite включён в WAL-режиме и с busy_timeout.
8. Резервные копии берутся из единой папки данных.

ВАЖНО ДЛЯ RENDER/ХОСТИНГА:
Укажи LIQUIDITYPLUS_DATA_DIR на подключённый persistent disk.
Пример: /var/data/liquidityplus
Иначе бесплатный хостинг может удалять SQLite после redeploy/restart.

Настройки:
SIGNAL_MAX_ACTIVE_HOURS=72
SIGNAL_CHECK_INTERVAL_SECONDS=20
