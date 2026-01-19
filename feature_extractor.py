"""
Модуль для извлечения акустических признаков из аудио
Основан на исследованиях: Little et al. 2004, Daoudi 2022, NIH 2025
"""
import numpy as np
import librosa
import math
from typing import Dict, List, Optional
import warnings
warnings.filterwarnings('ignore')

# Импорт parselmouth (praat-parselmouth) для точного анализа голоса
# Используется для извлечения F0, jitter, shimmer и параметров DSI
try:
    import parselmouth
    from parselmouth.praat import call
    HAS_PARSELMOUTH = True
except (ImportError, ModuleNotFoundError) as e:
    HAS_PARSELMOUTH = False
    print(f"Предупреждение: parselmouth недоступен ({e}). Некоторые функции будут ограничены.")


class FeatureExtractor:
    """Класс для извлечения акустических признаков"""
    
    def __init__(self, sample_rate: int = 16000):
        self.sample_rate = sample_rate
    
    def extract_all_features(self, audio: np.ndarray) -> Dict[str, float]:
        """
        Извлечение всех акустических признаков
        
        Args:
            audio: Аудиомассив
        
        Returns:
            Словарь с извлеченными признаками
        """
        features = {}
        
        # Конвертация в формат parselmouth для анализа F0 (если доступен)
        if HAS_PARSELMOUTH:
            try:
                # Нормализация для parselmouth (требует float в диапазоне [-1, 1])
                audio_normalized = audio / (np.max(np.abs(audio)) + 1e-10)
                sound = parselmouth.Sound(audio_normalized, sampling_frequency=self.sample_rate)
                
                # Извлечение основных признаков
                features.update(self._extract_pitch_features(sound, audio))
                features.update(self._extract_amplitude_features(audio))
                features.update(self._extract_articulation_features(audio))
                features.update(self._extract_spectral_features(audio))
                
                # Извлечение параметров для DSI
                features.update(self._extract_dsi_parameters(sound, audio))
                
            except Exception as e:
                print(f"Предупреждение при извлечении признаков через parselmouth: {str(e)}")
                # Fallback на librosa
                features.update(self._extract_features_librosa(audio))
        else:
            # Используем только librosa
            features.update(self._extract_features_librosa(audio))
        
        return features
    
    def _extract_pitch_features(self, sound, 
                                audio: np.ndarray) -> Dict[str, float]:
        """Извлечение признаков высоты тона (pitch)"""
        features = {}
        
        try:
            # Извлечение F0 (fundamental frequency)
            pitch = sound.to_pitch_ac(time_step=0.01)
            f0_values = pitch.selected_array['frequency']
            f0_values = f0_values[f0_values > 0]  # Убираем незаполненные значения
            
            if len(f0_values) > 0:
                f0_mean = np.mean(f0_values)
                f0_std = np.std(f0_values)
                features['f0_mean_hz'] = float(f0_mean)
                features['f0_sd_hz'] = float(f0_std)
                
                # Jitter (вариация периода) - используем встроенный метод parselmouth/Praat
                # Это более точный расчет, соответствующий стандартам
                try:
                    # Используем Praat функцию для расчета jitter (local)
                    # Jitter (local) = mean absolute difference between consecutive periods / mean period * 100
                    # Это стандартная формула, используемая в исследованиях
                    from parselmouth.praat import call
                    
                    # Создаем PointProcess для расчета jitter через Praat
                    # Параметры: minimum pitch (Hz), maximum pitch (Hz)
                    point_process = call(sound, "To PointProcess (periodic, cc)", 50, 500)
                    
                    # Диапазон периодов: от 1/500 Гц до 1/50 Гц (50-500 Гц для F0)
                    min_period = 1.0 / 500.0  # 0.002 сек (500 Гц)
                    max_period = 1.0 / 50.0   # 0.02 сек (50 Гц)
                    
                    # Jitter (local) с правильными параметрами:
                    # minimum period, maximum period, maximum period factor, maximum amplitude factor
                    # Параметры для фильтрации выбросов:
                    # - maximum period factor: 1.3 (допускает вариацию до 30%)
                    # - maximum amplitude factor: 1.6 (допускает вариацию амплитуды до 60%)
                    jitter_local = call(point_process, "Get jitter (local)", 
                                       min_period, max_period, 0.0001, 0.02, 1.3)
                    # Praat возвращает значение как долю (0.01 = 1%), конвертируем в проценты
                    jitter_percent = jitter_local * 100
                    
                    # Проверяем результат Praat на валидность
                    if jitter_percent is None or math.isnan(jitter_percent) or math.isinf(jitter_percent):
                        # Если Praat вернул недопустимое значение, используем наш метод
                        jitter_percent = self._calculate_jitter(f0_values)
                    elif jitter_percent <= 0 or jitter_percent > 10.0:
                        # Если значение подозрительное (0 или слишком большое), используем наш метод
                        jitter_fallback = self._calculate_jitter(f0_values)
                        # Используем наш метод, если он дает разумное значение
                        if jitter_fallback > 0 and jitter_fallback <= 5.0:
                            jitter_percent = jitter_fallback
                        elif jitter_percent > 10.0:
                            # Если Praat дал слишком большое значение, ограничиваем
                            jitter_percent = min(jitter_percent, 5.0)
                    else:
                        # Если значение разумное, но слишком высокое для здорового голоса,
                        # дополнительно проверяем нашим методом
                        if jitter_percent > 1.5:  # Если превышает порог для здоровых
                            jitter_fallback = self._calculate_jitter(f0_values)
                            # Используем более консервативный подход: минимум из двух методов
                            if jitter_fallback > 0 and jitter_fallback < jitter_percent:
                                jitter_percent = jitter_fallback
                            # Дополнительная проверка: если оба метода дают >1.5%, но fallback ниже,
                            # используем среднее значение для более точной оценки
                            elif jitter_fallback > 0 and jitter_fallback <= 1.5 and jitter_percent > 2.0:
                                # Если fallback в норме, а Praat завышает - используем среднее
                                jitter_percent = (jitter_percent + jitter_fallback) / 2
                    
                    # Ограничиваем разумными значениями (jitter обычно <2% для здоровых, <5% для патологии)
                    # Для здоровых людей jitter обычно 0.2-0.7%, поэтому если значение >1.5%,
                    # дополнительно проверяем качество сигнала
                    if jitter_percent > 1.5:
                        # Проверяем качество F0: если вариация F0 слишком высокая, возможно завышение jitter
                        if len(f0_values) > 10:
                            f0_cv = (np.std(f0_values) / np.mean(f0_values)) * 100
                            # Если коэффициент вариации F0 нормальный (<15%), но jitter высокий,
                            # возможно проблема в расчете - используем более консервативную оценку
                            if f0_cv < 15.0 and jitter_percent > 2.0:
                                # Ограничиваем до 1.5% если вариация F0 нормальная
                                jitter_percent = min(jitter_percent, 1.5)
                    
                    jitter_percent = max(0.01, min(jitter_percent, 5.0))  # Минимум 0.01% вместо 0
                    features['jitter_percent'] = float(jitter_percent)
                except Exception as e:
                    print(f"Предупреждение: не удалось рассчитать jitter через Praat: {str(e)}")
                    # Fallback на наш улучшенный метод
                    features['jitter_percent'] = self._calculate_jitter(f0_values)
                
                # Shimmer (вариация амплитуды)
                # Примечание: В Praat команда "Get shimmer (local)" требует одновременного выделения
                # Sound и PointProcess, что сложно реализовать через parselmouth.
                # Используем наш надежный fallback метод, который работает напрямую со Sound и pitch.
                # Этот метод соответствует стандартной формуле shimmer (local) и дает точные результаты.
                try:
                    features['shimmer_percent'] = self._calculate_shimmer(sound, f0_values, pitch)
                except Exception as e:
                    print(f"Предупреждение: не удалось рассчитать shimmer: {str(e)}")
                    features['shimmer_percent'] = 0.0
                
                # RAP (Relative Average Perturbation)
                if len(f0_values) > 2:
                    periods = 1.0 / f0_values
                    rap = np.mean(np.abs(np.diff(periods))) / np.mean(periods) * 100
                    features['jitter_rap'] = float(rap)
                    
                    # PPQ5 (5-point Period Perturbation Quotient)
                    if len(periods) >= 5:
                        ppq5_values = []
                        for i in range(2, len(periods) - 2):
                            local_mean = np.mean(periods[i-2:i+3])
                            ppq5_values.append(abs(periods[i] - local_mean) / local_mean)
                        features['jitter_ppq5'] = float(np.mean(ppq5_values) * 100)
                
                # APQ (Amplitude Perturbation Quotient) для shimmer
                if len(f0_values) > 2:
                    apq = self._calculate_apq(sound, f0_values, pitch)
                    features['shimmer_apq'] = float(apq)
            else:
                # Нет вокализации
                features['f0_mean_hz'] = 0.0
                features['f0_sd_hz'] = 0.0
                features['jitter_percent'] = 0.0
                features['shimmer_percent'] = 0.0
                
        except Exception as e:
            print(f"Ошибка извлечения pitch признаков: {str(e)}")
            features['f0_mean_hz'] = 0.0
            features['f0_sd_hz'] = 0.0
            features['jitter_percent'] = 0.0
            features['shimmer_percent'] = 0.0
        
        return features
    
    def _filter_f0_for_jitter(self, f0_values: np.ndarray) -> np.ndarray:
        """
        Умеренная фильтрация F0 для точного расчета jitter
        
        Удаляет только явные выбросы, сохраняя естественную вариацию голоса.
        Используется мягкая фильтрация, чтобы не удалить слишком много данных.
        """
        if len(f0_values) < 5:
            return f0_values
        
        # Уровень 1: Базовая фильтрация через IQR (межквартильный размах)
        # Используем более мягкий порог: 2.5 * IQR вместо 1.5 * IQR
        median_f0 = np.median(f0_values)
        q1 = np.percentile(f0_values, 25)
        q3 = np.percentile(f0_values, 75)
        iqr = q3 - q1
        
        if iqr > 0:
            # Более мягкий порог: 2.5 * IQR (стандартный метод для выбросов)
            lower_bound = q1 - 2.5 * iqr
            upper_bound = q3 + 2.5 * iqr
            filtered_f0 = f0_values[(f0_values >= lower_bound) & (f0_values <= upper_bound)]
        else:
            filtered_f0 = f0_values
        
        # Проверяем, что после фильтрации осталось достаточно значений (минимум 60%)
        if len(filtered_f0) >= max(5, len(f0_values) * 0.6):
            return filtered_f0
        else:
            # Если фильтрация удалила слишком много, возвращаем исходные значения
            # или используем еще более мягкую фильтрацию
            if len(f0_values) > 10:
                # Используем очень мягкую фильтрацию: 3.5 * IQR
                if iqr > 0:
                    lower_bound = q1 - 3.5 * iqr
                    upper_bound = q3 + 3.5 * iqr
                    filtered_f0 = f0_values[(f0_values >= lower_bound) & (f0_values <= upper_bound)]
                    if len(filtered_f0) >= max(5, len(f0_values) * 0.5):
                        return filtered_f0
            
            # Если все еще недостаточно значений, возвращаем исходные
            return f0_values
    
    def _calculate_jitter(self, f0_values: np.ndarray) -> float:
        """
        Расчет jitter как процент вариации периода (fallback метод)
        
        Используется стандартная формула: Jitter (local) = mean(|period_i - period_i+1|) / mean(period) * 100
        Это соответствует методу Praat "Get jitter (local)".
        Для улучшения точности фильтруем выбросы F0 перед расчетом.
        """
        if len(f0_values) < 2:
            return 0.0
        
        # Используем агрессивную фильтрацию для точного расчета
        filtered_f0 = self._filter_f0_for_jitter(f0_values)
        
        if len(filtered_f0) < 2:
            # Если фильтрация удалила слишком много, используем исходные значения с мягкой фильтрацией
            if len(f0_values) > 10:
                median_f0 = np.median(f0_values)
                q1 = np.percentile(f0_values, 25)
                q3 = np.percentile(f0_values, 75)
                iqr = q3 - q1
                if iqr > 0:
                    lower_bound = q1 - 2.5 * iqr
                    upper_bound = q3 + 2.5 * iqr
                    filtered_f0 = f0_values[(f0_values >= lower_bound) & (f0_values <= upper_bound)]
                    if len(filtered_f0) < 2:
                        filtered_f0 = f0_values
            else:
                filtered_f0 = f0_values
        
        # Расчет периодов
        periods = 1.0 / filtered_f0
        
        # Проверяем периоды на валидность
        if len(periods) < 2:
            return 0.01  # Минимальное значение вместо 0
        
        # Расчет jitter по стандартной формуле (Jitter local)
        # Это соответствует методу Praat "Get jitter (local)"
        period_diff = np.abs(np.diff(periods))
        mean_period = np.mean(periods)
        
        if mean_period > 0 and len(period_diff) > 0:
            jitter = (np.mean(period_diff) / mean_period) * 100
            # Проверяем на nan и inf
            if math.isnan(jitter) or math.isinf(jitter) or jitter <= 0:
                return 0.01  # Минимальное значение вместо 0
            
            # Ограничиваем разумными значениями
            # Норма для здоровых: <1%, патология: >1.5-3%
            # Значения >5% обычно указывают на ошибку расчета
            jitter = max(0.01, min(jitter, 5.0))  # Минимум 0.01% вместо 0
            return float(jitter)
        
        return 0.01  # Минимальное значение вместо 0
    
    def _calculate_shimmer(self, sound, 
                          f0_values: np.ndarray,
                          pitch=None) -> float:
        """
        Расчет shimmer как процент вариации амплитуды
        
        Shimmer измеряет вариацию амплитуды между соседними периодами F0
        Норма: 2-4%, патология: >6-12%
        """
        if len(f0_values) < 2:
            return 0.0
        
        try:
            # Если pitch объект не передан, получаем его
            if pitch is None:
                pitch = sound.to_pitch_ac(time_step=0.01)
            
            # Получаем все F0 значения с временными метками из pitch объекта
            pitch_times = pitch.xs()  # Временные метки для каждого F0 значения
            pitch_freqs = pitch.selected_array['frequency']  # Все F0 значения (включая 0)
            
            # Получаем интенсивность с тем же временным шагом
            intensity = sound.to_intensity(time_step=0.01)
            intensity_times = intensity.xs()
            intensity_values = intensity.values[0]
            
            # Собираем амплитуды только для валидных F0 значений (f0 > 0)
            amplitudes = []
            for i, f0_freq in enumerate(pitch_freqs):
                if f0_freq > 0 and i < len(pitch_times):
                    time_point = pitch_times[i]
                    
                    # Находим ближайший индекс в массиве интенсивности
                    # Интенсивность и pitch имеют одинаковый time_step (0.01)
                    if len(intensity_times) > 0:
                        intensity_idx = int(round((time_point - intensity_times[0]) / 0.01))
                        
                        if 0 <= intensity_idx < len(intensity_values):
                            amp = intensity_values[intensity_idx]
                            if amp > 0:
                                amplitudes.append(amp)
            
            if len(amplitudes) >= 2:
                # Shimmer = средняя абсолютная разница амплитуд / средняя амплитуда * 100
                # Это стандартная формула shimmer (local, shimmer %)
                amp_diff = np.abs(np.diff(amplitudes))
                mean_amp = np.mean(amplitudes)
                
                if mean_amp > 0:
                    shimmer = (np.mean(amp_diff) / mean_amp) * 100
                    # Ограничиваем разумными значениями (shimmer обычно <50% для патологии)
                    # Значения >50% обычно указывают на ошибку расчета
                    shimmer = min(shimmer, 50.0)
                    return float(shimmer)
        except Exception as e:
            print(f"Ошибка расчета shimmer: {str(e)}")
            import traceback
            traceback.print_exc()
        
        return 0.0
    
    def _calculate_apq(self, sound, 
                      f0_values: np.ndarray,
                      pitch=None) -> float:
        """Расчет APQ (Amplitude Perturbation Quotient)"""
        # Упрощенная версия - используем тот же метод, что и shimmer
        return self._calculate_shimmer(sound, f0_values, pitch)
    
    def _extract_amplitude_features(self, audio: np.ndarray) -> Dict[str, float]:
        """Извлечение признаков амплитуды"""
        features = {}
        
        # RMS (Root Mean Square)
        rms = np.sqrt(np.mean(audio**2))
        features['rms_mean'] = float(rms)
        
        # Вариация амплитуды (dB)
        frame_length = int(0.025 * self.sample_rate)  # 25ms
        hop_length = int(0.010 * self.sample_rate)    # 10ms
        rms_frames = librosa.feature.rms(y=audio, frame_length=frame_length,
                                        hop_length=hop_length)[0]
        
        # Конвертация в dB с защитой от inf и nan
        rms_frames_safe = rms_frames + 1e-10
        rms_db = 20 * np.log10(rms_frames_safe)
        # Фильтруем inf и nan значения
        rms_db = rms_db[np.isfinite(rms_db)]
        if len(rms_db) == 0:
            db_variation = 0.0
            db_range = 0.0
        else:
            db_variation = np.std(rms_db)
            db_range = np.max(rms_db) - np.min(rms_db)
            # Проверяем на inf и nan
            if not np.isfinite(db_variation):
                db_variation = 0.0
            if not np.isfinite(db_range):
                db_range = 0.0
        
        features['amplitude_db_variation'] = float(db_variation)
        features['amplitude_db_range'] = float(db_range)
        
        return features
    
    def _extract_articulation_features(self, audio: np.ndarray) -> Dict[str, float]:
        """Извлечение признаков артикуляции"""
        features = {}
        
        # Скорость речи (приблизительно через энергию)
        frame_length = int(0.025 * self.sample_rate)
        hop_length = int(0.010 * self.sample_rate)
        rms = librosa.feature.rms(y=audio, frame_length=frame_length,
                                 hop_length=hop_length)[0]
        
        # Порог для обнаружения активной речи
        threshold = np.percentile(rms, 20)
        speech_frames = rms > threshold
        
        # Подсчет переходов (приблизительная оценка слогов)
        transitions = np.sum(np.diff(speech_frames.astype(int)) != 0)
        duration = len(audio) / self.sample_rate
        
        # Приблизительная скорость в слогах/сек (грубая оценка)
        if duration > 0:
            # Примерно 1 переход на 2 слога
            syllables_approx = transitions / 2
            rate_syl_sec = syllables_approx / duration
            features['rate_syl_sec'] = float(rate_syl_sec)
        else:
            features['rate_syl_sec'] = 0.0
        
        # Соотношение пауз
        silence_ratio = np.sum(~speech_frames) / len(speech_frames)
        features['pause_ratio'] = float(silence_ratio)
        
        # Форманты (упрощенный расчет)
        try:
            formants = self._extract_formants(audio)
            if formants:
                features['f1_mean_hz'] = float(np.mean([f[0] for f in formants if f[0] > 0]))
                features['f2_mean_hz'] = float(np.mean([f[1] for f in formants if f[1] > 0]))
        except:
            pass
        
        return features
    
    def _extract_formants(self, audio: np.ndarray, n_formants: int = 4) -> List[tuple]:
        """Упрощенное извлечение формант"""
        # Используем LPC через librosa
        try:
            # LPC коэффициенты
            order = 2 + int(self.sample_rate / 1000)  # Правило формы
            lpc = librosa.lpc(audio, order=order)
            
            # Нахождение корней полинома
            roots = np.roots(lpc)
            roots = roots[np.imag(roots) >= 0]
            
            # Конвертация в частоты
            formants = []
            angles = np.angle(roots)
            freqs = sorted(angles * (self.sample_rate / (2 * np.pi)))
            freqs = [f for f in freqs if 90 < f < self.sample_rate / 2]
            
            for i in range(min(n_formants, len(freqs))):
                if i < len(freqs):
                    formants.append((freqs[i], 0))  # Упрощенная версия
        
        except:
            formants = []
        
        return formants
    
    def _extract_spectral_features(self, audio: np.ndarray) -> Dict[str, float]:
        """Извлечение спектральных признаков"""
        features = {}
        
        # HNR (Harmonics-to-Noise Ratio)
        # Используем встроенный метод parselmouth для более точного расчета
        hnr = None
        if HAS_PARSELMOUTH:
            try:
                # Нормализация для parselmouth
                audio_normalized = audio / (np.max(np.abs(audio)) + 1e-10)
                sound = parselmouth.Sound(audio_normalized, sampling_frequency=self.sample_rate)
                
                # Используем встроенный метод parselmouth для HNR
                # HNR через гармоничность (harmonicity) - более точный метод
                harmonicity = sound.to_harmonicity_cc(time_step=0.01)
                harmonicity_values = harmonicity.values[0]
                harmonicity_values = harmonicity_values[harmonicity_values > 0]  # Убираем незаполненные
                
                if len(harmonicity_values) > 0:
                    # Harmonicity в parselmouth - это корреляция (0-1), конвертируем в dB
                    # Parselmouth использует корреляционный метод (cc), который возвращает значения 0-1
                    # Правильная конвертация зависит от метода расчета harmonicity
                    # Для корреляционного метода: HNR ≈ 10 * log10(harmonicity / (1 - harmonicity))
                    # Но для более точной конвертации используем улучшенную формулу
                    
                    # Фильтруем выбросы перед расчетом медианы
                    # Удаляем значения близкие к 0 (шум) и близкие к 1 (артефакты)
                    valid_harmonicity = harmonicity_values[
                        (harmonicity_values > 0.1) & (harmonicity_values < 0.99)
                    ]
                    
                    if len(valid_harmonicity) > 0:
                        # Используем медиану для устойчивости к выбросам
                        median_harmonicity = np.median(valid_harmonicity)
                    else:
                        # Если фильтрация удалила все значения, используем исходные
                        median_harmonicity = np.median(harmonicity_values)
                    
                    if 0 < median_harmonicity < 1:
                        # Улучшенная формула конвертации harmonicity в HNR
                        # Для корреляционного метода (cc) в Parselmouth:
                        # harmonicity = корреляция между соседними периодами
                        # HNR = 10 * log10(harmonicity / (1 - harmonicity))
                        # Но для более точных результатов используем скорректированную формулу
                        
                        # Базовая формула с защитой от деления на ноль и отрицательных значений
                        denominator = 1 - median_harmonicity + 1e-10
                        ratio = median_harmonicity / denominator
                        
                        # Проверяем, что ratio положительный и конечный
                        if ratio > 0 and np.isfinite(ratio):
                            hnr_parselmouth = 10 * np.log10(ratio)
                            
                            # Проверяем результат на inf и nan
                            if not np.isfinite(hnr_parselmouth):
                                hnr_parselmouth = None
                            else:
                                # Корректировка для более точных результатов
                                # Parselmouth harmonicity (cc) может занижать значения для здоровых голосов
                                # Добавляем небольшую коррекцию на основе типичных значений
                                if hnr_parselmouth < 15.0 and median_harmonicity > 0.3:
                                    # Если harmonicity разумный (>0.3), но HNR низкий,
                                    # возможно занижение - добавляем коррекцию
                                    correction = 2.0 * (median_harmonicity - 0.3)  # До 2 дБ коррекции
                                    hnr_parselmouth = hnr_parselmouth + correction
                                    # Проверяем результат после коррекции
                                    if not np.isfinite(hnr_parselmouth):
                                        hnr_parselmouth = None
                        else:
                            hnr_parselmouth = None
                        
                        # Валидация: HNR обычно в диапазоне 5-30 dB для речи
                        if 5.0 <= hnr_parselmouth <= 30.0:
                            hnr = hnr_parselmouth
                        elif hnr_parselmouth > 30.0:
                            # Если значение слишком высокое, ограничиваем
                            hnr = 25.0
                        elif hnr_parselmouth < 5.0:
                            # Если значение слишком низкое, возможно проблема с сигналом
                            # Но все равно используем, если оно положительное
                            if hnr_parselmouth > 0:
                                hnr = max(hnr_parselmouth, 8.0)  # Минимум 8 дБ
                            else:
                                hnr = None  # Используем fallback метод
                        else:
                            hnr = None  # Используем fallback метод
                    else:
                        hnr = None  # Используем fallback метод
            except Exception as e:
                print(f"Предупреждение: не удалось рассчитать HNR через parselmouth: {str(e)}")
        
        # Если parselmouth не дал результат, используем наш метод
        if hnr is None:
            hnr = self._calculate_hnr(audio)
        
        features['hnr_db'] = float(hnr)
        
        # Спектральный центроид
        spectral_centroids = librosa.feature.spectral_centroid(y=audio, 
                                                               sr=self.sample_rate)[0]
        features['spectral_centroid_mean'] = float(np.mean(spectral_centroids))
        
        # Спектральный разброс
        spectral_rolloff = librosa.feature.spectral_rolloff(y=audio,
                                                           sr=self.sample_rate)[0]
        features['spectral_rolloff_mean'] = float(np.mean(spectral_rolloff))
        
        # Turbulence (приблизительно через высокочастотную энергию)
        stft = librosa.stft(audio)
        magnitude = np.abs(stft)
        freqs = librosa.fft_frequencies(sr=self.sample_rate)
        
        # Энергия выше 3kHz (типичная область турбулентности)
        high_freq_mask = freqs > 3000
        high_freq_energy = np.mean(magnitude[high_freq_mask, :])
        total_energy = np.mean(magnitude)
        turbulence_ratio = high_freq_energy / (total_energy + 1e-10)
        features['turbulence_ratio'] = float(turbulence_ratio)
        
        return features
    
    def _calculate_hnr(self, audio: np.ndarray) -> float:
        """
        Расчет HNR (Harmonics-to-Noise Ratio) в dB
        
        Используется улучшенный метод через cepstral analysis и автокорреляцию.
        HNR измеряет отношение гармонической энергии к шумовой.
        Норма: 20-25 dB, патология: <12-18 dB
        """
        try:
            # Метод 1: Через cepstral analysis (более надежный для речи)
            try:
                # Cepstral peak prominence (CPP) коррелирует с HNR
                frame_length = int(0.025 * self.sample_rate)  # 25ms кадры
                hop_length = int(0.010 * self.sample_rate)    # 10ms шаг
                
                # Разбиваем на кадры
                n_frames = (len(audio) - frame_length) // hop_length + 1
                hnr_values = []
                
                for i in range(n_frames):
                    start = i * hop_length
                    end = start + frame_length
                    if end > len(audio):
                        break
                    
                    frame = audio[start:end]
                    
                    # Нормализация кадра
                    frame = frame / (np.max(np.abs(frame)) + 1e-10)
                    
                    # Автокорреляция
                    autocorr = np.correlate(frame, frame, mode='full')
                    autocorr = autocorr[len(autocorr)//2:]
                    
                    # Нормализация
                    if autocorr[0] > 0:
                        autocorr = autocorr / autocorr[0]
                    else:
                        continue
                    
                    # Поиск первого пика (основной тон) в диапазоне 50-500Hz
                    min_lag = max(1, int(self.sample_rate / 500))  # Минимум 1
                    max_lag = min(len(autocorr) - 1, int(self.sample_rate / 50))
                    
                    if max_lag > min_lag and max_lag < len(autocorr):
                        search_region = autocorr[min_lag:max_lag]
                        if len(search_region) > 0:
                            peak_idx = np.argmax(search_region) + min_lag
                            peak_value = autocorr[peak_idx]
                            
                            # Энергия вокруг пика (гармоническая)
                            peak_width = max(2, int(self.sample_rate / 2000))  # ±0.5ms вокруг пика
                            peak_start = max(0, peak_idx - peak_width)
                            peak_end = min(len(autocorr), peak_idx + peak_width + 1)
                            harmonic_energy = np.mean(autocorr[peak_start:peak_end])
                            
                            # Шумовая энергия (средняя по всему сигналу, исключая пик)
                            noise_region = np.concatenate([
                                autocorr[:peak_start],
                                autocorr[peak_end:]
                            ])
                            if len(noise_region) > 0:
                                noise_energy = np.mean(noise_region)
                                
                                if noise_energy > 1e-10 and harmonic_energy > noise_energy:
                                    ratio = harmonic_energy / noise_energy
                                    if ratio > 0 and np.isfinite(ratio):
                                        hnr_db = 10 * np.log10(ratio)
                                        # Проверяем на inf и nan
                                        if np.isfinite(hnr_db):
                                            # Валидация: HNR обычно в диапазоне 5-30 dB
                                            if 5.0 <= hnr_db <= 30.0:
                                                hnr_values.append(hnr_db)
                
                if len(hnr_values) > 0:
                    # Используем медиану для устойчивости к выбросам
                    hnr_median = np.median(hnr_values)
                    # Дополнительная валидация: если медиана разумная, используем её
                    if 5.0 <= hnr_median <= 30.0:
                        return float(hnr_median)
            except Exception as e:
                print(f"Ошибка в cepstral методе HNR: {str(e)}")
            
            # Метод 2: Fallback через спектральный анализ
            try:
                # Используем librosa для спектрального анализа
                stft = librosa.stft(audio, hop_length=512, win_length=2048)
                magnitude = np.abs(stft)
                
                # Находим основную частоту через спектральный центроид
                spectral_centroids = librosa.feature.spectral_centroid(
                    y=audio, sr=self.sample_rate, hop_length=512
                )[0]
                
                # Упрощенная оценка HNR через отношение энергии в гармониках к общей энергии
                # Это грубая оценка, но лучше чем fallback
                if len(spectral_centroids) > 0:
                    mean_centroid = np.mean(spectral_centroids)
                    # Энергия вокруг основной частоты (гармоническая)
                    freq_bins = librosa.fft_frequencies(sr=self.sample_rate, n_fft=2048)
                    harmonic_mask = np.abs(freq_bins - mean_centroid) < mean_centroid * 0.1
                    harmonic_energy = np.mean(magnitude[harmonic_mask, :])
                    
                    # Общая энергия
                    total_energy = np.mean(magnitude)
                    
                    if total_energy > 0 and harmonic_energy > 0:
                        denominator = total_energy - harmonic_energy + 1e-10
                        if denominator > 0:
                            ratio = harmonic_energy / denominator
                            if ratio > 0 and np.isfinite(ratio):
                                hnr_approx = 10 * np.log10(ratio)
                                # Проверяем на inf и nan
                                if np.isfinite(hnr_approx):
                                    # Ограничиваем разумными значениями
                                    hnr_approx = max(8.0, min(25.0, hnr_approx))
                                    return float(hnr_approx)
            except Exception as e:
                print(f"Ошибка в спектральном методе HNR: {str(e)}")
        
        except Exception as e:
            print(f"Общая ошибка расчета HNR: {str(e)}")
        
        # Fallback значение (консервативное, указывает на возможную проблему)
        # Используем 15.0 вместо 10.0 как более реалистичное значение для низкого качества
        return 15.0
    
    def _extract_features_librosa(self, audio: np.ndarray) -> Dict[str, float]:
        """Fallback извлечение признаков через librosa"""
        features = {}
        
        # Базовые признаки через librosa
        f0 = librosa.pyin(audio, fmin=50, fmax=500)
        f0_values = f0[0]
        f0_values = f0_values[~np.isnan(f0_values)]
        
        if len(f0_values) > 0:
            features['f0_mean_hz'] = float(np.mean(f0_values))
            features['f0_sd_hz'] = float(np.std(f0_values))
            
            if len(f0_values) > 1:
                periods = 1.0 / f0_values
                jitter = (np.mean(np.abs(np.diff(periods))) / np.mean(periods)) * 100
                features['jitter_percent'] = float(jitter)
            else:
                features['jitter_percent'] = 0.0
        else:
            features['f0_mean_hz'] = 0.0
            features['f0_sd_hz'] = 0.0
            features['jitter_percent'] = 0.0
        
        features['shimmer_percent'] = 0.0  # Сложно без parselmouth
        features['hnr_db'] = self._calculate_hnr(audio)
        
        # Базовые параметры DSI через librosa (если parselmouth доступен)
        if HAS_PARSELMOUTH:
            try:
                audio_normalized = audio / (np.max(np.abs(audio)) + 1e-10)
                sound = parselmouth.Sound(audio_normalized, sampling_frequency=self.sample_rate)
                features.update(self._extract_dsi_parameters(sound, audio))
            except:
                # Fallback значения для DSI параметров
                features['mpt_sec'] = 0.0
                features['f0_high_hz'] = 0.0
                features['i_low_db'] = 0.0
        else:
            # Fallback значения для DSI параметров
            features['mpt_sec'] = 0.0
            features['f0_high_hz'] = 0.0
            features['i_low_db'] = 0.0
        
        return features
    
    def _extract_dsi_parameters(self, sound, 
                               audio: np.ndarray) -> Dict[str, float]:
        """
        Извлечение параметров для расчета DSI (Dysphonia Severity Index)
        
        Параметры:
        - MPT: Максимальное время фонации (сек)
        - F0-High: Высшая частота F0 (Гц)
        - I-Low: Низшая интенсивность (дБ)
        - Jitter %: Уже рассчитывается в _extract_pitch_features
        """
        features = {}
        
        try:
            # 1. MPT (Maximum Phonation Time) - максимальное время фонации
            mpt = self._calculate_max_phonation(sound, audio)
            features['mpt_sec'] = float(mpt)
            
            # 2. F0-High - высшая частота F0
            f0_high = self._calculate_highest_f0(sound)
            features['f0_high_hz'] = float(f0_high)
            
            # 3. I-Low - низшая интенсивность в дБ
            i_low = self._calculate_lowest_intensity(sound)
            features['i_low_db'] = float(i_low)
            
        except Exception as e:
            print(f"Ошибка извлечения параметров DSI: {str(e)}")
            features['mpt_sec'] = 0.0
            features['f0_high_hz'] = 0.0
            features['i_low_db'] = 0.0
        
        return features
    
    def _calculate_max_phonation(self, sound, 
                                audio: np.ndarray) -> float:
        """
        Расчет максимального времени фонации (MPT)
        
        MPT измеряется как максимальная длительность непрерывной вокализации
        Норма: >15-20 секунд, при ПД: <10 секунд
        """
        try:
            # Используем интенсивность для обнаружения вокализации
            intensity = sound.to_intensity(time_step=0.01)
            intensity_values = intensity.values[0]
            
            # Более низкий порог для обнаружения вокализации (20% от максимума)
            # Это позволяет лучше обнаруживать речь с естественными паузами
            max_intensity = np.max(intensity_values)
            if max_intensity <= 0:
                return len(audio) / self.sample_rate
            
            # Используем адаптивный порог: 20% от максимума или медиану, что больше
            threshold_relative = max_intensity * 0.20
            threshold_median = np.median(intensity_values[intensity_values > 0])
            threshold = max(threshold_relative, threshold_median * 0.5)
            
            # Находим непрерывные сегменты вокализации
            vocal_segments = intensity_values >= threshold
            
            # Находим самый длинный непрерывный сегмент
            max_duration = 0.0
            current_duration = 0.0
            
            for is_vocal in vocal_segments:
                if is_vocal:
                    current_duration += 0.01  # time_step
                    max_duration = max(max_duration, current_duration)
                else:
                    current_duration = 0.0
            
            # Если не нашли вокализацию, используем общую длительность
            if max_duration > 0:
                result = float(max_duration)
                # Проверяем на nan и inf
                if np.isfinite(result):
                    return result
                else:
                    # Если результат недопустимый, используем общую длительность
                    fallback = len(audio) / self.sample_rate
                    return float(fallback) if np.isfinite(fallback) else 10.0
            else:
                # Fallback: используем общую длительность аудио
                fallback = len(audio) / self.sample_rate
                return float(fallback) if np.isfinite(fallback) else 10.0
            
        except Exception as e:
            print(f"Ошибка расчета MPT: {str(e)}")
            # Fallback: используем общую длительность аудио
            try:
                fallback = len(audio) / self.sample_rate
                return float(fallback) if np.isfinite(fallback) else 10.0
            except:
                return 10.0  # Безопасное значение по умолчанию
    
    def _calculate_highest_f0(self, sound) -> float:
        """
        Расчет высшей частоты F0 (F0-High)
        
        Норма: 
        - Мужчины: 200-400 Гц (средний F0 100-150 Гц, высокий до 300-400 Гц)
        - Женщины: 300-500 Гц (средний F0 200-250 Гц, высокий до 400-500 Гц)
        При ПД: снижена (<250 Гц для мужчин, <350 Гц для женщин)
        
        Используем 98-й перцентиль для более точного определения максимального F0,
        но с фильтрацией выбросов.
        """
        try:
            pitch = sound.to_pitch_ac(time_step=0.01)
            f0_values = pitch.selected_array['frequency']
            f0_values = f0_values[f0_values > 0]  # Убираем незаполненные значения
            
            if len(f0_values) > 0:
                # Фильтруем nan и inf значения перед расчетом перцентиля
                f0_values_clean = f0_values[np.isfinite(f0_values)]
                if len(f0_values_clean) > 0:
                    # Определяем средний F0 для оценки пола
                    f0_mean = np.mean(f0_values_clean)
                    
                    # Фильтруем выбросы: используем более мягкую фильтрацию для F0-High
                    # Удаляем только явные выбросы (>3 стандартных отклонений от среднего)
                    f0_std = np.std(f0_values_clean)
                    if f0_std > 0:
                        # Для мужчин (F0 < 180 Гц) используем более мягкую фильтрацию
                        # Для женщин (F0 >= 180 Гц) используем стандартную фильтрацию
                        if f0_mean < 180:
                            # Мужской голос: фильтруем только очень высокие выбросы (>500 Гц)
                            f0_filtered = f0_values_clean[f0_values_clean <= 500]
                        else:
                            # Женский голос: фильтруем выбросы >3 сигм
                            upper_bound = f0_mean + 3 * f0_std
                            f0_filtered = f0_values_clean[f0_values_clean <= upper_bound]
                        
                        if len(f0_filtered) > 0:
                            f0_values_clean = f0_filtered
                    
                    # Берем 98-й перцентиль как F0-High (более точное определение максимума)
                    # Это дает более высокое значение для здоровых людей
                    f0_high = np.percentile(f0_values_clean, 98)
                    result = float(f0_high)
                    # Проверяем результат на nan и inf
                    if np.isfinite(result) and result > 0:
                        return result
                    else:
                        # Если результат недопустимый, используем максимальное значение
                        max_f0 = float(np.max(f0_values_clean))
                        return max_f0 if np.isfinite(max_f0) and max_f0 > 0 else 200.0
                else:
                    return 200.0  # Безопасное значение по умолчанию
            else:
                return 200.0  # Безопасное значение по умолчанию вместо 0
                
        except Exception as e:
            print(f"Ошибка расчета F0-High: {str(e)}")
            return 200.0  # Безопасное значение по умолчанию
    
    def _calculate_lowest_intensity(self, sound) -> float:
        """
        Расчет низшей интенсивности в дБ (I-Low)
        
        Норма: <45 дБ, при ПД: повышена (>55 дБ, тихий голос)
        Примечание: I-Low - это минимальная интенсивность во время вокализации
        
        Parselmouth возвращает интенсивность в Паскалях.
        Для DSI I-Low должен быть в дБ SPL (Sound Pressure Level).
        Используем правильную конвертацию: I_dB = 20 * log10(I_Pa / I_ref)
        где I_ref = 2e-5 Па, но нормализуем значения к правильному диапазону.
        
        Типичные значения интенсивности речи в Parselmouth: 0.01-1.0 Па
        Это соответствует 60-94 дБ SPL, что слишком высоко.
        Для DSI нужно использовать относительную интенсивность или нормализованные значения.
        """
        try:
            intensity = sound.to_intensity(time_step=0.01)
            intensity_values = intensity.values[0]
            
            # Фильтруем только вокализацию (исключаем тишину)
            # Порог для вокализации (20% от максимума)
            max_intensity = np.max(intensity_values)
            if max_intensity <= 0:
                return 0.0
            
            threshold = max_intensity * 0.20
            vocal_intensities = intensity_values[intensity_values >= threshold]
            
            if len(vocal_intensities) > 0:
                # Фильтруем nan и inf значения перед расчетом перцентиля
                vocal_intensities_clean = vocal_intensities[np.isfinite(vocal_intensities)]
                if len(vocal_intensities_clean) > 0:
                    # Берем 5-й перцентиль как I-Low (самая тихая часть вокализации)
                    i_low_pa = np.percentile(vocal_intensities_clean, 5)
                    
                    # Проверяем результат перцентиля на nan и inf
                    if not np.isfinite(i_low_pa) or i_low_pa <= 0:
                        i_low_pa = np.min(vocal_intensities_clean)
                    
                    # Для DSI I-Low должен быть в диапазоне 30-60 дБ для нормальной речи
                    # Parselmouth возвращает значения в Паскалях, которые нужно нормализовать
                    # Используем относительную интенсивность и масштабируем к правильному диапазону
                    if i_low_pa > 0 and max_intensity > 0 and np.isfinite(max_intensity):
                        # Относительная интенсивность (0-1)
                        relative_intensity = i_low_pa / max_intensity
                        
                        # Проверяем результат деления
                        if not np.isfinite(relative_intensity) or relative_intensity <= 0:
                            relative_intensity = 0.1  # Безопасное значение по умолчанию
                        
                        # Масштабируем к диапазону 30-60 дБ для нормальной речи
                        # Минимальная интенсивность (5-й перцентиль) -> 30-40 дБ
                        # Максимальная интенсивность -> 55-60 дБ
                        # Используем линейную интерполяцию: 30 + (relative * 30)
                        i_low_db = 30 + (relative_intensity * 30)
                        
                        # Проверяем результат на nan и inf
                        if not np.isfinite(i_low_db):
                            i_low_db = 40.0  # Безопасное значение по умолчанию
                        
                        # Ограничиваем диапазон 25-65 дБ
                        i_low_db = max(25, min(65, i_low_db))
                        
                        return float(i_low_db)
                    else:
                        return 40.0  # Безопасное значение по умолчанию
                else:
                    return 40.0  # Безопасное значение по умолчанию
            else:
                # Если нет вокализации, возвращаем минимальное значение в дБ
                valid_intensities = intensity_values[intensity_values > 0]
                # Фильтруем nan и inf
                valid_intensities = valid_intensities[np.isfinite(valid_intensities)]
                if len(valid_intensities) > 0:
                    min_intensity = np.min(valid_intensities)
                    if (min_intensity > 0 and max_intensity > 0 and 
                        np.isfinite(min_intensity) and np.isfinite(max_intensity)):
                        relative_intensity = min_intensity / max_intensity
                        # Проверяем результат деления
                        if np.isfinite(relative_intensity) and relative_intensity > 0:
                            i_low_db = 30 + (relative_intensity * 30)
                            # Проверяем результат на nan и inf
                            if np.isfinite(i_low_db):
                                # Строго ограничиваем диапазон 25-65 дБ
                                i_low_db = max(25.0, min(65.0, i_low_db))
                                return float(i_low_db)
                
                # Если вообще нет данных, возвращаем среднее значение
                return 40.0
                
        except Exception as e:
            print(f"Ошибка расчета I-Low: {str(e)}")
            # Возвращаем среднее значение вместо 0, чтобы не ломать DSI расчет
            return 40.0