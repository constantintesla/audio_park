"""
Модуль для обработки и предобработки аудиофайлов
"""
import librosa
import numpy as np
from typing import Tuple, List, Optional


class AudioProcessor:
    """Класс для обработки аудиофайлов"""
    
    def __init__(self, target_sr: int = 16000):
        self.target_sr = target_sr
    
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
    
    def noise_reduction(self, audio: np.ndarray) -> np.ndarray:
        """
        Простая редукция шума
        
        Args:
            audio: Аудиомассив
        
        Returns:
            Очищенный аудиомассив
        """
        # Простая фильтрация высокочастотного шума
        from scipy import signal
        b, a = signal.butter(3, 0.05, 'high')
        filtered_audio = signal.filtfilt(b, a, audio)
        return filtered_audio
    
    def segment_utterances(self, audio: np.ndarray, sr: int, 
                          min_duration: float = 0.5,
                          silence_threshold: float = 0.02) -> List[np.ndarray]:
        """
        Сегментация на высказывания по паузам
        
        Args:
            audio: Аудиомассив
            sr: Частота дискретизации
            min_duration: Минимальная длительность сегмента (сек)
            silence_threshold: Порог тишины для обнаружения пауз
        
        Returns:
            Список сегментов аудио
        """
        # Обнаружение пауз
        frame_length = int(0.025 * sr)  # 25ms кадры
        hop_length = int(0.010 * sr)    # 10ms шаг
        
        rms = librosa.feature.rms(y=audio, frame_length=frame_length, 
                                 hop_length=hop_length)[0]
        
        # Пороговое значение для тишины
        silence_frames = rms < silence_threshold
        
        # Поиск границ сегментов
        segments = []
        start_idx = 0
        
        for i in range(1, len(silence_frames)):
            # Начало нового сегмента
            if silence_frames[i-1] and not silence_frames[i]:
                if start_idx > 0:
                    end_idx = (i - 1) * hop_length
                    segment = audio[start_idx:end_idx]
                    if len(segment) / sr >= min_duration:
                        segments.append(segment)
                start_idx = i * hop_length
            # Конец сегмента
            elif not silence_frames[i-1] and silence_frames[i]:
                pass  # Продолжаем
        
        # Добавляем последний сегмент
        if start_idx < len(audio):
            segment = audio[start_idx:]
            if len(segment) / sr >= min_duration:
                segments.append(segment)
        
        # Если не найдено сегментов, возвращаем весь файл
        if not segments:
            segments = [audio]
        
        return segments
    
    def get_waveform(self, audio: np.ndarray) -> dict:
        """
        Получение данных волновой формы
        
        Args:
            audio: Аудиомассив
        
        Returns:
            Словарь с данными волновой формы
        """
        return {
            "amplitude": audio.tolist(),
            "duration": len(audio) / self.target_sr
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