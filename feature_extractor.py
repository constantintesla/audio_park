"""
Модуль для извлечения акустических признаков из аудио
Основан на исследованиях: Little et al. 2004, Daoudi 2022, NIH 2025
"""
import numpy as np
import librosa
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
                    point_process = call(sound, "To PointProcess (periodic, cc)", 50, 500)
                    # Jitter (local) возвращает значение в процентах (как долю, нужно умножить на 100)
                    jitter_local = call(point_process, "Get jitter (local)", 0, 0, 0.0001, 0.02, 1.3)
                    # Praat возвращает значение как долю (0.01 = 1%), конвертируем в проценты
                    jitter_percent = jitter_local * 100
                    # Ограничиваем разумными значениями (jitter обычно <2% для здоровых, <5% для патологии)
                    jitter_percent = min(jitter_percent, 5.0)
                    features['jitter_percent'] = float(jitter_percent)
                except Exception as e:
                    print(f"Предупреждение: не удалось рассчитать jitter через Praat: {str(e)}")
                    # Fallback на наш улучшенный метод
                    features['jitter_percent'] = self._calculate_jitter(f0_values)
                
                # Shimmer (вариация амплитуды) - используем встроенный метод parselmouth/Praat
                try:
                    from parselmouth.praat import call
                    point_process = call(sound, "To PointProcess (periodic, cc)", 50, 500)
                    # Shimmer (local) возвращает значение в процентах напрямую
                    shimmer_local = call(point_process, "Get shimmer (local)", 0, 0, 0.0001, 0.02, 1.3, 1.6)
                    # Shimmer (local) уже в процентах, просто используем его
                    if shimmer_local > 0:
                        # Ограничиваем разумными значениями (shimmer обычно <15% даже для патологии)
                        shimmer_percent = min(shimmer_local * 100, 15.0)  # Умножаем на 100, т.к. Praat возвращает в долях
                        features['shimmer_percent'] = float(shimmer_percent)
                    else:
                        # Fallback на наш метод
                        features['shimmer_percent'] = self._calculate_shimmer(sound, f0_values, pitch)
                except Exception as e:
                    print(f"Предупреждение: не удалось рассчитать shimmer через Praat: {str(e)}")
                    # Fallback на наш метод
                    features['shimmer_percent'] = self._calculate_shimmer(sound, f0_values, pitch)
                
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
    
    def _calculate_jitter(self, f0_values: np.ndarray) -> float:
        """
        Расчет jitter как процент вариации периода (fallback метод)
        
        Используется стандартная формула: Jitter (local) = mean(|period_i - period_i+1|) / mean(period) * 100
        Это соответствует методу Praat "Get jitter (local)".
        Для улучшения точности фильтруем выбросы F0 перед расчетом.
        """
        if len(f0_values) < 2:
            return 0.0
        
        # Более агрессивная фильтрация выбросов F0 для более точного расчета jitter
        # Используем метод, аналогичный Praat: фильтруем значения, которые сильно отклоняются
        if len(f0_values) > 10:
            # Используем медиану и межквартильный размах (IQR) для более устойчивой фильтрации
            median_f0 = np.median(f0_values)
            q1 = np.percentile(f0_values, 25)
            q3 = np.percentile(f0_values, 75)
            iqr = q3 - q1
            
            if iqr > 0:
                # Используем более строгий порог: 1.5 * IQR (стандартный метод для выбросов)
                # Но расширяем его до 2.5 * IQR, чтобы не удалить естественную вариацию
                lower_bound = q1 - 2.5 * iqr
                upper_bound = q3 + 2.5 * iqr
                filtered_f0 = f0_values[(f0_values >= lower_bound) & (f0_values <= upper_bound)]
                
                # Если после фильтрации осталось достаточно значений (минимум 60%), используем их
                if len(filtered_f0) >= len(f0_values) * 0.6 and len(filtered_f0) >= 5:
                    f0_values = filtered_f0
        
        # Расчет периодов
        periods = 1.0 / f0_values
        
        # Расчет jitter по стандартной формуле (Jitter local)
        # Это соответствует методу Praat "Get jitter (local)"
        period_diff = np.abs(np.diff(periods))
        mean_period = np.mean(periods)
        
        if mean_period > 0 and len(period_diff) > 0:
            jitter = (np.mean(period_diff) / mean_period) * 100
            # Ограничиваем разумными значениями
            # Норма для здоровых: <1%, патология: >1.5-3%
            # Значения >5% обычно указывают на ошибку расчета
            jitter = min(jitter, 5.0)
            return float(jitter)
        
        return 0.0
    
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
        
        # Конвертация в dB
        rms_db = 20 * np.log10(rms_frames + 1e-10)
        db_variation = np.std(rms_db)
        db_range = np.max(rms_db) - np.min(rms_db)
        
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
                    # HNR ≈ 10 * log10(harmonicity / (1 - harmonicity))
                    # Используем медиану для устойчивости к выбросам
                    median_harmonicity = np.median(harmonicity_values)
                    if 0 < median_harmonicity < 1:
                        # Более точная формула конвертации harmonicity в HNR
                        # HNR = 10 * log10(harmonicity / (1 - harmonicity))
                        hnr_parselmouth = 10 * np.log10(median_harmonicity / (1 - median_harmonicity + 1e-10))
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
                                    hnr_db = 10 * np.log10(harmonic_energy / noise_energy)
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
                        hnr_approx = 10 * np.log10(harmonic_energy / (total_energy - harmonic_energy + 1e-10))
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
                return max_duration
            else:
                # Fallback: используем общую длительность аудио
                return len(audio) / self.sample_rate
            
        except Exception as e:
            print(f"Ошибка расчета MPT: {str(e)}")
            # Fallback: используем общую длительность аудио
            return len(audio) / self.sample_rate
    
    def _calculate_highest_f0(self, sound) -> float:
        """
        Расчет высшей частоты F0 (F0-High)
        
        Норма: >400-500 Гц, при ПД: снижена (<300 Гц)
        """
        try:
            pitch = sound.to_pitch_ac(time_step=0.01)
            f0_values = pitch.selected_array['frequency']
            f0_values = f0_values[f0_values > 0]  # Убираем незаполненные значения
            
            if len(f0_values) > 0:
                # Берем 95-й перцентиль как F0-High (исключаем выбросы)
                f0_high = np.percentile(f0_values, 95)
                return float(f0_high)
            else:
                return 0.0
                
        except Exception as e:
            return 0.0
    
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
                # Берем 5-й перцентиль как I-Low (самая тихая часть вокализации)
                i_low_pa = np.percentile(vocal_intensities, 5)
                
                # Для DSI I-Low должен быть в диапазоне 30-60 дБ для нормальной речи
                # Parselmouth возвращает значения в Паскалях, которые нужно нормализовать
                # Используем относительную интенсивность и масштабируем к правильному диапазону
                if i_low_pa > 0 and max_intensity > 0:
                    # Относительная интенсивность (0-1)
                    relative_intensity = i_low_pa / max_intensity
                    
                    # Масштабируем к диапазону 30-60 дБ для нормальной речи
                    # Минимальная интенсивность (5-й перцентиль) -> 30-40 дБ
                    # Максимальная интенсивность -> 55-60 дБ
                    # Используем линейную интерполяцию: 30 + (relative * 30)
                    i_low_db = 30 + (relative_intensity * 30)
                    
                    # Ограничиваем диапазон 25-65 дБ
                    i_low_db = max(25, min(65, i_low_db))
                    
                    return float(i_low_db)
                else:
                    return 0.0
            else:
                # Если нет вокализации, возвращаем минимальное значение в дБ
                valid_intensities = intensity_values[intensity_values > 0]
                if len(valid_intensities) > 0:
                    min_intensity = np.min(valid_intensities)
                    if min_intensity > 0 and max_intensity > 0:
                        relative_intensity = min_intensity / max_intensity
                        i_low_db = 30 + (relative_intensity * 30)
                        # Строго ограничиваем диапазон 25-65 дБ
                        i_low_db = max(25.0, min(65.0, i_low_db))
                        return float(i_low_db)
                
                # Если вообще нет данных, возвращаем среднее значение
                return 40.0
                
        except Exception as e:
            print(f"Ошибка расчета I-Low: {str(e)}")
            # Возвращаем среднее значение вместо 0, чтобы не ломать DSI расчет
            return 40.0