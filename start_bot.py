"""
Скрипт для запуска Telegram бота
"""
import os
import sys

# Проверка наличия aiogram
try:
    from bot import main
except ImportError as e:
    print("Ошибка: aiogram не установлен")
    print("Установите зависимости: pip install -r requirements.txt")
    sys.exit(1)

if __name__ == "__main__":
    main()
