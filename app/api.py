"""
API сервер для сохранения и получения результатов анализа
"""
import os
import json
import math
from html import escape as html_escape
from datetime import datetime
from typing import List, Dict, Any, Optional
from flask import Flask, request, jsonify, send_from_directory, Response, g
import logging
import numpy as np
from threading import RLock
from functools import wraps
from app.auth import (
    archive_patient,
    create_patient,
    get_patient,
    get_patient_by_legacy_id,
    init_auth,
    is_manager,
    list_patients,
    owns_result,
    visible_results,
)
from app.dsi_protocol import parse_segments
from app.speech_protocol import parse_protocol_segments
from app.device_model_stats import loop_gain_check, model_spl_offset

from app.speaker_baseline import compare_to_baseline
from app.visualization import PARTS as VIZ_PARTS, chart_data, saved_waveform
from app.task_recordings import task_attempts, source_audio, clip_wav, clip_filename, build_archive

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__, static_folder=None)
init_auth(app)
RESULTS_LOCK = RLock()


def locked_results(func):
    @wraps(func)
    def wrapped(*args, **kwargs):
        with RESULTS_LOCK:
            return func(*args, **kwargs)
    return wrapped

# Страницы веб-интерфейса и assets/
WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'web')

# Данные хранятся в текущей папке (в контейнере это /app, туда смонтированы тома)
RESULTS_FILE = "results.json"
RESULTS_DIR = "results"

# Создание директории для результатов, если её нет
os.makedirs(RESULTS_DIR, exist_ok=True)

# После анализа и визуализации освобожденная память остается у процесса
# (так работает malloc в glibc), и контейнер долго держит пиковый объем.
# malloc_trim возвращает свободные страницы системе.
try:
    import ctypes
    _malloc_trim = ctypes.CDLL('libc.so.6').malloc_trim
except (OSError, AttributeError):
    _malloc_trim = None

HEAVY_ENDPOINTS = {'analyze_audio', 'recalculate_all', 'get_visualization_data'}


@app.teardown_request
def release_memory(exc=None):
    if _malloc_trim is not None and request.endpoint in HEAVY_ENDPOINTS:
        import gc
        gc.collect()
        _malloc_trim(0)


