"""
Эксперимент: насколько признаки зависят от АЧХ устройства и помогает ли выравнивание

Синтезируем устойчивую гласную /а/ для нескольких «дикторов» (разный F0, форманты,
доля придыхания), «записываем» ее через фильтры, имитирующие разные устройства,
и сравниваем признаки в трех вариантах:
    raw        - без коррекции
    reference  - LTAS каждой записи подогнан под эталонный LTAS
    device     - скомпенсирована АЧХ устройства, оцененная по другим дикторам
                 (leave-one-out: диктор не участвует в оценке кривой своего устройства)

Запуск: python experiments/eq_device_sim.py
"""
import os
import sys

import numpy as np
import librosa
from scipy import signal

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from feature_extractor import FeatureExtractor  # noqa: E402
from spectral_equalizer import SpectralEqualizer  # noqa: E402

SR_SYNTH = 44100
SR = 16000
DURATION = 3.0
RNG = np.random.default_rng(0)

FEATURES = ['hnr_db', 'spectral_centroid_mean', 'spectral_rolloff_mean',
            'turbulence_ratio', 'jitter_percent', 'shimmer_percent']


# ---------------------------------------------------------------- синтез голоса

def synth_vowel(f0, formant_scale, breathiness, seed):
    """Гласная /а/: импульсы Розенберга с jitter/shimmer + аспирационный шум + форманты"""
    rng = np.random.default_rng(seed)
    n = int(DURATION * SR_SYNTH)
    source = np.zeros(n)
    t = 0
    while t < n:
        period = SR_SYNTH / (f0 * (1 + 0.005 * rng.standard_normal()))
        amp = 1 + 0.03 * rng.standard_normal()
        p = int(period)
        tp, tn = int(0.4 * p), int(0.16 * p)
        pulse = np.zeros(p)
        k = np.arange(tp)
        pulse[:tp] = 0.5 * (1 - np.cos(np.pi * k / tp))
        k = np.arange(tn)
        pulse[tp:tp + tn] = np.cos(np.pi * k / (2 * tn))
        end = min(n, t + p)
        source[t:end] += amp * pulse[:end - t]
        t += p
    source = np.diff(source, prepend=0.0)  # производная потока
    noise = rng.standard_normal(n)
    noise = signal.lfilter(*signal.butter(2, 1000 / (SR_SYNTH / 2), 'high'), noise)
    noise *= np.abs(source).mean() / np.abs(noise).mean()
    excitation = source + breathiness * noise * (0.5 + np.abs(signal.hilbert(source)) / np.abs(source).max())
    y = excitation
    for f, bw in [(730, 90), (1090, 110), (2440, 160), (3400, 250), (4500, 300)]:
        f *= formant_scale
        r = np.exp(-np.pi * bw / SR_SYNTH)
        theta = 2 * np.pi * f / SR_SYNTH
        y = signal.lfilter([1 - r], [1, -2 * r * np.cos(theta), r * r], y)
    y = np.diff(y, prepend=0.0)  # излучение губ
    env = np.minimum(1, np.minimum(np.arange(n), n - np.arange(n)) / (0.1 * SR_SYNTH))
    y *= env
    return y / np.max(np.abs(y)) * 0.5


SPEAKERS = {
    # имя: (F0, масштаб формант, придыхание)
    'm_modal':   (115, 1.00, 0.05),
    'm_breathy': (125, 1.00, 0.40),
    'm2_modal':  (100, 0.95, 0.10),
    'f_modal':   (210, 1.15, 0.05),
    'f_breathy': (225, 1.15, 0.40),
    'f2_modal':  (190, 1.12, 0.10),
}


# ---------------------------------------------------------- имитация устройств

def peaking(f0, gain_db, q, sr):
    a = 10 ** (gain_db / 40)
    w = 2 * np.pi * f0 / sr
    alpha = np.sin(w) / (2 * q)
    b = [1 + alpha * a, -2 * np.cos(w), 1 - alpha * a]
    den = [1 + alpha / a, -2 * np.cos(w), 1 - alpha / a]
    return np.array(b) / den[0], np.array(den) / den[0]


def shelf(f0, gain_db, sr, high):
    a = 10 ** (gain_db / 40)
    w = 2 * np.pi * f0 / sr
    alpha = np.sin(w) / 2 * np.sqrt(2)
    c, s2 = np.cos(w), 2 * np.sqrt(a) * alpha
    if high:
        b = [a * ((a + 1) + (a - 1) * c + s2), -2 * a * ((a - 1) + (a + 1) * c), a * ((a + 1) + (a - 1) * c - s2)]
        d = [(a + 1) - (a - 1) * c + s2, 2 * ((a - 1) - (a + 1) * c), (a + 1) - (a - 1) * c - s2]
    else:
        b = [a * ((a + 1) - (a - 1) * c + s2), 2 * a * ((a - 1) - (a + 1) * c), a * ((a + 1) - (a - 1) * c - s2)]
        d = [(a + 1) + (a - 1) * c + s2, -2 * ((a - 1) + (a + 1) * c), (a + 1) + (a - 1) * c - s2]
    return np.array(b) / d[0], np.array(d) / d[0]


