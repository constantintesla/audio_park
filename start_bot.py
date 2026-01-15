"""
Скрипт для запуска Telegram бота
"""
import os
import sys

# Загрузка переменных окружения из .env файла
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    # Если python-dotenv не установлен, пробуем загрузить вручную
    env_file = '.env'
    if os.path.exists(env_file):
        with open(env_file, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    os.environ[key.strip()] = value.strip()

# Проверка наличия aiogram
try:
    from bot import main
except ImportError as e:
    print("Ошибка: aiogram не установлен")
    print("Установите зависимости: pip install -r requirements.txt")
    sys.exit(1)

if __name__ == "__main__":
    main()
