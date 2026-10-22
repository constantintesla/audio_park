"""
Речевые задания (документ «Задания по голосу»)

Тест записывается одной дорожкой, задания идут друг за другом. Границы
заданий приложение передает в device_info['protocol_segments'] как список
{"task": ..., "start_sec": ..., "end_sec": ...}:
- mpt            - время максимальной фонации: «а» на обычной громкости так долго и ровно,
                   как получится (несколько попыток, берется лучшая);
- reading        - чтение текста «Буря на реке» с выражением;
- syl_sibilant   - слоги: свистящие и шипящие (са-ша-за-жа);
- syl_sonorant   - слоги: сонорные (ла-ра-ра-ла);
- syl_voicing    - слоги: звонкие и глухие (па-ба-па, та-да-та, ка-га-ка);
- syl_palatal    - слоги: твердые и мягкие (са-ся, ла-ля, та-тя);
- syl_airflow    - слоги для мышечного тонуса и воздушного потока (ша ша ша, са са са, са-сы-су-се);
- phrase_slow, phrase_fast, phrase_normal - фразы в заданном темпе, одна попытка на фразу;
- intonation     - вопрос с логическим ударением на выделенном слове и ответ.

Показатели здесь - измерения с ориентирами, а не диагноз.
"""
from typing import Dict, List, Optional

import numpy as np
import parselmouth

from dsi_protocol import parse_segments, segment_audio, _phonation_time, noise_floor_db
from feature_extractor import frame_db, silence_threshold_db, syllable_nuclei

SYLLABLE_TASKS = {
    'syl_sibilant': 'свистящие и шипящие (са-ша-за-жа)',
    'syl_sonorant': 'сонорные (ла-ра-ра-ла)',
    'syl_voicing': 'звонкие и глухие (па-ба-па, та-да-та, ка-га-ка)',
    'syl_palatal': 'твердые и мягкие (са-ся, ла-ля, та-тя)',
    'syl_airflow': 'тонус и воздушный поток (ша ша ша, са са са, са-сы-су-се)',
}
PHRASE_TASKS = {
    'phrase_slow': 'фразы медленно',
    'phrase_normal': 'фразы в обычном темпе',
    'phrase_fast': 'фразы быстро',
}
TASK_NAMES = {
    'mpt': 'долгая «а» (время максимальной фонации)',
    'reading': 'чтение текста',
    **{task: 'слоги: ' + name for task, name in SYLLABLE_TASKS.items()},
    **PHRASE_TASKS,
    'intonation': 'интонация (вопрос к выделенному слову)',
}
PROTOCOL_TASKS = tuple(TASK_NAMES)
MAX_SEGMENTS = 40

# Ориентиры для заметок в отчете (не диагностические пороги)
MPT_SHORT_SEC = 10.0          # у взрослых обычно 15-25 с и больше, меньше 10 с - заметно короче
TEMPO_RATIO_FLAT = 1.3        # «быстро» лишь на 30% быстрее «медленно» - темп почти не меняется
READING_F0_SD_FLAT_ST = 2.0   # разброс тона при чтении меньше 2 полутонов - речь монотонная
INTONATION_RANGE_FLAT_ST = 4.0


def parse_protocol_segments(raw) -> List[Dict]:
    """Проверка и нормализация границ заданий; некорректные отбрасываются"""
    return parse_segments(raw, tasks=PROTOCOL_TASKS, max_segments=MAX_SEGMENTS)


def _f0_semitones(piece: np.ndarray, sr: int) -> np.ndarray:
    """Тон озвонченных кадров в полутонах относительно медианы"""
    sound = parselmouth.Sound(piece / (np.max(np.abs(piece)) + 1e-10), sampling_frequency=sr)
    f0 = sound.to_pitch_ac(time_step=0.01, pitch_floor=60.0, pitch_ceiling=600.0).selected_array['frequency']
    f0 = f0[f0 > 0]
    if len(f0) < 10:
        return np.array([])
    return 12 * np.log2(f0 / np.median(f0))


