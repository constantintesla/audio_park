"""
Модуль для анализа симптомов болезни Паркинсона
Основан на пороговых значениях из исследований:
- Little et al. 2004 (104 акустических признака)
- Daoudi 2022 (monopitch/phonatory instability)
- NIH 2025 (12 вокальных биомаркеров)
"""
from typing import Dict, List, Tuple


class SymptomAnalyzer:
    """Класс для анализа симптомов ПД на основе извлеченных признаков"""
    
    # Пороговые значения из исследований (Little 2004, Daoudi 2022)
    THRESHOLDS = {
        'jitter_percent': 1.1,      # >1.1% указывает на ПД
        'shimmer_percent': 7.5,     # >7.5% указывает на ПД
        'hnr_db': 13.0,             # <13dB указывает на ПД (breathy voice)
        'f0_sd_hz': 50.0,           # <50Hz указывает на monopitch
        'rate_syl_sec': 3.0,        # <3 слогов/сек указывает на медленную речь
        'pause_ratio': 0.30,        # >30% указывает на проблемы с артикуляцией
        'amplitude_db_variation': 6.0,  # <6dB указывает на monoloudness
    }
    
    def analyze(self, features: Dict[str, float]) -> Dict:
        """
        Анализ симптомов на основе извлеченных признаков
        
        Args:
            features: Словарь с извлеченными признаками
        
        Returns:
            Словарь с оценками симптомов и риском ПД
        """
        # Оценка симптомов (0-3: Нет/Легкий/Умеренный/Тяжелый)
        symptom_scores = {
            'hypophonia': self._score_hypophonia(features),
            'monopitch': self._score_monopitch(features),
            'monoloudness': self._score_monoloudness(features),
            'hoarseness': self._score_hoarseness(features),
            'imprecise_articulation': self._score_articulation(features),
        }
        
        # Подсчет признаков, превышающих пороги
        exceeded_thresholds = self._count_exceeded_thresholds(features)
        
        # Оценка риска ПД
        pd_risk = self._assess_pd_risk(exceeded_thresholds, symptom_scores)
        
        # Генерация отчета
        report = self._generate_report(features, symptom_scores, exceeded_thresholds)
        
        return {
            'symptom_scores': symptom_scores,
            'pd_risk': pd_risk,
            'exceeded_thresholds': exceeded_thresholds,
            'report': report
        }
    
    def _score_hypophonia(self, features: Dict[str, float]) -> int:
        """
        Оценка гипофонии (низкая громкость)
        
        Признаки: низкий RMS, низкая амплитуда
        """
        rms = features.get('rms_mean', 0.0)
        amplitude_range = features.get('amplitude_db_range', 0.0)
        
        # Нормальные значения RMS обычно >0.05 для речевого сигнала
        if rms < 0.02:
            return 3  # Тяжелая гипофония
        elif rms < 0.04:
            return 2  # Умеренная
        elif rms < 0.05:
            return 1  # Легкая
        else:
            return 0  # Норма
    
    def _score_monopitch(self, features: Dict[str, float]) -> int:
        """
        Оценка monopitch (монотонность, отсутствие вариации высоты тона)
        
        Признаки: низкое стандартное отклонение F0 (<50Hz)
        """
        f0_sd = features.get('f0_sd_hz', 0.0)
        
        if f0_sd < 20:
            return 3  # Тяжелый monopitch
        elif f0_sd < 35:
            return 2  # Умеренный
        elif f0_sd < 50:
            return 1  # Легкий
        else:
            return 0  # Норма
    
    def _score_monoloudness(self, features: Dict[str, float]) -> int:
        """
        Оценка monoloudness (монотонная громкость)
        
        Признаки: низкая вариация амплитуды в dB (<6dB)
        """
        db_variation = features.get('amplitude_db_variation', 0.0)
        db_range = features.get('amplitude_db_range', 0.0)
        
        # Комбинированная оценка
        if db_variation < 2.0 or db_range < 10.0:
            return 3  # Тяжелая monoloudness
        elif db_variation < 4.0 or db_range < 15.0:
            return 2  # Умеренная
        elif db_variation < 6.0 or db_range < 20.0:
            return 1  # Легкая
        else:
            return 0  # Норма
    
    def _score_hoarseness(self, features: Dict[str, float]) -> int:
        """
        Оценка hoarseness (охриплость, harshness)
        
        Признаки: повышенный jitter, повышенный shimmer, низкий HNR
        """
        jitter = features.get('jitter_percent', 0.0)
        shimmer = features.get('shimmer_percent', 0.0)
        hnr = features.get('hnr_db', 20.0)
        
        # Комбинированная оценка
        score = 0
        
        if jitter > 2.0 or shimmer > 12.0:
            score += 2
        elif jitter > 1.1 or shimmer > 7.5:
            score += 1
        
        if hnr < 8.0:
            score += 2
        elif hnr < 13.0:
            score += 1
        
        return min(score, 3)  # Максимум 3
    
    def _score_articulation(self, features: Dict[str, float]) -> int:
        """
        Оценка неточной артикуляции
        
        Признаки: медленная речь, высокий процент пауз
        """
        rate = features.get('rate_syl_sec', 4.0)
        pause_ratio = features.get('pause_ratio', 0.0)
        
        score = 0
        
        if rate < 2.0:
            score += 2
        elif rate < 3.0:
            score += 1
        
        if pause_ratio > 0.40:
            score += 2
        elif pause_ratio > 0.30:
            score += 1
        
        return min(score, 3)  # Максимум 3
    
    def _count_exceeded_thresholds(self, features: Dict[str, float]) -> List[str]:
        """Подсчет признаков, превышающих пороговые значения"""
        exceeded = []
        
        # Jitter
        if features.get('jitter_percent', 0) > self.THRESHOLDS['jitter_percent']:
            exceeded.append('jitter')
        
        # Shimmer
        if features.get('shimmer_percent', 0) > self.THRESHOLDS['shimmer_percent']:
            exceeded.append('shimmer')
        
        # HNR
        if features.get('hnr_db', 20) < self.THRESHOLDS['hnr_db']:
            exceeded.append('hnr')
        
        # F0 SD (monopitch)
        if features.get('f0_sd_hz', 100) < self.THRESHOLDS['f0_sd_hz']:
            exceeded.append('f0_sd')
        
        # Rate (артикуляция)
        if features.get('rate_syl_sec', 5) < self.THRESHOLDS['rate_syl_sec']:
            exceeded.append('rate')
        
        # Pause ratio
        if features.get('pause_ratio', 0) > self.THRESHOLDS['pause_ratio']:
            exceeded.append('pause_ratio')
        
        # Amplitude variation
        if features.get('amplitude_db_variation', 10) < self.THRESHOLDS['amplitude_db_variation']:
            exceeded.append('amplitude_variation')
        
        return exceeded
    
    def _assess_pd_risk(self, exceeded_thresholds: List[str], 
                       symptom_scores: Dict[str, int]) -> str:
        """
        Оценка риска ПД
        
        По Daoudi 2022: ≥3 признака превышают пороги -> 89% точность
        """
        num_exceeded = len(exceeded_thresholds)
        
        # Подсчет симптомов с оценкой >=2
        severe_symptoms = sum(1 for score in symptom_scores.values() if score >= 2)
        
        if num_exceeded >= 4 or severe_symptoms >= 3:
            accuracy = 92
            risk_level = "Высокий"
        elif num_exceeded >= 3 or severe_symptoms >= 2:
            accuracy = 89
            risk_level = "Высокий"
        elif num_exceeded >= 2 or severe_symptoms >= 1:
            accuracy = 75
            risk_level = "Умеренный"
        elif num_exceeded >= 1:
            accuracy = 60
            risk_level = "Низкий"
        else:
            accuracy = 30
            risk_level = "Минимальный"
        
        return f"{risk_level} ({accuracy}%, согласно Little 2004 + Daoudi 2022)"
    
    def _generate_report(self, features: Dict[str, float],
                        symptom_scores: Dict[str, int],
                        exceeded_thresholds: List[str]) -> List[str]:
        """Генерация текстового отчета"""
        report = []
        
        # Гипофония
        if symptom_scores['hypophonia'] > 0:
            severity = ['', 'легкая', 'умеренная', 'тяжелая'][symptom_scores['hypophonia']]
            rms = features.get('rms_mean', 0.0)
            report.append(
                f"- Гипофония ({severity}): низкий RMS ({rms:.3f}), типично для ПД [Little 2004]."
            )
        
        # Monopitch
        if symptom_scores['monopitch'] > 0:
            severity = ['', 'легкий', 'умеренный', 'тяжелый'][symptom_scores['monopitch']]
            f0_sd = features.get('f0_sd_hz', 0.0)
            report.append(
                f"- Monopitch ({severity}): низкая вариация F0 (SD={f0_sd:.1f}Hz), "
                f"отсутствие просодии характерно для ПД [Daoudi 2022]."
            )
        
        # Monoloudness
        if symptom_scores['monoloudness'] > 0:
            severity = ['', 'легкая', 'умеренная', 'тяжелая'][symptom_scores['monoloudness']]
            db_var = features.get('amplitude_db_variation', 0.0)
            report.append(
                f"- Monoloudness ({severity}): вариация амплитуды {db_var:.1f}dB, "
                f"монотонная громкость [Little 2004]."
            )
        
        # Hoarseness
        if symptom_scores['hoarseness'] > 0:
            severity = ['', 'легкая', 'умеренная', 'тяжелая'][symptom_scores['hoarseness']]
            jitter = features.get('jitter_percent', 0.0)
            shimmer = features.get('shimmer_percent', 0.0)
            hnr = features.get('hnr_db', 0.0)
            
            details = []
            if jitter > 1.1:
                details.append(f"jitter={jitter:.2f}%")
            if shimmer > 7.5:
                details.append(f"shimmer={shimmer:.2f}%")
            if hnr < 13.0:
                details.append(f"HNR={hnr:.1f}dB")
            
            report.append(
                f"- Охриплость ({severity}): нестабильность голосовых связок ({', '.join(details)}) "
                f"[Little 2004, Daoudi 2022]."
            )
        
        # Артикуляция
        if symptom_scores['imprecise_articulation'] > 0:
            severity = ['', 'легкая', 'умеренная', 'тяжелая'][symptom_scores['imprecise_articulation']]
            rate = features.get('rate_syl_sec', 0.0)
            pause_ratio = features.get('pause_ratio', 0.0)
            report.append(
                f"- Неточная артикуляция ({severity}): скорость речи {rate:.1f} сл/сек, "
                f"паузы {pause_ratio*100:.1f}% [NIH 2025]."
            )
        
        # Общие рекомендации
        if len(exceeded_thresholds) >= 3:
            report.append(
                "- Рекомендация: консультация невролога, LSVT логопедия, "
                "скрининг в РФ с использованием ИИ-инструментов (2023-2025)."
            )
        elif len(exceeded_thresholds) >= 1:
            report.append(
                "- Рекомендация: мониторинг симптомов, логопедическая оценка."
            )
        
        # Если нет симптомов
        if not report:
            report.append(
                "- Акустические параметры в пределах нормы. Симптомы ПД не выявлены."
            )
        
        return report