"""
Скрипт для локального тестирования: обработка аудио и отправка результатов на сервер
"""
import os
import sys
import argparse
import json
import requests
from datetime import datetime
from typing import Optional

# Загрузка переменных окружения
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    # Если python-dotenv не установлен, пробуем загрузить вручную
    env_file = '.env'
    if os.path.exists(env_file):
        with open(env_file, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    os.environ[key.strip()] = value.strip()

from parkinson_analyzer import ParkinsonAnalyzer


def analyze_and_send(
    audio_file: str,
    api_url: Optional[str] = None,
    username: str = "test_user",
    user_id: int = 0,
    save_raw: bool = True,
    verbose: bool = True
) -> dict:
    """
    Обработка аудиофайла локально и отправка результатов на сервер
    
    Args:
        audio_file: Путь к аудиофайлу
        api_url: URL API сервера (если не указан, берется из переменных окружения)
        username: Имя пользователя для сохранения в результатах
        user_id: ID пользователя
        save_raw: Сохранять ли сырые данные
        verbose: Выводить ли подробную информацию
    
    Returns:
        Словарь с результатами обработки и отправки
    """
    result = {
        "success": False,
        "local_analysis": None,
        "server_response": None,
        "error": None
    }
    
    # Получение URL API
    if api_url is None:
        api_url = os.getenv('API_URL', 'http://localhost:5000')
    
    # Убираем слэш в конце, если есть
    api_url = api_url.rstrip('/')
    
    if verbose:
        print(f"🔍 Начало обработки файла: {audio_file}")
        print(f"🌐 API URL: {api_url}")
    
    # Проверка существования файла
    if not os.path.exists(audio_file):
        error_msg = f"Файл не найден: {audio_file}"
        result["error"] = error_msg
        if verbose:
            print(f"❌ {error_msg}")
        return result
    
    try:
        # 1. Локальная обработка аудио
        if verbose:
            print("\n📊 Шаг 1: Локальная обработка аудио...")
        
        analyzer = ParkinsonAnalyzer(save_raw_data=save_raw)
        analysis_result = analyzer.analyze_audio_file(audio_file, save_raw=save_raw)
        
        # Проверка на ошибки в результате
        if "error" in analysis_result:
            error_msg = analysis_result.get("error", "Неизвестная ошибка при анализе")
            result["error"] = error_msg
            if verbose:
                print(f"❌ Ошибка анализа: {error_msg}")
            return result
        
        result["local_analysis"] = analysis_result
        
        # Удаляем visuals для уменьшения размера результата
        if "visuals" in analysis_result:
            del analysis_result["visuals"]
        
        if verbose:
            print("✅ Локальный анализ завершен")
            print(f"   DSI Score: {analysis_result.get('dsi', {}).get('dsi_score', 'N/A')}")
            print(f"   Риск ПД: {analysis_result.get('symptom_scores', {}).get('pd_risk', 'N/A')}")
        
        # 2. Добавление информации о пользователе
        analysis_result['user_info'] = {
            'tg_username': username,
            'tg_user_id': user_id,
            'timestamp': datetime.now().isoformat(),
            'source': 'local_test',
            'filename': os.path.basename(audio_file)
        }
        
        # 3. Отправка на сервер
        if verbose:
            print(f"\n📤 Шаг 2: Отправка результатов на сервер {api_url}...")
        
        api_endpoint = f"{api_url}/api/results"
        
        try:
            response = requests.post(
                api_endpoint,
                json=analysis_result,
                headers={'Content-Type': 'application/json'},
                timeout=30
            )
            
            result["server_response"] = {
                "status_code": response.status_code,
                "response": response.json() if response.headers.get('content-type', '').startswith('application/json') else response.text
            }
            
            if response.status_code == 200:
                result["success"] = True
                if verbose:
                    print("✅ Результаты успешно отправлены на сервер")
                    server_data = result["server_response"]["response"]
                    if isinstance(server_data, dict):
                        print(f"   Всего результатов на сервере: {server_data.get('total', 'N/A')}")
            else:
                error_msg = f"Сервер вернул код {response.status_code}: {result['server_response']['response']}"
                result["error"] = error_msg
                if verbose:
                    print(f"❌ {error_msg}")
        
        except requests.exceptions.ConnectionError:
            error_msg = f"Не удалось подключиться к серверу {api_url}. Убедитесь, что сервер запущен."
            result["error"] = error_msg
            if verbose:
                print(f"❌ {error_msg}")
        except requests.exceptions.Timeout:
            error_msg = "Превышено время ожидания ответа от сервера"
            result["error"] = error_msg
            if verbose:
                print(f"❌ {error_msg}")
        except Exception as e:
            error_msg = f"Ошибка при отправке на сервер: {str(e)}"
            result["error"] = error_msg
            if verbose:
                print(f"❌ {error_msg}")
    
    except Exception as e:
        error_msg = f"Ошибка обработки: {str(e)}"
        result["error"] = error_msg
        if verbose:
            print(f"❌ {error_msg}")
            import traceback
            traceback.print_exc()
    
    return result


def main():
    """Главная функция для запуска из командной строки"""
    parser = argparse.ArgumentParser(
        description='Локальное тестирование: обработка аудио и отправка результатов на сервер',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Примеры использования:
  # Обработка файла с отправкой на локальный сервер
  python test_local.py audio.wav
  
  # Обработка с указанием URL сервера
  python test_local.py audio.wav --api-url http://localhost:5000
  
  # Обработка без отправки на сервер (только локальный анализ)
  python test_local.py audio.wav --no-send
  
  # Обработка с сохранением результата в JSON файл
  python test_local.py audio.wav --output result.json
        """
    )
    
    parser.add_argument(
        'audio_file',
        type=str,
        help='Путь к аудиофайлу (WAV/MP3/OGG)'
    )
    
    parser.add_argument(
        '--api-url',
        type=str,
        default=None,
        help='URL API сервера (по умолчанию из переменной окружения API_URL или http://localhost:5000)'
    )
    
    parser.add_argument(
        '--username',
        type=str,
        default='test_user',
        help='Имя пользователя для сохранения в результатах (по умолчанию: test_user)'
    )
    
    parser.add_argument(
        '--user-id',
        type=int,
        default=0,
        help='ID пользователя (по умолчанию: 0)'
    )
    
    parser.add_argument(
        '--no-send',
        action='store_true',
        help='Не отправлять результаты на сервер (только локальная обработка)'
    )
    
    parser.add_argument(
        '--no-raw-data',
        action='store_true',
        help='Не сохранять сырые данные при обработке'
    )
    
    parser.add_argument(
        '-o', '--output',
        type=str,
        default=None,
        help='Путь для сохранения JSON результата (если не указан, вывод в stdout)'
    )
    
    parser.add_argument(
        '-q', '--quiet',
        action='store_true',
        help='Тихий режим (минимальный вывод)'
    )
    
    args = parser.parse_args()
    
    # Обработка файла
    result = analyze_and_send(
        audio_file=args.audio_file,
        api_url=None if args.no_send else args.api_url,
        username=args.username,
        user_id=args.user_id,
        save_raw=not args.no_raw_data,
        verbose=not args.quiet
    )
    
    # Сохранение или вывод результата
    output_data = {
        "timestamp": datetime.now().isoformat(),
        "audio_file": args.audio_file,
        "success": result["success"],
        "local_analysis": result.get("local_analysis"),
        "server_response": result.get("server_response"),
        "error": result.get("error")
    }
    
    if args.output:
        with open(args.output, 'w', encoding='utf-8') as f:
            json.dump(output_data, f, ensure_ascii=False, indent=2)
        if not args.quiet:
            print(f"\n💾 Результат сохранен в: {args.output}")
    else:
        if not args.quiet:
            print("\n" + "="*60)
            print("РЕЗУЛЬТАТ:")
            print("="*60)
        print(json.dumps(output_data, ensure_ascii=False, indent=2))
    
    # Код выхода
    sys.exit(0 if result["success"] or args.no_send else 1)


if __name__ == '__main__':
    main()
