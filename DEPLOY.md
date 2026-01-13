# Развертывание на хостинге

## Подготовка к развертыванию

### 1. Требования к серверу

- **ОС**: Linux (Ubuntu 20.04+ рекомендуется)
- **Docker** и **docker-compose** установлены
- **Минимум**: 2GB RAM, 1 CPU, 10GB диска
- **Порты**: 5000 (или другой, настраиваемый) должен быть открыт

### 2. Подготовка файлов

1. Скопируйте все файлы проекта на сервер
2. Создайте файл `.env` на основе `env.example`:

```bash
cp env.example .env
nano .env
```

3. Заполните переменные окружения:

```env
TELEGRAM_BOT_TOKEN=ваш_токен_бота
API_URL=https://yourdomain.com
API_PORT=5000
DEBUG=False
FLASK_ENV=production
```

**Важно**: `API_URL` должен быть внешним URL вашего сервера (с доменом или IP), чтобы бот мог к нему обращаться.

### 3. Создание директории для данных

```bash
mkdir -p data/results
touch data/results.json
chmod 666 data/results.json
```

## Развертывание

### Вариант 1: Docker Compose (рекомендуется)

```bash
# Использование production конфигурации
docker-compose -f docker-compose.prod.yml --env-file .env up -d --build

# Просмотр логов
docker-compose -f docker-compose.prod.yml logs -f

# Остановка
docker-compose -f docker-compose.prod.yml down
```

### Вариант 2: С Nginx Reverse Proxy

1. Установите Nginx:

```bash
sudo apt update
sudo apt install nginx
```

2. Создайте конфигурацию Nginx `/etc/nginx/sites-available/parkinson`:

```nginx
server {
    listen 80;
    server_name yourdomain.com;

    location / {
        proxy_pass http://localhost:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

3. Активируйте конфигурацию:

```bash
sudo ln -s /etc/nginx/sites-available/parkinson /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

4. Обновите `.env`:

```env
API_URL=https://yourdomain.com
```

5. Запустите Docker контейнеры:

```bash
docker-compose -f docker-compose.prod.yml --env-file .env up -d
```

### Вариант 3: С SSL (Let's Encrypt)

1. Установите Certbot:

```bash
sudo apt install certbot python3-certbot-nginx
```

2. Получите SSL сертификат:

```bash
sudo certbot --nginx -d yourdomain.com
```

3. Обновите `.env`:

```env
API_URL=https://yourdomain.com
```

4. Перезапустите контейнеры:

```bash
docker-compose -f docker-compose.prod.yml --env-file .env restart
```

## Проверка работы

1. **Проверьте API**:
```bash
curl http://yourdomain.com/api/stats
```

2. **Проверьте веб-интерфейс**:
Откройте в браузере: `http://yourdomain.com`

3. **Проверьте бота**:
Отправьте `/start` боту в Telegram

## Мониторинг

### Просмотр логов

```bash
# Все логи
docker-compose -f docker-compose.prod.yml logs -f

# Только API
docker-compose -f docker-compose.prod.yml logs -f api

# Только бот
docker-compose -f docker-compose.prod.yml logs -f bot
```

### Проверка статуса

```bash
docker-compose -f docker-compose.prod.yml ps
```

### Использование ресурсов

```bash
docker stats
```

## Резервное копирование

### Автоматический бэкап

Создайте скрипт `backup.sh`:

```bash
#!/bin/bash
BACKUP_DIR="/backups/parkinson"
DATE=$(date +%Y%m%d_%H%M%S)
mkdir -p $BACKUP_DIR
cp data/results.json $BACKUP_DIR/results_$DATE.json
# Хранить последние 30 дней
find $BACKUP_DIR -name "results_*.json" -mtime +30 -delete
```

Добавьте в crontab:

```bash
crontab -e
# Бэкап каждый день в 2:00
0 2 * * * /path/to/backup.sh
```

## Обновление

```bash
# Остановить контейнеры
docker-compose -f docker-compose.prod.yml down

# Обновить код (git pull и т.д.)

# Пересобрать и запустить
docker-compose -f docker-compose.prod.yml --env-file .env up -d --build
```

## Устранение неполадок

### Бот не может подключиться к API

- Проверьте, что `API_URL` в `.env` указывает на правильный внешний URL
- Убедитесь, что порт открыт в firewall
- Проверьте логи бота: `docker-compose logs bot`

### API недоступен извне

- Проверьте firewall: `sudo ufw status`
- Откройте порт: `sudo ufw allow 5000/tcp`
- Проверьте, что контейнер запущен: `docker ps`

### Ошибки при обработке аудио

- Проверьте логи: `docker-compose logs api`
- Убедитесь, что все зависимости установлены в образе
- Проверьте права доступа к директории `data/`

## Безопасность

1. **Не храните токены в коде** - используйте `.env` файл
2. **Используйте HTTPS** для production
3. **Ограничьте доступ** к API через firewall
4. **Регулярно обновляйте** зависимости
5. **Делайте бэкапы** `results.json`
