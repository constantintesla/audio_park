# Запуск в Docker контейнере

## Быстрый старт

### 1. Сборка и запуск

```bash
docker-compose up -d
```

Это запустит:
- **API сервер** на порту 5000
- **Telegram бот** с настроенным токеном

### 2. Просмотр логов

```bash
# Все логи
docker-compose logs -f

# Логи только API
docker-compose logs -f api

# Логи только бота
docker-compose logs -f bot
```

### 3. Остановка

```bash
docker-compose down
```

### 4. Пересборка после изменений

```bash
docker-compose up -d --build
```

## Структура

- **API контейнер** (`parkinson_api`) - веб-сервер на порту 5000
- **Bot контейнер** (`parkinson_bot`) - Telegram бот

Оба контейнера используют общий том для `results.json` и директории `results/`.

## Доступ к сервисам

- **Веб-интерфейс**: http://localhost:5000
- **API**: http://localhost:5000/api/...

## Переменные окружения

Токен бота должен быть указан в переменной окружения `TELEGRAM_BOT_TOKEN`. 

1. Создайте файл `.env` на основе `env.example`:
```bash
cp env.example .env
nano .env  # Укажите ваш TELEGRAM_BOT_TOKEN
```

2. Запустите с использованием .env файла:
```bash
docker-compose --env-file .env up -d
```

Или установите переменную окружения напрямую:
```bash
export TELEGRAM_BOT_TOKEN="ваш_токен"
docker-compose up -d
```

## Резервное копирование данных

Результаты сохраняются в `results.json` на хосте. Для резервного копирования:

```bash
cp results.json results_backup_$(date +%Y%m%d).json
```

## Обновление

```bash
# Остановить контейнеры
docker-compose down

# Обновить код (git pull и т.д.)

# Пересобрать и запустить
docker-compose up -d --build
```

## Отладка

### Вход в контейнер

```bash
# API контейнер
docker exec -it parkinson_api bash

# Bot контейнер
docker exec -it parkinson_bot bash
```

### Проверка работы

```bash
# Проверить статус контейнеров
docker-compose ps

# Проверить логи
docker-compose logs api
docker-compose logs bot
```

## Production развертывание

Для production рекомендуется:

1. Использовать переменные окружения из файла `.env`
2. Настроить reverse proxy (nginx) перед API
3. Использовать volume для постоянного хранения данных
4. Настроить автоматические бэкапы `results.json`

Пример `docker-compose.prod.yml`:

```yaml
version: '3.8'

services:
  api:
    build: .
    volumes:
      - ./data:/app/data  # Постоянное хранилище
    environment:
      - DEBUG=False
    restart: always

  bot:
    build: .
    environment:
      - TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN}
      - API_URL=http://api:5000
    restart: always
```
