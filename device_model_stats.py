"""
Статистика по моделям устройств из сохраненных результатов.

1. Проверка по тестовому звуку. Калибровочный свип играет динамик того же
   телефона, а записывает его микрофон, поэтому уровень записанного свипа =
   громкость динамика × чувствительность микрофона. Абсолютную чувствительность
   из него не получить, но на максимальной громкости у одной модели это число
   почти постоянно. Заметное отличие от обычного для модели значит: громкость
   убавлена, микрофон закрыт пальцем или телефон сам меняет усиление.

2. Поправка громкости голоса по модели. Обычная громкость спокойной «а»
   у разных людей в среднем около 70 дБ SPL на 30 см. Если на одной модели
   записались многие люди, медиана их «а» в dBFS соответствует этим 70 дБ,
   и для модели получается поправка dBFS -> дБ SPL без шумомера. Она точнее
   оценки по голосу самого человека: громкий и тихий от природы человек
   больше не сдвигают результат.
"""
from typing import Dict, List, Optional

import numpy as np

from dsi_protocol import COMFORTABLE_SPL_DB

MIN_LOOP_RECORDINGS = 3     # записей модели с калибровкой, чтобы знать обычный уровень свипа
LOOP_GAIN_TOLERANCE_DB = 6  # отличие больше - предупреждаем
MIN_MODEL_DEVICES = 5       # разных устройств модели, чтобы доверять поправке
MODEL_OFFSET_SD_DB = 3.0    # разброс поправки внутри модели и между людьми (оценка)


def model_key(device_info: Optional[Dict]) -> Optional[str]:
    """Модель устройства и режим микрофона: у одной модели разные режимы звучат по-разному"""
    if not device_info or not device_info.get('device_model'):
        return None
    return f"{str(device_info['device_model']).strip().lower()}|{device_info.get('mic_mode') or 'default'}"


def _same_model(history: List[Dict], key: str, exclude_device: Optional[str] = None) -> List[Dict]:
    return [r for r in history
            if 'error' not in r
            and model_key(r.get('device_info')) == key
            and (exclude_device is None or r.get('device_info', {}).get('device_id') != exclude_device)]


def loop_gain_check(calibration: Optional[Dict], history: List[Dict],
                    device_info: Optional[Dict]) -> Optional[Dict]:
    """Сравнение уровня записанного тестового звука с обычным для этой модели"""
    level = (calibration or {}).get('sweep_level_dbfs')
    key = model_key(device_info)
    if level is None or key is None:
        return None
    levels = [r['calibration']['sweep_level_dbfs'] for r in _same_model(history, key)
              if isinstance(r.get('calibration'), dict)
              and r['calibration'].get('sweep_level_dbfs') is not None]
    if len(levels) < MIN_LOOP_RECORDINGS:
        return {'status': 'learning', 'n_recordings': len(levels), 'level_dbfs': level}
    typical = float(np.median(levels))
    delta = float(level) - typical
    check = {'status': 'ok', 'n_recordings': len(levels), 'level_dbfs': level,
             'typical_dbfs': round(typical, 1), 'delta_db': round(delta, 1)}
    if abs(delta) > LOOP_GAIN_TOLERANCE_DB:
        check['status'] = 'deviation'
        where = 'тише' if delta < 0 else 'громче'
        check['message'] = (f"Тестовый звук записался на {abs(delta):.0f} дБ {where}, чем обычно "
                            f"у этой модели. Проверьте, что громкость на максимуме, микрофон "
                            f"не закрыт пальцем или чехлом, и повторите запись.")
    return check


def model_spl_offset(history: List[Dict], device_info: Optional[Dict]) -> Optional[Dict]:
    """
    Поправка dBFS -> дБ SPL для модели по обычной громкости многих людей.
    Каждое устройство дает одно значение (медиану своих записей), текущее
    устройство не учитывается, чтобы человек не мерил сам себя.
    """
    key = model_key(device_info)
    if key is None:
        return None
    per_device: Dict[str, List[float]] = {}
    for r in _same_model(history, key, exclude_device=(device_info or {}).get('device_id')):
        if r.get('quality', {}).get('verdict') == 'reject':
            continue
        level = (r.get('dsi') or {}).get('comfortable_dbfs')
        device = (r.get('device_info') or {}).get('device_id')
        if level is not None and device:
            per_device.setdefault(device, []).append(float(level))
    if len(per_device) < MIN_MODEL_DEVICES:
        return None
    typical = float(np.median([np.median(v) for v in per_device.values()]))
    return {'offset_db': round(COMFORTABLE_SPL_DB - typical, 1),
            'n_devices': len(per_device),
            'sd_db': MODEL_OFFSET_SD_DB}
