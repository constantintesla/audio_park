"""
Модуль для извлечения акустических признаков из аудио
Основан на исследованиях: Little et al. 2004, Daoudi 2022, NIH 2025
"""
import numpy as np
import librosa
import parselmouth
from parselmouth.praat import call
from typing import Dict, List, Optional
import warnings
warnings.filterwarnings('ignore')


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
        
        # Конвертация в формат parselmouth для анализа F0
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
            print(f"Предупреждение при извлечении признаков: {str(e)}")
            # Fallback на librosa
            features.update(self._extract_features_librosa(audio))
        
        return features
    
    def _extract_pitch_features(self, sound: parselmouth.Sound, 
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
                
                # Jitter (вариация периода)
                features['jitter_percent'] = self._calculate_jitter(f0_values)
                
                # Shimmer (вариация амплитуды)
                features['shimmer_percent'] = self._calculate_shimmer(sound, f0_values)
                
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
                    apq = self._calculate_apq(sound, f0_values)
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
        """Расчет jitter как процент вариации периода"""
        if len(f0_values) < 2:
            return 0.0
        
        periods = 1.0 / f0_values
        period_diff = np.abs(np.diff(periods))
        jitter = (np.mean(period_diff) / np.mean(periods)) * 100
        return float(jitter)
    
    def _calculate_shimmer(self, sound: parselmouth.Sound, 
                          f0_values: np.ndarray) -> float:
        """Расчет shimmer как процент вариации амплитуды"""
        if len(f0_values) < 2:
            return 0.0
        
        try:
            # Получение амплитуд для каждого периода
            amplitudes = []
            duration = sound.get_total_duration()
            time_step = duration / len(f0_values)
            
            for i, f0 in enumerate(f0_values):
                if f0 > 0:
                    time_point = i * time_step
                    if time_point < duration:
                        # Извлечение RMS амплитуды в окне вокруг точки
                        start_time = max(0, time_point - 0.01)
                        end_time = min(duration, time_point + 0.01)
                        segment = sound.extract_part(start_time, end_time, 
                                                    preserve_times=False)
                        rms = segment.values.std()  # Используем std как меру амплитуды
                        amplitudes.append(rms)
            
            if len(amplitudes) >= 2:
                amp_diff = np.abs(np.diff(amplitudes))
                shimmer = (np.mean(amp_diff) / (np.mean(amplitudes) + 1e-10)) * 100
                return float(shimmer)
        except:
            pass
        
        return 0.0
    
    def _calculate_apq(self, sound: parselmouth.Sound, 
                      f0_values: np.ndarray) -> float:
        """Расчет APQ (Amplitude Perturbation Quotient)"""
        # Упрощенная версия
        return self._calculate_shimmer(sound, f0_values)
    
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
        """Расчет HNR (Harmonics-to-Noise Ratio) в dB"""
        try:
            # Упрощенный метод через автокорреляцию
            frame_length = int(0.04 * self.sample_rate)  # 40ms кадры
            hop_length = int(0.02 * self.sample_rate)    # 20ms шаг
            
            # Разбиваем на кадры
            n_frames = (len(audio) - frame_length) // hop_length + 1
            hnr_values = []
            
            for i in range(n_frames):
                start = i * hop_length
                end = start + frame_length
                if end > len(audio):
                    break
                
                frame = audio[start:end]
                
                # Автокорреляция
                autocorr = np.correlate(frame, frame, mode='full')
                autocorr = autocorr[len(autocorr)//2:]
                
                # Нормализация
                autocorr = autocorr / (autocorr[0] + 1e-10)
                
                # Поиск первого пика (основной тон)
                # Ищем пик в диапазоне 50-500Hz
                min_lag = int(self.sample_rate / 500)
                max_lag = int(self.sample_rate / 50)
                
                if max_lag < len(autocorr):
                    search_region = autocorr[min_lag:max_lag]
                    if len(search_region) > 0:
                        peak_idx = np.argmax(search_region) + min_lag
                        peak_value = autocorr[peak_idx]
                        
                        # HNR как отношение гармонической энергии к шуму
                        harmonic_energy = peak_value
                        noise_energy = np.mean(autocorr) - peak_value
                        
                        if noise_energy > 0:
                            hnr_db = 10 * np.log10(harmonic_energy / noise_energy)
                            if hnr_db > 0:  # Валидация
                                hnr_values.append(hnr_db)
            
            if hnr_values:
                return np.mean(hnr_values)
        
        except:
            pass
        
        # Fallback значение
        return 10.0
    
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
        
        # Базовые параметры DSI через librosa
        try:
            audio_normalized = audio / (np.max(np.abs(audio)) + 1e-10)
            sound = parselmouth.Sound(audio_normalized, sampling_frequency=self.sample_rate)
            features.update(self._extract_dsi_parameters(sound, audio))
        except:
            # Fallback значения для DSI параметров
            features['mpt_sec'] = 0.0
            features['f0_high_hz'] = 0.0
            features['i_low_db'] = 0.0
        
        return features
    
    def _extract_dsi_parameters(self, sound: parselmouth.Sound, 
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
    
    def _calculate_max_phonation(self, sound: parselmouth.Sound, 
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
            
            # Порог для обнаружения вокализации (50% от максимума)
            threshold = np.max(intensity_values) * 0.5
            
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
            
            return max_duration if max_duration > 0 else len(audio) / self.sample_rate
            
        except Exception as e:
            # Fallback: используем общую длительность аудио
            return len(audio) / self.sample_rate
    
    def _calculate_highest_f0(self, sound: parselmouth.Sound) -> float:
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
    
    def _calculate_lowest_intensity(self, sound: parselmouth.Sound) -> float:
        """
        Расчет низшей интенсивности в дБ (I-Low)
        
        Норма: <45 дБ, при ПД: повышена (>55 дБ, тихий голос)
        Примечание: I-Low - это минимальная интенсивность во время вокализации
        """
        try:
            intensity = sound.to_intensity(time_step=0.01)
            intensity_values = intensity.values[0]
            
            # Фильтруем только вокализацию (исключаем тишину)
            # Порог для вокализации (30% от максимума)
            threshold = np.max(intensity_values) * 0.3
            vocal_intensities = intensity_values[intensity_values >= threshold]
            
            if len(vocal_intensities) > 0:
                # Берем 5-й перцентиль как I-Low (самая тихая часть вокализации)
                i_low = np.percentile(vocal_intensities, 5)
                return float(i_low)
            else:
                # Если нет вокализации, возвращаем минимальное значение
                return float(np.min(intensity_values))
                
        except Exception as e:
            return 0.0