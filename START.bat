@echo off
REM Скрипт для быстрого запуска Docker контейнеров (Windows)

echo 🚀 Запуск системы анализа голоса на болезнь Паркинсона
echo.

REM Проверка наличия Docker
where docker >nul 2>nul
if %ERRORLEVEL% NEQ 0 (
    echo ❌ Docker не установлен. Установите Docker Desktop: https://www.docker.com/products/docker-desktop
    pause
    exit /b 1
)

REM Проверка наличия docker-compose
where docker-compose >nul 2>nul
if %ERRORLEVEL% NEQ 0 (
    echo ❌ docker-compose не установлен. Установите docker-compose
    pause
    exit /b 1
)

echo 📦 Сборка и запуск контейнеров...
docker-compose up -d --build

echo.
echo ✅ Система запущена!
echo.
echo 📊 Веб-интерфейс: http://localhost:5000
echo 🤖 Telegram бот: найдите вашего бота в Telegram
echo.
echo 📋 Просмотр логов: docker-compose logs -f
echo 🛑 Остановка: docker-compose down
echo.
pause
