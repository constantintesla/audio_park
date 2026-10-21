"""
Параметры DSI по упражнениям (Wuyts et al. 2000)

Запись теста DSI из приложения или веб-страницы - одна дорожка, в которой
упражнения идут друг за другом. Границы упражнений передаются в
device_info['dsi_segments'] как список {"task": ..., "start_sec": ..., "end_sec": ...}:
- mpt   - самая долгая гласная «а» на одном выдохе (несколько попыток, берется лучшая);
- glide - скольжение голосом вверх до самой высокой ноты (лучшая попытка);
- soft  - самый тихий голос, «а» без шепота (самая тихая попытка);
- vowel - спокойная протяжная «а» 3-5 с: по ней считаются jitter и охриплость.
"""
from typing import Dict, List, Optional

import numpy as np
import parselmouth
from scipy.signal import medfilt

from feature_extractor import PRAAT_DB_AT_FULL_SCALE, frame_db

DSI_TASKS = ('mpt', 'glide', 'soft', 'vowel')
TASK_NAMES = {
    'mpt': 'самая долгая «а»',
    'glide': 'скольжение голосом вверх',
    'soft': 'самый тихий голос',
    'vowel': 'спокойная «а»',
}
MAX_SEGMENTS = 20

# Оценка I-Low без шумомера. Спокойная «а» обычной громкостью на 30 см у взрослых
# в среднем около 70 дБ SPL (разброс между людьми примерно ±5 дБ). Зная, сколько
# dBFS дала эта «а» на этом телефоне, переводим самый тихий голос в дБ SPL:
#   I-Low ≈ тихий_dBFS - обычный_dBFS + 70.
# Чувствительность микрофона при этом сокращается, остается только разница
# между тихим и обычным голосом самого человека.
COMFORTABLE_SPL_DB = 70.0
COMFORTABLE_SPL_SD_DB = 5.0


def parse_segments(raw, tasks=DSI_TASKS, max_segments: int = MAX_SEGMENTS) -> List[Dict]:
    """Проверка и нормализация границ упражнений; некорректные отбрасываются"""
    if not isinstance(raw, list):
        return []
    segments = []
    for item in raw[:max_segments]:
        if not isinstance(item, dict) or item.get('task') not in tasks:
            continue
        try:
            start = float(item.get('start_sec'))
            end = float(item.get('end_sec'))
        except (TypeError, ValueError):
            continue
        if not (np.isfinite(start) and np.isfinite(end)) or start < 0 or end - start < 0.2:
            continue
        segments.append({'task': item['task'], 'start_sec': round(start, 3), 'end_sec': round(end, 3)})
    return segments


def segment_audio(audio: np.ndarray, sr: int, segments: List[Dict], task: str) -> List[np.ndarray]:
    """Куски записи для одного упражнения"""
    pieces = []
    for seg in segments:
        if seg['task'] != task:
            continue
        piece = audio[int(seg['start_sec'] * sr):int(seg['end_sec'] * sr)]
        if len(piece) >= int(0.2 * sr):
            pieces.append(piece)
    return pieces


def _phonation_time(piece: np.ndarray, sr: int, floor_db: Optional[float] = None) -> float:
    """
    Самый длинный непрерывный звук в попытке, сек.
    Звук - кадры не тише 25 дБ от громкой части попытки и не ближе 6 дБ к шуму
    комнаты floor_db (noise_floor_db всей записи: сама попытка почти вся - голос,
    по ней шум не оценить); провалы до 0.2 с (вдох не успеть) не прерывают фонацию.
    """
    db = frame_db(piece, sr)
    if len(db) == 0:
        return 0.0
    threshold = np.percentile(db, 95) - 25.0
    if floor_db is not None:
        threshold = max(threshold, floor_db + 6.0)
    loud = db > threshold
    max_gap = 20
    longest = current = gap = 0
    for is_loud in loud:
        if is_loud:
            current += 1 + gap
            gap = 0
        elif current > 0 and gap < max_gap:
            gap += 1
        else:
            current = gap = 0
        longest = max(longest, current)
    return longest * 0.01


def noise_floor_db(audio: np.ndarray, sr: int) -> Optional[float]:
    """
    Шум комнаты по всей записи теста, дБ (10% самых тихих кадров).
    Между попытками человек читает задание и молчит, так что тишины в записи много.
    Если тихие кадры почти так же громки, как голос, тишины в записи нет - шум не оценить.
    """
    db = frame_db(audio, sr)
    if len(db) == 0:
        return None
    floor = float(np.percentile(db, 10))
    return floor if floor < np.percentile(db, 95) - 10.0 else None


