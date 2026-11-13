"""Compare identical task samples with/without instruction gaps; no app changes."""
import sys
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.audio_processor import AudioProcessor
from app.dsi_protocol import measure_dsi_tasks, noise_floor_db, segment_audio
from app.speech_protocol import measure_speech_tasks
from app.recording_quality import assess_recording_quality

sr = 16000
rng = np.random.default_rng(42)
t = np.arange(10 * sr) / sr
carrier = np.sin(2 * np.pi * 180 * t) + .25 * np.sin(2 * np.pi * 360 * t)
noise = lambda n: rng.normal(0, .0001, n)
pad = noise(int(.3 * sr))
tasks = []
for task, amplitude in [('mpt', np.where(t < 6, .12, .012)),
                        ('vowel', .12), ('soft', .02), ('glide', .12), ('reading', .10)]:
    tasks.append((task, np.concatenate([pad, amplitude * carrier + noise(len(t)), pad]).astype('float32')))

def build(gaps):
    chunks, segments, n = [], [], 0
    for task, piece in tasks:
        if gaps:
            gap = noise(8 * sr).astype('float32')
            chunks.append(gap)
            n += len(gap)
        segments.append(dict(task=task, start_sec=n / sr, end_sec=(n + len(piece)) / sr))
        chunks.append(piece)
        n += len(piece)
    return np.concatenate(chunks), segments

processor = AudioProcessor()
results = {}
recordings = {}
for label, gaps in [('continuous', True), ('paused', False)]:
    raw, segments = build(gaps)
    recordings[label] = (raw, segments)
    normalized, loudness = processor.normalize_loudness(raw, sr)
    dsi = measure_dsi_tasks(raw, normalized, sr, segments, None)
    speech = measure_speech_tasks(normalized, sr, segments)
    results[label] = dict(duration_sec=len(raw) / sr, gain_db=loudness['gain_db'],
                          normalized_noise_floor_db=noise_floor_db(normalized, sr),
                          dsi={k: dsi[k] for k in ('mpt_sec', 'f0_high_hz', 'i_low_db', 'i_low_dbfs')},
                          speech_mpt=speech['mpt'], reading=speech['reading'],
                          quality=assess_recording_quality(raw, sr, noise_floor_dbfs=-80)['metrics'])

for task, _ in tasks:
    a = segment_audio(*recordings['continuous'][:1], sr, recordings['continuous'][1], task)[0]
    b = segment_audio(*recordings['paused'][:1], sr, recordings['paused'][1], task)[0]
    assert np.array_equal(a, b), task

results['task_samples_identical'] = True
assert results['continuous']['dsi']['mpt_sec'] > results['paused']['dsi']['mpt_sec'] + 3

# Exercise the real pipeline, including normalization, quality, features and DSI.
from app.parkinson_analyzer import ParkinsonAnalyzer

with tempfile.TemporaryDirectory() as tmp:
    analyzer = ParkinsonAnalyzer(save_raw_data=False, raw_data_dir=tmp)
    analyzer._generate_waveform_base64 = lambda *args: None
    analyzer._generate_spectrogram_base64 = lambda *args: None
    reports = {}
    for label, (raw, segments) in recordings.items():
        wav = Path(tmp) / (label + '.wav')
        sf.write(wav, raw, sr, subtype='FLOAT')
        info = dict(task='exam', noise_floor_dbfs=-80,
                    dsi_segments=[s for s in segments if s['task'] in ('mpt', 'vowel', 'soft', 'glide')],
                    protocol_segments=[s for s in segments if s['task'] in ('mpt', 'reading')])
        reports[label] = analyzer.analyze_audio_file(str(wav), device_info=info)
        assert 'error' not in reports[label], reports[label].get('error')
    for key in ('features', 'dsi', 'speech_protocol', 'symptom_scores', 'quality'):
        assert key in reports['continuous'], key
        assert reports['continuous'][key] == reports['paused'][key], key
    assert reports['paused']['speech_protocol']['mpt']['mpt_sec'] > 9.9
print('pause regression: identical samples; legacy MPT drift reproduced; '
      'full pipeline features, DSI, speech, symptoms and quality invariant: ok')