@locked_results
def load_results() -> List[Dict]:
    """Загрузка результатов из файла"""
    if os.path.exists(RESULTS_FILE):
        try:
            with open(RESULTS_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                # Убеждаемся, что это список
                if isinstance(data, list):
                    return data
                else:
                    logger.warning("Файл results.json не содержит список, инициализируем пустым списком")
                    return []
        except json.JSONDecodeError as e:
            logger.error(f"Ошибка парсинга JSON: {e}")
            raise RuntimeError("Хранилище результатов повреждено") from e
        except Exception as e:
            logger.error(f"Ошибка загрузки результатов: {e}")
            raise
    else:
        # Файл не существует, создаем пустой список
        logger.info(f"Файл {RESULTS_FILE} не найден, создаем новый")
        save_results([])
        return []


def clean_json_values(obj: Any) -> Any:
    """
    Рекурсивная очистка значений от inf, -inf и NaN для JSON сериализации
    
    Args:
        obj: Объект для очистки (dict, list, или примитив)
    
    Returns:
        Очищенный объект с заменой недопустимых значений на None или 0.0
    """
    if isinstance(obj, dict):
        return {key: clean_json_values(value) for key, value in obj.items()}
    elif isinstance(obj, list):
        return [clean_json_values(item) for item in obj]
    elif isinstance(obj, (float, np.floating)):
        # Проверяем на inf, -inf и nan
        if math.isinf(obj) or math.isnan(obj):
            logger.warning(f"Обнаружено недопустимое значение float: {obj}, заменяю на 0.0")
            return 0.0
        # Проверяем на очень большие числа, которые могут вызвать проблемы
        if abs(obj) > 1e10:
            logger.warning(f"Обнаружено очень большое значение: {obj}, ограничиваю до 1e10")
            return 1e10 if obj > 0 else -1e10
        return float(obj)
    elif isinstance(obj, (int, np.integer)):
        # Проверяем на очень большие целые числа
        if abs(obj) > 2**31 - 1:  # Максимальное значение для JSON int
            logger.warning(f"Обнаружено очень большое целое: {obj}, конвертирую в float")
            return float(obj)
        return int(obj)
    elif isinstance(obj, np.ndarray):
        # Конвертируем numpy массивы в списки
        return clean_json_values(obj.tolist())
    else:
        # Для остальных типов (str, None, bool) возвращаем как есть
        return obj


@app.before_request
def authorize_result():
    """One ownership check for details, visualizations and audio."""
    key = (request.view_args or {}).get('result_key')
    if key is None or not g.user:
        return None
    with RESULTS_LOCK:
        results = load_results()
    index = next((i for i, result in enumerate(results) if result.get('study_id') == key), None)
    if index is None and key.isdigit() and int(key) < len(results):
        index = int(key)
    if index is None or not (is_manager() or owns_result(results[index])):
        return jsonify(error="Исследование не найдено"), 404
    g.result_index = index


def without_visuals(result: Dict) -> Dict:
    """
    Результат без картинок (base64 PNG): они нужны только в ответе на анализ.
    В results.json они занимали почти весь объем (~0,5 МБ на запись), а файл
    читается целиком при каждом запросе списка и статистики.
    """
    return {k: v for k, v in result.items() if k != 'visuals'}


@locked_results
def save_results(results: List[Dict]):
    """Сохранение результатов в файл"""
    try:
        # Получаем абсолютный путь к файлу
        file_path = os.path.abspath(RESULTS_FILE)
        logger.info(f"Сохранение результатов в файл: {file_path} (количество записей: {len(results)})")
        
        # Очищаем результаты от недопустимых значений перед сохранением
        cleaned_results = clean_json_values(results)
        
        with open(RESULTS_FILE, 'w', encoding='utf-8') as f:
            json.dump(cleaned_results, f, ensure_ascii=False, indent=2)
        
        # Проверяем, что файл создан
        if os.path.exists(RESULTS_FILE):
            file_size = os.path.getsize(RESULTS_FILE)
            logger.info(f"Файл успешно сохранен. Размер: {file_size} байт")
        else:
            logger.error(f"Файл не был создан: {file_path}")
    except Exception as e:
        logger.error(f"Ошибка сохранения результатов: {e}", exc_info=True)
        raise


def sync_legacy_patients(owner_id: int) -> None:
    """Give old owner-scoped patient IDs stable UUIDs without changing their displayed labels."""
    with RESULTS_LOCK:
        results = load_results()
        changed = False
        for result in results:
            if result.get('owner_id') != owner_id or result.get('patient_uuid'):
                continue
            legacy_id = str(result.get('patient_id') or '').strip()
            if not legacy_id:
                continue
            patient = get_patient_by_legacy_id(owner_id, legacy_id) or create_patient(owner_id, legacy_id)
            result['patient_uuid'] = patient['uuid']
            changed = True
        if changed:
            save_results(results)


@app.route('/api/patients', methods=['GET'])
def get_patients():
    """Patients are private to the signed-in doctor; local numbers may repeat across doctors."""
    sync_legacy_patients(g.user['id'])
    results = load_results()
    stats = {}
    for result in results:
        if result.get('owner_id') != g.user['id'] or not result.get('patient_uuid'):
            continue
        item = stats.setdefault(result['patient_uuid'], {'study_count': 0, 'last_visit': None})
        item['study_count'] += 1
        timestamp = result.get('user_info', {}).get('timestamp')
        if timestamp and (not item['last_visit'] or timestamp > item['last_visit']):
            item['last_visit'] = timestamp
    patients = []
    for patient in list_patients(g.user['id'], request.args.get('include_archived') == '1'):
        patients.append({**patient, **stats.get(patient['uuid'], {'study_count': 0, 'last_visit': None})})
    return jsonify(patients=patients, count=len(patients))


@app.route('/api/patients', methods=['POST'])
def add_patient():
    patient = create_patient(g.user['id'])
    return jsonify(patient=patient), 201


@app.route('/api/patients/<patient_uuid>/archive', methods=['POST'])
def archive_patient_route(patient_uuid: str):
    patient = archive_patient(patient_uuid, g.user['id'])
    if not patient:
        return jsonify(error="Пациент не найден"), 404
    return jsonify(patient=patient)


# Поля метаданных устройства, которые принимаются от приложения
DEVICE_INFO_FIELDS = {
    'device_id': str,          # стабильный идентификатор устройства в приложении
    'device_model': str,       # модель телефона
    'os': str,                 # ОС и версия
    'app_version': str,
    'mic_mode': str,           # режим микрофона: 'unprocessed' / 'measurement' / 'default'
    'input_format': str,       # формат записи: 'wav', 'm4a' и т.п.
    'sample_rate': int,        # частота дискретизации записи
    'noise_floor_dbfs': float, # уровень шума по калибровочной тишине перед записью
    'spl_offset_db': float,    # поправка dBFS -> dB SPL из калибровки устройства
    'task': str,               # что записано: 'speech' / 'vowel' / 'dsi' / 'speech_protocol' / 'exam' (см. README)
    'dsi_segments': parse_segments,  # границы упражнений теста DSI (см. dsi_protocol.py)
    'protocol_segments': parse_protocol_segments,  # границы речевых заданий (см. speech_protocol.py)
}


def parse_device_info(raw: Optional[str]) -> Dict:
    """Разбор JSON с метаданными устройства; неизвестные и некорректные поля отбрасываются"""
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        logger.warning("Некорректный JSON в device_info, поле проигнорировано")
        return {}
    if not isinstance(data, dict):
        return {}
    info = {}
    for key, cast in DEVICE_INFO_FIELDS.items():
        if data.get(key) is None:
            continue
        try:
            value = cast(data[key])
        except (ValueError, TypeError):
            continue
        if isinstance(value, float) and not math.isfinite(value):
            continue
        info[key] = value[:200] if isinstance(value, str) else value
    return info


def add_loop_gain_check(result: Dict, calibration: Dict, history: List[Dict], device_info: Dict) -> None:
    """Сверка уровня тестового звука с обычным для модели; при отклонении - строка в отчете"""
    check = loop_gain_check(calibration, history, device_info)
    if not check:
        return
    result['calibration_check'] = check
    if check['status'] == 'deviation' and isinstance(result.get('report'), list):
        result['report'].insert(0, f"⚠️ {check['message']}")


MAX_CALIBRATION_JSON = 20000  # байт; результаты калибровки — небольшой словарь


def parse_calibration(raw: Optional[str]) -> Optional[Dict]:
    """
    Результаты калибровки, которые веб-рекордер измерил перед записью
    (тишина + тестовый свип + ступенька тона). Сохраняются в результат как есть.
    """
    if not raw or len(raw) > MAX_CALIBRATION_JSON:
        return None
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        logger.warning("Некорректный JSON в calibration, поле проигнорировано")
        return None
    return clean_json_values(data) if isinstance(data, dict) else None


def save_calibration_audio(file, result: Dict) -> None:
    """Сохраняет запись калибровки рядом с сырыми данными анализа"""
    data_dir = result.get('raw_data', {}).get('data_directory')
    if not file or not data_dir or not os.path.isdir(data_dir):
        return
    try:
        path = os.path.join(data_dir, 'calibration.wav')
        file.save(path)
        result['raw_data'].setdefault('files', {})['calibration_audio'] = path
    except Exception as e:
        logger.warning(f"Не удалось сохранить запись калибровки: {e}")


@app.route('/api/analyze', methods=['POST'])
def analyze_audio():
    """Анализ аудиофайла с сохранением сырых данных"""
    try:
        from app.parkinson_analyzer import ParkinsonAnalyzer
        
        # Проверка наличия файла
        if 'file' not in request.files:
            return jsonify({"error": "Файл не предоставлен"}), 400
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({"error": "Файл не выбран"}), 400
        
        # Сохранение временного файла
        import tempfile
        import uuid
        patient_uuid = request.form.get('patient_uuid', '').strip()
        patient = get_patient(patient_uuid, g.user['id']) if patient_uuid else None
        if patient_uuid and (not patient or patient['status'] != 'active'):
            return jsonify(error="Выбранный пациент не найден или архивирован"), 400
        # Backward compatibility for integrations and old clients. The web UI always sends patient_uuid.
        patient_id = patient['label'] if patient else request.form.get('patient_id', '').strip()
        if not patient_id or len(patient_id) > 100 or any(ord(c) < 32 for c in patient_id):
            return jsonify(error="Выберите пациента"), 400
        result_id = f"AP-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex}"
        
        temp_dir = tempfile.gettempdir()
        suffix = os.path.splitext(file.filename)[1].lower()
        if suffix not in {'.wav', '.mp3', '.ogg', '.oga', '.webm', '.m4a', '.flac', '.aac'}:
            return jsonify(error="Неподдерживаемый формат аудио"), 400
        temp_file = os.path.join(temp_dir, f"temp_{result_id}{suffix}")
        file.save(temp_file)
        
        try:
            # Анализ с сохранением сырых данных
            # Используем абсолютный путь для RESULTS_DIR
            results_dir_abs = os.path.abspath(RESULTS_DIR)
            logger.info(f"📁 Сохранение сырых данных в: {results_dir_abs}")
            device_info = parse_device_info(request.form.get('device_info'))
            history = load_results()
            analyzer = ParkinsonAnalyzer(save_raw_data=True, raw_data_dir=results_dir_abs)
            result = analyzer.analyze_audio_file(temp_file, save_raw=True, result_id=result_id,
                                                 device_info=device_info,
                                                 model_offset=model_spl_offset(history, device_info))
            if 'error' in result:
                return jsonify(result), 422
            result['study_id'] = result_id
            result['patient_id'] = patient_id
            if patient:
                result['patient_uuid'] = patient['uuid']
                result['patient_local_number'] = patient['local_number']
            result['owner_id'] = g.user['id']
            calibration = parse_calibration(request.form.get('calibration'))
            if calibration:
                result['calibration'] = calibration
                add_loop_gain_check(result, calibration, history, device_info)
            save_calibration_audio(request.files.get('calibration_file'), result)
            
            # Добавление информации о пользователе
            result['user_info'] = {
                'tg_username': g.user['login'],
                'tg_user_id': g.user['id'],
                'timestamp': datetime.now().isoformat(),
                'source': 'web_interface',
                'filename': file.filename
            }
            
            # Сохранение результата
            # Результат уже очищен в parkinson_analyzer, но дополнительно проверяем
            with RESULTS_LOCK:
                results = load_results()
                if patient:
                    history = [r for r in results if r.get('patient_uuid') == patient['uuid'] and owns_result(r)]
                else:
                    history = [r for r in results if r.get('patient_id') == patient_id and owns_result(r)]
                result['baseline'] = compare_to_baseline(
                    result.get('features', {}), history,
                    result['user_info']['tg_user_id'], device_info,
                    result.get('quality', {}).get('unreliable_features'))
                cleaned_result = clean_json_values(without_visuals(result))
                results.append(cleaned_result)
                save_results(results)
                # В ответ (не в файл): индекс для ссылок на записи заданий и сами попытки
                result['_index'] = len(results) - 1
                result['task_recordings'] = task_attempts(cleaned_result)
            
            # Проверяем наличие сырых данных в результате
            if 'raw_data' in result:
                logger.info(f"✅ Сырые данные сохранены для {result_id}: {result['raw_data'].get('data_directory', 'N/A')}")
                logger.info(f"   Файлы: {list(result['raw_data'].get('files', {}).keys())}")
            else:
                logger.warning(f"⚠️  Сырые данные НЕ сохранены для {result_id}")
            
            logger.info(f"Анализ выполнен и сохранен: {result_id}")
            
            return jsonify(result), 200
            
        finally:
            # Удаление временного файла
            try:
                if os.path.exists(temp_file):
                    os.remove(temp_file)
            except:
                pass
        
    except Exception as e:
        logger.error(f"Ошибка анализа аудио: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route('/api/results', methods=['POST'])
def save_result():
    return jsonify(error="Результат сохраняется автоматически через /api/analyze"), 405


@app.route('/api/results', methods=['GET'])
def get_results():
    """Получение всех результатов"""
    try:
        all_results = load_results()
        
        # Фильтрация по user_id, если указан,
        # при этом сохраняем исходный индекс записи в файле results.json
        user_id = request.args.get('user_id', type=int)
        visible = visible_results(all_results)
        allowed_ids = {id(result) for result in visible}
        indexed_results = []
        for original_index, result in enumerate(all_results):
            if id(result) not in allowed_ids:
                continue
            if user_id is not None:
                if result.get('user_info', {}).get('tg_user_id') != user_id:
                    continue
            # Не мутируем исходный объект, чтобы не портить данные на диске
            result_with_index = without_visuals(result)
            result_with_index['_index'] = original_index
            result_with_index['task_recordings'] = task_attempts(result)
            indexed_results.append(result_with_index)
        
        # Сортировка по дате (новые первыми), но индекс остаётся исходным
        indexed_results.sort(
            key=lambda x: x.get('user_info', {}).get('timestamp', ''),
            reverse=True
        )
        
        # Ограничение количества результатов (опционально)
        limit = request.args.get('limit', type=int)
        if limit:
            indexed_results = indexed_results[:limit]
        
        return jsonify({"results": indexed_results, "count": len(indexed_results)}), 200
        
    except Exception as e:
        logger.error(f"Ошибка получения результатов: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/recalculate-all', methods=['POST'])
@locked_results
def recalculate_all():
    """Пересчет всех результатов с обновленными параметрами (без капов и фолбеков)"""
    try:
        from app.parkinson_analyzer import ParkinsonAnalyzer
        
        logger.info("🔄 Начало пересчета всех результатов...")
        results = load_results()
        
        if not results:
            return jsonify({"status": "success", "message": "Нет результатов для пересчета", "recalculated": 0}), 200
        
        # Используем абсолютный путь для RESULTS_DIR
        results_dir_abs = os.path.abspath(RESULTS_DIR)
        analyzer = ParkinsonAnalyzer(save_raw_data=False, raw_data_dir=results_dir_abs)
        
        recalculated = 0
        errors = []
        
        for idx, result in enumerate(results):
            try:
                # Получаем путь к оригинальному аудиофайлу
                raw_data = result.get('raw_data', {})
                files = raw_data.get('files', {}) if isinstance(raw_data, dict) else {}
                audio_path = files.get('original_audio') or files.get('processed_audio')
                
                if not audio_path or not os.path.exists(audio_path):
                    logger.warning(f"⚠️  Результат {idx}: аудиофайл не найден: {audio_path}")
                    errors.append(f"Результат {idx}: аудиофайл не найден")
                    continue
                
                # Получаем result_id из результата
                result_id = raw_data.get('result_id') or result.get('user_info', {}).get('timestamp', '').replace(':', '').replace('-', '')[:15]
                
                # Пересчитываем анализ
                logger.info(f"🔄 Пересчет результата {idx + 1}/{len(results)}: {audio_path}")
                # device_info сохраняем, иначе при пересчете теряются калибровки устройства
                device_info = result.get('device_info') or {}
                others = results[:idx] + results[idx + 1:]
                new_result = analyzer.analyze_audio_file(audio_path, save_raw=False, result_id=result_id,
                                                         device_info=device_info,
                                                         model_offset=model_spl_offset(others, device_info))
                if isinstance(result.get('calibration'), dict):
                    add_loop_gain_check(new_result, result['calibration'], others, device_info)
                
                # Сохраняем оригинальную информацию о пользователе
                original_user_info = result.get('user_info', {})
                new_result['user_info'] = original_user_info
                for key in ('study_id', 'patient_id', 'patient_uuid', 'patient_local_number', 'owner_id'):
                    if key in result:
                        new_result[key] = result[key]
                
                # Сохраняем оригинальные raw_data (пути к файлам)
                if raw_data:
                    new_result['raw_data'] = raw_data
                if result.get('calibration'):
                    new_result['calibration'] = result['calibration']
                
                # Обновляем результат в списке
                results[idx] = clean_json_values(without_visuals(new_result))
                recalculated += 1
                logger.info(f"✅ Результат {idx + 1} пересчитан успешно")
                
            except Exception as e:
                logger.error(f"❌ Ошибка при пересчете результата {idx}: {e}", exc_info=True)
                errors.append(f"Результат {idx}: {str(e)}")
                continue
        
        # Сохраняем все пересчитанные результаты
        if recalculated > 0:
            save_results(results)
            logger.info(f"✅ Сохранено {recalculated} пересчитанных результатов")
        
        response_data = {
            "status": "success",
            "message": f"Пересчитано {recalculated} из {len(results)} результатов",
            "recalculated": recalculated,
            "total": len(results),
            "errors": errors if errors else None
        }
        
        return jsonify(response_data), 200
        
    except Exception as e:
        logger.error(f"Ошибка пересчета всех результатов: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route('/api/results/<result_key>', methods=['GET'])
def get_result(result_key: str):
    """Получение доступного исследования по ID (старые индексы тоже поддержаны)."""
    index = g.result_index
    try:
        results = load_results()
        
        if index < 0 or index >= len(results):
            return jsonify({"error": "Индекс вне диапазона"}), 404
        
        result = without_visuals(results[index])
        result['_index'] = index
        result['task_recordings'] = task_attempts(result)
        return jsonify(result), 200
        
    except Exception as e:
        logger.error(f"Ошибка получения результата: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/visualization/<result_key>', methods=['GET'])
def get_visualization_data(result_key: str):
    """Получение данных для визуализации параметров"""
    index = g.result_index
    try:
        results = load_results()
        
        if index < 0 or index >= len(results):
            return jsonify({"error": "Индекс вне диапазона"}), 404
        
        result = results[index]
        
        # Проверяем наличие сырых данных
        raw_data = result.get('raw_data', {})
        if not raw_data:
            return jsonify({"error": "Сырые данные не найдены для этого результата"}), 404
        
        data_directory = raw_data.get('data_directory')
        if not data_directory or not os.path.exists(data_directory):
            return jsonify({"error": "Директория с сырыми данными не найдена"}), 404
        
        # Загрузка данных сегментов
        segments_data = []
        segment_features_file = raw_data.get('files', {}).get('segment_features')
        if segment_features_file and os.path.exists(segment_features_file):
            try:
                with open(segment_features_file, 'r', encoding='utf-8') as f:
                    segments_data = json.load(f)
                # Очистка от NaN
                segments_data = clean_json_values(segments_data) if segments_data else []
            except Exception as e:
                logger.warning(f"Ошибка загрузки данных сегментов: {e}")
        
        # Загрузка метаданных спектрограммы
        spectrogram_meta = None
        spectrogram_meta_file = raw_data.get('files', {}).get('spectrogram_meta')
        if spectrogram_meta_file and os.path.exists(spectrogram_meta_file):
            try:
                with open(spectrogram_meta_file, 'r', encoding='utf-8') as f:
                    spectrogram_meta = json.load(f)
            except Exception as e:
                logger.warning(f"Ошибка загрузки метаданных спектрограммы: {e}")
        
        # Получение пути к обработанному аудио
        audio_url = None
        processed_audio = raw_data.get('files', {}).get('processed_audio')
        if processed_audio and os.path.exists(processed_audio):
            # Возвращаем относительный путь для доступа через статический сервер
            audio_url = f"/api/audio/{result.get('study_id') or index}"
        else:
            # Пробуем исходный файл
            original_audio = raw_data.get('files', {}).get('original_audio')
            if original_audio and os.path.exists(original_audio):
                audio_url = f"/api/audio/{result.get('study_id') or index}"
        
        # Какие графики нужны: страница просит каждый отдельно (part=...),
        # без part считаются все сразу
        part = request.args.get('part')
        if part is not None and part not in VIZ_PARTS:
            return jsonify({"error": f"Неизвестный график: {part}"}), 400
        parts = (part,) if part else VIZ_PARTS
        
        # Используем обработанное аудио, если доступно, иначе исходное
        audio_file = None
        if processed_audio and os.path.exists(processed_audio):
            audio_file = processed_audio
        else:
            original_audio = raw_data.get('files', {}).get('original_audio')
            if original_audio and os.path.exists(original_audio):
                audio_file = original_audio
        
        charts = {p: {} for p in parts}
        if audio_file:
            try:
                charts = chart_data(audio_file, parts)
            except Exception as e:
                logger.error(f"Ошибка обработки аудио для визуализации: {e}", exc_info=True)

        # Без аудио волну берем из сохраненного файла
        waveform_file = raw_data.get('files', {}).get('waveform_data')
        if 'waveform' in parts and not charts['waveform'] and waveform_file and os.path.exists(waveform_file):
            try:
                charts['waveform'] = saved_waveform(waveform_file)
            except Exception as e:
                logger.warning(f"Ошибка загрузки waveform данных: {e}")

        if part:
            return jsonify(clean_json_values({**charts, 'audio_url': audio_url})), 200
        
        # Подготовка данных сегментов с временными метками
        segments_with_time = []
        if segments_data:
            current_time = 0
            for seg_data in segments_data:
                duration = seg_data.get('duration_sec', 0)
                # Очищаем features от NaN
                features = seg_data.get('features', {})
                cleaned_features = clean_json_values(features) if features else {}
                
                segments_with_time.append({
                    'segment_index': seg_data.get('segment_index', len(segments_with_time)),
                    'start_time': float(current_time) if np.isfinite(current_time) else 0.0,
                    'end_time': float(current_time + duration) if np.isfinite(current_time + duration) else 0.0,
                    'duration_sec': float(duration) if np.isfinite(duration) else 0.0,
                    'features': cleaned_features
                })
                current_time += duration + 0.1  # Небольшая пауза между сегментами
        
        # Формирование ответа
        visualization_data = {
            **charts,
            'segments': segments_with_time,
            'audio_url': audio_url,
            'spectrogram_meta': spectrogram_meta or {}
        }
        
        # Очистка данных от NaN и inf перед отправкой
        cleaned_data = clean_json_values(visualization_data)
        
        return jsonify(cleaned_data), 200
        
    except Exception as e:
        logger.error(f"Ошибка получения данных визуализации: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route('/api/audio/<result_key>', methods=['GET'])
def get_audio_file(result_key: str):
    """Получение аудиофайла для воспроизведения"""
    index = g.result_index
    try:
        results = load_results()
        
        if index < 0 or index >= len(results):
            return jsonify({"error": "Индекс вне диапазона"}), 404
        
        result = results[index]
        raw_data = result.get('raw_data', {})
        
        if not raw_data:
            return jsonify({"error": "Сырые данные не найдены"}), 404
        
        # Пробуем обработанный аудио
        processed_audio = raw_data.get('files', {}).get('processed_audio')
        if processed_audio and os.path.exists(processed_audio):
            return send_from_directory(os.path.abspath(os.path.dirname(processed_audio)),
                                     os.path.basename(processed_audio),
                                     mimetype='audio/wav')
        
        # Пробуем исходный файл
        original_audio = raw_data.get('files', {}).get('original_audio')
        if original_audio and os.path.exists(original_audio):
            ext = os.path.splitext(original_audio)[1].lower()
            mimetype_map = {
                '.wav': 'audio/wav',
                '.ogg': 'audio/ogg',
                '.mp3': 'audio/mpeg'
            }
            mimetype = mimetype_map.get(ext, 'audio/wav')
            return send_from_directory(os.path.abspath(os.path.dirname(original_audio)),
                                     os.path.basename(original_audio),
                                     mimetype=mimetype)
        
        return jsonify({"error": "Аудиофайл не найден"}), 404
        
    except Exception as e:
        logger.error(f"Ошибка получения аудиофайла: {e}")
        return jsonify({"error": str(e)}), 500


def _download_name(result: Dict, index: int) -> str:
    """Имя архива: пользователь и время записи (латиница и цифры, чтобы не ломать заголовок)"""
    info = result.get('user_info') or {}
    stamp = ''.join(ch for ch in str(info.get('timestamp', ''))[:16] if ch.isdigit())
    user = ''.join(ch for ch in str(info.get('tg_username', '')) if ch.isascii() and (ch.isalnum() or ch in '-_'))
    return '_'.join(part for part in ('audio_park', user, stamp or str(index)) if part)


@app.route('/api/audio/<result_key>/task/<int:n>', methods=['GET'])
def get_task_audio(result_key: str, n: int):
    index = g.result_index
    """Запись одной попытки задания; ?download=1 - скачать файлом"""
    try:
        results = load_results()
        if index < 0 or index >= len(results):
            return jsonify({"error": "Индекс вне диапазона"}), 404
        result = results[index]
        attempts = task_attempts(result)
        source = source_audio(result)
        if n < 0 or n >= len(attempts) or not source:
            return jsonify({"error": "Запись задания не найдена"}), 404
        item = attempts[n]
        headers = {}
        if request.args.get('download'):
            name = f"{_download_name(result, index)}_{clip_filename(item)}"
            headers['Content-Disposition'] = f'attachment; filename="{name}"'
        return Response(clip_wav(source, item['start_sec'], item['end_sec']),
                        mimetype='audio/wav', headers=headers)
    except Exception as e:
        logger.error(f"Ошибка получения записи задания: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route('/api/archive/<result_key>', methods=['GET'])
def get_archive(result_key: str):
    index = g.result_index
    """ZIP всего прогона: запись целиком, записи заданий и results.json"""
    try:
        results = load_results()
        if index < 0 or index >= len(results):
            return jsonify({"error": "Индекс вне диапазона"}), 404
        result = results[index]
        name = _download_name(result, index) + '.zip'
        return Response(build_archive(result), mimetype='application/zip',
                        headers={'Content-Disposition': f'attachment; filename="{name}"'})
    except Exception as e:
        logger.error(f"Ошибка сборки архива: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route('/api/health', methods=['GET'])
def api_health():
    """Проверка, что сервер жив (для healthcheck): results.json не читает"""
    return jsonify({"status": "ok"}), 200


@app.route('/api/stats', methods=['GET'])
def get_stats():
    """Получение статистики"""
    try:
        results = visible_results(load_results())
        
        stats = {
            "total_analyses": len(results),
            "users_count": len(set(r.get('user_info', {}).get('tg_user_id', 0) for r in results)),
            "recent_analyses": len([r for r in results if is_recent(r)])
        }
        
        return jsonify(stats), 200
        
    except Exception as e:
        logger.error(f"Ошибка получения статистики: {e}")
        return jsonify({"error": str(e)}), 500


def is_recent(result: Dict, days: int = 7) -> bool:
    """Проверка, является ли результат недавним"""
    try:
        timestamp = result.get('user_info', {}).get('timestamp', '')
        if not timestamp:
            return False
        
        result_date = datetime.fromisoformat(timestamp)
        days_diff = (datetime.now() - result_date).days
        return days_diff <= days
    except:
        return False


@app.route('/')
def index():
    """Главная страница - отдача index.html"""
    return send_from_directory(WEB_DIR, 'index.html')


@app.route('/results')
def results_page():
    """Страница со списком всех результатов"""
    return send_from_directory(WEB_DIR, 'results.html')


@app.route('/visualization')
def visualization():
    """Страница визуализации параметров"""
    return send_from_directory(WEB_DIR, 'visualization.html')


@app.route('/guide')
def guide_page():
    """Краткая инструкция врача по проведению обследования."""
    return send_from_directory(WEB_DIR, 'guide.html')


@app.route('/api/export/csv', methods=['GET'])
def export_csv():
    """Экспорт результатов в CSV"""
    try:
        results = visible_results(load_results())
        
        if not results:
            return jsonify({"error": "Нет результатов для экспорта"}), 404
        
        # Создание CSV в памяти
        output = []
        output.append([
            'ID исследования', 'ID пациента', 'Дата/Время', 'Специалист', 'Account ID',
            'DSI Score', 'DSI Range', 'Риск ПД',
            'Jitter (%)', 'Shimmer (%)', 'HNR (dB)',
            'F0 Mean (Hz)', 'F0 SD (Hz)', 'Скорость речи (сл/сек)',
            'MPT (сек)', 'F0-High (Hz)', 'I-Low (дБ)',
            'Гипофония', 'Monopitch', 'Monoloudness', 'Охриплость', 'Артикуляция',
            'Длительность (сек)', 'Частота дискретизации'
        ])
        
        for result in results:
            user_info = result.get('user_info', {})
            dsi = result.get('dsi', {})
            features = result.get('features', {})
            symptom_scores = result.get('symptom_scores', {})
            audio_summary = result.get('audio_summary', {})
            
            dsi_breakdown = dsi.get('dsi_breakdown', {})
            
            row = [
                result.get('study_id', ''),
                result.get('patient_id', ''),
                user_info.get('timestamp', ''),
                user_info.get('tg_username', ''),
                user_info.get('tg_user_id', ''),
                dsi.get('dsi_score', ''),
                dsi.get('dsi_range', ''),
                symptom_scores.get('pd_risk', ''),
                features.get('jitter_percent', ''),
                features.get('shimmer_percent', ''),
                features.get('hnr_db', ''),
                features.get('f0_mean_hz', ''),
                features.get('f0_sd_hz', ''),
                features.get('rate_syl_sec', ''),
                dsi_breakdown.get('mpt_sec', ''),
                dsi_breakdown.get('f0_high_hz', ''),
                dsi_breakdown.get('i_low_db', ''),
                symptom_scores.get('hypophonia', ''),
                symptom_scores.get('monopitch', ''),
                symptom_scores.get('monoloudness', ''),
                symptom_scores.get('hoarseness', ''),
                symptom_scores.get('imprecise_articulation', ''),
                audio_summary.get('duration_sec', ''),
                audio_summary.get('sample_rate', '')
            ]
            output.append(row)
        
        # Генерация CSV
        def generate():
            import io
            import csv as csv_module
            
            output_io = io.StringIO()
            writer = csv_module.writer(output_io, delimiter=',', quotechar='"', quoting=csv_module.QUOTE_MINIMAL)
            
            for row in output:
                writer.writerow(["'" + value if isinstance(value, str) and value.startswith(('=', '+', '-', '@', '\t', '\r')) else value for value in row])
            
            output_io.seek(0)
            return output_io.getvalue()
        
        csv_data = generate()
        
        # Создание ответа
        response = Response(
            csv_data.encode('utf-8-sig'),  # UTF-8 BOM для правильного отображения в Excel
            mimetype='text/csv',
            headers={
                'Content-Disposition': f'attachment; filename=parkinson_analysis_report_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv'
            }
        )
        
        return response
        
    except Exception as e:
        logger.error(f"Ошибка экспорта CSV: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/export/json', methods=['GET'])
def export_json():
    """Экспорт результатов в JSON"""
    try:
        results = visible_results(load_results())
        
        if not results:
            return jsonify({"error": "Нет результатов для экспорта"}), 404
        
        # Формирование полного отчета
        report = {
            "export_date": datetime.now().isoformat(),
            "total_records": len(results),
            "results": results
        }
        
        json_data = json.dumps(report, ensure_ascii=False, indent=2)
        
        response = Response(
            json_data.encode('utf-8'),
            mimetype='application/json',
            headers={
                'Content-Disposition': f'attachment; filename=parkinson_analysis_report_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json'
            }
        )
        
        return response
        
    except Exception as e:
        logger.error(f"Ошибка экспорта JSON: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/export/html', methods=['GET'])
def export_html():
    """Экспорт результатов в HTML отчет"""
    try:
        results = visible_results(load_results())
        
        if not results:
            return jsonify({"error": "Нет результатов для экспорта"}), 404
        
        # Генерация HTML отчета
        html_content = generate_html_report(results)
        
        response = Response(
            html_content.encode('utf-8'),
            mimetype='text/html',
            headers={
                'Content-Disposition': f'attachment; filename=parkinson_analysis_report_{datetime.now().strftime("%Y%m%d_%H%M%S")}.html'
            }
        )
        
        return response
        
    except Exception as e:
        logger.error(f"Ошибка экспорта HTML: {e}")
        return jsonify({"error": str(e)}), 500


def generate_html_report(results: List[Dict]) -> str:
    """Генерация HTML отчета"""
    html = """<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Отчет анализа голоса на болезнь Паркинсона</title>
    <style>
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            margin: 20px;
            background: #f5f5f5;
        }
        .header {
            background: linear-gradient(135deg, #2c5f8d 0%, #4a90c2 100%);
            color: white;
            padding: 30px;
            border-radius: 10px;
            margin-bottom: 20px;
        }
        .stats {
            display: flex;
            gap: 20px;
            margin-bottom: 20px;
        }
        .stat-card {
            background: white;
            padding: 20px;
            border-radius: 10px;
            box-shadow: 0 2px 5px rgba(0,0,0,0.1);
            flex: 1;
        }
        .stat-card h3 {
            margin: 0;
            font-size: 2em;
            color: #2c5f8d;
        }
        .stat-card p {
            margin: 5px 0 0 0;
            color: #666;
        }
        table {
            width: 100%;
            border-collapse: collapse;
            background: white;
            margin-bottom: 20px;
            box-shadow: 0 2px 5px rgba(0,0,0,0.1);
        }
        th {
            background: #2c5f8d;
            color: white;
            padding: 12px;
            text-align: left;
        }
        td {
            padding: 10px;
            border-bottom: 1px solid #ddd;
        }
        tr:hover {
            background: #f5f5f5;
        }
        .badge {
            padding: 4px 8px;
            border-radius: 4px;
            font-size: 0.9em;
        }
        .badge-high { background: #dc3545; color: white; }
        .badge-medium { background: #ffc107; color: #333; }
        .badge-low { background: #28a745; color: white; }
        .result-card {
            background: white;
            padding: 20px;
            margin-bottom: 20px;
            border-radius: 10px;
            box-shadow: 0 2px 5px rgba(0,0,0,0.1);
        }
        .result-header {
            border-bottom: 2px solid #2c5f8d;
            padding-bottom: 10px;
            margin-bottom: 15px;
        }
    </style>
</head>
<body>
    <div class="header">
        <h1>📊 Отчет анализа голоса на болезнь Паркинсона</h1>
        <p>Дата создания: """ + datetime.now().strftime("%d.%m.%Y %H:%M:%S") + """</p>
        <p>Всего записей: """ + str(len(results)) + """</p>
    </div>
    
    <div class="stats">
        <div class="stat-card">
            <h3>""" + str(len(results)) + """</h3>
            <p>Всего анализов</p>
        </div>
        <div class="stat-card">
            <h3>""" + str(len(set(r.get('user_info', {}).get('tg_user_id', 0) for r in results))) + """</h3>
            <p>Уникальных пользователей</p>
        </div>
        <div class="stat-card">
            <h3>""" + str(len([r for r in results if is_recent(r)])) + """</h3>
            <p>За последние 7 дней</p>
        </div>
    </div>
    
    <h2>Сводная таблица</h2>
    <table>
        <thead>
            <tr>
                <th>ID исследования</th>
                <th>ID пациента</th>
                <th>Дата/Время</th>
                <th>Username</th>
                <th>DSI Score</th>
                <th>DSI Range</th>
                <th>Риск ПД</th>
                <th>Jitter (%)</th>
                <th>Shimmer (%)</th>
                <th>HNR (dB)</th>
            </tr>
        </thead>
        <tbody>
"""
    
    for result in results:
        user_info = result.get('user_info', {})
        dsi = result.get('dsi', {})
        features = result.get('features', {})
        symptom_scores = result.get('symptom_scores', {})
        
        timestamp = user_info.get('timestamp', '')
        try:
            dt = datetime.fromisoformat(timestamp)
            timestamp = dt.strftime("%d.%m.%Y %H:%M")
        except:
            pass
        
        dsi_score = dsi.get('dsi_score')
        if dsi_score is None:
            dsi_score = 'N/A'
        pd_risk = symptom_scores.get('pd_risk', 'N/A')
        
        risk_class = 'badge-low'
        if 'Высокий' in str(pd_risk):
            risk_class = 'badge-high'
        elif 'Умеренный' in str(pd_risk):
            risk_class = 'badge-medium'
        
        html += f"""
            <tr>
                <td>{html_escape(str(result.get('study_id', '')))}</td>
                <td>{html_escape(str(result.get('patient_id', '')))}</td>
                <td>{timestamp}</td>
                <td>{user_info.get('tg_username', 'N/A')}</td>
                <td>{dsi_score if dsi_score != 'N/A' else 'N/A'}</td>
                <td>{dsi.get('dsi_range', 'N/A')}</td>
                <td><span class="badge {risk_class}">{pd_risk}</span></td>
                <td>{features.get('jitter_percent', 'N/A')}</td>
                <td>{features.get('shimmer_percent', 'N/A')}</td>
                <td>{features.get('hnr_db', 'N/A')}</td>
            </tr>
"""
    
    html += """
        </tbody>
    </table>
    
    <h2>Детальные результаты</h2>
"""
    
    for i, result in enumerate(results, 1):
        user_info = result.get('user_info', {})
        dsi = result.get('dsi', {})
        features = result.get('features', {})
        symptom_scores = result.get('symptom_scores', {})
        
        timestamp = user_info.get('timestamp', '')
        try:
            dt = datetime.fromisoformat(timestamp)
            timestamp = dt.strftime("%d.%m.%Y %H:%M:%S")
        except:
            pass
        
        html += f"""
    <div class="result-card">
        <div class="result-header">
            <h3>Запись #{i}: {user_info.get('tg_username', 'N/A')}</h3>
            <p><strong>Дата/Время:</strong> {timestamp}</p>
        </div>
        
        <h4>DSI (Dysphonia Severity Index)</h4>
        <p><strong>Score:</strong> {dsi.get('dsi_score') if dsi.get('dsi_score') is not None else 'N/A'}</p>
        <p><strong>Range:</strong> {dsi.get('reason') or dsi.get('dsi_range', 'N/A')}</p>
        
        <h4>Акустические признаки</h4>
        <ul>
            <li>Jitter: {features.get('jitter_percent', 'N/A')}%</li>
            <li>Shimmer: {features.get('shimmer_percent', 'N/A')}%</li>
            <li>HNR: {features.get('hnr_db', 'N/A')} dB</li>
            <li>F0 Mean: {features.get('f0_mean_hz', 'N/A')} Hz</li>
            <li>F0 SD: {features.get('f0_sd_hz', 'N/A')} Hz</li>
        </ul>
        
        <h4>Оценка симптомов</h4>
        <ul>
            <li>Гипофония: {symptom_scores.get('hypophonia') if symptom_scores.get('hypophonia') is not None else 'не оценивался'}</li>
            <li>Monopitch: {symptom_scores.get('monopitch') if symptom_scores.get('monopitch') is not None else 'не оценивался'}</li>
            <li>Monoloudness: {symptom_scores.get('monoloudness') if symptom_scores.get('monoloudness') is not None else 'не оценивался'}</li>
            <li>Охриплость: {symptom_scores.get('hoarseness') if symptom_scores.get('hoarseness') is not None else 'не оценивался'}</li>
            <li>Артикуляция: {symptom_scores.get('imprecise_articulation') if symptom_scores.get('imprecise_articulation') is not None else 'не оценивался'}</li>
        </ul>
        
        <p><strong>Риск ПД:</strong> {symptom_scores.get('pd_risk', 'N/A')}</p>
    </div>
"""
    
    html += """
</body>
</html>
"""
    
    return html


@app.get('/login')
def login_page():
    return send_from_directory(WEB_DIR, 'login.html')


@app.get('/all-results')
def all_results_page():
    return send_from_directory(WEB_DIR, 'results.html')


@app.get('/users')
def users_page():
    return send_from_directory(WEB_DIR, 'users.html')


@app.get('/auth-logs')
def auth_logs_page():
    return send_from_directory(WEB_DIR, 'auth-logs.html')


@app.get('/assets/<path:path>')
def serve_assets(path):
    return send_from_directory(os.path.join(WEB_DIR, 'assets'), path)


if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    host = os.getenv('HOST', '0.0.0.0')
    # В production отключить debug
    debug = os.getenv('DEBUG', 'False').lower() == 'true'
    flask_env = os.getenv('FLASK_ENV', 'development')
    
    logger.info(f"Запуск API сервера на {host}:{port} (env: {flask_env}, debug: {debug})")
    app.run(host=host, port=port, debug=debug)
