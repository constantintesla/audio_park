"""
Сравнение записи с личной базой говорящего.

Абсолютные пороги (jitter > 1.5% и т.п.) плохо переносятся между
устройствами: разные микрофоны дают разные значения для одного и того же
голоса. Изменение относительно прошлых записей того же человека на том же
устройстве намного надежнее, поэтому здесь считается отклонение от
медианы его предыдущих пригодных записей.
"""
from typing import Dict, List, Optional

import numpy as np

# Признаки, которые отслеживаются относительно базы
BASELINE_FEATURES = [
    'jitter_percent',
    'shimmer_percent',
    'hnr_db',
    'cpps_db',
    'f0_mean_hz',
    'f0_sd_hz',
    'amplitude_db_variation',
    'rate_syl_sec',
    'pause_ratio',
]

MIN_BASELINE_RECORDINGS = 3   # меньше — база не считается
MAX_BASELINE_RECORDINGS = 10  # берем последние N записей
ROBUST_Z_FLAG = 2.5           # |z| выше — заметное отклонение от нормы человека
MIN_RELATIVE_SPREAD = 0.05    # нижняя граница разброса (5% медианы), чтобы не делить на ~0


def device_key(device_info: Optional[Dict]) -> str:
    """Ключ устройства: device_id из приложения, иначе модель, иначе 'unknown'"""
    if not device_info:
        return 'unknown'
    for key in ('device_id', 'device_model'):
        value = device_info.get(key)
        if value:
            return str(value)
    return 'unknown'


def _matches(result: Dict, user_id: str, device: str) -> bool:
    if str(result.get('user_info', {}).get('tg_user_id', '')) != user_id:
        return False
    if device_key(result.get('device_info')) != device:
        return False
    if 'error' in result:
        return False
    return result.get('quality', {}).get('verdict') != 'reject'


def _unreliable(result: Dict) -> List[str]:
    return result.get('quality', {}).get('unreliable_features', [])


def compare_to_baseline(features: Dict[str, float], history: List[Dict],
                        user_id, device_info: Optional[Dict],
                        unreliable_features: Optional[List[str]] = None) -> Dict:
    """
    Сравнение признаков с прошлыми записями того же человека на том же устройстве

    Args:
        features: Признаки текущей записи (блок 'features' результата)
        history: Ранее сохраненные результаты (results.json), старые первыми
        user_id: Идентификатор пользователя
        device_info: Метаданные устройства текущей записи
        unreliable_features: Признаки текущей записи, испорченные шумом или
            клиппингом: по ним отклонение считается, но не помечается

    Returns:
        Словарь со статусом базы и отклонениями по признакам
    """
    device = device_key(device_info)
    user = str(user_id)
    if not user or user in ('0', 'None'):
        return {'status': 'no_user', 'device_key': device, 'n_recordings': 0, 'deviations': {}}

    past = [r for r in history if _matches(r, user, device)]
    past = past[-MAX_BASELINE_RECORDINGS:]

    if len(past) < MIN_BASELINE_RECORDINGS:
        return {
            'status': 'collecting',
            'device_key': device,
            'n_recordings': len(past),
            'needed': MIN_BASELINE_RECORDINGS,
            'deviations': {},
        }

    deviations = {}
    for name in BASELINE_FEATURES:
        value = features.get(name)
        # Записи, где признак был испорчен шумом, в базу по нему не входят
        values = [r.get('features', {}).get(name) for r in past if name not in _unreliable(r)]
        values = np.array([v for v in values if isinstance(v, (int, float)) and np.isfinite(v)], dtype=float)
        if value is None or len(values) < MIN_BASELINE_RECORDINGS:
            continue
        median = float(np.median(values))
        # MAD * 1.4826 — оценка стандартного отклонения, устойчивая к выбросам
        spread = 1.4826 * float(np.median(np.abs(values - median)))
        spread = max(spread, MIN_RELATIVE_SPREAD * abs(median), 1e-6)
        z = (float(value) - median) / spread
        reliable = name not in (unreliable_features or [])
        deviations[name] = {
            'value': float(value),
            'baseline_median': round(median, 3),
            'delta': round(float(value) - median, 3),
            'robust_z': round(z, 2),
            'reliable': reliable,
            'flagged': bool(reliable and abs(z) > ROBUST_Z_FLAG),
        }

    return {
        'status': 'ready',
        'device_key': device,
        'n_recordings': len(past),
        'deviations': deviations,
        'flagged_features': [k for k, v in deviations.items() if v['flagged']],
    }
