"""
API сервер для сохранения и получения результатов анализа
"""
import os
import json
import csv
import math
from datetime import datetime
from typing import List, Dict, Any
from flask import Flask, request, jsonify, send_from_directory, Response
from flask_cors import CORS
import logging
import numpy as np

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
# Разрешить CORS для веб-интерфейса со всех источников для всех маршрутов
CORS(app, resources={r"/*": {"origins": "*"}})

# Путь к файлу для хранения результатов
RESULTS_FILE = "results.json"
RESULTS_DIR = "results"

# Создание директории для результатов, если её нет
os.makedirs(RESULTS_DIR, exist_ok=True)


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
            # Создаем новый файл с пустым списком
            save_results([])
            return []
        except Exception as e:
            logger.error(f"Ошибка загрузки результатов: {e}")
            return []
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


@app.route('/api/analyze', methods=['POST'])
def analyze_audio():
    """Анализ аудиофайла с сохранением сырых данных"""
    try:
        from parkinson_analyzer import ParkinsonAnalyzer
        from datetime import datetime
        
        # Проверка наличия файла
        if 'file' not in request.files:
            return jsonify({"error": "Файл не предоставлен"}), 400
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({"error": "Файл не выбран"}), 400
        
        # Сохранение временного файла
        import tempfile
        import uuid
        result_id = f"web_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')[:-3]}_{uuid.uuid4().hex[:8]}"
        
        temp_dir = tempfile.gettempdir()
        temp_file = os.path.join(temp_dir, f"temp_{result_id}_{file.filename}")
        file.save(temp_file)
        
        try:
            # Анализ с сохранением сырых данных
            # Используем абсолютный путь для RESULTS_DIR
            results_dir_abs = os.path.abspath(RESULTS_DIR)
            logger.info(f"📁 Сохранение сырых данных в: {results_dir_abs}")
            analyzer = ParkinsonAnalyzer(save_raw_data=True, raw_data_dir=results_dir_abs)
            result = analyzer.analyze_audio_file(temp_file, save_raw=True, result_id=result_id)
            
            # Добавление информации о пользователе
            result['user_info'] = {
                'tg_username': request.form.get('username', 'web_user'),
                'tg_user_id': request.form.get('user_id', 0),
                'timestamp': datetime.now().isoformat(),
                'source': 'web_interface',
                'filename': file.filename
            }
            
            # Сохранение результата
            # Результат уже очищен в parkinson_analyzer, но дополнительно проверяем
            cleaned_result = clean_json_values(result)
            results = load_results()
            results.append(cleaned_result)
            save_results(results)
            
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
    """Сохранение результата анализа"""
    try:
        data = request.json
        
        if not data:
            logger.warning("Попытка сохранить результат без данных")
            return jsonify({"error": "Нет данных"}), 400
        
        logger.info(f"Получен запрос на сохранение результата от пользователя {data.get('user_info', {}).get('tg_username', 'unknown')}")
        
        # Загрузка существующих результатов
        results = load_results()
        logger.info(f"Загружено существующих результатов: {len(results)}")
        
        # Очистка данных от недопустимых значений перед добавлением
        cleaned_data = clean_json_values(data)
        
        # Добавление нового результата
        results.append(cleaned_data)
        logger.info(f"Добавлен новый результат. Всего результатов: {len(results)}")
        
        # Сохранение
        save_results(results)
        
        # Проверяем, что результат действительно сохранен
        saved_results = load_results()
        if len(saved_results) != len(results):
            logger.error(f"ОШИБКА: Количество результатов не совпадает! Ожидалось: {len(results)}, сохранено: {len(saved_results)}")
        else:
            logger.info(f"Результат успешно сохранен. Всего в файле: {len(saved_results)}")
        
        return jsonify({"status": "success", "message": "Результат сохранен", "total": len(saved_results)}), 200
        
    except Exception as e:
        logger.error(f"Ошибка сохранения результата: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route('/api/results', methods=['GET'])
def get_results():
    """Получение всех результатов"""
    try:
        results = load_results()
        
        # Фильтрация по user_id, если указан
        user_id = request.args.get('user_id', type=int)
        if user_id:
            results = [r for r in results if r.get('user_info', {}).get('tg_user_id') == user_id]
        
        # Сортировка по дате (новые первыми)
        results.sort(key=lambda x: x.get('user_info', {}).get('timestamp', ''), reverse=True)
        
        # Ограничение количества результатов (опционально)
        limit = request.args.get('limit', type=int)
        if limit:
            results = results[:limit]
        
        return jsonify({"results": results, "count": len(results)}), 200
        
    except Exception as e:
        logger.error(f"Ошибка получения результатов: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/results/<int:index>', methods=['GET'])
def get_result(index: int):
    """Получение конкретного результата по индексу"""
    try:
        results = load_results()
        
        if index < 0 or index >= len(results):
            return jsonify({"error": "Индекс вне диапазона"}), 404
        
        return jsonify(results[index]), 200
        
    except Exception as e:
        logger.error(f"Ошибка получения результата: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/visualization/<int:index>', methods=['GET'])
def get_visualization_data(index: int):
    """Получение данных для визуализации параметров"""
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
        
        # Загрузка waveform данных
        waveform_data = None
        waveform_file = raw_data.get('files', {}).get('waveform_data')
        if waveform_file and os.path.exists(waveform_file):
            try:
                with open(waveform_file, 'r', encoding='utf-8') as f:
                    waveform_data = json.load(f)
                # Очистка от NaN
                waveform_data = clean_json_values(waveform_data) if waveform_data else None
            except Exception as e:
                logger.warning(f"Ошибка загрузки waveform данных: {e}")
        
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
            audio_url = f"/api/audio/{index}"
        else:
            # Пробуем исходный файл
            original_audio = raw_data.get('files', {}).get('original_audio')
            if original_audio and os.path.exists(original_audio):
                audio_url = f"/api/audio/{index}"
        
        # Вычисление F0 данных из аудио (если доступно)
        f0_data = None
        intensity_data = None
        spectrogram_data = None
        
        # Используем обработанное аудио, если доступно, иначе исходное
        audio_file_for_f0 = None
        if processed_audio and os.path.exists(processed_audio):
            audio_file_for_f0 = processed_audio
        else:
            original_audio = raw_data.get('files', {}).get('original_audio')
            if original_audio and os.path.exists(original_audio):
                audio_file_for_f0 = original_audio
        
        if audio_file_for_f0:
            try:
                from audio_processor import AudioProcessor
                from feature_extractor import FeatureExtractor
                import librosa
                import numpy as np
                
                audio_processor = AudioProcessor(target_sr=16000)
                feature_extractor = FeatureExtractor(sample_rate=16000)
                
                # Загрузка аудио
                audio, sr = audio_processor.load_audio(audio_file_for_f0)
                
                # Извлечение F0
                try:
                    import parselmouth
                    audio_normalized = audio / (np.max(np.abs(audio)) + 1e-10)
                    sound = parselmouth.Sound(audio_normalized, sampling_frequency=sr)
                    pitch = sound.to_pitch_ac(time_step=0.01)
                    
                    f0_times = pitch.xs()
                    f0_values = pitch.selected_array['frequency']
                    
                    # Фильтруем валидные значения (исключаем NaN и inf)
                    valid_indices = (f0_values > 0) & np.isfinite(f0_values)
                    if np.any(valid_indices):
                        f0_times_valid = f0_times[valid_indices]
                        f0_values_valid = f0_values[valid_indices]
                        f0_mean = float(np.mean(f0_values_valid))
                        # Проверяем на NaN и inf
                        if not (math.isnan(f0_mean) or math.isinf(f0_mean)):
                            f0_data = {
                                'time': [float(t) for t in f0_times_valid if np.isfinite(t)],
                                'values': [float(v) for v in f0_values_valid if np.isfinite(v)],
                                'mean': f0_mean
                            }
                        else:
                            f0_data = None
                    else:
                        f0_data = None
                except Exception as e:
                    logger.warning(f"Ошибка извлечения F0 через parselmouth: {e}")
                    # Fallback на librosa
                    f0 = librosa.pyin(audio, fmin=50, fmax=500)
                    f0_times = librosa.frames_to_time(np.arange(len(f0[0])), sr=sr)
                    valid_indices = ~np.isnan(f0[0]) & np.isfinite(f0[0])
                    if np.any(valid_indices):
                        f0_values_valid = f0[0][valid_indices]
                        f0_times_valid = f0_times[valid_indices]
                        f0_mean = float(np.mean(f0_values_valid))
                        # Проверяем на NaN и inf
                        if not (math.isnan(f0_mean) or math.isinf(f0_mean)):
                            f0_data = {
                                'time': [float(t) for t in f0_times_valid if np.isfinite(t)],
                                'values': [float(v) for v in f0_values_valid if np.isfinite(v)],
                                'mean': f0_mean
                            }
                        else:
                            f0_data = None
                    else:
                        f0_data = None
                
                # Извлечение интенсивности
                try:
                    import parselmouth
                    audio_normalized = audio / (np.max(np.abs(audio)) + 1e-10)
                    sound = parselmouth.Sound(audio_normalized, sampling_frequency=sr)
                    intensity = sound.to_intensity(time_step=0.01)
                    intensity_times = intensity.xs()
                    intensity_values = intensity.values[0]
                    
                    # Фильтруем NaN и inf значения
                    valid_indices = np.isfinite(intensity_values)
                    if np.any(valid_indices):
                        intensity_data = {
                            'time': [float(t) for t in intensity_times if np.isfinite(t)],
                            'values': [float(v) for v in intensity_values[valid_indices] if np.isfinite(v)]
                        }
                    else:
                        intensity_data = None
                except Exception as e:
                    logger.warning(f"Ошибка извлечения интенсивности: {e}")
                    # Fallback через RMS
                    frame_length = int(0.025 * sr)
                    hop_length = int(0.010 * sr)
                    rms = librosa.feature.rms(y=audio, frame_length=frame_length, hop_length=hop_length)[0]
                    rms_times = librosa.frames_to_time(np.arange(len(rms)), sr=sr, hop_length=hop_length)
                    # Конвертация в dB
                    rms_db = 20 * np.log10(rms + 1e-10)
                    # Фильтруем NaN и inf значения
                    valid_indices = np.isfinite(rms_db)
                    if np.any(valid_indices):
                        intensity_data = {
                            'time': [float(t) for t in rms_times if np.isfinite(t)],
                            'values': [float(v) for v in rms_db[valid_indices] if np.isfinite(v)]
                        }
                    else:
                        intensity_data = None
                
                # Извлечение спектрограммы
                freqs, times, spectrogram = audio_processor.get_spectrogram(audio, sr)
                # Ограничиваем частоты до 5kHz для визуализации
                freq_mask = freqs <= 5000
                freqs_filtered = freqs[freq_mask]
                spectrogram_filtered = spectrogram[freq_mask, :]
                
                # Очистка от NaN и inf
                # Фильтруем частоты
                freq_valid = [float(f) for f in freqs_filtered if np.isfinite(f)]
                # Фильтруем времена
                times_valid = [float(t) for t in times if np.isfinite(t)]
                # Фильтруем magnitude (заменяем NaN и inf на -80 dB)
                magnitude_cleaned = []
                for row in spectrogram_filtered:
                    cleaned_row = []
                    for val in row:
                        if np.isfinite(val):
                            cleaned_row.append(float(val))
                        else:
                            cleaned_row.append(-80.0)  # Минимальное значение для визуализации
                    magnitude_cleaned.append(cleaned_row)
                
                spectrogram_data = {
                    'frequencies': freq_valid,
                    'times': times_valid,
                    'magnitude': magnitude_cleaned
                }
                
            except Exception as e:
                logger.error(f"Ошибка обработки аудио для визуализации: {e}")
                import traceback
                traceback.print_exc()
        
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
            'waveform': waveform_data or {},
            'f0_data': f0_data or {},
            'intensity': intensity_data or {},
            'spectrogram': spectrogram_data or {},
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


@app.route('/api/audio/<int:index>', methods=['GET'])
def get_audio_file(index: int):
    """Получение аудиофайла для воспроизведения"""
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
            return send_from_directory(os.path.dirname(processed_audio), 
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
            return send_from_directory(os.path.dirname(original_audio),
                                     os.path.basename(original_audio),
                                     mimetype=mimetype)
        
        return jsonify({"error": "Аудиофайл не найден"}), 404
        
    except Exception as e:
        logger.error(f"Ошибка получения аудиофайла: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/stats', methods=['GET'])
def get_stats():
    """Получение статистики"""
    try:
        results = load_results()
        
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
    return send_from_directory('.', 'index.html')


@app.route('/visualization')
def visualization():
    """Страница визуализации параметров"""
    return send_from_directory('.', 'visualization.html')


@app.route('/api/export/csv', methods=['GET'])
def export_csv():
    """Экспорт результатов в CSV"""
    try:
        results = load_results()
        
        if not results:
            return jsonify({"error": "Нет результатов для экспорта"}), 404
        
        # Создание CSV в памяти
        output = []
        output.append([
            'Дата/Время', 'Telegram Username', 'User ID',
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
                writer.writerow(row)
            
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
        results = load_results()
        
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
        results = load_results()
        
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
        
        dsi_score = dsi.get('dsi_score', 'N/A')
        pd_risk = symptom_scores.get('pd_risk', 'N/A')
        
        risk_class = 'badge-low'
        if 'Высокий' in str(pd_risk):
            risk_class = 'badge-high'
        elif 'Умеренный' in str(pd_risk):
            risk_class = 'badge-medium'
        
        html += f"""
            <tr>
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
        <p><strong>Score:</strong> {dsi.get('dsi_score', 'N/A')}</p>
        <p><strong>Range:</strong> {dsi.get('dsi_range', 'N/A')}</p>
        
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
            <li>Гипофония: {symptom_scores.get('hypophonia', 'N/A')}</li>
            <li>Monopitch: {symptom_scores.get('monopitch', 'N/A')}</li>
            <li>Monoloudness: {symptom_scores.get('monoloudness', 'N/A')}</li>
            <li>Охриплость: {symptom_scores.get('hoarseness', 'N/A')}</li>
            <li>Артикуляция: {symptom_scores.get('imprecise_articulation', 'N/A')}</li>
        </ul>
        
        <p><strong>Риск ПД:</strong> {symptom_scores.get('pd_risk', 'N/A')}</p>
    </div>
"""
    
    html += """
</body>
</html>
"""
    
    return html


@app.route('/<path:path>')
def serve_static(path):
    """Отдача статических файлов"""
    try:
        # Игнорируем запросы к API маршрутам
        if path.startswith('api/'):
            return jsonify({"error": "Неверный маршрут"}), 404
        
        return send_from_directory('.', path)
    except Exception as e:
        logger.error(f"Ошибка при отдаче статического файла {path}: {e}")
        # Для несуществующих файлов возвращаем 404, а не 500
        if hasattr(e, 'code') and e.code == 404:
            return jsonify({"error": "Файл не найден"}), 404
        return jsonify({"error": "Ошибка сервера"}), 500


if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    host = os.getenv('HOST', '0.0.0.0')
    # В production отключить debug
    debug = os.getenv('DEBUG', 'False').lower() == 'true'
    flask_env = os.getenv('FLASK_ENV', 'development')
    
    logger.info(f"Запуск API сервера на {host}:{port} (env: {flask_env}, debug: {debug})")
    app.run(host=host, port=port, debug=debug)
