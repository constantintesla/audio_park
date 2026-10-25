"""
Модуль для обработки и предобработки аудиофайлов
"""
import librosa
import numpy as np
from typing import Tuple, List, Dict


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
    
    def get_waveform(self, audio: np.ndarray) -> dict:
        """
        Получение данных волновой формы
        
        Args:
            audio: Аудиомассив
        
        Returns:
            Словарь с данными волновой формы
        """
        duration = len(audio) / self.target_sr
        time_axis = np.linspace(0, duration, len(audio))
        return {
            "amplitude": audio,
            "time": time_axis,
            "duration": duration
        }
    
    def get_spectrogram(self, audio: np.ndarray, sr: int) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """
        Получение спектрограммы
        
        Args:
            audio: Аудиомассив
            sr: Частота дискретизации
        
        Returns:
            Tuple[частоты, времена, амплитуды]
        """
        # Извлечение спектрограммы
        stft = librosa.stft(audio, hop_length=512, win_length=2048)
        magnitude = np.abs(stft)
        
        # Логарифмическая шкала для визуализации
        spectrogram = librosa.amplitude_to_db(magnitude, ref=np.max)
        
        # Временные и частотные оси
        times = librosa.frames_to_time(np.arange(magnitude.shape[1]), 
                                      sr=sr, hop_length=512)
        frequencies = librosa.fft_frequencies(sr=sr, n_fft=2048)
        
        return frequencies, times, spectrogram