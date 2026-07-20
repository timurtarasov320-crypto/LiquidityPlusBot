LIQUIDITYPLUS V4 — ROLES, BACKUPS, LOGGING, WEB ADMIN

Добавлено:
1. Система ролей: owner, admin, moderator, analyst.
2. Журналирование в logs/liquidityplus.log и logs/errors.log.
3. Автоматические резервные копии всех SQLite-баз.
4. Команда /backup для владельца.
5. Команды /setrole, /delrole, /staff, /myrole.
6. Защищённая веб-панель статистики (read-only).

КОМАНДЫ
/setrole USER_ID admin
/setrole USER_ID moderator
/setrole USER_ID analyst
/delrole USER_ID
/staff
/myrole
/backup

ВЕБ-ПАНЕЛЬ
По умолчанию выключена. Добавьте в .env:
ADMIN_WEB_HOST=0.0.0.0
ADMIN_WEB_PORT=8081
ADMIN_WEB_TOKEN=сложный_секретный_токен

Открытие:
http://IP:8081/?token=ваш_токен

ВАЖНО
- Текущий ADMIN_ID автоматически получает роль owner.
- Роль admin получает доступ к существующей админ-панели.
- moderator и analyst пока используются как база разрешений для следующих модулей.
- Резервные копии хранятся в папке backups и ротируются автоматически.
- Перед заменой проекта сохраните старую папку.
