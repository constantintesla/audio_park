"""
Данные для страницы «Графики».

Каждый график считается отдельно: страница запрашивает его, только когда
до него докрутили, и не ждет, пока посчитаются все сразу.
"""
import logging
from typing import Dict, Optional

import numpy as np

from app.audio_processor import AudioProcessor, thin_spectrogram, SPECTROGRAM_TIMES

logger = logging.getLogger(__name__)

# Ключи ответа /api/visualization (part=...)
PARTS = ('waveform', 'f0_data', 'intensity', 'spectrogram')
# Спектрограмма до 5 кГц: выше голоса почти нет
SPECTROGRAM_MAX_HZ = 5000
# Нижняя граница шкалы, дБ относительно самого громкого места записи
SPECTROGRAM_FLOOR_DB = -80.0


def _sound(audio: np.ndarray, sr: int):
    import parselmouth
    return parselmouth.Sound(audio / (np.max(np.abs(audio)) + 1e-10), sampling_frequency=sr)


def waveform(audio: np.ndarray, sr: int, processor: AudioProcessor) -> Dict:
    return processor.get_waveform(processor.normalize_loudness(audio, sr)[0])


def f0_track(audio: np.ndarray, sr: int, processor: AudioProcessor) -> Optional[Dict]:
    try:
        pitch = _sound(audio, sr).to_pitch_ac(time_step=0.01)
        times, values = pitch.xs(), pitch.selected_array['frequency']
    except Exception as e:
        logger.warning(f"Ошибка извлечения F0 через parselmouth: {e}")
        import librosa
        values = librosa.pyin(audio, fmin=50, fmax=500, sr=sr)[0]
        times = librosa.frames_to_time(np.arange(len(values)), sr=sr)
    valid = np.isfinite(values) & (values > 0) & np.isfinite(times)
    if not np.any(valid):
        return None
    return {'time': np.round(times[valid], 3), 'values': np.round(values[valid], 1),
            'mean': float(np.mean(values[valid]))}


def intensity(audio: np.ndarray, sr: int, processor: AudioProcessor) -> Optional[Dict]:
    try:
        track = _sound(audio, sr).to_intensity(time_step=0.01)
        times, values = track.xs(), track.values[0]
    except Exception as e:
        logger.warning(f"Ошибка извлечения интенсивности: {e}")
        import librosa
        hop_length = int(0.010 * sr)
        rms = librosa.feature.rms(y=audio, frame_length=int(0.025 * sr), hop_length=hop_length)[0]
        times = librosa.frames_to_time(np.arange(len(rms)), sr=sr, hop_length=hop_length)
        values = 20 * np.log10(rms + 1e-10)
    valid = np.isfinite(values) & np.isfinite(times)
    if not np.any(valid):
        return None
    return {'time': np.round(times[valid], 3), 'values': np.round(values[valid], 2)}


def spectrogram(audio: np.ndarray, sr: int, processor: AudioProcessor) -> Dict:
    """
    Сетка уровней в дБ (0 - самое громкое место записи) для картинки:
    magnitude[i][j] - частота frequencies[i], время times[j]
    """
    freqs, times, spec = processor.get_spectrogram(audio, sr, max_frames=SPECTROGRAM_TIMES)
    keep = freqs <= SPECTROGRAM_MAX_HZ
    freqs, times, spec = thin_spectrogram(freqs[keep], times, spec[keep, :])
    spec = np.where(np.isfinite(spec), spec, SPECTROGRAM_FLOOR_DB)
    return {
        'frequencies': np.round(freqs, 1),
        'times': np.round(times, 3),
        'magnitude': np.round(np.maximum(spec, SPECTROGRAM_FLOOR_DB), 1),
        'db_min': SPECTROGRAM_FLOOR_DB,
        'db_max': 0.0,
    }


BUILDERS = {'waveform': waveform, 'f0_data': f0_track, 'intensity': intensity, 'spectrogram': spectrogram}


def chart_data(audio_path: str, parts) -> Dict:
    """Данные выбранных графиков по аудиофайлу; график, который не удалось посчитать, - {}"""
    processor = AudioProcessor(target_sr=16000)
    audio, sr = processor.load_audio(audio_path)
    data = {}
    for part in parts:
        try:
            data[part] = BUILDERS[part](audio, sr, processor) or {}
        except Exception as e:
            logger.error(f"Ошибка расчета графика {part}: {e}", exc_info=True)
            data[part] = {}
    return data


def saved_waveform(path: str) -> Dict:
    """Волна из сохраненного waveform_data.json (старые записи хранят в нем каждый отсчет)"""
    import json
    with open(path, 'r', encoding='utf-8') as f:
        saved = json.load(f)
    amplitude = np.nan_to_num(np.asarray(saved.get('amplitude', []), dtype=float))
    del saved
    return AudioProcessor(target_sr=16000).get_waveform(amplitude)
