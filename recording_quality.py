"""
Проверка качества записи перед анализом.

Разные устройства по-разному записывают голос: уровень шума, клиппинг,
полоса частот. Jitter, shimmer и HNR очень чувствительны к шуму и
клиппингу, поэтому прежде чем доверять этим цифрам, запись нужно проверить.

Пороги SNR взяты по Deliyski et al. 2005: при SNR ниже ~30 dB jitter,
shimmer и HNR становятся ненадежными, ниже ~20 dB — непригодными.
"""
from typing import Dict, List, Optional

import numpy as np

# Пороги проверки качества
MIN_DURATION_SEC = 1.0          # короче — анализировать нечего
RECOMMENDED_DURATION_SEC = 3.0  # короче — оценки F0/jitter нестабильны
SNR_REJECT_DB = 20.0
SNR_WARNING_DB = 30.0
CLIP_LEVEL = 0.98               # доля от полной шкалы, выше — считаем клиппингом
CLIP_REJECT_RATIO = 0.01        # >1% отсчетов в клиппинге — запись испорчена
CLIP_WARNING_RATIO = 0.001
MIN_SOURCE_SAMPLE_RATE = 16000  # ниже — узкополосная запись (телефонный кодек)

# Признаки, которые портятся от шума и клиппинга
NOISE_SENSITIVE_FEATURES = ['jitter_percent', 'shimmer_percent', 'hnr_db', 'cpps_db']


def _frame_levels_db(audio: np.ndarray, sr: int) -> np.ndarray:
    """Уровень кадров 25 мс с шагом 10 мс в dBFS"""
    frame = int(0.025 * sr)
    hop = int(0.010 * sr)
    if len(audio) < frame:
        return np.array([])
    n_frames = 1 + (len(audio) - frame) // hop
    idx = np.arange(frame)[None, :] + hop * np.arange(n_frames)[:, None]
    rms = np.sqrt(np.mean(audio[idx] ** 2, axis=1))
    return 20 * np.log10(rms + 1e-10)


def assess_recording_quality(audio: np.ndarray, sr: int,
                             source_sample_rate: Optional[int] = None,
                             noise_floor_dbfs: Optional[float] = None) -> Dict:
    """
    Оценка пригодности записи для акустического анализа

    Args:
        audio: Аудиомассив в диапазоне [-1, 1]
        sr: Частота дискретизации audio
        source_sample_rate: Исходная частота файла до ресемплирования
        noise_floor_dbfs: Уровень шума, измеренный приложением по
            калибровочной тишине. Если не передан, оценивается по самым
            тихим кадрам записи (занижает SNR, если в записи нет пауз).

    Returns:
        Словарь с метриками, вердиктом ('ok' / 'warning' / 'reject'),
        списком проблем и списком признаков, которым нельзя доверять.
    """
    issues: List[Dict[str, str]] = []

    def add_issue(severity: str, code: str, message: str):
        issues.append({'severity': severity, 'code': code, 'message': message})

    duration = len(audio) / sr if sr else 0.0
    peak = float(np.max(np.abs(audio))) if len(audio) else 0.0
    clip_ratio = float(np.mean(np.abs(audio) >= CLIP_LEVEL)) if len(audio) else 0.0

    levels = _frame_levels_db(audio, sr)
    if len(levels):
        speech_level = float(np.percentile(levels, 95))
        measured_noise = float(np.percentile(levels, 10))
    else:
        speech_level = measured_noise = -100.0
    noise_floor = float(noise_floor_dbfs) if noise_floor_dbfs is not None else measured_noise
    snr = speech_level - noise_floor

    if duration < MIN_DURATION_SEC:
        add_issue('reject', 'too_short',
                  f'Запись слишком короткая ({duration:.1f} с), нужно хотя бы {RECOMMENDED_DURATION_SEC:.0f} с.')
    elif duration < RECOMMENDED_DURATION_SEC:
        add_issue('warning', 'short',
                  f'Запись короткая ({duration:.1f} с), оценки могут быть неустойчивыми.')

    if peak < 1e-4:
        add_issue('reject', 'silent', 'В записи нет сигнала.')

    if clip_ratio > CLIP_REJECT_RATIO:
        add_issue('reject', 'clipping',
                  f'Сильный клиппинг ({clip_ratio * 100:.1f}% отсчетов): микрофон перегружен, '
                  f'отодвиньте телефон или говорите тише.')
    elif clip_ratio > CLIP_WARNING_RATIO:
        add_issue('warning', 'clipping',
                  f'Есть клиппинг ({clip_ratio * 100:.2f}% отсчетов).')

    if peak >= 1e-4:
        if snr < SNR_REJECT_DB:
            add_issue('reject', 'low_snr',
                      f'Слишком шумно (SNR {snr:.0f} dB, нужно не меньше {SNR_WARNING_DB:.0f} dB): '
                      f'перезапишите в тихом помещении.')
        elif snr < SNR_WARNING_DB:
            add_issue('warning', 'low_snr',
                      f'Повышенный шум (SNR {snr:.0f} dB): jitter, shimmer и HNR менее надежны.')

    if source_sample_rate and source_sample_rate < MIN_SOURCE_SAMPLE_RATE:
        add_issue('warning', 'narrowband',
                  f'Исходная частота дискретизации {source_sample_rate} Гц: '
                  f'запись узкополосная, HNR и спектральные признаки занижены.')

    severities = {i['severity'] for i in issues}
    if 'reject' in severities:
        verdict = 'reject'
    elif 'warning' in severities:
        verdict = 'warning'
    else:
        verdict = 'ok'

    noise_related = {'low_snr', 'clipping', 'narrowband'}
    unreliable = NOISE_SENSITIVE_FEATURES if any(i['code'] in noise_related for i in issues) else []

    return {
        'verdict': verdict,
        'issues': issues,
        'unreliable_features': list(unreliable),
        'metrics': {
            'duration_sec': round(duration, 2),
            'peak_dbfs': round(20 * np.log10(peak + 1e-10), 1),
            'clipping_ratio': round(clip_ratio, 5),
            'speech_level_dbfs': round(speech_level, 1),
            'noise_floor_dbfs': round(noise_floor, 1),
            'noise_floor_source': 'app_calibration' if noise_floor_dbfs is not None else 'estimated',
            'snr_db': round(snr, 1),
            'source_sample_rate': source_sample_rate,
        },
    }
