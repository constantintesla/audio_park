"""
Smoke-проверка для CI: синтетическая запись проходит через анализатор и веб-API
"""
import io
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
    print("api: ok")


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        wav_path = os.path.join(tmp, "ci_smoke.wav")
        make_vowel(wav_path)
        check_analyzer(wav_path, os.path.join(tmp, "results"))
        # api.py пишет results.json и results/ в текущую папку, поэтому запускаем его во временной
        os.chdir(tmp)
        try:
            check_api(wav_path)
        finally:
            os.chdir(ROOT)
    print("smoke: ok")


if __name__ == "__main__":
    main()
