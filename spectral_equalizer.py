"""
Модуль для выравнивания АЧХ разных записывающих устройств

Идея: микрофон (и тракт записи) действует как линейный фильтр H(f), поэтому
долговременный средний спектр (LTAS) записи = LTAS голоса + АЧХ устройства (в дБ).
Если знать АЧХ устройства, ее можно скомпенсировать обратным фильтром.

Два режима:
- 'device': компенсация известной кривой устройства. Кривая оценивается как
  средний LTAS записей с этого устройства (много разных людей) минус средний LTAS
  эталонного устройства. Голос конкретного человека при этом не трогается.
- 'reference': подгонка LTAS конкретной записи под эталонный LTAS. Убирает
  влияние устройства, но вместе с ним и индивидуальный наклон спектра голоса
  (в том числе придыхание), поэтому годится только как запасной вариант.

Фильтр линейно-фазовый (FIR), чтобы не сдвигать периоды основного тона
(важно для jitter/shimmer).
"""
import numpy as np
import librosa
from scipy import signal
from typing import Dict, Iterable, Optional, Tuple


class SpectralEqualizer:
    """Выравнивание АЧХ записи по LTAS"""

    def __init__(self, sample_rate: int = 16000,
                 n_fft: int = 1024,
                 band_fraction: int = 3,
                 fmin: float = 80.0,
                 fmax: float = 7000.0,
                 max_gain_db: float = 12.0,
                 fir_taps: int = 513):
        """
        Args:
            sample_rate: Частота дискретизации
            n_fft: Размер окна для LTAS
            band_fraction: Сглаживание LTAS в долях октавы (3 = 1/3 октавы)
            fmin, fmax: Полоса, в которой выполняется коррекция
            max_gain_db: Ограничение усиления/ослабления коррекции
            fir_taps: Длина FIR-фильтра коррекции (нечетная)
        """
        self.sample_rate = sample_rate
        self.n_fft = n_fft
        self.band_fraction = band_fraction
        self.fmin = fmin
        self.fmax = min(fmax, sample_rate / 2 * 0.95)
        self.max_gain_db = max_gain_db
        self.fir_taps = fir_taps | 1
        self.freqs = librosa.fft_frequencies(sr=sample_rate, n_fft=n_fft)

    def compute_ltas(self, audio: np.ndarray, active_percentile: float = 30.0) -> np.ndarray:
        """
        Долговременный средний спектр (дБ) по активным кадрам, сглаженный по долям октавы

        Args:
            audio: Аудиомассив
            active_percentile: Кадры тише этого перцентиля энергии (паузы) не учитываются
        """
        stft = librosa.stft(audio, n_fft=self.n_fft, hop_length=self.n_fft // 4)
        power = np.abs(stft) ** 2
        frame_energy = power.sum(axis=0)
        if frame_energy.size == 0:
            return np.zeros_like(self.freqs)
        active = frame_energy >= np.percentile(frame_energy, active_percentile)
        ltas = power[:, active].mean(axis=1) + 1e-20
        return self._smooth_octave(10.0 * np.log10(ltas))

    def _smooth_octave(self, spectrum_db: np.ndarray) -> np.ndarray:
        """Сглаживание спектра окном шириной 1/band_fraction октавы (по мощности)"""
        power = 10.0 ** (spectrum_db / 10.0)
        smoothed = np.empty_like(power)
        half = 2.0 ** (1.0 / (2 * self.band_fraction))
        for i, f in enumerate(self.freqs):
            if f <= 0:
                smoothed[i] = power[i]
                continue
            mask = (self.freqs >= f / half) & (self.freqs <= f * half)
            smoothed[i] = power[mask].mean()
        return 10.0 * np.log10(smoothed + 1e-20)

    def correction_curve(self, measured_db: np.ndarray, target_db: np.ndarray) -> np.ndarray:
        """
        Кривая коррекции (дБ) = target - measured в рабочей полосе

        Кривая центрируется (среднее по полосе = 0 дБ), чтобы менять только форму
        спектра, а не громкость: громкостью занимается отдельная нормализация.
        Вне полосы коррекция плавно уходит в 0 дБ.
        """
        diff = np.asarray(target_db) - np.asarray(measured_db)
        band = (self.freqs >= self.fmin) & (self.freqs <= self.fmax)
        if not np.any(band):
            return np.zeros_like(self.freqs)
        diff = diff - np.mean(diff[band])
        diff = np.clip(diff, -self.max_gain_db, self.max_gain_db)
        curve = np.zeros_like(self.freqs)
        curve[band] = diff[band]
        # Вне полосы держим значение края, затем коррекция плавно уходит в 0 дБ
        idx = np.where(band)[0]
        curve[:idx[0]] = diff[idx[0]] * np.linspace(0.0, 1.0, idx[0], endpoint=False)
        tail = len(curve) - idx[-1] - 1
        curve[idx[-1] + 1:] = diff[idx[-1]] * np.linspace(1.0, 0.0, tail + 1)[1:]
        return curve

    def apply_curve(self, audio: np.ndarray, curve_db: np.ndarray) -> np.ndarray:
        """Применение кривой коррекции линейно-фазовым FIR-фильтром без задержки"""
        nyquist = self.sample_rate / 2.0
        freqs = np.clip(self.freqs / nyquist, 0.0, 1.0)
        freqs[0], freqs[-1] = 0.0, 1.0
        gains = 10.0 ** (np.asarray(curve_db) / 20.0)
        taps = signal.firwin2(self.fir_taps, freqs, gains, window='hann')
        filtered = signal.fftconvolve(audio, taps, mode='full')
        delay = (self.fir_taps - 1) // 2
        return filtered[delay:delay + len(audio)].astype(audio.dtype, copy=False)

    def equalize_to_reference(self, audio: np.ndarray, reference_ltas_db: np.ndarray) -> np.ndarray:
        """Режим 'reference': подогнать LTAS записи под эталонный LTAS"""
        curve = self.correction_curve(self.compute_ltas(audio), reference_ltas_db)
        return self.apply_curve(audio, curve)

    def equalize_device(self, audio: np.ndarray, device_curve_db: np.ndarray) -> np.ndarray:
        """Режим 'device': скомпенсировать известную АЧХ устройства (в дБ относительно эталона)"""
        curve = self.correction_curve(device_curve_db, np.zeros_like(self.freqs))
        return self.apply_curve(audio, curve)

    def mean_ltas(self, recordings: Iterable[np.ndarray]) -> np.ndarray:
        """Средний LTAS (дБ) набора записей, каждая с одинаковым весом"""
        ltas_list = [self.compute_ltas(a) for a in recordings]
        if not ltas_list:
            return np.zeros_like(self.freqs)
        # Убираем различие в громкости между записями, усредняем только форму
        centered = [l - np.mean(l[self._band()]) for l in ltas_list]
        return np.mean(centered, axis=0)

    def estimate_device_curve(self, device_recordings: Iterable[np.ndarray],
                              reference_ltas_db: np.ndarray) -> np.ndarray:
        """
        Оценка АЧХ устройства относительно эталона

        Args:
            device_recordings: Записи разных людей на этом устройстве (модели)
            reference_ltas_db: Средний LTAS записей на эталонном устройстве (mean_ltas)
        """
        device_ltas = self.mean_ltas(device_recordings)
        curve = device_ltas - reference_ltas_db
        return curve - np.mean(curve[self._band()])

    def _band(self) -> np.ndarray:
        return (self.freqs >= self.fmin) & (self.freqs <= self.fmax)
