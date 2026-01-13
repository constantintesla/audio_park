#!/bin/bash
# Скрипт быстрого развертывания на production сервере

echo "🚀 Развертывание системы анализа голоса на production"
echo ""

# Проверка наличия Docker
if ! command -v docker &> /dev/null; then
    echo "❌ Docker не установлен"
    exit 1
fi

if ! command -v docker-compose &> /dev/null; then
    echo "❌ docker-compose не установлен"
    exit 1
fi

# Создание .env файла, если его нет
if [ ! -f .env ]; then
    echo "📝 Создание .env файла из примера..."
    cp env.example .env
    echo "⚠️  ВАЖНО: Отредактируйте .env и укажите:"
    echo "   - TELEGRAM_BOT_TOKEN (токен вашего бота)"
    echo "   - API_URL (внешний URL вашего сервера, например: https://yourdomain.com)"
    echo ""
    read -p "Нажмите Enter после редактирования .env файла..."
fi

# Создание директории для данных
echo "📁 Создание директории для данных..."
mkdir -p data/results
touch data/results.json
chmod 666 data/results.json

# Запуск контейнеров
echo "🐳 Запуск Docker контейнеров..."
docker-compose -f docker-compose.prod.yml --env-file .env up -d --build

echo ""
echo "✅ Развертывание завершено!"
echo ""
echo "📊 Проверьте статус: docker-compose -f docker-compose.prod.yml ps"
echo "📋 Просмотр логов: docker-compose -f docker-compose.prod.yml logs -f"
echo "🌐 Веб-интерфейс: проверьте URL из API_URL в .env"
echo ""
echo "⚠️  Убедитесь, что:"
echo "   1. API_URL в .env указывает на правильный внешний URL"
echo "   2. Порт открыт в firewall"
echo "   3. Если используете домен - настроен DNS"