def hp(fc, order, sr):
    return signal.butter(order, fc / (sr / 2), 'high')


def lp(fc, order, sr):
    return signal.butter(order, fc / (sr / 2), 'low')


DEVICES = {
    'studio':  [],
    'phone_a': [hp(150, 2, SR_SYNTH), peaking(3500, 6, 1.0, SR_SYNTH), lp(7000, 4, SR_SYNTH)],
    'phone_b': [hp(300, 4, SR_SYNTH), peaking(1500, -4, 1.2, SR_SYNTH), shelf(4000, 5, SR_SYNTH, True)],
    'headset': [shelf(300, 6, SR_SYNTH, False), shelf(4000, -6, SR_SYNTH, True)],
    'laptop':  [hp(250, 2, SR_SYNTH), peaking(800, -5, 1.5, SR_SYNTH), peaking(5000, 8, 2.0, SR_SYNTH)],
}


def record(voice, device, seed):
    y = voice
    for b, a in DEVICES[device]:
        y = signal.lfilter(b, a, y)
    y = y / np.max(np.abs(y)) * 0.5
    # одинаковый шум тракта на всех устройствах (-60 дБ от пика), чтобы изолировать АЧХ
    y = y + 0.5e-3 * np.random.default_rng(seed).standard_normal(len(y))
    return librosa.resample(y, orig_sr=SR_SYNTH, target_sr=SR)


# ------------------------------------------------------------------- метрики

def features_of(fe, audio):
    f = fe.extract_all_features(audio.astype(np.float64))
    return [f.get(k, np.nan) for k in FEATURES]


def main():
    fe = FeatureExtractor(sample_rate=SR)
    eq = SpectralEqualizer(sample_rate=SR)

    recs = {}
    for si, (name, (f0, fs, br)) in enumerate(SPEAKERS.items()):
        voice = synth_vowel(f0, fs, br, seed=si)
        for di, dev in enumerate(DEVICES):
            recs[(name, dev)] = record(voice, dev, seed=100 * si + di)

    speakers, devices = list(SPEAKERS), list(DEVICES)
    reference_ltas = eq.mean_ltas([recs[(s, 'studio')] for s in speakers])

    results = {m: {} for m in ['raw', 'reference', 'device']}
    for s in speakers:
        others = [o for o in speakers if o != s]
        ref_loo = eq.mean_ltas([recs[(o, 'studio')] for o in others])
        for d in devices:
            audio = recs[(s, d)]
            results['raw'][(s, d)] = features_of(fe, audio)
            results['reference'][(s, d)] = features_of(fe, eq.equalize_to_reference(audio, ref_loo))
            if d == 'studio':
                dev_eq = audio
            else:
                curve = eq.estimate_device_curve([recs[(o, d)] for o in others], ref_loo)
                dev_eq = eq.equalize_device(audio, curve)
            results['device'][(s, d)] = features_of(fe, dev_eq)

    # 1) Разброс между устройствами: для каждого диктора и признака отклонение от
    #    «студийного» raw-значения (истины), усредненное по дикторам и устройствам
    print('\nСреднее |отклонение| от студийной записи (без коррекции), по устройствам кроме studio')
    print(f"{'признак':<26}" + ''.join(f'{m:>12}' for m in results))
    for fi, feat in enumerate(FEATURES):
        row = []
        for m in results:
            errs = [abs(results[m][(s, d)][fi] - results['raw'][(s, 'studio')][fi])
                    for s in speakers for d in devices if d != 'studio']
            row.append(np.nanmean(errs))
        print(f'{feat:<26}' + ''.join(f'{v:12.3f}' for v in row))

    # 2) Сохранился ли признак болезни: разница breathy - modal (истина = studio raw)
    print('\nРазница «придыхательный - модальный» (сохраняет ли коррекция полезный сигнал)')
    print(f"{'признак':<26}{'истина':>10}" + ''.join(f'{m:>12}' for m in results))
    pairs = [('m_breathy', 'm_modal'), ('f_breathy', 'f_modal')]
    for fi, feat in enumerate(FEATURES):
        truth = np.mean([results['raw'][(b, 'studio')][fi] - results['raw'][(mo, 'studio')][fi] for b, mo in pairs])
        row = []
        for m in results:
            row.append(np.mean([results[m][(b, d)][fi] - results[m][(mo, d)][fi]
                                for b, mo in pairs for d in devices if d != 'studio']))
        print(f'{feat:<26}{truth:10.3f}' + ''.join(f'{v:12.3f}' for v in row))

    # 3) Подробно по HNR
    print('\nHNR (дБ) по устройствам, без коррекции / device-коррекция')
    print(f"{'диктор':<12}" + ''.join(f'{d:>18}' for d in devices))
    hi = FEATURES.index('hnr_db')
    for s in speakers:
        print(f'{s:<12}' + ''.join(
            f"{results['raw'][(s, d)][hi]:8.1f} /{results['device'][(s, d)][hi]:7.1f} " for d in devices))


if __name__ == '__main__':
    main()
