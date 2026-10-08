"""
Основной модуль для анализа речи на предмет симптомов болезни Паркинсона
"""
import json
import base64
import io
import numpy as np
import os
import shutil
from datetime import datetime
from typing import Dict, Optional, List, Tuple, Any
import argparse
import sys
import logging
import math

# Настройка логирования
logger = logging.getLogger(__name__)
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

try:
    import matplotlib
    matplotlib.use('Agg')  # Неинтерактивный бэкенд
    import matplotlib.pyplot as plt
    HAS_MATPLOTLIB = True
except ImportError:
    HAS_MATPLOTLIB = False

from audio_processor import AudioProcessor
from feature_extractor import FeatureExtractor
from symptom_analyzer import SymptomAnalyzer
from recording_quality import assess_recording_quality
from robust_features import compute_cpps
from dsi_protocol import parse_segments, segment_audio, measure_dsi_tasks, COMFORTABLE_SPL_SD_DB


DSI_FORMULA = "DSI = 0.13 × MPT + 0.0053 × F0-High - 0.26 × I-Low - 1.18 × Jitter(%) + 12.4"
DSI_NOTE = "DSI оценивает качество голоса (дисфонию), а не болезнь Паркинсона, и не является диагнозом."


class ParkinsonAnalyzer:
    """Главный класс для анализа речи на симптомы ПД"""
    
    def __init__(self, save_raw_data: bool = True, raw_data_dir: str = "results"):
        self.audio_processor = AudioProcessor(target_sr=16000)
        self.feature_extractor = FeatureExtractor(sample_rate=16000)
        self.symptom_analyzer = SymptomAnalyzer()
        self.save_raw_data = save_raw_data
        # Преобразуем в абсолютный путь для надежности
        self.raw_data_dir = os.path.abspath(raw_data_dir)
        # Всегда создаем директорию для результатов, даже если сохранение отключено
        os.makedirs(self.raw_data_dir, exist_ok=True)
        logger.info(f"📁 Директория для сырых данных: {self.raw_data_dir} (save_raw_data={save_raw_data})")
    
    def _clean_json_values(self, obj: Any) -> Any:
        """
        Рекурсивная очистка значений от inf, -inf и NaN для JSON сериализации
        
        Args:
            obj: Объект для очистки (dict, list, или примитив)
        
        Returns:
            Очищенный объект с заменой недопустимых значений на None или 0.0
        """
        if isinstance(obj, dict):
            return {key: self._clean_json_values(value) for key, value in obj.items()}
        elif isinstance(obj, list):
            return [self._clean_json_values(item) for item in obj]
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
            return self._clean_json_values(obj.tolist())
        else:
            # Для остальных типов (str, None, bool) возвращаем как есть
            return obj
    
    def analyze_audio_file(self, file_path: str, save_raw: Optional[bool] = None, result_id: Optional[str] = None,
                           device_info: Optional[Dict] = None) -> Dict:
        """
        Полный анализ аудиофайла
        
        Args:
            file_path: Путь к аудиофайлу (WAV/MP3)
            device_info: Метаданные устройства от приложения (модель, режим микрофона,
                уровень шума по калибровочной тишине и т.п.)
        
        Returns:
            Структурированный JSON отчет
        """
        try:
            # Определяем, нужно ли сохранять сырые данные
            should_save_raw = save_raw if save_raw is not None else self.save_raw_data
            logger.info(f"🔍 Отладка: should_save_raw={should_save_raw}, save_raw={save_raw}, self.save_raw_data={self.save_raw_data}")
            
            # Генерируем ID для результата, если не передан
            if result_id is None:
                result_id = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
            
            logger.info(f"🔍 Отладка: result_id={result_id}, raw_data_dir={self.raw_data_dir}")
            
            raw_data_paths = {}
            result_dir = None
            
            # Создаем директорию для сырых данных, если нужно сохранять
            if should_save_raw:
                result_dir = os.path.join(self.raw_data_dir, result_id)
                try:
                    os.makedirs(result_dir, exist_ok=True)
                    if os.path.exists(result_dir):
                        logger.info(f"✅ Директория для сырых данных создана: {result_dir}")
                    else:
                        logger.error(f"⚠️  ОШИБКА: Директория не создана: {result_dir}")
                except Exception as e:
                    logger.error(f"⚠️  ОШИБКА при создании директории {result_dir}: {e}")
                    result_dir = None
            
            # 1. Загрузка аудио и приведение к единой громкости,
            # чтобы признаки не зависели от микрофона и усиления устройства
            raw_audio, sr = self.audio_processor.load_audio(file_path)
            
            # Проверка качества записи (шум, клиппинг, длительность, полоса).
            # Делается до нормализации громкости: клиппинг и уровень шума
            # от приложения (noise_floor_dbfs) заданы в шкале исходного файла.
            device_info = device_info or {}
            try:
                import librosa
                source_sr = librosa.get_samplerate(file_path)
            except Exception:
                source_sr = device_info.get('sample_rate')
            quality = assess_recording_quality(
                raw_audio, sr,
                source_sample_rate=source_sr,
                noise_floor_dbfs=device_info.get('noise_floor_dbfs'))
            
            audio, loudness_info = self.audio_processor.normalize_loudness(raw_audio, sr)
            logger.info(f"Громкость записи {loudness_info['input_lufs']:.1f} LUFS, "
                        f"усиление {loudness_info['gain_db']:+.1f} дБ")
            
            # Сохранение исходного аудиофайла
            if should_save_raw and result_dir:
                try:
                    # Копируем исходный файл
                    original_ext = os.path.splitext(file_path)[1] or '.wav'
                    original_path = os.path.join(result_dir, f"original{original_ext}")
                    shutil.copy2(file_path, original_path)
                    if os.path.exists(original_path):
                        raw_data_paths['original_audio'] = original_path
                        logger.info(f"✅ Сохранен исходный файл: {original_path}")
                    else:
                        logger.error(f"⚠️  Ошибка: файл не создан {original_path}")
                except Exception as e:
                    logger.error(f"⚠️  Ошибка при сохранении исходного файла: {e}")
            
            # 2. Извлечение признаков
            # I-Low (абсолютный уровень для DSI) считается по исходному сигналу
            # с калибровкой устройства, если приложение ее передало
            # В тесте DSI голос оцениваем по спокойной «а», а параметры DSI -
            # по своим упражнениям (границы упражнений передает приложение)
            dsi_segments = (parse_segments(device_info.get('dsi_segments'))
                            if device_info.get('task') == 'dsi' else [])
            feature_audio, feature_raw = audio, raw_audio
            if dsi_segments:
                vowel = segment_audio(audio, sr, dsi_segments, 'vowel')
                if vowel:
                    feature_audio = np.concatenate(vowel)
                    feature_raw = np.concatenate(segment_audio(raw_audio, sr, dsi_segments, 'vowel'))
            all_features = self.feature_extractor.extract_all_features(
                feature_audio, level_audio=feature_raw,
                spl_offset_db=device_info.get('spl_offset_db'))
            all_features['cpps_db'] = compute_cpps(feature_audio, sr)
            dsi_protocol = None
            if dsi_segments:
                dsi_protocol = measure_dsi_tasks(raw_audio, audio, sr, dsi_segments,
                                                 device_info.get('spl_offset_db'))
                for key in ('mpt_sec', 'f0_high_hz', 'i_low_db', 'i_low_dbfs', 'i_low_calibrated'):
                    all_features[key] = dsi_protocol[key]
            
            # 3. Анализ симптомов: что можно оценить, зависит от типа записи
            recording_task = self._detect_recording_task(all_features, device_info)
            analysis = self.symptom_analyzer.analyze(all_features, task=recording_task)
            
            # 4. Расчет DSI (Dysphonia Severity Index)
            dsi_result = self._calculate_dsi(all_features, task=recording_task,
                                             protocol=dsi_protocol)
            
            # 5. Получение визуализаций
            waveform_data = self.audio_processor.get_waveform(audio)
            freqs, times, spectrogram = self.audio_processor.get_spectrogram(audio, sr)
            
            # Сохранение сырых данных визуализаций
            if should_save_raw and result_dir:
                try:
                    import json as json_lib
                    # Сохраняем waveform данные
                    waveform_data_file = os.path.join(result_dir, "waveform_data.json")
                    with open(waveform_data_file, 'w', encoding='utf-8') as f:
                        # Безопасное преобразование в списки
                        amplitude = waveform_data.get('amplitude', [])
                        time_data = waveform_data.get('time', [])
                        if isinstance(amplitude, np.ndarray):
                            amplitude = amplitude.tolist()
                        if isinstance(time_data, np.ndarray):
                            time_data = time_data.tolist()
                        
                        json_lib.dump({
                            'amplitude': amplitude,
                            'time': time_data,
                            'duration': waveform_data.get('duration', 0.0)
                        }, f, ensure_ascii=False, indent=2)
                    if os.path.exists(waveform_data_file):
                        raw_data_paths['waveform_data'] = waveform_data_file
                        logger.info(f"✅ Сохранены waveform данные: {waveform_data_file}")
                    
                    # Сохраняем spectrogram данные (сохраняем только метаданные, т.к. полный спектр может быть большим)
                    spectrogram_meta_file = os.path.join(result_dir, "spectrogram_meta.json")
                    with open(spectrogram_meta_file, 'w', encoding='utf-8') as f:
                        json_lib.dump({
                            'frequencies_range': [float(freqs.min()), float(freqs.max())],
                            'time_range': [float(times.min()), float(times.max())],
                            'spectrogram_shape': list(spectrogram.shape),
                            'sample_rate': int(sr)
                        }, f, ensure_ascii=False, indent=2)
                    if os.path.exists(spectrogram_meta_file):
                        raw_data_paths['spectrogram_meta'] = spectrogram_meta_file
                        logger.info(f"✅ Сохранены метаданные спектрограммы: {spectrogram_meta_file}")
                    
                except Exception as e:
                    logger.error(f"⚠️  Ошибка при сохранении данных визуализаций: {e}")
                    import traceback
                    traceback.print_exc()
            
            # Генерация base64 визуализаций (опционально)
            try:
                waveform_base64 = self._generate_waveform_base64(audio, sr)
                spectrogram_base64 = self._generate_spectrogram_base64(freqs, times, spectrogram)
            except:
                waveform_base64 = None
                spectrogram_base64 = None
            
            # 6. Формирование финального отчета
            # Получаем данные о риске
            pd_risk_data = analysis.get('pd_risk_data', {})
            risk_probability = pd_risk_data.get('risk_probability', 0.0)
            risk_level = pd_risk_data.get('risk_level', 'Low')
            
            # Определение отклонения MFCC (упрощенная оценка)
            mfcc_deviation = "normal"
            if len(analysis.get('exceeded_thresholds', [])) >= 3:
                mfcc_deviation = "high"
            elif len(analysis.get('exceeded_thresholds', [])) >= 1:
                mfcc_deviation = "moderate"
            
            # Формирование рекомендации
            recommendation = self._generate_recommendation(risk_level, risk_probability, 
                                                          analysis.get('exceeded_thresholds', []),
                                                          all_features)
            report = self._add_dsi_to_report(analysis['report'], dsi_result)
            report = self._add_quality_to_report(report, quality)
            if quality['verdict'] == 'reject':
                recommendation = ("Запись непригодна для анализа, результаты ненадежны. "
                                  "Перезапишите голос: " +
                                  " ".join(i['message'] for i in quality['issues'] if i['severity'] == 'reject'))
            
            result = {
                "audio_summary": {
                    "duration_sec": round(len(audio) / sr, 2),
                    "sample_rate": sr,
                    "segments": 1,
                    "input_lufs": round(loudness_info['input_lufs'], 1) if np.isfinite(loudness_info['input_lufs']) else None,
                    "normalization_gain_db": round(loudness_info['gain_db'], 1)
                },
                "features": {
                    "jitter_percent": round(all_features.get('jitter_percent', 0.0), 2),
                    "shimmer_percent": round(all_features.get('shimmer_percent', 0.0), 2),
                    "hnr_db": round(all_features.get('hnr_db', 0.0), 1),
                    "cpps_db": round(all_features.get('cpps_db', 0.0), 2),
                    "rate_syl_sec": round(all_features.get('rate_syl_sec', 0.0), 1),
                    "f0_sd_hz": round(all_features.get('f0_sd_hz', 0.0), 1),
                    "f0_mean_hz": round(all_features.get('f0_mean_hz', 0.0), 1),
                    "amplitude_db_variation": round(all_features.get('amplitude_db_variation', 0.0), 1),
                    "pause_ratio": round(all_features.get('pause_ratio', 0.0), 3),
                    "spectral_tilt_db": round(all_features.get('spectral_tilt_db', 0.0), 1),
                    "loudness_decay_db": round(all_features.get('loudness_decay_db', 0.0), 1)
                },
                "dsi": dsi_result,
                "recording_task": recording_task,
                "symptom_scores": {
                    **analysis['symptom_scores'],
                    "pd_risk": analysis['pd_risk']  # Для обратной совместимости
                },
                # Новый формат согласно требованиям
                "risk_probability": round(risk_probability, 3),
                "risk_level": risk_level,
                "key_features": {
                    "jitter": round(all_features.get('jitter_percent', 0.0), 2),
                    "shimmer": round(all_features.get('shimmer_percent', 0.0), 2),
                    "hnr": round(all_features.get('hnr_db', 0.0), 1),
                    "pitch_mean": round(all_features.get('f0_mean_hz', 0.0), 1),
                    "mfcc_deviation": mfcc_deviation
                },
                "recommendation": recommendation,
                "confidence": round(pd_risk_data.get('confidence', 0.0), 3),
                "report": report,
                "quality": quality,
                "device_info": device_info,
                "visuals": {
                    "waveform": waveform_base64 or f"Данные: {len(waveform_data['amplitude'])} точек, "
                               f"длительность {waveform_data['duration']:.2f}с",
                    "spectrogram": spectrogram_base64 or f"Частоты: 0-{sr/2:.0f}Hz, "
                                  f"временные кадры: {len(times)}"
                }
            }
            
            # Добавляем информацию о сырых данных
            if should_save_raw:
                if result_dir and raw_data_paths:
                    # Проверяем, что файлы действительно существуют
                    existing_files = {}
                    for key, path in raw_data_paths.items():
                        # Для всех файлов проверяем существование
                        if os.path.exists(path):
                            existing_files[key] = path
                    
                    if existing_files:
                        result['raw_data'] = {
                            'result_id': result_id,
                            'data_directory': result_dir,
                            'files': existing_files
                        }
                        saved_files = list(existing_files.keys())
                        logger.info(f"✅ Сырые данные сохранены в: {result_dir}")
                        logger.info(f"   Сохраненные файлы: {', '.join(saved_files)}")
                    else:
                        logger.warning(f"⚠️  Предупреждение: should_save_raw=True, но ни один файл не найден в result_dir={result_dir}")
                        logger.warning(f"   Ожидаемые файлы: {list(raw_data_paths.keys())}")
                else:
                    logger.warning(f"⚠️  Предупреждение: should_save_raw=True, но result_dir={result_dir}, raw_data_paths={len(raw_data_paths) if raw_data_paths else 0} файлов")
                    logger.warning(f"   Отладка: result_dir={result_dir}, should_save_raw={should_save_raw}")
                    if not result_dir:
                        logger.error(f"   ОШИБКА: result_dir не создан! Проверьте права доступа к директории {self.raw_data_dir}")
            
            # Очистка результата от недопустимых значений (inf, nan) перед возвратом
            result = self._clean_json_values(result)
            
            return result
        
        except Exception as e:
            # Возврат ошибки в JSON формате
            return {
                "error": f"Ошибка обработки: {str(e)}",
                "audio_summary": {},
                "features": {},
                "dsi": {},
                "symptom_scores": {},
                "report": [f"Ошибка анализа: {str(e)}"],
                "visuals": {}
            }
    
    def _generate_recommendation(self, risk_level: str, risk_probability: float,
                                exceeded_thresholds: List[str], features: Dict[str, float]) -> str:
        """
        Генерация рекомендации на основе уровня риска
        
        Args:
            risk_level: Low, Medium, High
            risk_probability: Вероятность риска (0.0-1.0)
            exceeded_thresholds: Список превышенных порогов
            features: Извлеченные признаки
        
        Returns:
            Текстовая рекомендация
        """
        num_exceeded = len(exceeded_thresholds)
        
        if risk_level == "High":
            # Высокий риск - детальная рекомендация
            details = []
            if 'jitter' in exceeded_thresholds:
                jitter_val = features.get('jitter_percent', 0)
                details.append(f"повышенный jitter ({jitter_val:.2f}%)")
            if 'shimmer' in exceeded_thresholds:
                shimmer_val = features.get('shimmer_percent', 0)
                details.append(f"повышенный shimmer ({shimmer_val:.2f}%)")
            if 'hnr' in exceeded_thresholds:
                hnr_val = features.get('hnr_db', 25)
                details.append(f"сниженный HNR ({hnr_val:.1f}dB)")
            
            detail_text = "; ".join(details) if details else f"{num_exceeded} признаков отклонены"
            
            return (f"Несколько признаков голоса вне нормы: {detail_text}. "
                   f"Это не диагноз. Если есть жалобы на голос или речь, "
                   f"стоит показаться врачу (фониатру или неврологу).")
        
        elif risk_level == "Medium":
            return (f"Вне нормы признаков: {num_exceeded}. Это не диагноз; "
                   f"имеет смысл повторить запись и сравнить с прошлыми.")
        
        else:  # Low
            if num_exceeded == 0:
                return "Акустические параметры в пределах нормы."
            else:
                return (f"Незначительные отклонения ({num_exceeded} признак). "
                       f"Это не диагноз; имеет смысл повторить запись.")
    
    def _add_quality_to_report(self, report: List[str], quality: Dict) -> List[str]:
        """Добавление предупреждений о качестве записи в начало отчета"""
        if quality['verdict'] == 'ok':
            return report
        lines = []
        if quality['verdict'] == 'reject':
            lines.append("⚠️ Запись непригодна для анализа, значения ниже ненадежны.")
        else:
            lines.append("⚠️ Качество записи снижено, часть значений может быть неточной.")
        lines.extend(f"- {issue['message']}" for issue in quality['issues'])
        return lines + report
    
    def _add_dsi_to_report(self, report: List[str], dsi_result: Dict) -> List[str]:
        """Добавление информации о DSI в отчет"""
        updated_report = report.copy()
        dsi_score = dsi_result.get('dsi_score')
        
        if dsi_score is None:
            reason = dsi_result.get('reason') or dsi_result.get('error') or 'Не удалось рассчитать'
            updated_report.append(f"\nDSI не рассчитан: {reason}")
            return updated_report
        
        breakdown = dsi_result.get('dsi_breakdown', {})
        interpretation = dsi_result.get('interpretation', {})
        updated_report.extend([
            "\n=== DSI (Dysphonia Severity Index) ===",
            (f"DSI (оценка): {dsi_score:.1f} ± {dsi_result.get('uncertainty', 0):.1f} "
             f"({dsi_result.get('dsi_range', 'N/A')})" if dsi_result.get('approximate')
             else f"DSI: {dsi_score:.2f} ({dsi_result.get('dsi_range', 'N/A')})"),
            "Параметры:",
            f"  - MPT: {breakdown.get('mpt_sec', 0):.2f}с ({interpretation.get('mpt_status', 'N/A')})",
            f"  - F0-High: {breakdown.get('f0_high_hz', 0):.1f} Гц ({interpretation.get('f0_high_status', 'N/A')})",
            f"  - I-Low: {breakdown.get('i_low_db', 0):.1f} дБ SPL"
            f"{' (оценка)' if dsi_result.get('approximate') else ''} ({interpretation.get('i_low_status', 'N/A')})",
            f"  - Jitter: {breakdown.get('jitter_percent', 0):.2f}% ({interpretation.get('jitter_status', 'N/A')})",
            DSI_NOTE,
        ])
        if dsi_result.get('approximate'):
            updated_report.append(dsi_result.get('estimate_note', ''))
        return updated_report
    
    @staticmethod
    def _detect_recording_task(features: Dict[str, float], device_info: Dict) -> str:
        """
        Тип записи: 'speech' (обычная речь), 'vowel' (протяжная гласная)
        или 'dsi' (набор заданий для DSI).
        
        Приложение может передать его в device_info['task']. Иначе определяем сами:
        в обычной речи тон прерывается на глухих согласных и паузах, а протяжная
        гласная - это один длинный непрерывный озвонченный участок.
        """
        task = str(device_info.get('task') or '').strip().lower()
        if task in ('speech', 'vowel', 'dsi'):
            return task
        longest = features.get('longest_voiced_sec', 0.0)
        voiced = features.get('voiced_sec', 0.0)
        if longest >= 1.5 and voiced > 0 and longest >= 0.6 * voiced:
            return 'vowel'
        return 'speech'
    
    def _calculate_dsi(self, features: Dict[str, float], task: str = 'speech',
                       protocol: Optional[Dict] = None) -> Dict:
        """
        Расчет DSI (Dysphonia Severity Index, Wuyts et al. 2000)
        
        Формула: DSI = 0.13 × MPT + 0.0053 × F0-High - 0.26 × I-Low - 1.18 × Jitter(%) + 12.4
        
        Каждый параметр измеряется отдельным заданием: MPT - самая долгая гласная
        на одном выдохе, F0-High - самая высокая нота (скольжение голосом вверх),
        I-Low - самый тихий голос в дБ SPL, jitter - на протяжной гласной.
        Из обычной записи эти величины не получить (MPT станет длиной фразы,
        F0-High - обычным тоном, I-Low - громкостью речи), и формула дает
        около -11 даже для здорового голоса. Поэтому DSI считается только для
        записи с заданиями DSI (task='dsi') с откалиброванным микрофоном.
        protocol - параметры, измеренные по отдельным упражнениям (dsi_protocol.py).
        
        Интерпретация (Wuyts 2000): +5 - здоровый голос, -5 - тяжелая дисфония,
        порог нормы около +1.6. DSI оценивает качество голоса, а не болезнь Паркинсона.
        """
        breakdown = {
            "mpt_sec": round(float(features.get('mpt_sec', 0.0) or 0.0), 2),
            "f0_high_hz": round(float(features.get('f0_high_hz', 0.0) or 0.0), 1),
            "i_low_db": round(float(features.get('i_low_db', 0.0) or 0.0), 1),
            "jitter_percent": round(float(features.get('jitter_percent', 0.0) or 0.0), 2),
        }
        calibrated = features.get('i_low_calibrated', 0.0) == 1.0
        # Без калибровки тест DSI оценивает I-Low по обычной громкости самого человека
        self_reference = bool(protocol) and protocol.get('i_low_method') == 'self_reference'
        
        reasons = []
        if protocol and protocol['missing']:
            reasons.append("В тесте не хватает упражнений: " + ", ".join(protocol['missing']) + ".")
        if task != 'dsi':
            reasons.append("Нужна отдельная запись с заданиями DSI: самая долгая гласная «а» "
                           "на одном выдохе, скольжение голосом до самой высокой ноты и самый "
                           "тихий голос. По обычной записи эти величины не измеряются.")
        if not calibrated and not self_reference:
            reasons.append("Нужна калибровка громкости микрофона: без нее неизвестно, насколько "
                           "тихий самый тихий голос в децибелах (I-Low), а этот параметр "
                           "сильно влияет на DSI.")
        protocol_info = {}
        if protocol:
            # Параметры измерены по упражнениям: показываем их, даже если DSI не считается
            protocol_info = {"measured": True, "attempts": protocol['attempts'],
                             "i_low_dbfs": round(protocol['i_low_dbfs'], 1),
                             "i_low_method": protocol.get('i_low_method'),
                             "soft_below_comfortable_db": protocol.get('soft_below_comfortable_db'),
                             "interpretation": self._dsi_parameter_status(breakdown)}
            if self_reference and not calibrated:
                # Погрешность I-Low ±5 дБ (разброс обычной громкости между людьми) дает ±1.3 DSI
                protocol_info.update({
                    "approximate": True,
                    "uncertainty": round(0.26 * COMFORTABLE_SPL_SD_DB, 1),
                    "estimate_note": ("Оценка: микрофон не откалиброван, поэтому самый тихий голос "
                                      "пересчитан в децибелы по вашей обычной громкости (считаем ее "
                                      "около 70 дБ на 30 см). Точность примерно ±1.3. Для наблюдения "
                                      "за изменениями записывайтесь на том же устройстве и на том же "
                                      "расстоянии."),
                })
        if reasons:
            return {
                "dsi_score": None,
                "dsi_range": "Не рассчитывается",
                "status": "not_applicable",
                "reason": " ".join(reasons),
                "dsi_breakdown": breakdown,
                "formula": DSI_FORMULA,
                **protocol_info,
            }
        
        values = list(breakdown.values())
        if any(not math.isfinite(v) for v in values) or min(values[:3]) <= 0.0:
            return {
                "dsi_score": None,
                "dsi_range": "Недостаточно данных для расчета DSI",
                "status": "error",
                "dsi_breakdown": breakdown,
                "error": "Не удалось измерить один или несколько параметров DSI"
            }
        
        mpt_sec = breakdown['mpt_sec']
        f0_high_hz = breakdown['f0_high_hz']
        i_low_db = breakdown['i_low_db']
        jitter_percent = breakdown['jitter_percent']
        dsi_score = (0.13 * mpt_sec + 0.0053 * f0_high_hz
                     - 0.26 * i_low_db - 1.18 * jitter_percent + 12.4)
        
        if dsi_score >= 1.6:
            dsi_range = "В пределах нормы"
        elif dsi_score >= 0.0:
            dsi_range = "Легкие нарушения голоса"
        elif dsi_score >= -2.0:
            dsi_range = "Умеренные нарушения голоса"
        else:
            dsi_range = "Выраженные нарушения голоса"
        
        return {
            "dsi_score": round(dsi_score, 2),
            "dsi_range": dsi_range,
            "status": "ok",
            "dsi_breakdown": breakdown,
            "formula": DSI_FORMULA,
            "i_low_dbfs": round(float(features.get('i_low_dbfs', 0.0)), 1),
            **protocol_info,
            "interpretation": {**self._dsi_parameter_status(breakdown), "note": DSI_NOTE},
        }
    
    @staticmethod
    def _dsi_parameter_status(breakdown: Dict[str, float]) -> Dict[str, str]:
        """Оценка каждого параметра DSI относительно нормы (I-Low - только в дБ SPL)"""
        mpt_sec = breakdown['mpt_sec']
        f0_high_hz = breakdown['f0_high_hz']
        i_low_db = breakdown['i_low_db']
        jitter_percent = breakdown['jitter_percent']
        return {
            "mpt_status": "Низкий" if mpt_sec < 10 else "Нормальный" if mpt_sec >= 15 else "Снижен",
            "f0_high_status": "Низкий" if f0_high_hz < 250 else "Нормальный" if f0_high_hz >= 400 else "Снижен",
            "i_low_status": ("нужна калибровка" if i_low_db <= 0 else
                             "Повышен" if i_low_db > 55 else "Нормальный" if i_low_db <= 45 else "Пограничный"),
            "jitter_status": "Высокий" if jitter_percent > 1.5 else "Нормальный" if jitter_percent < 1.0 else "Повышен",
        }
    
    def _average_features(self, feature_list: list) -> Dict:
        """Усреднение признаков из нескольких сегментов"""
        if not feature_list:
            return {}
        
        averaged = {}
        keys = set()
        
        # Собираем все ключи
        for feat in feature_list:
            keys.update(feat.keys())
        
        # Усредняем по каждому ключу
        for key in keys:
            values = [feat.get(key, 0) for feat in feature_list if feat.get(key, 0) != 0]
            if values:
                averaged[key] = np.mean(values)
            else:
                averaged[key] = 0.0
        
        return averaged
    
    def _generate_waveform_base64(self, audio: np.ndarray, sr: int) -> Optional[str]:
        """Генерация base64 изображения волновой формы"""
        if not HAS_MATPLOTLIB:
            return None
        
        try:
            fig, ax = plt.subplots(figsize=(10, 3))
            time_axis = np.linspace(0, len(audio) / sr, len(audio))
            ax.plot(time_axis, audio, linewidth=0.5)
            ax.set_xlabel('Время (с)')
            ax.set_ylabel('Амплитуда')
            ax.set_title('Волновая форма')
            ax.grid(True, alpha=0.3)
            
            # Конвертация в base64
            buf = io.BytesIO()
            plt.savefig(buf, format='png', dpi=100, bbox_inches='tight')
            buf.seek(0)
            img_base64 = base64.b64encode(buf.read()).decode('utf-8')
            plt.close(fig)
            
            return f"data:image/png;base64,{img_base64}"
        except:
            return None
    
    def _generate_spectrogram_base64(self, freqs: np.ndarray, times: np.ndarray, 
                                    spectrogram: np.ndarray) -> Optional[str]:
        """Генерация base64 изображения спектрограммы"""
        if not HAS_MATPLOTLIB:
            return None
        
        try:
            fig, ax = plt.subplots(figsize=(10, 6))
            
            # Показываем только до 5kHz для читаемости
            freq_mask = freqs <= 5000
            spec_to_show = spectrogram[freq_mask, :]
            freqs_to_show = freqs[freq_mask]
            
            im = ax.imshow(spec_to_show, aspect='auto', origin='lower',
                          extent=[times[0], times[-1], freqs_to_show[0], freqs_to_show[-1]],
                          cmap='viridis', interpolation='bilinear')
            ax.set_xlabel('Время (с)')
            ax.set_ylabel('Частота (Hz)')
            ax.set_title('Спектрограмма')
            plt.colorbar(im, ax=ax, label='dB')
            
            # Конвертация в base64
            buf = io.BytesIO()
            plt.savefig(buf, format='png', dpi=100, bbox_inches='tight')
            buf.seek(0)
            img_base64 = base64.b64encode(buf.read()).decode('utf-8')
            plt.close(fig)
            
            return f"data:image/png;base64,{img_base64}"
        except:
            return None
    
    def analyze_to_json(self, file_path: str) -> str:
        """
        Анализ и возврат результата в виде JSON строки
        
        Args:
            file_path: Путь к аудиофайлу
        
        Returns:
            JSON строка с результатами анализа
        """
        result = self.analyze_audio_file(file_path)
        # Результат уже очищен в analyze_audio_file, но дополнительно проверяем перед сериализацией
        cleaned_result = self._clean_json_values(result)
        return json.dumps(cleaned_result, ensure_ascii=False, indent=2)


def main():
    """Главная функция для запуска из командной строки"""
    parser = argparse.ArgumentParser(
        description='Анализ речи на симптомы болезни Паркинсона'
    )
    parser.add_argument(
        'audio_file',
        type=str,
        help='Путь к аудиофайлу (WAV/MP3)'
    )
    parser.add_argument(
        '-o', '--output',
        type=str,
        help='Путь для сохранения JSON отчета (если не указан, вывод в stdout)'
    )
    
    args = parser.parse_args()
    
    # Создание анализатора (с сохранением сырых данных по умолчанию)
    analyzer = ParkinsonAnalyzer()
    
    # Анализ файла
    try:
        json_result = analyzer.analyze_to_json(args.audio_file)
        
        # Сохранение или вывод результата
        if args.output:
            with open(args.output, 'w', encoding='utf-8') as f:
                f.write(json_result)
            print(f"Отчет сохранен в: {args.output}")
        else:
            print(json_result)
    
    except FileNotFoundError:
        print(json.dumps({
            "error": f"Файл не найден: {args.audio_file}"
        }, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(json.dumps({
            "error": f"Ошибка обработки: {str(e)}"
        }, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()