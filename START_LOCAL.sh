#!/bin/bash
# Скрипт для запуска системы без Docker (локально)

echo "🚀 Запуск системы анализа голоса на болезнь Паркинсона (локально)"
echo ""

# Проверка наличия Python
if ! command -v python3 &> /dev/null && ! command -v python &> /dev/null; then
    echo "❌ Python не установлен"
    exit 1
fi

PYTHON_CMD=$(command -v python3 || command -v python)

# Проверка наличия .env файла
if [ ! -f .env ]; then
    echo "📝 Создание .env файла из примера..."
    cp env.example .env
    echo "⚠️  При необходимости отредактируйте .env"
fi

# Загрузка переменных из .env
if [ -f .env ]; then
    export $(grep -v '^#' .env | xargs)
fi

echo ""
echo "✅ Запуск веб-интерфейса на http://localhost:5000"
echo "⚠️  Для остановки нажмите Ctrl+C"
echo ""

exec $PYTHON_CMD start_api.py
