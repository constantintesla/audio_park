#!/bin/bash
# Скрипт для локального тестирования: обработка аудио и отправка на сервер
# Использование: ./TEST_LOCAL.sh <путь_к_аудиофайлу> [опции]

if [ -z "$1" ]; then
    echo "Использование: ./TEST_LOCAL.sh <путь_к_аудиофайлу> [опции]"
    echo ""
    echo "Примеры:"
    echo "  ./TEST_LOCAL.sh audio.wav"
    echo "  ./TEST_LOCAL.sh audio.wav --api-url http://localhost:5000"
    echo "  ./TEST_LOCAL.sh audio.wav --username test_user --user-id 123"
    echo "  ./TEST_LOCAL.sh audio.wav --no-send"
    echo "  ./TEST_LOCAL.sh audio.wav --output result.json"
    exit 1
fi

python3 test_local.py "$@"
