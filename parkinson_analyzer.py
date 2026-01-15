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
from typing import Dict, Optional, List, Tuple
import argparse
import sys
import logging

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
    
    def analyze_audio_file(self, file_path: str, save_raw: Optional[bool] = None, result_id: Optional[str] = None) -> Dict:
        """
        Полный анализ аудиофайла
        
        Args:
            file_path: Путь к аудиофайлу (WAV/MP3)
        
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
            
            # 1. Загрузка и предобработка аудио
            audio, sr = self.audio_processor.load_audio(file_path)
            
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
            
            # Редукция шума
            audio_cleaned = self.audio_processor.noise_reduction(audio)
            
            # Сохранение обработанного аудио
            if should_save_raw and result_dir:
                try:
                    import soundfile as sf
                    processed_path = os.path.join(result_dir, "processed_audio.wav")
                    sf.write(processed_path, audio_cleaned, sr)
                    if os.path.exists(processed_path):
                        raw_data_paths['processed_audio'] = processed_path
                        logger.info(f"✅ Сохранен обработанный аудио: {processed_path}")
                    else:
                        logger.error(f"⚠️  Ошибка: файл не создан {processed_path}")
                except Exception as e:
                    logger.error(f"⚠️  Ошибка при сохранении обработанного аудио: {e}")
            
            # Сегментация
            segments = self.audio_processor.segment_utterances(audio_cleaned, sr)
            
            # Сохранение сегментов
            segment_paths = []
            if should_save_raw and result_dir and len(segments) > 0:
                import soundfile as sf
                segments_dir = os.path.join(result_dir, "segments")
                os.makedirs(segments_dir, exist_ok=True)
                for i, segment in enumerate(segments):
                    segment_path = os.path.join(segments_dir, f"segment_{i:03d}.wav")
                    sf.write(segment_path, segment, sr)
                    segment_paths.append(segment_path)
                raw_data_paths['segments'] = segment_paths
            
            # 2. Извлечение признаков
            # ВАЖНО: Основные акустические признаки (jitter, shimmer, HNR, F0) должны 
            # извлекаться из всего файла целиком, а не усредняться по сегментам.
            # Сегментация используется только для анализа артикуляции (скорость речи, паузы).
            
            # Извлекаем основные признаки из всего файла
            all_features = self.feature_extractor.extract_all_features(audio_cleaned)
            
            # Дополнительно анализируем сегменты для артикуляции и сохранения сырых данных
            raw_segment_features = []
            if len(segments) > 0:
                segment_features = []
                for i, segment in enumerate(segments):
                    segment_feat = self.feature_extractor.extract_all_features(segment)
                    segment_features.append(segment_feat)
                    # Сохраняем сырые признаки каждого сегмента
                    if should_save_raw:
                        raw_segment_features.append({
                            'segment_index': i,
                            'features': segment_feat,
                            'duration_sec': len(segment) / sr
                        })
                
                # Для артикуляции используем усредненные значения из сегментов
                # (скорость речи, паузы - эти признаки зависят от сегментации)
                if segment_features:
                    segment_avg = self._average_features(segment_features)
                    # Обновляем только артикуляционные признаки из сегментов
                    if 'rate_syl_sec' in segment_avg:
                        all_features['rate_syl_sec'] = segment_avg['rate_syl_sec']
                    if 'pause_ratio' in segment_avg:
                        all_features['pause_ratio'] = segment_avg['pause_ratio']
            
            # 3. Анализ симптомов
            analysis = self.symptom_analyzer.analyze(all_features)
            
            # 4. Расчет DSI (Dysphonia Severity Index)
            dsi_result = self._calculate_dsi(all_features)
            
            # 5. Получение визуализаций
            waveform_data = self.audio_processor.get_waveform(audio_cleaned)
            freqs, times, spectrogram = self.audio_processor.get_spectrogram(audio_cleaned, sr)
            
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
                    
                    # Сохраняем сырые признаки сегментов
                    if raw_segment_features:
                        segment_features_file = os.path.join(result_dir, "segment_features.json")
                        with open(segment_features_file, 'w', encoding='utf-8') as f:
                            json_lib.dump(raw_segment_features, f, ensure_ascii=False, indent=2)
                        if os.path.exists(segment_features_file):
                            raw_data_paths['segment_features'] = segment_features_file
                            logger.info(f"✅ Сохранены признаки сегментов: {segment_features_file}")
                except Exception as e:
                    logger.error(f"⚠️  Ошибка при сохранении данных визуализаций: {e}")
                    import traceback
                    traceback.print_exc()
            
            # Генерация base64 визуализаций (опционально)
            try:
                waveform_base64 = self._generate_waveform_base64(audio_cleaned, sr)
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
            
            result = {
                "audio_summary": {
                    "duration_sec": round(len(audio) / sr, 2),
                    "sample_rate": sr,
                    "segments": len(segments)
                },
                "features": {
                    "jitter_percent": round(all_features.get('jitter_percent', 0.0), 2),
                    "shimmer_percent": round(all_features.get('shimmer_percent', 0.0), 2),
                    "hnr_db": round(all_features.get('hnr_db', 0.0), 1),
                    "rate_syl_sec": round(all_features.get('rate_syl_sec', 0.0), 1),
                    "f0_sd_hz": round(all_features.get('f0_sd_hz', 0.0), 1),
                    "f0_mean_hz": round(all_features.get('f0_mean_hz', 0.0), 1),
                    "amplitude_db_variation": round(all_features.get('amplitude_db_variation', 0.0), 1),
                    "pause_ratio": round(all_features.get('pause_ratio', 0.0), 3)
                },
                "dsi": dsi_result,
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
                "report": self._add_dsi_to_report(analysis['report'], dsi_result),
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
                        if key == 'segments':
                            # Для сегментов проверяем каждый файл
                            existing_segments = [p for p in path if os.path.exists(p)]
                            if existing_segments:
                                existing_files['segments'] = existing_segments
                        else:
                            # Для остальных файлов проверяем существование
                            if os.path.exists(path):
                                existing_files[key] = path
                    
                    if existing_files:
                        result['raw_data'] = {
                            'result_id': result_id,
                            'data_directory': result_dir,
                            'files': existing_files
                        }
                        saved_files = list(existing_files.keys())
                        if 'segments' in existing_files:
                            saved_files.append(f"segments ({len(existing_files['segments'])} файлов)")
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
            
            return (f"Высокий риск ПД ({int(risk_probability * 100)}%): {detail_text}. "
                   f"Рекомендуется консультация невролога, оценка по MDS-UPDRS, "
                   f"логопедическая терапия (LSVT LOUD).")
        
        elif risk_level == "Medium":
            return (f"Умеренный риск ПД ({int(risk_probability * 100)}%): "
                   f"выявлено {num_exceeded} отклонений признаков. "
                   f"Рекомендуется мониторинг симптомов, повторное обследование через 3-6 месяцев.")
        
        else:  # Low
            if num_exceeded == 0:
                return ("Низкий риск ПД: акустические параметры в пределах нормы. "
                       "Симптомы ПД не выявлены. Рекомендуется профилактическое наблюдение.")
            else:
                return (f"Низкий риск ПД ({int(risk_probability * 100)}%): "
                       f"незначительные отклонения ({num_exceeded} признак). "
                       f"Рекомендуется мониторинг и повторная оценка при появлении симптомов.")
    
    def _add_dsi_to_report(self, report: List[str], dsi_result: Dict) -> List[str]:
        """Добавление информации о DSI в отчет"""
        updated_report = report.copy()
        
        if dsi_result.get('dsi_score') is not None:
            dsi_score = dsi_result['dsi_score']
            dsi_range = dsi_result['dsi_range']
            breakdown = dsi_result.get('dsi_breakdown', {})
            interpretation = dsi_result.get('interpretation', {})
            
            dsi_info = [
                f"\n=== DSI (Dysphonia Severity Index) ===",
                f"DSI Score: {dsi_score} ({dsi_range})",
                f"Параметры:",
                f"  - MPT: {breakdown.get('mpt_sec', 0):.2f}с ({interpretation.get('mpt_status', 'N/A')})",
                f"  - F0-High: {breakdown.get('f0_high_hz', 0):.1f} Гц ({interpretation.get('f0_high_status', 'N/A')})",
                f"  - I-Low: {breakdown.get('i_low_db', 0):.1f} дБ ({interpretation.get('i_low_status', 'N/A')})",
                f"  - Jitter: {breakdown.get('jitter_percent', 0):.2f}% ({interpretation.get('jitter_status', 'N/A')})",
                f"Интерпретация: {interpretation.get('pd_risk_note', '')}",
                f"DSI коррелирует с Voice Handicap Index и идеален для мониторинга терапии (LSVT LOUD)."
            ]
            updated_report.extend(dsi_info)
        elif dsi_result.get('error'):
            updated_report.append(f"\nDSI: {dsi_result.get('error', 'Не удалось рассчитать')}")
        
        return updated_report
    
    def _calculate_dsi(self, features: Dict[str, float]) -> Dict:
        """
        Расчет DSI (Dysphonia Severity Index)
        
        Формула: DSI = 0.13 × MPT + 0.0053 × F0-High - 0.26 × I-Low - 1.18 × Jitter(%) + 12.4
        
        Интерпретация (согласно исследованиям):
        - Около +5: Нормальный голос (среднее для здоровых: +3.05, диапазон 2.13-3.98)
        - Около 0: Пограничное состояние
        - Около -5: Тяжелая дисфония
        - Отрицательные значения: Указывают на ухудшение качества голоса
        
        Практические диапазоны:
        - >= 2.0: Нормальный голос
        - 0…2.0: Легкая дисфония
        - -2…0: Умеренная дисфония (PD 1-2)
        - < -2: Тяжелая дисфония (PD 3-5)
        """
        try:
            # Получение параметров
            mpt_sec = features.get('mpt_sec', 0.0)
            f0_high_hz = features.get('f0_high_hz', 0.0)
            i_low_db = features.get('i_low_db', 0.0)
            jitter_percent = features.get('jitter_percent', 0.0)
            
            # Проверка наличия всех параметров
            if mpt_sec == 0.0 or f0_high_hz == 0.0 or i_low_db == 0.0:
                return {
                    "dsi_score": None,
                    "dsi_range": "Недостаточно данных для расчета DSI",
                    "dsi_breakdown": {
                        "mpt_sec": round(mpt_sec, 2),
                        "f0_high_hz": round(f0_high_hz, 1),
                        "i_low_db": round(i_low_db, 1),
                        "jitter_percent": round(jitter_percent, 2)
                    },
                    "error": "Отсутствуют необходимые параметры для расчета DSI"
                }
            
            # Расчет DSI по формуле
            dsi_score = (0.13 * mpt_sec + 
                        0.0053 * f0_high_hz - 
                        0.26 * i_low_db - 
                        1.18 * jitter_percent + 
                        12.4)
            
            # Интерпретация DSI
            if dsi_score >= 2.0:
                dsi_range = "Нормальный голос"
                pd_risk_note = "Низкий риск ПД"
            elif dsi_score >= 0.0:
                dsi_range = "Легкая дисфония"
                pd_risk_note = "Умеренный риск ПД"
            elif dsi_score >= -2.0:
                dsi_range = "Умеренная дисфония (PD риск высокий)"
                pd_risk_note = "Высокий риск ПД (стадия 1-2)"
            else:
                dsi_range = "Тяжелая дисфония (PD риск очень высокий)"
                pd_risk_note = "Очень высокий риск ПД (стадия 3-5)"
            
            return {
                "dsi_score": round(dsi_score, 2),
                "dsi_range": dsi_range,
                "dsi_breakdown": {
                    "mpt_sec": round(mpt_sec, 2),
                    "f0_high_hz": round(f0_high_hz, 1),
                    "i_low_db": round(i_low_db, 1),
                    "jitter_percent": round(jitter_percent, 2)
                },
                "interpretation": {
                    "mpt_status": "Низкий" if mpt_sec < 10 else "Нормальный" if mpt_sec >= 15 else "Снижен",
                    "f0_high_status": "Низкий" if f0_high_hz < 300 else "Нормальный" if f0_high_hz >= 400 else "Снижен",
                    "i_low_status": "Повышен" if i_low_db > 55 else "Нормальный" if i_low_db < 45 else "Повышен",
                    "jitter_status": "Высокий" if jitter_percent > 1.5 else "Нормальный" if jitter_percent < 1.0 else "Повышен",
                    "pd_risk_note": pd_risk_note
                },
                "formula": "DSI = 0.13 × MPT + 0.0053 × F0-High - 0.26 × I-Low - 1.18 × Jitter(%) + 12.4"
            }
            
        except Exception as e:
            return {
                "dsi_score": None,
                "dsi_range": "Ошибка расчета DSI",
                "dsi_breakdown": {},
                "error": str(e)
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
        return json.dumps(result, ensure_ascii=False, indent=2)


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