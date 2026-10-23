"""
Признаки, устойчивые к различиям между записывающими устройствами.

CPPS (Smoothed Cepstral Peak Prominence) измеряет, насколько выражена
периодичность голоса. В отличие от HNR и jitter он не требует точного
выделения периодов, не зависит от усиления и мало зависит от АЧХ
микрофона, поэтому лучше сравним между устройствами.
"""
import numpy as np

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
        sound = parselmouth.Sound(audio_normalized, sampling_frequency=sr)
        cepstrogram = call(sound, 'To PowerCepstrogram', 60, 0.002, 5000, 50)
        cpps = call(cepstrogram, 'Get CPPS', 'no', 0.01, 0.001, 60, 330, 0.05,
                    'Parabolic', 0.001, 0.05, 'Exponential decay', 'Robust slow')
        return float(cpps) if np.isfinite(cpps) else 0.0
    except Exception as e:
        print(f"Предупреждение: не удалось рассчитать CPPS: {str(e)}")
        return 0.0