def _highest_f0(piece: np.ndarray, sr: int) -> float:
    """Самая высокая устойчивая нота попытки, Гц (медианный фильтр убирает октавные скачки)"""
    sound = parselmouth.Sound(piece / (np.max(np.abs(piece)) + 1e-10), sampling_frequency=sr)
    pitch = sound.to_pitch_ac(time_step=0.01, pitch_floor=60.0, pitch_ceiling=1500.0)
    f0 = pitch.selected_array['frequency']
    voiced = f0[f0 > 0]
    if len(voiced) < 5:
        return 0.0
    return float(np.max(medfilt(voiced, 5)))


def _median_voiced_dbfs(piece: np.ndarray, sr: int) -> Optional[float]:
    """Типичный уровень голоса в попытке (медиана озвонченных кадров), dBFS"""
    sound = parselmouth.Sound(piece, sampling_frequency=sr)
    intensity = sound.to_intensity(minimum_pitch=75.0, time_step=0.01)
    pitch = sound.to_pitch_ac(time_step=0.01, pitch_floor=60.0, pitch_ceiling=600.0)
    times = intensity.xs()
    db = intensity.values[0]
    f0 = np.array([pitch.get_value_at_time(t) for t in times])
    voiced = np.isfinite(f0) & (f0 > 0) & np.isfinite(db)
    if np.sum(voiced) < 10:
        return None  # шепот или тишина: голоса нет
    return float(np.median(db[voiced]) - PRAAT_DB_AT_FULL_SCALE)


def measure_dsi_tasks(raw_audio: np.ndarray, audio: np.ndarray, sr: int,
                      segments: List[Dict], spl_offset_db: Optional[float],
                      model_offset: Optional[Dict] = None) -> Dict:
    """
    Параметры DSI по упражнениям.

    Args:
        raw_audio: исходная запись (для I-Low нужен абсолютный уровень)
        audio: запись после нормализации громкости (для MPT и F0-High)
        segments: границы упражнений
        spl_offset_db: калибровка микрофона, дБ SPL = dBFS + offset
        model_offset: поправка модели по обычной громкости многих людей
            (device_model_stats.model_spl_offset), если калибровки нет

    Returns:
        mpt_sec, f0_high_hz, i_low_dbfs, i_low_db, i_low_calibrated, а также
        missing (упражнения, которых нет в записи) и attempts (значения попыток)
    """
    floor_db = noise_floor_db(audio, sr)
    attempts = {
        'mpt': [round(_phonation_time(p, sr, floor_db), 2) for p in segment_audio(audio, sr, segments, 'mpt')],
        'glide': [round(_highest_f0(p, sr), 1) for p in segment_audio(audio, sr, segments, 'glide')],
        'soft_dbfs': [],
    }
    for piece in segment_audio(raw_audio, sr, segments, 'soft'):
        level = _median_voiced_dbfs(piece, sr)
        if level is not None:
            attempts['soft_dbfs'].append(round(level, 1))
    comfortable = [level for level in (_median_voiced_dbfs(p, sr)
                                       for p in segment_audio(raw_audio, sr, segments, 'vowel'))
                   if level is not None]

    calibrated = spl_offset_db is not None
    comfortable_dbfs = float(np.median(comfortable)) if comfortable else None
    if calibrated:
        i_low_method = 'calibrated'
        offset_db = float(spl_offset_db)
    elif model_offset:
        i_low_method = 'model_reference'
        offset_db = float(model_offset['offset_db'])
    elif comfortable_dbfs is not None:
        i_low_method = 'self_reference'
        offset_db = COMFORTABLE_SPL_DB - comfortable_dbfs
    else:
        i_low_method = None
        offset_db = None
    result = {
        'mpt_sec': max(attempts['mpt'], default=0.0),
        'f0_high_hz': max(attempts['glide'], default=0.0),
        'i_low_dbfs': min(attempts['soft_dbfs'], default=0.0),
        'i_low_calibrated': 1.0 if calibrated else 0.0,
        'i_low_method': i_low_method,
        'comfortable_dbfs': round(comfortable_dbfs, 1) if comfortable_dbfs is not None else None,
        'model_offset': model_offset if i_low_method == 'model_reference' else None,
        'attempts': attempts,
        'missing': [TASK_NAMES[t] for t in DSI_TASKS
                    if not segment_audio(audio, sr, segments, t)],
    }
    if not attempts['soft_dbfs'] and 'soft' in {s['task'] for s in segments}:
        result['missing'].append('самый тихий голос (в попытках не найден голос, только шепот или тишина)')
    result['i_low_db'] = (result['i_low_dbfs'] + offset_db
                          if offset_db is not None and attempts['soft_dbfs'] else 0.0)
    # Насколько самый тихий голос тише обычного: не зависит от микрофона,
    # удобно сравнивать записи одного человека между собой
    result['soft_below_comfortable_db'] = (
        round(result['i_low_dbfs'] - comfortable_dbfs, 1)
        if comfortable_dbfs is not None and attempts['soft_dbfs'] else None)
    return result
