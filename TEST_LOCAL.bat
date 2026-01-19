@echo off
REM Скрипт для локального тестирования: обработка аудио и отправка на сервер
REM Использование: TEST_LOCAL.bat [--api-url URL] <путь_к_аудиофайлу> [опции]
REM Или: TEST_LOCAL.bat <путь_к_аудиофайлу> [опции]

setlocal enabledelayedexpansion

REM Проверяем наличие аргументов
if "%1"=="" (
    echo Использование: TEST_LOCAL.bat [--api-url URL] ^<путь_к_аудиофайлу^> [опции]
    echo Или: TEST_LOCAL.bat ^<путь_к_аудиофайлу^> [опции]
    echo.
    echo Примеры:
    echo   TEST_LOCAL.bat audio.wav
    echo   TEST_LOCAL.bat --api-url http://localhost:5000 audio.wav
    echo   TEST_LOCAL.bat http://127.0.0.1:5000 audio.wav
    echo   TEST_LOCAL.bat audio.wav --username test_user --user-id 123
    echo   TEST_LOCAL.bat audio.wav --no-send
    echo   TEST_LOCAL.bat audio.wav --output result.json
    echo.
    exit /b 1
)

REM Определяем, является ли первый аргумент URL (начинается с http:// или https://)
set "first_arg=%1"

if "%first_arg:~0,7%"=="http://" (
    REM Первый аргумент - это URL, второй - файл
    set "api_url=%1"
    set "audio_file=%2"
    if "%audio_file%"=="" (
        echo Ошибка: после URL должен быть указан путь к аудиофайлу
        exit /b 1
    )
    REM Собираем остальные аргументы (начиная с 3-го)
    set "other_args="
    shift
    shift
    :loop1
    if not "%1"=="" (
        set "other_args=!other_args! %1"
        shift
        goto loop1
    )
    python test_local.py "%audio_file%" --api-url "%api_url%"!other_args!
) else if "%first_arg:~0,8%"=="https://" (
    REM Первый аргумент - это URL (https), второй - файл
    set "api_url=%1"
    set "audio_file=%2"
    if "%audio_file%"=="" (
        echo Ошибка: после URL должен быть указан путь к аудиофайлу
        exit /b 1
    )
    REM Собираем остальные аргументы (начиная с 3-го)
    set "other_args="
    shift
    shift
    :loop2
    if not "%1"=="" (
        set "other_args=!other_args! %1"
        shift
        goto loop2
    )
    python test_local.py "%audio_file%" --api-url "%api_url%"!other_args!
) else (
    REM Первый аргумент - это путь к файлу, остальные передаем как есть
    REM Просто передаем все аргументы в test_local.py - он сам разберется
    python test_local.py %*
)