def _f0_range_st(st: np.ndarray) -> Optional[float]:
    return round(float(np.percentile(st, 95) - np.percentile(st, 5)), 1) if len(st) else None


def _intensity_range_db(piece: np.ndarray, sr: int) -> Optional[float]:
    """Размах громкости звучащих кадров (5-95 перцентиль), дБ"""
    db = frame_db(piece, sr)
    if len(db) == 0:
        return None
    db = db[db > silence_threshold_db(db)]
    return round(float(np.percentile(db, 95) - np.percentile(db, 5)), 1) if len(db) >= 10 else None


def _syllable_series(piece: np.ndarray, sr: int) -> Dict:
    """
    Повторение слогов: темп (слогов в секунду) и ровность (разброс интервалов между слогами).
    Паузы длиннее 0.6 с (между сериями) в ровность не входят.
    """
    peaks, sounding, _ = syllable_nuclei(piece, sr)
    result = {'syllables': int(len(peaks)), 'rate_syl_sec': None, 'cv_percent': None,
              'duration_sec': round(len(piece) / sr, 1)}
    if len(peaks) >= 3:
        intervals = np.diff(peaks)
        within = intervals[intervals <= 0.6]
        if len(within) >= 2:
            result['rate_syl_sec'] = round(float(1.0 / np.mean(within)), 1)
            result['cv_percent'] = round(float(100 * np.std(within) / np.mean(within)), 1)
    return result


def _phrase_rate(piece: np.ndarray, sr: int) -> Optional[float]:
    """Темп артикуляции фразы, слогов в секунду без пауз"""
    peaks, sounding, _ = syllable_nuclei(piece, sr)
    return round(float(len(peaks) / sounding), 1) if sounding > 0.3 and len(peaks) >= 3 else None


def measure_speech_tasks(audio: np.ndarray, sr: int, segments: List[Dict]) -> Dict:
    """
    Показатели по заданиям документа «Задания по голосу».

    Args:
        audio: запись после нормализации громкости
        segments: границы заданий (parse_protocol_segments)
    """
    pieces = {task: segment_audio(audio, sr, segments, task) for task in PROTOCOL_TASKS}
    notes = []

    floor_db = noise_floor_db(audio, sr)
    mpt_attempts = [round(_phonation_time(p, sr, floor_db), 2) for p in pieces['mpt']]
    mpt = {'mpt_sec': max(mpt_attempts, default=None), 'attempts': mpt_attempts}
    if mpt['mpt_sec'] is not None and mpt['mpt_sec'] < MPT_SHORT_SEC:
        notes.append(f"Долгая «а» длится {mpt['mpt_sec']:.1f} с: заметно короче обычного "
                     f"(у взрослых обычно 15–25 с и больше). Это может говорить о нехватке "
                     f"дыхания или неполном смыкании голосовых складок.")

    reading = None
    if pieces['reading']:
        piece = np.concatenate(pieces['reading'])
        peaks, sounding, pause_ratio = syllable_nuclei(piece, sr)
        st = _f0_semitones(piece, sr)
        reading = {
            'duration_sec': round(len(piece) / sr, 1),
            'rate_syl_sec': round(float(len(peaks) / sounding), 1) if sounding > 0 else None,
            'pause_ratio': round(float(pause_ratio), 3),
            'f0_sd_st': round(float(np.std(st)), 1) if len(st) else None,
            'f0_range_st': _f0_range_st(st),
        }
        if reading['f0_sd_st'] is not None and reading['f0_sd_st'] < READING_F0_SD_FLAT_ST:
            notes.append(f"При чтении тон почти не меняется (разброс {reading['f0_sd_st']:.1f} полутона, "
                         f"ориентир от {READING_F0_SD_FLAT_ST:.0f}): речь звучит монотонно.")

    syllables = []
    for task, name in SYLLABLE_TASKS.items():
        for piece in pieces[task]:
            syllables.append({'task': task, 'name': name, **_syllable_series(piece, sr)})

    phrases = {}
    for task in PHRASE_TASKS:
        rates = [_phrase_rate(p, sr) for p in pieces[task]]
        valid = [r for r in rates if r is not None]
        phrases[task.replace('phrase_', '')] = {
            'rate_syl_sec': round(float(np.median(valid)), 1) if valid else None,
            'attempts': rates,
        }
    slow, fast = phrases['slow']['rate_syl_sec'], phrases['fast']['rate_syl_sec']
    phrases['fast_to_slow_ratio'] = round(fast / slow, 2) if slow and fast else None
    if phrases['fast_to_slow_ratio'] is not None and phrases['fast_to_slow_ratio'] < TEMPO_RATIO_FLAT:
        notes.append(f"Быстрые фразы лишь в {phrases['fast_to_slow_ratio']:.2f} раза быстрее медленных: "
                     f"темп речи почти не меняется по заданию.")

    intonation = None
    if pieces['intonation']:
        items = [{'f0_range_st': _f0_range_st(_f0_semitones(p, sr)),
                  'intensity_range_db': _intensity_range_db(p, sr)} for p in pieces['intonation']]
        ranges = [i['f0_range_st'] for i in items if i['f0_range_st'] is not None]
        intonation = {'items': items,
                      'f0_range_st': round(float(np.median(ranges)), 1) if ranges else None}
        if intonation['f0_range_st'] is not None and intonation['f0_range_st'] < INTONATION_RANGE_FLAT_ST:
            notes.append(f"В вопросах с логическим ударением тон меняется мало "
                         f"({intonation['f0_range_st']:.1f} полутона): интонация бедная.")

    return {
        'mpt': mpt,
        'reading': reading,
        'syllables': syllables,
        'phrases': phrases,
        'intonation': intonation,
        'done': [TASK_NAMES[t] for t in PROTOCOL_TASKS if pieces[t]],
        'missing': [TASK_NAMES[t] for t in PROTOCOL_TASKS if not pieces[t]],
        'notes': notes,
    }


