# Развертывание и Docker

## 🐳 Docker (локальная разработка)

### Быстрый старт

```bash
# Создайте .env файл
cp env.example .env

# Запустите
docker-compose --env-file .env up -d

# Просмотр логов
docker-compose logs -f

# Остановка
docker-compose down
```

### Структура

- **API контейнер** (`parkinson_api`) - веб-сервер на порту 5000

Контейнер хранит `results.json` и директорию `results/` в смонтированном томе.

### Отладка

**Вход в контейнер:**
```bash
docker exec -it parkinson_api bash
```

**Проверка работы:**
```bash
docker-compose ps
docker-compose logs api
```

---

## 🌐 Развертывание на хостинге (Production)

### Подготовка к развертыванию

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
API_PORT=5000
DEBUG=False
FLASK_ENV=production
```

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
    # Для доступа по IP используйте: server_name _;
    # Для доступа по домену используйте: server_name yourdomain.com;
    server_name _;  # Принимать запросы с любого домена/IP
    
    # Увеличенный размер загружаемых файлов (для аудио)
    client_max_body_size 50M;
    
    location / {
        proxy_pass http://localhost:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        
        # Таймауты для обработки аудио
        proxy_connect_timeout 300s;
        proxy_send_timeout 300s;
        proxy_read_timeout 300s;
        
        # Убираем ограничения буфера
        proxy_buffering off;
        proxy_request_buffering off;
    }
}
```

3. Активируйте конфигурацию:

```bash
sudo ln -s /etc/nginx/sites-available/parkinson /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

4. Запустите Docker контейнеры:

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

3. Перезапустите контейнеры:

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

## Мониторинг

### Просмотр логов

```bash
docker-compose -f docker-compose.prod.yml logs -f api
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

### Ошибка 403 Forbidden при доступе через порт 80

Если при доступе по внешнему IP на порт 80 вы получаете ошибку 403 Forbidden, а на порт 5000 всё работает, проблема в конфигурации nginx.

**Решение:**

1. Проверьте текущую конфигурацию nginx:
```bash
sudo cat /etc/nginx/sites-available/parkinson
```

2. Убедитесь, что в конфигурации используется `server_name _;` (для доступа по IP) вместо `server_name yourdomain.com;`:
```nginx
server {
    listen 80;
    server_name _;  # Для доступа по IP
    # ...
}
```

3. Если используете домен, но хотите также разрешить доступ по IP, используйте:
```nginx
server {
    listen 80;
    server_name _ yourdomain.com;  # И IP, и домен
    # ...
}
```

4. Проверьте конфигурацию и перезагрузите nginx:
```bash
sudo nginx -t
sudo systemctl reload nginx
```

5. Проверьте логи nginx для диагностики:
```bash
sudo tail -f /var/log/nginx/error.log
```

### API недоступен извне

- Проверьте firewall: `sudo ufw status`
- Откройте порт: `sudo ufw allow 5000/tcp`
- Проверьте, что контейнер запущен: `docker ps`

### Ошибки при обработке аудио

- Проверьте логи: `docker-compose logs api`
- Убедитесь, что все зависимости установлены в образе
- Проверьте права доступа к директории `data/`

## Безопасность

### Хранение секретов

⚠️ **НИКОГДА не храните секреты в коде!**

- Используйте переменные окружения через файл `.env`
- Файл `.env` автоматически исключен из Git
- Для production используйте безопасное хранилище секретов

### Дополнительные рекомендации

1. **Используйте HTTPS** для production
2. **Ограничьте доступ** к API через firewall
3. **Регулярно обновляйте** зависимости
4. **Делайте бэкапы** `results.json`
5. **Мониторьте логи** на подозрительную активность
