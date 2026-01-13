"""
Скрипт для запуска API сервера
"""
import os
import sys

# Проверка наличия Flask
try:
    from api import app
except ImportError as e:
    print("Ошибка: Flask не установлен")
    print("Установите зависимости: pip install -r requirements.txt")
    sys.exit(1)

if __name__ == "__main__":
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
