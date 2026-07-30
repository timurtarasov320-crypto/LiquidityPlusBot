@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist .env (
  echo [ОШИБКА] Нет файла .env
  echo Скопируйте .env.example в .env и вставьте BOT_TOKEN.
  pause
  exit /b 1
)
py healthcheck.py
if errorlevel 1 (
  pause
  exit /b 1
)
py bot.py
pause
