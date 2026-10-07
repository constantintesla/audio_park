"""
Модуль для анализа симптомов болезни Паркинсона
Основан на пороговых значениях из исследований:
- Little et al. 2004 (104 акустических признака)
- Daoudi 2022 (monopitch/phonatory instability)
- NIH 2025 (12 вокальных биомаркеров)
"""
import numpy as np
from typing import Dict, List, Tuple


class SymptomAnalyzer:
    """Класс для анализа симптомов ПД на основе извлеченных признаков"""
    
    # Пороговые значения из исследований (Little 2004, Daoudi 2022)
    # Обновлены согласно типичным значениям из исследований:
    # - Здоровые: Jitter 0.2-0.7%, Shimmer 2-4%, HNR 20-25 dB
    # - Паркинсон: Jitter >1.5-3%, Shimmer >6-12%, HNR <12-18 dB
    # Примечание: jitter и shimmer хранятся в процентах (1.5 = 1.5%, 6.0 = 6%)
    THRESHOLDS = {
        'jitter_percent': 1.5,        # >1.5% указывает на риск (норма: 0.2-0.7%, патология: >1.5-3%)
        'shimmer_percent': 6.0,        # >6.0% указывает на аномалию (норма: 2-4%, патология: >6-12%)
        'hnr_db': 18.0,               # <18 dB указывает на дисфонию (норма: 20-25 dB, патология: <12-18 dB)
        'rate_syl_sec': 4.5,          # <4.5 слогов/сек указывает на медленную речь
        'pause_ratio': 0.30,          # >30% указывает на проблемы с артикуляцией
        'amplitude_db_variation': 6.0,  # <6dB указывает на monoloudness
    }
    
    # Гипофония: пороги наклона спектра (энергия 1-3.5 кГц относительно 0.3-1 кГц)
    # для легкой/умеренной/тяжелой степени и порог затухания громкости.
    # Не зависят от устройства, но предварительные: откалибровать на реальных записях.
    HYPOPHONIA_TILT_DB = (-16.0, -20.0, -24.0)
    HYPOPHONIA_DECAY_DB = 3.0
    
    # Monopitch: разброс высоты тона в полутонах для легкой/умеренной/тяжелой
    # степени. Полутоны не зависят от пола: SD=23 Гц у мужчины с F0 120 Гц
    # это около 3 полутонов, нормальная интонация. Пороги предварительные.
    MONOPITCH_SEMITONES = (2.0, 1.5, 1.0)
    
    # Какие симптомы можно оценить по какому типу записи.
    # Jitter, shimmer и HNR по нормам измеряются на протяжной гласной: в обычной
    # речи они завышаются из-за смены звуков и интонации, и здоровый голос
    # выглядит «охрипшим». Интонацию и темп, наоборот, можно оценить только по речи.
    VOWEL_ONLY = ('hoarseness',)
    SPEECH_ONLY = ('monopitch', 'monoloudness', 'imprecise_articulation')
    VOWEL_ONLY_THRESHOLDS = ('jitter', 'shimmer', 'hnr')
    SPEECH_ONLY_THRESHOLDS = ('f0_variability', 'f0_sd', 'rate', 'pause_ratio', 'amplitude_variation')
    
    def analyze(self, features: Dict[str, float], task: str = 'speech') -> Dict:
        """
        Анализ симптомов на основе извлеченных признаков
        
        Args:
            features: Словарь с извлеченными признаками
            task: Тип записи: 'speech' (обычная речь) или 'vowel' (протяжная гласная)
        
        Returns:
            Словарь с оценками симптомов и риском ПД. Симптомы, которые по этому
            типу записи оценить нельзя, имеют оценку None.
        """
        is_vowel = task in ('vowel', 'dsi')
        skipped = self.SPEECH_ONLY if is_vowel else self.VOWEL_ONLY
        
        # Оценка симптомов (0-3: Нет/Легкий/Умеренный/Тяжелый, None - не оценивался)
        scorers = {
            'hypophonia': self._score_hypophonia,
            'monopitch': self._score_monopitch,
            'monoloudness': self._score_monoloudness,
            'hoarseness': self._score_hoarseness,
            'imprecise_articulation': self._score_articulation,
        }
        symptom_scores = {name: (None if name in skipped else scorer(features))
                          for name, scorer in scorers.items()}
        
        # Подсчет признаков, превышающих пороги
        skipped_thresholds = self.SPEECH_ONLY_THRESHOLDS if is_vowel else self.VOWEL_ONLY_THRESHOLDS
        exceeded_thresholds = [t for t in self._count_exceeded_thresholds(features)
                               if t not in skipped_thresholds]
        
        # Оценка риска ПД (вероятностная модель)
        assessed_scores = {k: v for k, v in symptom_scores.items() if v is not None}
        pd_risk_data = self._assess_pd_risk(exceeded_thresholds, assessed_scores, features)
        
        # Генерация отчета
        report = self._generate_report(features, symptom_scores, exceeded_thresholds)
        if is_vowel:
            report.append("- Интонация, громкость и темп речи не оценивались: это запись "
                          "протяжной гласной, для них нужна обычная речь.")
        else:
            report.append("- Охриплость (jitter, shimmer, HNR) не оценивалась: эти показатели "
                          "надежны только на протяжной гласной «а-а-а», по обычной речи они "
                          "завышаются и даже у здорового голоса выглядят как патология.")
        
        return {
            'symptom_scores': symptom_scores,
            'recording_task': 'vowel' if is_vowel else 'speech',
            'pd_risk': pd_risk_data['risk_text'],  # Для обратной совместимости
            'pd_risk_data': pd_risk_data,  # Полные данные о риске
            'exceeded_thresholds': exceeded_thresholds,
            'report': report
        }
    
    def _score_hypophonia(self, features: Dict[str, float]) -> int:
        """
        Оценка гипофонии (низкая громкость)
        
        Абсолютный уровень (RMS) зависит от микрофона, усиления и расстояния, а записи
        перед анализом приводятся к единой громкости (AudioProcessor.normalize_loudness).
        Поэтому оцениваем не уровень, а признаки слабого голосового усилия:
        - крутой наклон спектра (spectral_tilt_db): мало энергии в 1-3.5 кГц;
        - затухание громкости к концу записи (loudness_decay_db).
        
        Пороги предварительные и требуют калибровки на записях здоровых дикторов.
        """
        tilt = features.get('spectral_tilt_db', 0.0)
        decay = features.get('loudness_decay_db', 0.0)
        
        score = 0
        # tilt == 0.0 означает, что признак не удалось рассчитать
        if tilt != 0.0:
            if tilt < self.HYPOPHONIA_TILT_DB[2]:
                score = 3  # Тяжелая
            elif tilt < self.HYPOPHONIA_TILT_DB[1]:
                score = 2  # Умеренная
            elif tilt < self.HYPOPHONIA_TILT_DB[0]:
                score = 1  # Легкая
        
        # Голос заметно затихает к концу фразы
        if decay > self.HYPOPHONIA_DECAY_DB:
            score += 1
        
        return min(score, 3)
    
    def _score_monopitch(self, features: Dict[str, float]) -> int:
        """
        Оценка monopitch (монотонность, отсутствие вариации высоты тона)
        
        Признак: малый разброс высоты тона в полутонах (не зависит от пола)
        """
        f0_sd_st = self._f0_sd_semitones(features)
        if f0_sd_st <= 0.0:
            return 0  # Тон не найден, оценить нельзя
        
        if f0_sd_st < self.MONOPITCH_SEMITONES[2]:
            return 3  # Тяжелый monopitch
        elif f0_sd_st < self.MONOPITCH_SEMITONES[1]:
            return 2  # Умеренный
        elif f0_sd_st < self.MONOPITCH_SEMITONES[0]:
            return 1  # Легкий
        else:
            return 0  # Норма
    
    @staticmethod
    def _f0_sd_semitones(features: Dict[str, float]) -> float:
        """Разброс F0 в полутонах; для старых записей - оценка по SD и среднему в Гц"""
        f0_sd_st = features.get('f0_sd_semitones')
        if f0_sd_st:
            return float(f0_sd_st)
        f0_mean = features.get('f0_mean_hz', 0.0)
        f0_sd = features.get('f0_sd_hz', 0.0)
        if f0_mean > 0 and f0_sd > 0:
            return float(12.0 * np.log2(1.0 + f0_sd / f0_mean))
        return 0.0
    
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
        Обновлены пороги согласно исследованиям (Little 2004):
        - Норма: Jitter 0.2-0.7%, Shimmer 2-4%, HNR 20-25 dB
        - Патология: Jitter >1.5-3%, Shimmer >6-12%, HNR <12-18 dB
        """
        jitter = features.get('jitter_percent', 0.0)
        shimmer = features.get('shimmer_percent', 0.0)
        hnr = features.get('hnr_db', 25.0)
        
        # Комбинированная оценка с обновленными порогами
        score = 0
        
        # Jitter: норма 0.2-0.7%, патология >1.5-3%
        # Порог 1.5%, выраженный >2.5%
        if jitter > 2.5:
            score += 2
        elif jitter > 1.5:
            score += 1
        
        # Shimmer: норма 2-4%, патология >6-12%
        # Порог 6%, выраженный >9%
        if shimmer > 9.0:
            score += 2
        elif shimmer > 6.0:
            score += 1
        
        # HNR: норма 20-25 dB, патология <12-18 dB
        # Порог 18 dB, выраженный <15 dB
        if hnr < 15.0:
            score += 2
        elif hnr < 18.0:
            score += 1
        
        return min(score, 3)  # Максимум 3
    
    def _score_articulation(self, features: Dict[str, float]) -> int:
        """
        Оценка неточной артикуляции
        
        Признаки: медленная речь, высокий процент пауз
        Обновлен порог скорости речи: <4.5 сл/сек
        """
        rate = features.get('rate_syl_sec', 4.5)
        pause_ratio = features.get('pause_ratio', 0.0)
        
        score = 0
        
        # Скорость речи: <4.5 сл/сек - порог, <3.0 - выраженная
        if rate < 3.0:
            score += 2
        elif rate < 4.5:
            score += 1
        
        if pause_ratio > 0.40:
            score += 2
        elif pause_ratio > 0.30:
            score += 1
        
        return min(score, 3)  # Максимум 3
    
    def _count_exceeded_thresholds(self, features: Dict[str, float]) -> List[str]:
        """Подсчет признаков, превышающих пороговые значения"""
        exceeded = []
        
        # Jitter (>2.0%)
        jitter_val = features.get('jitter_percent', 0)
        if jitter_val > self.THRESHOLDS['jitter_percent']:
            # Игнорируем аномально высокие значения (возможные артефакты расчета)
            if jitter_val < 50.0:  # Разумный максимум для jitter
                exceeded.append('jitter')
        
        # Shimmer (>10.0%)
        shimmer_val = features.get('shimmer_percent', 0)
        if shimmer_val > self.THRESHOLDS['shimmer_percent']:
            # Игнорируем аномально высокие значения (возможные артефакты расчета)
            # Shimmer >50% обычно указывает на проблему с расчетом, а не на реальную патологию
            if shimmer_val < 50.0:  # Разумный максимум для shimmer
                exceeded.append('shimmer')
        
        # HNR (<20dB)
        hnr_val = features.get('hnr_db', 25)
        if hnr_val < self.THRESHOLDS['hnr_db']:
            # Игнорируем аномально низкие значения (возможные артефакты)
            if hnr_val > 5.0:  # Разумный минимум для HNR
                exceeded.append('hnr')
        
        # Разброс F0 (monopitch) в полутонах
        f0_sd_st = self._f0_sd_semitones(features)
        if 0.0 < f0_sd_st < self.MONOPITCH_SEMITONES[1]:
            exceeded.append('f0_variability')
        
        # Rate (артикуляция) - <4.5 сл/сек
        if features.get('rate_syl_sec', 5) < self.THRESHOLDS['rate_syl_sec']:
            exceeded.append('rate')
        
        # Pause ratio
        if features.get('pause_ratio', 0) > self.THRESHOLDS['pause_ratio']:
            exceeded.append('pause_ratio')
        
        # Amplitude variation
        if features.get('amplitude_db_variation', 10) < self.THRESHOLDS['amplitude_db_variation']:
            exceeded.append('amplitude_variation')
        
        return exceeded
    
    def _calculate_risk_probability(self, exceeded_thresholds: List[str], 
                                   symptom_scores: Dict[str, int],
                                   features: Dict[str, float]) -> float:
        """
        Расчет вероятности риска ПД на основе признаков
        
        Используется упрощенная модель на основе:
        - Количества превышенных порогов
        - Тяжести симптомов
        - Весов признаков (Little 2004, Daoudi 2022)
        """
        num_exceeded = len(exceeded_thresholds)
        
        # Базовая вероятность от количества превышенных порогов
        # Согласно требованиям: Low <70%, Medium 70-89%, High ≥89%
        # Более консервативный подход для снижения ложных срабатываний
        base_prob = 0.0
        if num_exceeded == 0:
            base_prob = 0.20  # 20% для здоровых (Low Risk <70%)
        elif num_exceeded == 1:
            base_prob = 0.45  # 45% при 1 признаке (Low Risk <70%)
        elif num_exceeded == 2:
            base_prob = 0.70  # 70% при 2 признаках (Medium Risk 70-89%)
        elif num_exceeded >= 3:
            # Высокий риск только при ≥3 признаках И значительных отклонениях
            # Проверяем, насколько сильно превышены пороги
            jitter_val = features.get('jitter_percent', 0)
            shimmer_val = features.get('shimmer_percent', 0)
            hnr_val = features.get('hnr_db', 25)
            
            # Если отклонения незначительные, снижаем базовую вероятность
            # Согласно исследованиям: патология Jitter >2.5%, Shimmer >9%, HNR <15 dB
            significant_deviations = 0
            if jitter_val > 2.5:  # Значительное превышение (патология >2.5%)
                significant_deviations += 1
            if shimmer_val > 9.0:  # Значительное превышение (патология >9%)
                significant_deviations += 1
            if hnr_val < 15.0:  # Значительное снижение (патология <15 dB)
                significant_deviations += 1
            
            # Если есть ≥3 признака, но отклонения незначительные - Medium Risk
            if significant_deviations >= 2:
                base_prob = 0.89  # 89% при ≥3 признаках с значительными отклонениями
            else:
                base_prob = 0.75  # 75% при ≥3 признаках, но незначительных отклонениях (Medium Risk)
        
        # Корректировка на основе тяжести симптомов (более консервативная)
        severe_symptoms = sum(1 for score in symptom_scores.values() if score >= 3)
        moderate_symptoms = sum(1 for score in symptom_scores.values() if score == 2)
        
        # Увеличение вероятности при тяжелых симптомах (меньшие корректировки)
        if severe_symptoms >= 2:
            base_prob = min(base_prob + 0.05, 0.95)
        elif severe_symptoms >= 1:
            base_prob = min(base_prob + 0.03, 0.95)
        
        if moderate_symptoms >= 2:
            base_prob = min(base_prob + 0.03, 0.95)
        elif moderate_symptoms >= 1:
            base_prob = min(base_prob + 0.01, 0.95)
        
        # Корректировка на основе конкретных признаков (веса из исследований)
        # Более консервативные корректировки
        weight_adjustment = 0.0
        
        # Jitter и Shimmer - важные признаки
        # Согласно исследованиям: патология Jitter >2.5%, Shimmer >9%
        if 'jitter' in exceeded_thresholds:
            jitter_val = features.get('jitter_percent', 0)
            if jitter_val > 2.5:  # Значительная патология (>2.5%)
                weight_adjustment += 0.04
            elif jitter_val > 1.5:  # Порог превышен (>1.5%)
                weight_adjustment += 0.02
        
        if 'shimmer' in exceeded_thresholds:
            shimmer_val = features.get('shimmer_percent', 0)
            if shimmer_val > 9.0:  # Значительная патология (>9%)
                weight_adjustment += 0.04
            elif shimmer_val > 6.0:  # Порог превышен (>6%)
                weight_adjustment += 0.02
        
        # HNR - важный признак
        # Согласно исследованиям: патология HNR <15 dB
        if 'hnr' in exceeded_thresholds:
            hnr_val = features.get('hnr_db', 25)
            if hnr_val < 15.0:  # Значительная патология (<15 dB)
                weight_adjustment += 0.04
            elif hnr_val < 18.0:  # Порог превышен (<18 dB)
                weight_adjustment += 0.02
        
        # Комбинированная вероятность (максимум 95% для консервативности)
        final_prob = min(base_prob + weight_adjustment, 0.95)
        
        # Критически важно: для здоровых людей вероятность должна быть <70%
        if num_exceeded == 0 and severe_symptoms == 0 and moderate_symptoms == 0:
            # Для абсолютно здоровых людей вероятность 15-40%
            final_prob = max(final_prob, 0.15)
            final_prob = min(final_prob, 0.40)  # Гарантируем Low Risk (<70%)
        
        # Если только 1 признак и нет тяжелых симптомов - тоже Low Risk
        elif num_exceeded == 1 and severe_symptoms == 0 and moderate_symptoms == 0:
            final_prob = min(final_prob, 0.65)  # Максимум 65% (Low Risk <70%)
        
        # Если 2 признака, но отклонения незначительные и нет симптомов - тоже Low Risk
        elif num_exceeded == 2 and severe_symptoms == 0 and moderate_symptoms == 0:
            # Проверяем, насколько значительны отклонения
            jitter_val = features.get('jitter_percent', 0)
            shimmer_val = features.get('shimmer_percent', 0)
            hnr_val = features.get('hnr_db', 25)
            
            # Если отклонения незначительные (близки к порогам), снижаем риск
            # Согласно исследованиям: незначительные отклонения - близки к порогам
            # Jitter <2.5%, Shimmer <9%, HNR >15dB - незначительные отклонения
            significant = 0
            if jitter_val > 2.5:  # Значительная патология
                significant += 1
            if shimmer_val > 9.0:  # Значительная патология
                significant += 1
            if hnr_val < 15.0:  # Значительная патология
                significant += 1
            
            # Если менее 2 значительных отклонений - Low Risk
            if significant < 2:
                final_prob = min(final_prob, 0.68)  # Low Risk даже при 2 признаках
        
        return final_prob
    
    def _assess_pd_risk(self, exceeded_thresholds: List[str], 
                       symptom_scores: Dict[str, int],
                       features: Dict[str, float]) -> Dict[str, any]:
        """
        Оценка риска ПД на основе вероятностной модели
        
        Согласно требованиям:
        - Low Risk: <70% вероятность
        - Medium Risk: 70-89% вероятность
        - High Risk: ≥89% вероятность (только при ≥3 признаках, превышающих пороги)
        
        По Daoudi 2022: ≥3 признака превышают пороги -> 89% точность
        """
        num_exceeded = len(exceeded_thresholds)
        
        # Расчет вероятности риска
        risk_probability = self._calculate_risk_probability(exceeded_thresholds, symptom_scores, features)
        
        # Определение уровня риска согласно требованиям:
        # Low Risk: <70% probability
        # Medium Risk: 70-89% probability (1-2 features deviated, AUC 0.8-0.9)
        # High Risk: ≥89% probability (≥3 features exceeded thresholds)
        # ДОПОЛНИТЕЛЬНАЯ ПРОВЕРКА: для здоровых людей с нормальными признаками - всегда Low Risk
        severe_symptoms = sum(1 for score in symptom_scores.values() if score >= 3)
        moderate_symptoms = sum(1 for score in symptom_scores.values() if score == 2)
        
        # Если все признаки в норме или только незначительные отклонения - Low Risk
        if num_exceeded == 0:
            risk_level = "Low"
            confidence = max(risk_probability, 0.20)
            risk_probability = min(risk_probability, 0.65)  # Гарантируем <70%
        elif num_exceeded == 1 and severe_symptoms == 0 and moderate_symptoms == 0:
            # Один признак отклонен, но нет симптомов - Low Risk
            risk_level = "Low"
            confidence = risk_probability
            risk_probability = min(risk_probability, 0.68)  # Гарантируем <70%
        elif risk_probability >= 0.89 and num_exceeded >= 3:
            # Высокий риск только при ≥3 признаках и вероятности ≥89%
            risk_level = "High"
            confidence = min(risk_probability, 0.95)
        elif risk_probability >= 0.70:
            # Средний риск при вероятности 70-89%
            risk_level = "Medium"
            confidence = risk_probability
        else:
            # Низкий риск при вероятности <70%
            risk_level = "Low"
            confidence = max(risk_probability, 0.20)
        
        # Текст для обратной совместимости. Процент не показываем: модель
        # эвристическая и не откалибрована на реальных пациентах.
        if risk_level == "High":
            # Высокий риск: ≥89% и ≥3 признака
            risk_text = f"Высокий (признаков вне нормы: {num_exceeded}, это не диагноз)"
        elif risk_level == "Medium":
            # Средний риск: 70-89%
            risk_text = f"Умеренный (признаков вне нормы: {num_exceeded}, это не диагноз)"
        else:
            # Низкий риск: <70%
            risk_text = f"Низкий (признаков вне нормы: {num_exceeded}, это не диагноз)"
        
        return {
            'risk_probability': round(risk_probability, 3),
            'risk_level': risk_level,
            'risk_text': risk_text,  # Для обратной совместимости
            'confidence': round(confidence, 3),
            'num_exceeded': num_exceeded
        }
    
    def _generate_report(self, features: Dict[str, float],
                        symptom_scores: Dict[str, int],
                        exceeded_thresholds: List[str]) -> List[str]:
        """Генерация текстового отчета"""
        report = []
        
        # Гипофония
        if (symptom_scores['hypophonia'] or 0) > 0:
            severity = ['', 'легкая', 'умеренная', 'тяжелая'][symptom_scores['hypophonia']]
            tilt = features.get('spectral_tilt_db', 0.0)
            decay = features.get('loudness_decay_db', 0.0)
            report.append(
                f"- Гипофония ({severity}): слабое голосовое усилие "
                f"(наклон спектра {tilt:.1f} дБ, затухание громкости {decay:.1f} дБ), типично для ПД."
            )
        
        # Monopitch
        if (symptom_scores['monopitch'] or 0) > 0:
            severity = ['', 'легкий', 'умеренный', 'тяжелый'][symptom_scores['monopitch']]
            f0_sd = features.get('f0_sd_hz', 0.0)
            f0_sd_st = self._f0_sd_semitones(features)
            report.append(
                f"- Monopitch ({severity}): низкая вариация F0 ({f0_sd_st:.1f} полутона, "
                f"SD={f0_sd:.1f} Гц), бедная интонация встречается при ПД [Daoudi 2022]."
            )
        
        # Monoloudness
        if (symptom_scores['monoloudness'] or 0) > 0:
            severity = ['', 'легкая', 'умеренная', 'тяжелая'][symptom_scores['monoloudness']]
            db_var = features.get('amplitude_db_variation', 0.0)
            report.append(
                f"- Monoloudness ({severity}): вариация амплитуды {db_var:.1f}dB, "
                f"монотонная громкость [Little 2004]."
            )
        
        # Hoarseness
        if (symptom_scores['hoarseness'] or 0) > 0:
            severity = ['', 'легкая', 'умеренная', 'тяжелая'][symptom_scores['hoarseness']]
            jitter = features.get('jitter_percent', 0.0)
            shimmer = features.get('shimmer_percent', 0.0)
            hnr = features.get('hnr_db', 0.0)
            
            details = []
            if jitter > 2.0:  # >2%
                details.append(f"jitter={jitter:.2f}%")
            if shimmer > 10.0:  # >10%
                details.append(f"shimmer={shimmer:.2f}%")
            if hnr < 20.0:
                details.append(f"HNR={hnr:.1f}dB")
            
            report.append(
                f"- Охриплость ({severity}): нестабильность голосовых связок ({', '.join(details)}) "
                f"[Little 2004, Daoudi 2022]."
            )
        
        # Артикуляция
        if (symptom_scores['imprecise_articulation'] or 0) > 0:
            severity = ['', 'легкая', 'умеренная', 'тяжелая'][symptom_scores['imprecise_articulation']]
            rate = features.get('rate_syl_sec', 0.0)
            pause_ratio = features.get('pause_ratio', 0.0)
            report.append(
                f"- Неточная артикуляция ({severity}): темп {rate:.1f} слогов/сек без пауз, "
                f"паузы {pause_ratio*100:.1f}% [NIH 2025]."
            )
        
        # Общие рекомендации
        if len(exceeded_thresholds) >= 3:
            report.append(
                "- Несколько показателей вне нормы. Это не диагноз: если есть жалобы на голос "
                "или речь, стоит показаться врачу (фониатру или неврологу)."
            )
        elif len(exceeded_thresholds) >= 1:
            report.append(
                "- Отдельные показатели вне нормы. Это не диагноз; имеет смысл повторить запись."
            )
        
        # Если нет симптомов
        if not report:
            report.append(
                "- Акустические параметры в пределах нормы."
            )
        
        return report