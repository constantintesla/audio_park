"""
Основной модуль для анализа речи на предмет симптомов болезни Паркинсона
"""
import json
import base64
import io
import numpy as np
from typing import Dict, Optional, List
import argparse
import sys

try:
    import matplotlib
    matplotlib.use('Agg')  # Неинтерактивный бэкенд
    import matplotlib.pyplot as plt
    HAS_MATPLOTLIB = True
except ImportError:
    HAS_MATPLOTLIB = False

from audio_processor import AudioProcessor
from feature_extractor import FeatureExtractor
from symptom_analyzer import SymptomAnalyzer


class ParkinsonAnalyzer:
    """Главный класс для анализа речи на симптомы ПД"""
    
    def __init__(self):
        self.audio_processor = AudioProcessor(target_sr=16000)
        self.feature_extractor = FeatureExtractor(sample_rate=16000)
        self.symptom_analyzer = SymptomAnalyzer()
    
    def analyze_audio_file(self, file_path: str) -> Dict:
        """
        Полный анализ аудиофайла
        
        Args:
            file_path: Путь к аудиофайлу (WAV/MP3)
        
        Returns:
            Структурированный JSON отчет
        """
        try:
            # 1. Загрузка и предобработка аудио
            audio, sr = self.audio_processor.load_audio(file_path)
            
            # Редукция шума
            audio_cleaned = self.audio_processor.noise_reduction(audio)
            
            # Сегментация
            segments = self.audio_processor.segment_utterances(audio_cleaned, sr)
            
            # 2. Извлечение признаков (из всех сегментов)
            all_features = {}
            
            if len(segments) > 0:
                # Анализируем каждый сегмент и усредняем признаки
                segment_features = []
                for segment in segments:
                    features = self.feature_extractor.extract_all_features(segment)
                    segment_features.append(features)
                
                # Усреднение признаков по сегментам
                if segment_features:
                    all_features = self._average_features(segment_features)
            
            # Если не удалось извлечь признаки из сегментов, пробуем весь файл
            if not all_features or all_features.get('f0_mean_hz', 0) == 0:
                all_features = self.feature_extractor.extract_all_features(audio_cleaned)
            
            # 3. Анализ симптомов
            analysis = self.symptom_analyzer.analyze(all_features)
            
            # 4. Расчет DSI (Dysphonia Severity Index)
            dsi_result = self._calculate_dsi(all_features)
            
            # 5. Получение визуализаций
            waveform_data = self.audio_processor.get_waveform(audio_cleaned)
            freqs, times, spectrogram = self.audio_processor.get_spectrogram(audio_cleaned, sr)
            
            # Генерация base64 визуализаций (опционально)
            try:
                waveform_base64 = self._generate_waveform_base64(audio_cleaned, sr)
                spectrogram_base64 = self._generate_spectrogram_base64(freqs, times, spectrogram)
            except:
                waveform_base64 = None
                spectrogram_base64 = None
            
            # 6. Формирование финального отчета
            result = {
                "audio_summary": {
                    "duration_sec": round(len(audio) / sr, 2),
                    "sample_rate": sr,
                    "segments": len(segments)
                },
                "features": {
                    "jitter_percent": round(all_features.get('jitter_percent', 0.0), 2),
                    "shimmer_percent": round(all_features.get('shimmer_percent', 0.0), 2),
                    "hnr_db": round(all_features.get('hnr_db', 0.0), 1),
                    "rate_syl_sec": round(all_features.get('rate_syl_sec', 0.0), 1),
                    "f0_sd_hz": round(all_features.get('f0_sd_hz', 0.0), 1),
                    "f0_mean_hz": round(all_features.get('f0_mean_hz', 0.0), 1),
                    "amplitude_db_variation": round(all_features.get('amplitude_db_variation', 0.0), 1),
                    "pause_ratio": round(all_features.get('pause_ratio', 0.0), 3)
                },
                "dsi": dsi_result,
                "symptom_scores": {
                    **analysis['symptom_scores'],
                    "pd_risk": analysis['pd_risk']
                },
                "report": self._add_dsi_to_report(analysis['report'], dsi_result),
                "visuals": {
                    "waveform": waveform_base64 or f"Данные: {len(waveform_data['amplitude'])} точек, "
                               f"длительность {waveform_data['duration']:.2f}с",
                    "spectrogram": spectrogram_base64 or f"Частоты: 0-{sr/2:.0f}Hz, "
                                  f"временные кадры: {len(times)}"
                }
            }
            
            return result
        
        except Exception as e:
            # Возврат ошибки в JSON формате
            return {
                "error": f"Ошибка обработки: {str(e)}",
                "audio_summary": {},
                "features": {},
                "dsi": {},
                "symptom_scores": {},
                "report": [f"Ошибка анализа: {str(e)}"],
                "visuals": {}
            }
    
    def _add_dsi_to_report(self, report: List[str], dsi_result: Dict) -> List[str]:
        """Добавление информации о DSI в отчет"""
        updated_report = report.copy()
        
        if dsi_result.get('dsi_score') is not None:
            dsi_score = dsi_result['dsi_score']
            dsi_range = dsi_result['dsi_range']
            breakdown = dsi_result.get('dsi_breakdown', {})
            interpretation = dsi_result.get('interpretation', {})
            
            dsi_info = [
                f"\n=== DSI (Dysphonia Severity Index) ===",
                f"DSI Score: {dsi_score} ({dsi_range})",
                f"Параметры:",
                f"  - MPT: {breakdown.get('mpt_sec', 0):.2f}с ({interpretation.get('mpt_status', 'N/A')})",
                f"  - F0-High: {breakdown.get('f0_high_hz', 0):.1f} Гц ({interpretation.get('f0_high_status', 'N/A')})",
                f"  - I-Low: {breakdown.get('i_low_db', 0):.1f} дБ ({interpretation.get('i_low_status', 'N/A')})",
                f"  - Jitter: {breakdown.get('jitter_percent', 0):.2f}% ({interpretation.get('jitter_status', 'N/A')})",
                f"Интерпретация: {interpretation.get('pd_risk_note', '')}",
                f"DSI коррелирует с Voice Handicap Index и идеален для мониторинга терапии (LSVT LOUD)."
            ]
            updated_report.extend(dsi_info)
        elif dsi_result.get('error'):
            updated_report.append(f"\nDSI: {dsi_result.get('error', 'Не удалось рассчитать')}")
        
        return updated_report
    
    def _calculate_dsi(self, features: Dict[str, float]) -> Dict:
        """
        Расчет DSI (Dysphonia Severity Index)
        
        Формула: DSI = 0.13 × MPT + 0.0053 × F0-High - 0.26 × I-Low - 1.18 × Jitter(%) + 12.4
        
        Интерпретация:
        - +2…+5: Нормальный голос
        - 0…+2: Легкая дисфония
        - -2…0: Умеренная дисфония (PD 1-2)
        - <-2: Тяжелая дисфония (PD 3-5)
        """
        try:
            # Получение параметров
            mpt_sec = features.get('mpt_sec', 0.0)
            f0_high_hz = features.get('f0_high_hz', 0.0)
            i_low_db = features.get('i_low_db', 0.0)
            jitter_percent = features.get('jitter_percent', 0.0)
            
            # Проверка наличия всех параметров
            if mpt_sec == 0.0 or f0_high_hz == 0.0 or i_low_db == 0.0:
                return {
                    "dsi_score": None,
                    "dsi_range": "Недостаточно данных для расчета DSI",
                    "dsi_breakdown": {
                        "mpt_sec": round(mpt_sec, 2),
                        "f0_high_hz": round(f0_high_hz, 1),
                        "i_low_db": round(i_low_db, 1),
                        "jitter_percent": round(jitter_percent, 2)
                    },
                    "error": "Отсутствуют необходимые параметры для расчета DSI"
                }
            
            # Расчет DSI по формуле
            dsi_score = (0.13 * mpt_sec + 
                        0.0053 * f0_high_hz - 
                        0.26 * i_low_db - 
                        1.18 * jitter_percent + 
                        12.4)
            
            # Интерпретация DSI
            if dsi_score >= 2.0:
                dsi_range = "Нормальный голос"
                pd_risk_note = "Низкий риск ПД"
            elif dsi_score >= 0.0:
                dsi_range = "Легкая дисфония"
                pd_risk_note = "Умеренный риск ПД"
            elif dsi_score >= -2.0:
                dsi_range = "Умеренная дисфония (PD риск высокий)"
                pd_risk_note = "Высокий риск ПД (стадия 1-2)"
            else:
                dsi_range = "Тяжелая дисфония (PD риск очень высокий)"
                pd_risk_note = "Очень высокий риск ПД (стадия 3-5)"
            
            return {
                "dsi_score": round(dsi_score, 2),
                "dsi_range": dsi_range,
                "dsi_breakdown": {
                    "mpt_sec": round(mpt_sec, 2),
                    "f0_high_hz": round(f0_high_hz, 1),
                    "i_low_db": round(i_low_db, 1),
                    "jitter_percent": round(jitter_percent, 2)
                },
                "interpretation": {
                    "mpt_status": "Низкий" if mpt_sec < 10 else "Нормальный" if mpt_sec >= 15 else "Снижен",
                    "f0_high_status": "Низкий" if f0_high_hz < 300 else "Нормальный" if f0_high_hz >= 400 else "Снижен",
                    "i_low_status": "Повышен" if i_low_db > 55 else "Нормальный" if i_low_db < 45 else "Повышен",
                    "jitter_status": "Высокий" if jitter_percent > 1.5 else "Нормальный" if jitter_percent < 1.0 else "Повышен",
                    "pd_risk_note": pd_risk_note
                },
                "formula": "DSI = 0.13 × MPT + 0.0053 × F0-High - 0.26 × I-Low - 1.18 × Jitter(%) + 12.4"
            }
            
        except Exception as e:
            return {
                "dsi_score": None,
                "dsi_range": "Ошибка расчета DSI",
                "dsi_breakdown": {},
                "error": str(e)
            }
    
    def _average_features(self, feature_list: list) -> Dict:
        """Усреднение признаков из нескольких сегментов"""
        if not feature_list:
            return {}
        
        averaged = {}
        keys = set()
        
        # Собираем все ключи
        for feat in feature_list:
            keys.update(feat.keys())
        
        # Усредняем по каждому ключу
        for key in keys:
            values = [feat.get(key, 0) for feat in feature_list if feat.get(key, 0) != 0]
            if values:
                averaged[key] = np.mean(values)
            else:
                averaged[key] = 0.0
        
        return averaged
    
    def _generate_waveform_base64(self, audio: np.ndarray, sr: int) -> Optional[str]:
        """Генерация base64 изображения волновой формы"""
        if not HAS_MATPLOTLIB:
            return None
        
        try:
            fig, ax = plt.subplots(figsize=(10, 3))
            time_axis = np.linspace(0, len(audio) / sr, len(audio))
            ax.plot(time_axis, audio, linewidth=0.5)
            ax.set_xlabel('Время (с)')
            ax.set_ylabel('Амплитуда')
            ax.set_title('Волновая форма')
            ax.grid(True, alpha=0.3)
            
            # Конвертация в base64
            buf = io.BytesIO()
            plt.savefig(buf, format='png', dpi=100, bbox_inches='tight')
            buf.seek(0)
            img_base64 = base64.b64encode(buf.read()).decode('utf-8')
            plt.close(fig)
            
            return f"data:image/png;base64,{img_base64}"
        except:
            return None
    
    def _generate_spectrogram_base64(self, freqs: np.ndarray, times: np.ndarray, 
                                    spectrogram: np.ndarray) -> Optional[str]:
        """Генерация base64 изображения спектрограммы"""
        if not HAS_MATPLOTLIB:
            return None
        
        try:
            fig, ax = plt.subplots(figsize=(10, 6))
            
            # Показываем только до 5kHz для читаемости
            freq_mask = freqs <= 5000
            spec_to_show = spectrogram[freq_mask, :]
            freqs_to_show = freqs[freq_mask]
            
            im = ax.imshow(spec_to_show, aspect='auto', origin='lower',
                          extent=[times[0], times[-1], freqs_to_show[0], freqs_to_show[-1]],
                          cmap='viridis', interpolation='bilinear')
            ax.set_xlabel('Время (с)')
            ax.set_ylabel('Частота (Hz)')
            ax.set_title('Спектрограмма')
            plt.colorbar(im, ax=ax, label='dB')
            
            # Конвертация в base64
            buf = io.BytesIO()
            plt.savefig(buf, format='png', dpi=100, bbox_inches='tight')
            buf.seek(0)
            img_base64 = base64.b64encode(buf.read()).decode('utf-8')
            plt.close(fig)
            
            return f"data:image/png;base64,{img_base64}"
        except:
            return None
    
    def analyze_to_json(self, file_path: str) -> str:
        """
        Анализ и возврат результата в виде JSON строки
        
        Args:
            file_path: Путь к аудиофайлу
        
        Returns:
            JSON строка с результатами анализа
        """
        result = self.analyze_audio_file(file_path)
        return json.dumps(result, ensure_ascii=False, indent=2)


def main():
    """Главная функция для запуска из командной строки"""
    parser = argparse.ArgumentParser(
        description='Анализ речи на симптомы болезни Паркинсона'
    )
    parser.add_argument(
        'audio_file',
        type=str,
        help='Путь к аудиофайлу (WAV/MP3)'
    )
    parser.add_argument(
        '-o', '--output',
        type=str,
        help='Путь для сохранения JSON отчета (если не указан, вывод в stdout)'
    )
    
    args = parser.parse_args()
    
    # Создание анализатора
    analyzer = ParkinsonAnalyzer()
    
    # Анализ файла
    try:
        json_result = analyzer.analyze_to_json(args.audio_file)
        
        # Сохранение или вывод результата
        if args.output:
            with open(args.output, 'w', encoding='utf-8') as f:
                f.write(json_result)
            print(f"Отчет сохранен в: {args.output}")
        else:
            print(json_result)
    
    except FileNotFoundError:
        print(json.dumps({
            "error": f"Файл не найден: {args.audio_file}"
        }, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(json.dumps({
            "error": f"Ошибка обработки: {str(e)}"
        }, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()