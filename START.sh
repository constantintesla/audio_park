#!/bin/bash
# Скрипт для быстрого запуска Docker контейнеров

echo "🚀 Запуск системы анализа голоса на болезнь Паркинсона"
echo ""

# Проверка наличия Docker
if ! command -v docker &> /dev/null; then
    echo "❌ Docker не установлен. Установите Docker: https://docs.docker.com/get-docker/"
    exit 1
fi

# Проверка наличия docker-compose
if ! command -v docker-compose &> /dev/null; then
    echo "❌ docker-compose не установлен. Установите docker-compose"
    exit 1
fi

echo "📦 Сборка и запуск контейнеров..."
docker-compose up -d --build

echo ""
echo "✅ Система запущена!"
echo ""
echo "📊 Веб-интерфейс: http://localhost:5000"
echo "🤖 Telegram бот: найдите вашего бота в Telegram"
echo ""
echo "📋 Просмотр логов: docker-compose logs -f"
echo "🛑 Остановка: docker-compose down"
