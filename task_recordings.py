"""
Записи отдельных заданий обследования

Обследование пишется одной дорожкой, границы попыток приходят в
device_info['dsi_segments'] и device_info['protocol_segments'] (долгая «а» - в обоих).
Здесь из них собирается общий список попыток по порядку записи, к каждой попытке
подписывается ее измерение, и режутся куски записи для прослушивания и архива.
"""
import io
import json
import os
import zipfile
from typing import Dict, List, Optional

import numpy as np
import soundfile as sf

from dsi_protocol import TASK_NAMES as DSI_TASK_NAMES
from speech_protocol import TASK_NAMES as SPEECH_TASK_NAMES, SYLLABLE_TASKS

TASK_NAMES = {**SPEECH_TASK_NAMES, **DSI_TASK_NAMES, 'mpt': SPEECH_TASK_NAMES['mpt']}


def _num(value, digits: int = 1) -> Optional[str]:
    return None if value is None else f"{float(value):.{digits}f}"


def task_attempts(result: Dict) -> List[Dict]:
    """
    Попытки заданий по порядку записи: n (номер куска для /api/audio/<i>/task/<n>),
    task, name, attempt (номер попытки в задании), start_sec, end_sec, value (измерение)
    """
    info = result.get('device_info') or {}
    seen, segments = set(), []
    for seg in (info.get('dsi_segments') or []) + (info.get('protocol_segments') or []):
        key = (seg.get('task'), seg.get('start_sec'), seg.get('end_sec'))
        if key in seen or seg.get('task') not in TASK_NAMES:
            continue
        seen.add(key)
        segments.append(seg)
    segments.sort(key=lambda s: (s['start_sec'], s['end_sec']))

    attempts, counts = [], {}
    for n, seg in enumerate(segments):
        task = seg['task']
        counts[task] = counts.get(task, 0) + 1
        attempts.append({
            'n': n, 'task': task, 'name': TASK_NAMES[task], 'attempt': counts[task],
            'start_sec': seg['start_sec'], 'end_sec': seg['end_sec'],
            'duration_sec': round(seg['end_sec'] - seg['start_sec'], 1),
        })
    _attach_values(attempts, result, counts)
    return attempts


def _attach_values(attempts: List[Dict], result: Dict, counts: Dict[str, int]) -> None:
    """Измерение каждой попытки и отметка засчитанной (best) из сохраненного результата"""
    dsi = result.get('dsi') or {}
    dsi_attempts = dsi.get('attempts') or {}
    sp = result.get('speech_protocol') or {}
    features = result.get('features') or {}

    per_task: Dict[str, List[Optional[str]]] = {}
    best: Dict[str, Optional[int]] = {}

    mpt = (sp.get('mpt') or {}).get('attempts') or dsi_attempts.get('mpt') or []
    per_task['mpt'] = [f"звук {_num(v)} с" for v in mpt]
    best['mpt'] = int(np.argmax(mpt)) if mpt else None
    glide = dsi_attempts.get('glide') or []
    per_task['glide'] = [f"до {_num(v, 0)} Гц" for v in glide]
    best['glide'] = int(np.argmax(glide)) if glide else None
    # Попытки шепотом в soft_dbfs не попадают, тогда номера не совпадут и подписывать нельзя
    soft = dsi_attempts.get('soft_dbfs') or []
    if len(soft) == counts.get('soft'):
        per_task['soft'] = [f"{_num(v)} dBFS" for v in soft]
        best['soft'] = int(np.argmin(soft))
    if counts.get('vowel') and result.get('recording_task') in ('exam', 'dsi', 'vowel'):
        per_task['vowel'] = [f"jitter {_num(features.get('jitter_percent'), 2)}%, "
                             f"shimmer {_num(features.get('shimmer_percent'), 2)}%, "
                             f"HNR {_num(features.get('hnr_db'))} дБ"]
    reading = sp.get('reading')
    if reading:
        per_task['reading'] = [f"темп {_num(reading.get('rate_syl_sec'))} слог/с, "
                               f"паузы {_num(100 * (reading.get('pause_ratio') or 0), 0)}%, "
                               f"разброс тона {_num(reading.get('f0_sd_st'))} пт"]
    for task in SYLLABLE_TASKS:
        items = [s for s in sp.get('syllables') or [] if s.get('task') == task]
        per_task[task] = [(f"{_num(s['rate_syl_sec'])} слог/с, неровность {_num(s.get('cv_percent'), 0)}%"
                           if s.get('rate_syl_sec') is not None else 'слоги не распознаны') for s in items]
    for key, phrase in (sp.get('phrases') or {}).items():
        if isinstance(phrase, dict):
            per_task['phrase_' + key] = [f"{_num(r)} слог/с" if r is not None else 'темп не измерен'
                                         for r in phrase.get('attempts') or []]
    intonation = sp.get('intonation') or {}
    per_task['intonation'] = [f"размах тона {_num(i.get('f0_range_st'))} пт" if i.get('f0_range_st') is not None
                              else 'тон не измерен' for i in intonation.get('items') or []]

    for item in attempts:
        values = per_task.get(item['task']) or []
        k = item['attempt'] - 1
        item['value'] = values[k] if k < len(values) else None
        item['best'] = counts[item['task']] > 1 and best.get(item['task']) == k


def source_audio(result: Dict) -> Optional[str]:
    """Исходная запись: по ней заданы границы попыток"""
    files = (result.get('raw_data') or {}).get('files') or {}
    for key in ('original_audio', 'processed_audio'):
        path = files.get(key)
        if path and os.path.exists(path):
            return path
    return None


def _read_piece(path: str, start_sec: float, end_sec: float):
    try:
        with sf.SoundFile(path) as f:
            sr = f.samplerate
            f.seek(min(int(start_sec * sr), f.frames))
            return f.read(max(0, int((end_sec - start_sec) * sr)), dtype='float32'), sr
    except RuntimeError:
        # Форматы, которые не читает soundfile (mp3 и т.п.)
        import librosa
        audio, sr = librosa.load(path, sr=None, mono=False, offset=start_sec, duration=end_sec - start_sec)
        return audio.T, sr


def clip_wav(path: str, start_sec: float, end_sec: float) -> bytes:
    """Кусок записи в WAV (16 бит, без пересчета частоты)"""
    piece, sr = _read_piece(path, start_sec, end_sec)
    buf = io.BytesIO()
    sf.write(buf, piece, sr, format='WAV', subtype='PCM_16')
    return buf.getvalue()


def clip_filename(item: Dict) -> str:
    return f"{item['n'] + 1:02d}_{item['task']}_{item['attempt']}.wav"


def build_archive(result: Dict) -> bytes:
    """
    Архив прогона: вся запись, калибровка, записи заданий по отдельности
    и results.json с результатами анализа (плюс список попыток)
    """
    attempts = task_attempts(result)
    files = (result.get('raw_data') or {}).get('files') or {}
    source = source_audio(result)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        if source:
            zf.write(source, 'recording' + os.path.splitext(source)[1])
        calibration = files.get('calibration_audio')
        if calibration and os.path.exists(calibration):
            zf.write(calibration, 'calibration.wav')
        if source:
            for item in attempts:
                zf.writestr('tasks/' + clip_filename(item),
                            clip_wav(source, item['start_sec'], item['end_sec']))
        data = {k: v for k, v in result.items() if k not in ('raw_data', 'visuals', '_index')}
        data['task_recordings'] = [{**item, 'file': 'tasks/' + clip_filename(item)} for item in attempts]
        zf.writestr('results.json', json.dumps(data, ensure_ascii=False, indent=2))
    return buf.getvalue()
