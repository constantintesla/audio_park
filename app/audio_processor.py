"""
Модуль для обработки и предобработки аудиофайлов
"""
import librosa
import numpy as np
from typing import Tuple, List, Dict

# Сколько точек отдавать на графики: страница визуализации рисует
# не больше 5000 точек волны и сетку спектрограммы 300 x 80 (отдаем ее с запасом вдвое)
WAVEFORM_POINTS = 5000
SPECTROGRAM_TIMES = 600
SPECTROGRAM_FREQS = 160
# Картинка спектрограммы в отчете шириной 1000 пикселей
REPORT_SPECTROGRAM_FRAMES = 2000


def thin_spectrogram(freqs: np.ndarray, times: np.ndarray, spectrogram: np.ndarray,
                     max_freqs: int = SPECTROGRAM_FREQS,
                     max_times: int = SPECTROGRAM_TIMES) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Прореживание спектрограммы до сетки, которую реально рисует график"""
    f_step = max(1, int(np.ceil(len(freqs) / max_freqs)))
    t_step = max(1, int(np.ceil(len(times) / max_times)))
    return freqs[::f_step], times[::t_step], spectrogram[::f_step, ::t_step]


class AudioProcessor:
    """Класс для обработки аудиофайлов"""
    
    # Целевая громкость (EBU R128). Все записи приводятся к ней, чтобы
    # разные микрофоны и усиления давали сопоставимый уровень сигнала.
    TARGET_LUFS = -23.0
    
    def __init__(self, target_sr: int = 16000, target_lufs: float = TARGET_LUFS):
        self.target_sr = target_sr
        self.target_lufs = target_lufs
    
    def load_audio(self, file_path: str) -> Tuple[np.ndarray, int]:
        """
        Загрузка аудиофайла с ресемплированием до 16kHz моно
        
        Args:
            file_path: Путь к аудиофайлу (WAV/MP3)
        
        Returns:
            Tuple[аудиомассив, частота дискретизации]
        """
        try:
            # Загрузка аудио с автоматическим ресемплированием
            audio, sr = librosa.load(file_path, sr=self.target_sr, mono=True)
            return audio, sr
        except Exception as e:
            raise ValueError(f"Ошибка загрузки аудио: {str(e)}")
    
    @staticmethod
    def _k_weighting_filters(sr: int) -> List[Tuple[np.ndarray, np.ndarray]]:
        """Коэффициенты K-фильтра ITU-R BS.1770 (high-shelf + high-pass) для частоты sr"""
        # Стадия 1: high-shelf, моделирует акустику головы
        f0, gain_db, q = 1681.974450955533, 3.999843853973347, 0.7071752369554196
        a = 10 ** (gain_db / 40)
        w0 = 2 * np.pi * f0 / sr
        alpha = np.sin(w0) / (2 * q)
        cos_w0 = np.cos(w0)
        sqrt_a = np.sqrt(a)
        shelf_b = np.array([
            a * ((a + 1) + (a - 1) * cos_w0 + 2 * sqrt_a * alpha),
            -2 * a * ((a - 1) + (a + 1) * cos_w0),
            a * ((a + 1) + (a - 1) * cos_w0 - 2 * sqrt_a * alpha),
        ])
        shelf_a = np.array([
            (a + 1) - (a - 1) * cos_w0 + 2 * sqrt_a * alpha,
            2 * ((a - 1) - (a + 1) * cos_w0),
            (a + 1) - (a - 1) * cos_w0 - 2 * sqrt_a * alpha,
        ])
        # Стадия 2: high-pass (RLB), убирает инфранизкие частоты
        f0, q = 38.13547087602444, 0.5003270373238773
        w0 = 2 * np.pi * f0 / sr
        alpha = np.sin(w0) / (2 * q)
        cos_w0 = np.cos(w0)
        hp_b = np.array([(1 + cos_w0) / 2, -(1 + cos_w0), (1 + cos_w0) / 2])
        hp_a = np.array([1 + alpha, -2 * cos_w0, 1 - alpha])
        return [(shelf_b / shelf_a[0], shelf_a / shelf_a[0]),
                (hp_b / hp_a[0], hp_a / hp_a[0])]
    
    def measure_loudness(self, audio: np.ndarray, sr: int) -> float:
        """
        Интегральная громкость в LUFS по ITU-R BS.1770-4 (с гейтированием)
        
        Гейтирование отбрасывает паузы и тишину, поэтому результат отражает
        громкость самой речи, а не долю пауз в записи.
        
        Returns:
            Громкость в LUFS или -inf, если сигнал пустой/тихий
        """
        from scipy import signal
        
        if audio.size == 0:
            return float('-inf')
        
        weighted = audio.astype(np.float64)
        for b, a in self._k_weighting_filters(sr):
            weighted = signal.lfilter(b, a, weighted)
        
        block = int(0.400 * sr)
        hop = int(0.100 * sr)  # перекрытие 75%
        if len(weighted) < block:
            # Короткая запись: один блок на весь сигнал
            mean_squares = np.array([np.mean(weighted ** 2)])
        else:
            n_blocks = 1 + (len(weighted) - block) // hop
            mean_squares = np.array([
                np.mean(weighted[i * hop:i * hop + block] ** 2)
                for i in range(n_blocks)
            ])
        
        with np.errstate(divide='ignore'):
            block_loudness = -0.691 + 10 * np.log10(mean_squares)
        
        # Абсолютный гейт -70 LUFS
        gated = mean_squares[block_loudness > -70.0]
        if gated.size == 0:
            return float('-inf')
        
        # Относительный гейт: на 10 LU ниже громкости после абсолютного гейта
        relative_gate = -0.691 + 10 * np.log10(np.mean(gated)) - 10.0
        gated = mean_squares[(block_loudness > -70.0) & (block_loudness > relative_gate)]
        if gated.size == 0:
            return float('-inf')
        
        return float(-0.691 + 10 * np.log10(np.mean(gated)))
    
    def normalize_loudness(self, audio: np.ndarray, sr: int) -> Tuple[np.ndarray, Dict[str, float]]:
        """
        Приведение записи к целевой громкости (self.target_lufs)
        
        Убирает разницу в чувствительности микрофонов и усилении устройств:
        одна и та же речь, записанная разными устройствами, после нормализации
        имеет одинаковый уровень. Сигнал во float, поэтому пики выше 1.0
        не обрезаются и форма волны не искажается.
        
        Returns:
            Tuple[нормализованный аудиомассив, сведения о нормализации]
        """
        input_lufs = self.measure_loudness(audio, sr)
        info = {
            'input_lufs': input_lufs,
            'target_lufs': self.target_lufs,
            'gain_db': 0.0,
            'normalized': False,
        }
        
        # Тишину не усиливаем: это только поднимет шум
        if not np.isfinite(input_lufs):
            return audio, info
        
        gain_db = self.target_lufs - input_lufs
        normalized = audio * (10 ** (gain_db / 20))
        info['gain_db'] = float(gain_db)
        info['normalized'] = True
        return normalized.astype(audio.dtype, copy=False), info
    
    def get_waveform(self, audio: np.ndarray, max_points: int = WAVEFORM_POINTS) -> dict:
        """
        Получение данных волновой формы для графика

        Каждый отсчет не нужен: график рисует не больше нескольких тысяч точек,
        а полная запись в 7 минут - это 6,7 млн чисел (сотни МБ в JSON и в памяти).
        Поэтому берем пик (по модулю, со знаком) в каждом из max_points отрезков.

        Args:
            audio: Аудиомассив
            max_points: Сколько точек оставить

        Returns:
            Словарь с данными волновой формы
        """
        duration = len(audio) / self.target_sr
        step = max(1, int(np.ceil(len(audio) / max_points)))
        n = len(audio) // step * step
        if step > 1 and n:
            blocks = audio[:n].reshape(-1, step)
            amplitude = blocks[np.arange(len(blocks)), np.argmax(np.abs(blocks), axis=1)]
        else:
            amplitude = np.asarray(audio)
        time_axis = np.arange(len(amplitude)) * step / self.target_sr
        return {
            "amplitude": np.round(amplitude.astype(float), 5),
            "time": np.round(time_axis, 4),
            "duration": duration
        }

    def get_spectrogram(self, audio: np.ndarray, sr: int,
                        max_frames: int = None) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """
        Получение спектрограммы
        
        Args:
            audio: Аудиомассив
            sr: Частота дискретизации
            max_frames: Не больше стольких кадров по времени (для графиков):
                у длинной записи шаг кадров растет, и спектрограмма не занимает
                сотни МБ
        
        Returns:
            Tuple[частоты, времена, амплитуды]
        """
        hop_length = 512
        if max_frames:
            hop_length = max(hop_length, int(np.ceil(len(audio) / max_frames)))
        # Извлечение спектрограммы
        stft = librosa.stft(audio, hop_length=hop_length, win_length=2048)
        magnitude = np.abs(stft)
        del stft
        
        # Логарифмическая шкала для визуализации
        spectrogram = librosa.amplitude_to_db(magnitude, ref=np.max)
        
        # Временные и частотные оси
        times = librosa.frames_to_time(np.arange(magnitude.shape[1]), 
                                      sr=sr, hop_length=hop_length)
        frequencies = librosa.fft_frequencies(sr=sr, n_fft=2048)
        
        return frequencies, times, spectrogram
