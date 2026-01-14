FROM python:3.11-slim

# Установка системных зависимостей
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    make \
    libsndfile1 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Создание рабочей директории
WORKDIR /app

# Копирование файлов зависимостей (для лучшего кэширования)
COPY requirements.txt .

# Установка Python зависимостей
RUN pip install --no-cache-dir -r requirements.txt

# Копирование всех файлов проекта
COPY . .

# Создание директории для результатов
RUN mkdir -p /app/results && \
    chmod 755 /app/results

# Открытие порта для API
EXPOSE 5000

# Команда по умолчанию (запуск API)
CMD ["python", "start_api.py"]
