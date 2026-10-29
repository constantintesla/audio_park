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


# Уровень сигнала с RMS = 1.0 в шкале Praat (дБ re 2e-5, отсчеты = Па):
# 20 * log10(1 / 2e-5) ~ 93.98 дБ. Нужен для перевода дБ Praat в dBFS (RMS).
PRAAT_DB_AT_FULL_SCALE = 20.0 * math.log10(1.0 / 2e-5)

# Типичная чувствительность цифрового MEMS-микрофона телефона:
# 94 дБ SPL дают -26 dBFS, т.е. дБ SPL = dBFS + 120. Используется, пока
# для устройства нет калибровки.
DEFAULT_SPL_OFFSET_DB = 120.0


def frame_db(audio: np.ndarray, sr: int) -> np.ndarray:
    """Уровень кадров 25 мс с шагом 10 мс, дБ"""
    frame = int(0.025 * sr)
    if len(audio) < frame:
        return np.array([])
    frames = np.lib.stride_tricks.sliding_window_view(audio, frame)[::int(0.01 * sr)]
    return 10 * np.log10(np.mean(frames ** 2, axis=1) + 1e-12)


def silence_threshold_db(db: np.ndarray, floor_db: Optional[float] = None) -> float:
    """
    Граница тишины: на 25 дБ тише громкой части, но не ниже шума + 6 дБ.
    floor_db - уровень шума; если не задан, берется по самым тихим кадрам (5%),
    в куске с паузами или с запасом тишины по краям это шум комнаты.
    """
    if floor_db is None:
        floor_db = float(np.percentile(db, 5))
    return max(float(np.percentile(db, 99)) - 25.0, floor_db + 6.0)


def syllable_nuclei(audio: np.ndarray, sr: int):
    """
    Ядра слогов и паузы (по мотивам de Jong & Wempe 2009).

    Ядро слога - пик громкости в полосе гласных 300-2500 Гц с провалом вокруг
    хотя бы 3 дБ, в котором есть тон. Без полосы и проверки тона шипящие и
    свистящие (ш, с) сами давали пик, и «ша-ша-ша» считалось за 6 слогов.
    Пауза - тишина от 0.25 с между началом и концом речи; тишина - на 25 дБ
    тише громкой части записи, поэтому не зависит от усиления микрофона.
    В шумной записи (шум ближе 25 дБ к голосу) порог поднимается над шумом
    (silence_threshold_db), иначе шум между фразами считался речью и паузы пропадали.

    Returns:
        (времена ядер в секундах, длительность звучания без пауз в секундах, доля пауз)
    """
    from scipy.signal import butter, find_peaks, sosfiltfilt

    hop = int(0.01 * sr)
    frame = int(0.025 * sr)
    if len(audio) < frame + 10 * hop:
        return np.array([]), 0.0, 0.0
    kernel = np.hanning(7)
    kernel /= kernel.sum()

    def smooth_db(x):
        return np.convolve(frame_db(x, sr), kernel, mode='same')  # сглаживание ~50 мс

    db = smooth_db(audio)
    speech = db > silence_threshold_db(db)
    idx = np.flatnonzero(speech)
    if len(idx) == 0:
        return np.array([]), 0.0, 0.0
    first, last = idx[0], idx[-1] + 1
    pause = run = 0
    for is_speech in speech[first:last]:
        if is_speech:
            if run >= 25:
                pause += run
            run = 0
        else:
            run += 1
    span = last - first

    band_db = smooth_db(sosfiltfilt(butter(4, [300 / (sr / 2), 2500 / (sr / 2)], 'band', output='sos'), audio))
    # Не чаще чем раз в 80 мс (больше 12 слогов в секунду не бывает)
    peaks, _ = find_peaks(band_db, height=silence_threshold_db(band_db), prominence=3.0, distance=8)
    times = peaks * 0.01 + frame / (2 * sr)  # центр кадра
    if HAS_PARSELMOUTH and len(times):
        sound = parselmouth.Sound(audio / (np.max(np.abs(audio)) + 1e-10), sampling_frequency=sr)
        pitch = sound.to_pitch_ac(time_step=0.01, pitch_floor=60.0, pitch_ceiling=600.0)
        f0 = np.array([pitch.get_value_at_time(t) for t in times])
        times = times[np.isfinite(f0) & (f0 > 0)]
    return times, (span - pause) * 0.01, pause / span if span else 0.0


