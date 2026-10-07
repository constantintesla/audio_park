"""
Smoke-проверка для CI: синтетическая запись проходит через анализатор и веб-API
"""
import io
import json
import os
import sys
import tempfile

import numpy as np
import soundfile as sf

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def make_vowel(path: str, sr: int = 22050, seconds: float = 3.0) -> None:
    """Синтетическая гласная «а»: основной тон ~150 Гц с лёгким вибрато и гармониками"""
    t = np.arange(int(sr * seconds)) / sr
    f0 = 150 + 3 * np.sin(2 * np.pi * 5 * t)
    phase = 2 * np.pi * np.cumsum(f0) / sr
    signal = sum(np.sin(k * phase) / k for k in range(1, 8))
    signal += 0.005 * np.random.default_rng(0).standard_normal(len(t))
    signal = 0.5 * signal / np.max(np.abs(signal))
    sf.write(path, signal.astype(np.float32), sr)


def make_speech(path: str, sr: int = 22050, seconds: float = 4.0) -> None:
    """Похожая на речь запись: слоги по ~150 мс с меняющимся тоном, глухие шумы и паузы"""
    rng = np.random.default_rng(1)
    parts = []
    t_total = 0.0
    while t_total < seconds:
        for _ in range(int(rng.integers(3, 7))):
            n = int(sr * 0.15)
            t = np.arange(n) / sr
            f0 = 120 * 2 ** (rng.uniform(-4, 4) / 12) * (1 + 0.1 * t / 0.15)
            phase = 2 * np.pi * np.cumsum(f0) / sr
            syllable = sum(np.sin(k * phase) / k for k in range(1, 8)) * np.hanning(n)
            parts += [syllable, 0.05 * rng.standard_normal(int(sr * 0.06))]
        parts.append(np.zeros(int(sr * 0.4)))
        t_total = sum(len(p) for p in parts) / sr
    signal = np.concatenate(parts)
    signal = 0.5 * signal / np.max(np.abs(signal))
    sf.write(path, signal.astype(np.float32), sr)


def check_recording_tasks(tmp: str, results_dir: str) -> None:
    """DSI и охриплость считаются только там, где их можно измерить"""
    from parkinson_analyzer import ParkinsonAnalyzer

    analyzer = ParkinsonAnalyzer(save_raw_data=False, raw_data_dir=results_dir)
    vowel_path = os.path.join(tmp, "vowel.wav")
    speech_path = os.path.join(tmp, "speech.wav")
    make_vowel(vowel_path, seconds=4.0)
    make_speech(speech_path)

    vowel = analyzer.analyze_audio_file(vowel_path, save_raw=False)
    assert vowel["recording_task"] == "vowel", vowel["recording_task"]
    assert vowel["symptom_scores"]["hoarseness"] is not None, "охриплость не оценена на гласной"
    assert vowel["symptom_scores"]["monopitch"] is None, "интонация оценена по гласной"

    speech = analyzer.analyze_audio_file(speech_path, save_raw=False)
    assert speech["recording_task"] == "speech", speech["recording_task"]
    assert speech["symptom_scores"]["hoarseness"] is None, "охриплость оценена по речи"
    assert speech["symptom_scores"]["monopitch"] is not None, "интонация не оценена по речи"

    # Обычная запись без заданий DSI и без калибровки: DSI не показываем
    for result in (vowel, speech):
        assert result["dsi"]["dsi_score"] is None, f"DSI посчитан по обычной записи: {result['dsi']}"
        assert result["dsi"]["status"] == "not_applicable"
    report = " ".join(vowel["report"] + speech["report"] + [vowel["recommendation"], speech["recommendation"]])
    assert "стади" not in report, "в отчете стадия болезни"

    # Задания DSI с калибровкой микрофона: DSI считается
    dsi = analyzer.analyze_audio_file(vowel_path, save_raw=False,
                                      device_info={"task": "dsi", "spl_offset_db": 100.0})
    assert dsi["dsi"]["dsi_score"] is not None, dsi["dsi"]
    print("recording tasks: ok")


def check_analyzer(wav_path: str, results_dir: str) -> None:
    from parkinson_analyzer import ParkinsonAnalyzer

    analyzer = ParkinsonAnalyzer(save_raw_data=False, raw_data_dir=results_dir)
    result = analyzer.analyze_audio_file(wav_path, save_raw=False, result_id="ci_smoke")
    assert "error" not in result, result.get("error")
    for key in ("features", "dsi", "symptom_scores", "report"):
        assert key in result, f"нет ключа {key} в результате анализа"
    assert result["features"], "пустой набор признаков"
    print(f"analyzer: ok, признаков {len(result['features'])}")


def check_api(wav_path: str) -> None:
    import api

    client = api.app.test_client()
    for route in ("/", "/results", "/visualization", "/api/results", "/api/stats"):
        resp = client.get(route)
        assert resp.status_code == 200, f"GET {route} -> {resp.status_code}"
    with open(wav_path, "rb") as f:
        data = {"file": (io.BytesIO(f.read()), "ci_smoke.wav"), "username": "ci"}
    resp = client.post("/api/analyze", data=data, content_type="multipart/form-data")
    assert resp.status_code == 200, f"POST /api/analyze -> {resp.status_code}: {resp.get_data(as_text=True)[:500]}"

    # Запись из веб-рекордера: данные устройства, результаты калибровки и сама калибровка
    with open(wav_path, "rb") as f:
        audio = f.read()
    calibration = {"version": 1, "noise_floor_dbfs": -62.5, "sweep_detected": True,
                   "bandwidth_high_hz": 7800, "agc_suspected": False, "warnings": []}
    data = {"file": (io.BytesIO(audio), "audio.wav"), "username": "ci",
            "device_info": json.dumps({"device_id": "web-ci:mic", "noise_floor_dbfs": -62.5}),
            "calibration": json.dumps(calibration),
            "calibration_file": (io.BytesIO(audio), "calibration.wav")}
    resp = client.post("/api/analyze", data=data, content_type="multipart/form-data")
    assert resp.status_code == 200, f"POST /api/analyze (калибровка) -> {resp.status_code}"
    result = resp.get_json()
    assert result.get("calibration", {}).get("bandwidth_high_hz") == 7800, "калибровка не сохранена в результат"
    assert result["device_info"].get("noise_floor_dbfs") == -62.5, "noise_floor_dbfs не дошел до анализа"
    assert result["quality"]["metrics"]["noise_floor_source"] == "app_calibration"
    cal_path = result["raw_data"]["files"].get("calibration_audio")
    assert cal_path and os.path.exists(cal_path), "файл калибровки не сохранен"
    print("api: ok")


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        wav_path = os.path.join(tmp, "ci_smoke.wav")
        make_vowel(wav_path)
        check_analyzer(wav_path, os.path.join(tmp, "results"))
        check_recording_tasks(tmp, os.path.join(tmp, "results"))
        # api.py пишет results.json и results/ в текущую папку, поэтому запускаем его во временной
        os.chdir(tmp)
        try:
            check_api(wav_path)
        finally:
            os.chdir(ROOT)
    print("smoke: ok")


if __name__ == "__main__":
    main()
