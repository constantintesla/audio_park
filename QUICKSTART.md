# Быстрый запуск проекта

## Подготовка

1. **Создайте файл `.env`** на основе `env.example`:
   ```bash
   cp env.example .env
   ```

2. **Отредактируйте `.env`** и укажите:
   - `TELEGRAM_BOT_TOKEN` - токен вашего Telegram бота (получите у @BotFather)
   - `API_URL` - URL для доступа к API (для Docker используйте `http://api:5000`)

## Запуск через Docker (рекомендуется)

### Windows:
```bash
START.bat
```

### Linux/Mac:
```bash
chmod +x START.sh
./START.sh
```

### Или вручную:
```bash
docker-compose up -d --build
```

**Веб-интерфейс:** http://localhost:5000

## Запуск без Docker

### Windows:
```bash
START_LOCAL.bat
```

### Linux/Mac:
```bash
chmod +x START_LOCAL.sh
./START_LOCAL.sh
```

## Полезные команды

- **Просмотр логов:** `docker-compose logs -f`
- **Остановка:** `docker-compose down`
- **Перезапуск:** `docker-compose restart`
- **Пересборка:** `docker-compose up -d --build`

## Production

Для production используйте:
```bash
docker-compose -f docker-compose.prod.yml up -d --build
```

Подробные инструкции см. в [README.md](README.md) и [DEPLOY.md](DEPLOY.md)