class FeatureExtractor:
    """Класс для извлечения акустических признаков"""
    
    def __init__(self, sample_rate: int = 16000,
                 spl_offset_db: Optional[float] = None):
        """
        Args:
            sample_rate: Частота дискретизации
            spl_offset_db: Калибровка устройства: дБ SPL = dBFS + spl_offset_db.
                Если не задана, берется типичное значение DEFAULT_SPL_OFFSET_DB.
        """
        self.sample_rate = sample_rate
        self.spl_calibrated = spl_offset_db is not None
        self.spl_offset_db = float(spl_offset_db) if self.spl_calibrated else DEFAULT_SPL_OFFSET_DB
    
    def extract_all_features(self, audio: np.ndarray,
                             level_audio: Optional[np.ndarray] = None,
                             spl_offset_db: Optional[float] = None) -> Dict[str, float]:
        """
        Извлечение всех акустических признаков
        
        Args:
            audio: Аудиомассив
            level_audio: Исходный сигнал до нормализации громкости. По нему
                считается I-Low, иначе абсолютный уровень теряется.
            spl_offset_db: Калибровка устройства для этой записи
                (перекрывает значение из конструктора)
        
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
                features.update(self._extract_dsi_parameters(sound, audio, level_audio, spl_offset_db))
                
            except Exception as e:
                print(f"Предупреждение при извлечении признаков через parselmouth: {str(e)}")
                # Fallback на librosa
                features.update(self._extract_features_librosa(audio, level_audio, spl_offset_db))
        else:
            # Используем только librosa
            features.update(self._extract_features_librosa(audio, level_audio, spl_offset_db))
        
        return features
    
    def _extract_pitch_features(self, sound, 
                                audio: np.ndarray) -> Dict[str, float]:
        """Извлечение признаков высоты тона (pitch)"""
        features = {}
        
        try:
            # Извлечение F0 (fundamental frequency)
            pitch = sound.to_pitch_ac(time_step=0.01)
            f0_track = pitch.selected_array['frequency']
            features.update(self._voicing_features(f0_track, 0.01))
            f0_values = f0_track[f0_track > 0]  # Убираем незаполненные значения
            
            if len(f0_values) > 0:
                f0_mean = np.mean(f0_values)
                f0_std = np.std(f0_values)
                features['f0_mean_hz'] = float(f0_mean)
                features['f0_sd_hz'] = float(f0_std)
                
                # Jitter и shimmer - стандартные измерения Praat по меткам периодов
                # (PointProcess): от них отсчитаны нормы (MDVP, Little 2004) и коэффициенты DSI.
                # Аргументы: время от/до (0, 0 = вся запись), самый короткий и самый длинный
                # период, во сколько раз соседние периоды (и амплитуды для shimmer) могут
                # различаться, чтобы пара еще учитывалась.
                try:
                    point_process = call(sound, "To PointProcess (periodic, cc)", 50, 500)
                    jitter_args = (0, 0, 0.0001, 0.02, 1.3)
                    shimmer_args = jitter_args + (1.6,)
                    perturbation = {
                        'jitter_percent': call(point_process, "Get jitter (local)", *jitter_args),
                        'jitter_rap': call(point_process, "Get jitter (rap)", *jitter_args),
                        'jitter_ppq5': call(point_process, "Get jitter (ppq5)", *jitter_args),
                        'shimmer_percent': call([sound, point_process], "Get shimmer (local)", *shimmer_args),
                        'shimmer_apq': call([sound, point_process], "Get shimmer (apq3)", *shimmer_args),
                    }
                    for key, value in perturbation.items():
                        if value is not None and np.isfinite(value) and value > 0:
                            features[key] = float(value * 100)  # Praat возвращает долю
                except Exception as e:
                    print(f"Предупреждение: не удалось рассчитать jitter/shimmer через Praat: {str(e)}")
                # Если Praat не нашел пар периодов, jitter оцениваем по треку F0
                if 'jitter_percent' not in features:
                    features['jitter_percent'] = self._calculate_jitter(f0_values)
                features.setdefault('shimmer_percent', 0.0)
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
    
    @staticmethod
    def _voicing_features(f0_track: np.ndarray, time_step: float) -> Dict[str, float]:
        """
        Признаки озвончения по треку F0 (0 = кадр без тона):
        - f0_sd_semitones: разброс высоты тона в полутонах. В отличие от SD в Гц
          не зависит от пола и среднего тона (у мужчин SD в Гц вдвое меньше);
        - voiced_sec: суммарная длительность озвонченных кадров;
        - longest_voiced_sec: самый длинный непрерывный озвонченный участок.
          В обычной речи тон прерывается на глухих согласных и паузах, поэтому
          длинный непрерывный участок, занимающий большую часть записи, означает
          протяжную гласную.
        """
        f0_track = np.nan_to_num(np.asarray(f0_track, dtype=float))
        voiced = f0_track > 0
        features = {'f0_sd_semitones': 0.0, 'voiced_sec': 0.0, 'longest_voiced_sec': 0.0}
        if not np.any(voiced):
            return features
        
        f0_voiced = f0_track[voiced]
        semitones = 12.0 * np.log2(f0_voiced / np.median(f0_voiced))
        features['f0_sd_semitones'] = float(np.std(semitones))
        features['voiced_sec'] = float(np.sum(voiced) * time_step)
        
        # Короткие провалы трекера (до 50 мс) внутри гласной не считаем разрывом
        max_gap = int(round(0.05 / time_step))
        longest = current = gap = 0
        for is_voiced in voiced:
            if is_voiced:
                current += 1 + gap
                gap = 0
            elif current > 0 and gap < max_gap:
                gap += 1
            else:
                current = gap = 0
            longest = max(longest, current)
        features['longest_voiced_sec'] = float(longest * time_step)
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
                return 0.01  # Только для невалидных значений
            
            # Возвращаем настоящее значение без ограничений
            return float(jitter)
        
        return 0.01  # Только если расчет невозможен (нет данных)
    
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
        
        # Признаки гипофонии, не зависящие от усиления устройства
        features.update(self._extract_vocal_effort_features(audio, rms_frames,
                                                            frame_length, hop_length))
        
        return features
    
    def _extract_vocal_effort_features(self, audio: np.ndarray, rms_frames: np.ndarray,
                                       frame_length: int, hop_length: int) -> Dict[str, float]:
        """
        Признаки голосового усилия, инвариантные к громкости записи
        
        Абсолютный уровень (RMS) зависит от микрофона и расстояния до него, поэтому
        гипофонию оцениваем по относительным величинам, которые от усиления не зависят:
        - spectral_tilt_db: энергия 1-3.5 кГц относительно 0.3-1 кГц в речевых кадрах.
          Тихий голос с малым усилием беднее высокими гармониками, наклон круче
          (alpha ratio, Eyben et al. 2016, GeMAPS). Полосы выбраны так, чтобы
          уместиться и в телефонную полосу (до 4 кГц).
        - loudness_decay_db: на сколько дБ речь затихает от первой трети записи
          к последней (затухание громкости при ПД, Ho et al. 1999).
        """
        features = {'spectral_tilt_db': 0.0, 'loudness_decay_db': 0.0}
        
        if len(rms_frames) == 0 or np.max(rms_frames) <= 0:
            return features
        
        # Речевые кадры: в пределах 30 дБ от громких участков
        rms_db = 20 * np.log10(rms_frames + 1e-10)
        active = rms_db > np.percentile(rms_db, 95) - 30
        
        # Наклон спектра
        n_fft = 512
        spec = np.abs(librosa.stft(audio, n_fft=n_fft, win_length=frame_length,
                                   hop_length=hop_length)) ** 2
        freqs = librosa.fft_frequencies(sr=self.sample_rate, n_fft=n_fft)
        n = min(spec.shape[1], len(active))
        spec_active = spec[:, :n][:, active[:n]]
        if spec_active.shape[1] > 0:
            low = np.sum(spec_active[(freqs >= 300) & (freqs < 1000)])
            high = np.sum(spec_active[(freqs >= 1000) & (freqs < 3500)])
            if low > 0 and high > 0:
                features['spectral_tilt_db'] = float(10 * np.log10(high / low))
        
        # Затухание громкости: медиана уровня речи в первой и последней трети
        thirds = np.array_split(np.arange(len(rms_db)), 3)
        first = rms_db[thirds[0]][active[thirds[0]]]
        last = rms_db[thirds[2]][active[thirds[2]]]
        if len(first) > 0 and len(last) > 0:
            features['loudness_decay_db'] = float(np.median(first) - np.median(last))
        
        return features
    
    def _extract_articulation_features(self, audio: np.ndarray) -> Dict[str, float]:
        """
        Извлечение признаков артикуляции

        - rate_syl_sec: темп артикуляции, слогов в секунду без учета пауз
          (ядра слогов - syllable_nuclei);
        - pause_ratio: доля пауз (тишина длиннее 0.25 с) между началом и концом речи.
        """
        features = {'rate_syl_sec': 0.0, 'pause_ratio': 0.0}
        nuclei, phonation_sec, pause_ratio = syllable_nuclei(audio, self.sample_rate)
        if phonation_sec <= 0:
            return features
        features['pause_ratio'] = float(pause_ratio)
        features['rate_syl_sec'] = float(len(nuclei) / phonation_sec)
        
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
                
                # Praat HNR (cc): to_harmonicity_cc возвращает значения уже в дБ,
                # кадры без вокализации помечены -200 дБ и не учитываются в "Get mean"
                harmonicity = sound.to_harmonicity_cc(time_step=0.01)
                hnr_praat = call(harmonicity, "Get mean", 0, 0)
                if hnr_praat is not None and np.isfinite(hnr_praat):
                    hnr = float(hnr_praat)
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
                                        # Проверяем на inf и nan, но не ограничиваем диапазон
                                        if np.isfinite(hnr_db) and hnr_db > 0:
                                            hnr_values.append(hnr_db)
                
                if len(hnr_values) > 0:
                    # Используем медиану для устойчивости к выбросам
                    hnr_median = np.median(hnr_values)
                    # Возвращаем настоящее значение без ограничений
                    if np.isfinite(hnr_median) and hnr_median > 0:
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
                                # Проверяем на inf и nan, но не ограничиваем значения
                                if np.isfinite(hnr_approx) and hnr_approx > 0:
                                    return float(hnr_approx)
            except Exception as e:
                print(f"Ошибка в спектральном методе HNR: {str(e)}")
        
        except Exception as e:
            print(f"Общая ошибка расчета HNR: {str(e)}")
        
        # Fallback значение только если расчет полностью не удался
        # Возвращаем None, чтобы вызывающий код мог обработать это
        return 0.0
    
    def _extract_features_librosa(self, audio: np.ndarray,
                                  level_audio: Optional[np.ndarray] = None,
                                  spl_offset_db: Optional[float] = None) -> Dict[str, float]:
        """Fallback извлечение признаков через librosa"""
        features = {}
        
        # Базовые признаки через librosa
        f0 = librosa.pyin(audio, fmin=50, fmax=500, sr=self.sample_rate)
        f0_values = f0[0]
        features.update(self._voicing_features(np.nan_to_num(f0_values), 512 / self.sample_rate))
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
        features.update(self._extract_amplitude_features(audio))
        
        # Базовые параметры DSI через librosa (если parselmouth доступен)
        if HAS_PARSELMOUTH:
            try:
                audio_normalized = audio / (np.max(np.abs(audio)) + 1e-10)
                sound = parselmouth.Sound(audio_normalized, sampling_frequency=self.sample_rate)
                features.update(self._extract_dsi_parameters(sound, audio, level_audio, spl_offset_db))
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
                               audio: np.ndarray,
                               level_audio: Optional[np.ndarray] = None,
                               spl_offset_db: Optional[float] = None) -> Dict[str, float]:
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
            
            # 3. I-Low - низшая интенсивность в дБ (по исходному сигналу)
            features.update(self._calculate_lowest_intensity(
                level_audio if level_audio is not None else audio, spl_offset_db))
            
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
    
    def _calculate_lowest_intensity(self, audio: np.ndarray,
                                    spl_offset_db: Optional[float] = None) -> Dict[str, float]:
        """
        Расчет низшей интенсивности в дБ (I-Low)
        
        Норма: <45 дБ, при ПД: повышена (>55 дБ, тихий голос)
        Примечание: I-Low - это минимальная интенсивность во время вокализации
        
        Praat (to_intensity) возвращает интенсивность уже в дБ относительно 2e-5,
        считая значения отсчетов давлением в Па. Поэтому:
            уровень в dBFS = дБ_Praat - PRAAT_DB_AT_FULL_SCALE
            уровень в дБ SPL = dBFS + spl_offset_db (чувствительность устройства)
        
        Считается по исходному сигналу (без нормализации по пику и до любой
        нормализации громкости), иначе абсолютный уровень теряется.
        Без калибровки устройства значение в дБ SPL - оценка по типичной
        чувствительности микрофона, на это указывает i_low_calibrated = 0.
        """
        calibrated = spl_offset_db is not None or self.spl_calibrated
        offset_db = float(spl_offset_db) if spl_offset_db is not None else self.spl_offset_db
        result = {
            'i_low_dbfs': 0.0,
            'i_low_db': 0.0,
            'i_low_calibrated': 1.0 if calibrated else 0.0,
        }
        try:
            sound = parselmouth.Sound(audio, sampling_frequency=self.sample_rate)
            intensity = sound.to_intensity(minimum_pitch=75.0, time_step=0.01)
            intensity_db = intensity.values[0]
            times = intensity.xs()
            
            # Вокализация = кадры, где Praat нашел основной тон
            pitch = sound.to_pitch_ac(time_step=0.01, pitch_floor=50.0, pitch_ceiling=500.0)
            f0_at_frames = np.array([pitch.get_value_at_time(t) for t in times])
            voiced = np.isfinite(f0_at_frames) & (f0_at_frames > 0) & np.isfinite(intensity_db)
            vocal_db = intensity_db[voiced]
            
            # Если тон не найден, берем кадры не тише 30 дБ от максимума
            if len(vocal_db) < 5:
                finite_db = intensity_db[np.isfinite(intensity_db)]
                if len(finite_db) == 0:
                    return result
                vocal_db = finite_db[finite_db >= np.max(finite_db) - 30.0]
            
            # 5-й перцентиль - самая тихая часть вокализации
            i_low_praat_db = float(np.percentile(vocal_db, 5))
            if not np.isfinite(i_low_praat_db):
                return result
            
            i_low_dbfs = i_low_praat_db - PRAAT_DB_AT_FULL_SCALE
            result['i_low_dbfs'] = float(i_low_dbfs)
            result['i_low_db'] = float(i_low_dbfs + offset_db)
            return result
                
        except Exception as e:
            print(f"Ошибка расчета I-Low: {str(e)}")
            return result