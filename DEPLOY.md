# Развертывание

## Учётки и проверка состояния

Production Compose сохраняет учётки и ключ сессий в `./data/auth/`.
Сохраняйте эту папку при обновлениях и включайте её в резервные копии вместе с результатами.
`SESSION_COOKIE_SECURE` по умолчанию включён в production Compose: используйте HTTPS.
Для отдельной локальной HTTP-проверки задайте `SESSION_COOKIE_SECURE=false`.

После первого запуска импортируйте закрытую выгрузку через `scripts/manage_accounts.py`
(см. README). Файл с хэшами передавайте отдельно от кода и удаляйте временную копию после импорта.
Регистрация посетителями не предусмотрена: новые учётки создают moderator/admin на `/users`.

Healthcheck: `GET /healthz`. `/api/stats` теперь требует входа.

Журнал авторизации доступен только admin в `/auth-logs`, хранится в базе учёток
90 дней. Пароли и токены не записываются. События до внедрения журнала недоступны.
В серверном `.env` задайте `TRUSTED_PROXY_CIDRS=172.22.0.1/32` для текущего
Docker-шлюза lab-сервера. При изменении сети проверьте адрес заново. Приложение
принимает X-Forwarded-For только от указанных прокси; не указывайте `0.0.0.0/0`.

## Как обновляется рабочий сервер

Сервер: lab-сервер, адрес снаружи — https://msr25.ru/audio_park/.

Ничего руками делать не нужно. Порядок такой (`.github/workflows/smoke.yml`):

1. На каждый pull request запускается **smoke-тест** (`scripts/ci_smoke.py`).
2. После слияния в `main` снова идет smoke-тест, и если он зеленый — задание **deploy**:
   - собирает образ `audio_park-api:latest` из `Dockerfile`;
   - перезапускает контейнер `parkinson_api` по `docker-compose.prod.yml`;
   - ждет, пока ответит `http://127.0.0.1:5005/healthz`.

Оба задания выполняет self-hosted runner, установленный на lab-сервере как служба.
С сервера нет стабильного доступа к PyPI, поэтому виртуальное окружение для тестов
кэшируется в `~/.cache/audio_park-ci/` и пересоздается только при изменении `requirements.txt`.

### Что лежит на сервере вне репозитория

Папка `~/audio_park` (это `--project-directory` для docker compose):

- `.env` — настройки (в нем `API_PORT=5005`);
- `data/results.json` и `data/results/` — все результаты и записи. Монтируются в контейнер
  и при выкладке не трогаются.

В production папка `data/` подключается как `/app/data`, а `RESULTS_FILE` указывает
на `/app/data/results.json`. Подключение родительской папки необходимо для атомарной
замены JSON через `os.replace`: отдельный bind mount файла вызывает `EBUSY`.
Путь файла на хосте остаётся `~/audio_park/data/results.json`; перенос данных и ручная
миграция не нужны. Старые пути `/app/results` и `/app/auth` сохраняются отдельными
монтированиями, чтобы ранее сохранённые аудиозаписи и учётные записи оставались доступны.
После обновления нужно пересоздать контейнер с новым compose-файлом; обычный
`docker restart` не применяет изменения монтирований. Штатная выкладка через
`docker compose ... up -d --no-build` применяет новую конфигурацию автоматически.

Локальный `docker-compose.yml` также подключает родительскую папку: рабочий каталог
проекта доступен контейнеру как `/app/local-data`, а JSON остаётся в прежнем
`./results.json` на хосте. При запуске Python без Docker путь по умолчанию —
`results.json`; его можно изменить переменной `RESULTS_FILE`.

Наружу сервис отдает общий контейнер nginx (`msr_map_nginx`), который проксирует
`/audio_park/` на порт 5005. Его конфигурация лежит вне этого репозитория.

### Если что-то пошло не так

```bash
docker logs --tail 100 parkinson_api                 # логи приложения
docker compose -p audio_park -f docker-compose.prod.yml --project-directory ~/audio_park ps
```

Резервная копия данных — это просто копия `~/audio_park/data/`.

## Развертывание на новом сервере

Нужны Docker и docker compose.

```bash
git clone https://github.com/constantintesla/audio_park.git
cd audio_park
cp env.example .env              # при необходимости поменяйте API_PORT
mkdir -p data/results
test -f data/results.json || printf '[]\n' > data/results.json
docker compose -f docker-compose.prod.yml up -d --build
curl http://localhost:5000/healthz
```

Если сервис открывается через nginx, увеличьте лимит на размер файла и таймауты —
анализ длинной записи идет до нескольких минут:

```nginx
location / {
    proxy_pass http://localhost:5000;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    client_max_body_size 50M;
    proxy_read_timeout 300s;
    proxy_send_timeout 300s;
}
```
