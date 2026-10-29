"""
Признаки, устойчивые к различиям между записывающими устройствами.

CPPS (Smoothed Cepstral Peak Prominence) измеряет, насколько выражена
периодичность голоса. В отличие от HNR и jitter он не требует точного
выделения периодов, не зависит от усиления и мало зависит от АЧХ
микрофона, поэтому лучше сравним между устройствами.
"""
from typing import Optional

import numpy as np

# Praat строит кепстрограмму всей записи сразу (500 кадров в секунду),
# поэтому память растет с длиной: запись в 7 минут - около гигабайта.
# Считаем по кускам и усредняем с весом по длине куска.
CPPS_CHUNK_SEC = 10.0

try:
    import parselmouth
    from parselmouth.praat import call
    HAS_PARSELMOUTH = True
except ImportError:
    HAS_PARSELMOUTH = False


def compute_cpps(audio: np.ndarray, sr: int) -> float:
    """
    CPPS в dB, рассчитанный через Praat (PowerCepstrogram)

    Returns:
        CPPS в dB или 0.0, если посчитать не удалось
    """
    if not HAS_PARSELMOUTH or len(audio) < int(0.1 * sr):
        return 0.0
    try:
        audio_normalized = audio / (np.max(np.abs(audio)) + 1e-10)
        chunk = int(CPPS_CHUNK_SEC * sr)
        pieces = [audio_normalized[i:i + chunk] for i in range(0, len(audio_normalized), chunk)]
        if len(pieces) > 1 and len(pieces[-1]) < chunk // 2:
            # Короткий хвост присоединяем к предыдущему куску
            pieces[-2] = np.concatenate(pieces[-2:])
            pieces.pop()
        total, weight = 0.0, 0
        for piece in pieces:
            cpps = _cpps_praat(piece, sr)
            if cpps is not None:
                total += cpps * len(piece)
                weight += len(piece)
        return float(total / weight) if weight else 0.0
    except Exception as e:
        print(f"Предупреждение: не удалось рассчитать CPPS: {str(e)}")
        return 0.0


def _cpps_praat(audio: np.ndarray, sr: int) -> Optional[float]:
    """CPPS одного куска записи или None, если Praat не смог посчитать"""
    sound = parselmouth.Sound(audio, sampling_frequency=sr)
    cepstrogram = call(sound, 'To PowerCepstrogram', 60, 0.002, 5000, 50)
    cpps = call(cepstrogram, 'Get CPPS', 'no', 0.01, 0.001, 60, 330, 0.05,
                'Parabolic', 0.001, 0.05, 'Exponential decay', 'Robust slow')
    return float(cpps) if np.isfinite(cpps) else None
