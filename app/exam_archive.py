"""Portable exam metadata and strict segment validation, shared by export and API."""
import math
from copy import deepcopy

FORMAT = 'audio-park-exam'
SEGMENT_EOF_TOLERANCE = .002
INFO_FIELDS = ('task', 'sample_rate', 'input_format', 'mic_mode', 'noise_floor_dbfs',
               'dsi_segments', 'protocol_segments')


def portable_info(info):
    return {k: info[k] for k in INFO_FIELDS if k in info}


def validate_segments(info, duration):
    from app.task_recordings import TASK_NAMES
    from app.dsi_protocol import DSI_TASKS
    from app.speech_protocol import PROTOCOL_TASKS
    if not isinstance(info, dict) or not math.isfinite(duration) or not 0 < duration <= 1800:
        raise ValueError('Недопустимая длительность записи (максимум 30 минут)')
    present = set()
    for key in ('dsi_segments', 'protocol_segments'):
        segments = info.get(key, [])
        if not isinstance(segments, list) or len(segments) > (20 if key == 'dsi_segments' else 40):
            raise ValueError('Недопустимая разметка заданий')
        previous = 0
        for seg in segments:
            if not isinstance(seg, dict):
                raise ValueError('Недопустимая попытка задания')
            start, end = seg.get('start_sec'), seg.get('end_sec')
            if (seg.get('task') not in (DSI_TASKS if key == 'dsi_segments' else PROTOCOL_TASKS) or
                    any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in (start, end)) or
                    start < previous or end - start < .2 or end > duration + SEGMENT_EOF_TOLERANCE + 1e-9):
                raise ValueError('Границы заданий не согласованы с аудио')
            previous = end
            present.add(seg['task'])
    if info.get('task') == 'exam':
        missing = set(TASK_NAMES) - present
        if missing:
            raise ValueError('Для полного обследования отсутствует разметка: ' + ', '.join(sorted(missing)))


def clip_segment_ends(info, duration):
    """Bound analysis metadata, leaving transport manifests and retry identity intact."""
    info = deepcopy(info)
    if not isinstance(info, dict):
        return info
    for key in ('dsi_segments', 'protocol_segments'):
        segments = info.get(key, [])
        if not isinstance(segments, list):
            continue
        for segment in segments:
            end = segment.get('end_sec') if isinstance(segment, dict) else None
            if type(end) in (int, float) and duration < end <= duration + SEGMENT_EOF_TOLERANCE + 1e-9:
                segment['end_sec'] = duration
    return info
