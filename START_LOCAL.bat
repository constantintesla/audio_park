@echo off
REM Скрипт для запуска системы без Docker (локально)

echo 🚀 Запуск системы анализа голоса на болезнь Паркинсона (локально)
echo.

REM Проверка наличия Python
python --version >nul 2>nul
if %ERRORLEVEL% NEQ 0 (
    echo ❌ Python не установлен или не найден в PATH
    pause
    exit /b 1
)

REM Проверка наличия .env файла
if not exist .env (
    echo 📝 Создание .env файла из примера...
    copy env.example .env
    echo ⚠️  При необходимости отредактируйте .env
)

REM Загрузка переменных из .env
echo 📋 Загрузка переменных окружения из .env...
for /f "usebackq tokens=1,* delims==" %%a in (".env") do (
    if not "%%a"=="" if not "%%a"=="#" (
        set "%%a=%%b"
    )
)

echo.
echo ✅ Запуск веб-интерфейса на http://localhost:5000
echo ⚠️  Для остановки нажмите Ctrl+C
echo.

python start_api.py

pause