def protocol_report(protocol: Dict) -> List[str]:
    """Строки отчета по речевым заданиям"""
    lines = ["\n=== Речевые задания ==="]
    mpt = protocol['mpt']
    if mpt['mpt_sec'] is not None:
        lines.append(f"  - Долгая «а» (MPT): {mpt['mpt_sec']:.1f} с, лучшая из {len(mpt['attempts'])}")
    reading = protocol.get('reading')
    if reading:
        lines.append(f"  - Чтение текста: {reading['duration_sec']:.0f} с, темп {reading['rate_syl_sec'] or 0:.1f} слог/с, "
                     f"паузы {100 * reading['pause_ratio']:.0f}%, разброс тона {reading['f0_sd_st'] or 0:.1f} полутона")
    for item in protocol['syllables']:
        if item['rate_syl_sec'] is not None:
            lines.append(f"  - Слоги, {item['name']}: {item['rate_syl_sec']:.1f} слог/с, "
                         f"неровность {item['cv_percent']:.0f}%")
        else:
            lines.append(f"  - Слоги, {item['name']}: слоги не распознаны")
    phrases = protocol['phrases']
    rates = [f"{label} {phrases[key]['rate_syl_sec']:.1f}"
             for key, label in (('slow', 'медленно'), ('normal', 'обычно'), ('fast', 'быстро'))
             if phrases[key]['rate_syl_sec'] is not None]
    if rates:
        lines.append("  - Темп фраз, слог/с: " + ", ".join(rates))
    intonation = protocol.get('intonation')
    if intonation and intonation['f0_range_st'] is not None:
        lines.append(f"  - Интонация вопросов: размах тона {intonation['f0_range_st']:.1f} полутона")
    lines.extend(protocol['notes'])
    if protocol['missing']:
        lines.append("Не выполнены задания: " + ", ".join(protocol['missing']) + ".")
    return lines
