# Откуда берутся NaN значения в анализе аудио

## Основные источники NaN

### 1. **Извлечение F0 (основной частоты) - librosa.pyin()**

**Проблема:** `librosa.pyin()` возвращает `NaN` для кадров, где не удалось определить основную частоту.

```python
f0 = librosa.pyin(audio, fmin=50, fmax=500)
f0_values = f0[0]  # Может содержать NaN!
```

**Когда это происходит:**
- Тишина в аудио (нет сигнала)
- Шум без четкой периодичности
- Переходы между звуками
- Очень тихие сегменты

**Решение:** Фильтрация NaN перед использованием:
```python
f0_values = f0_values[~np.isnan(f0_values)]  # Удаляем NaN
```

---

### 2. **Математические операции с пустыми массивами**

**Проблема:** `np.mean([])`, `np.std([])`, `np.median([])` возвращают `NaN`.

```python
if len(f0_values) > 0:
    f0_mean = np.mean(f0_values)  # OK
else:
    f0_mean = np.mean([])  # ❌ Вернет NaN!
```

**Решение:** Проверка длины перед вычислениями:
```python
if len(f0_values) > 0:
    f0_mean = float(np.mean(f0_values))
else:
    f0_mean = 0.0  # Значение по умолчанию
```

---

### 3. **Деление на ноль или очень маленькие числа**

**Проблема:** Деление на ноль или очень маленькое число может дать `inf`, который затем становится `NaN`.

```python
periods = 1.0 / f0_values  # Если f0_values содержит 0 → inf
jitter = np.mean(period_diff) / np.mean(periods)  # Если mean(periods) = 0 → NaN
```

**Примеры:**
- `1.0 / 0` → `inf`
- `inf - inf` → `NaN`
- `inf / inf` → `NaN`

**Решение:** Защита от деления на ноль:
```python
mean_period = np.mean(periods)
if mean_period > 0 and len(period_diff) > 0:
    jitter = (np.mean(period_diff) / mean_period) * 100
else:
    jitter = 0.0
```

---

### 4. **Логарифм от нуля или отрицательного числа**

**Проблема:** `np.log10(0)` = `-inf`, `np.log10(-1)` = `NaN`.

```python
rms_db = 20 * np.log10(rms_frames)  # Если rms_frames содержит 0 → -inf
```

**Решение:** Добавление малого значения (epsilon):
```python
rms_frames_safe = rms_frames + 1e-10  # Защита от нуля
rms_db = 20 * np.log10(rms_frames_safe)
rms_db = rms_db[np.isfinite(rms_db)]  # Фильтруем inf и NaN
```

---

### 5. **Операции с inf значениями**

**Проблема:** Арифметические операции с `inf` могут дать `NaN`.

```python
# Примеры:
inf - inf → NaN
inf / inf → NaN
0 * inf → NaN
```

**Решение:** Проверка на `isfinite()`:
```python
if np.isfinite(value):
    # Используем значение
else:
    # Заменяем на безопасное значение
    value = 0.0
```

---

### 6. **Статистические функции с пустыми данными**

**Проблема:** `np.percentile()`, `np.std()` с пустыми массивами или только NaN.

```python
f0_high = np.percentile(f0_values_clean, 98)  # Если массив пустой → NaN
```

**Решение:** Проверка перед вычислением:
```python
if len(f0_values_clean) > 0:
    f0_high = np.percentile(f0_values_clean, 98)
    if not np.isfinite(f0_high):
        f0_high = 0.0
else:
    f0_high = 0.0
```

---

### 7. **Конвертация numpy типов в Python float**

**Проблема:** `float(np.nan)` остается `NaN`, который не сериализуется в JSON.

```python
value = float(np.nan)  # Все еще NaN!
json.dumps({"value": value})  # ❌ Ошибка!
```

**Решение:** Проверка перед конвертацией:
```python
if np.isfinite(value):
    result = float(value)
else:
    result = 0.0  # Или None
```

---

### 8. **Parselmouth может возвращать NaN**

**Проблема:** Parselmouth (Praat) может вернуть `NaN` для некоторых операций.

```python
pitch = sound.to_pitch_ac(time_step=0.01)
f0_values = pitch.selected_array['frequency']  # Может содержать NaN
```

**Решение:** Фильтрация после получения:
```python
f0_values = f0_values[f0_values > 0]  # Убираем 0 и NaN
f0_values = f0_values[np.isfinite(f0_values)]  # Дополнительная проверка
```

---

## Типичные сценарии появления NaN

### Сценарий 1: Тихая запись
```
Аудио → Очень тихий сигнал → RMS ≈ 0 → log10(0) → -inf → NaN в расчетах
```

### Сценарий 2: Отсутствие вокализации
```
Аудио → Нет голоса → librosa.pyin() → все NaN → mean([]) → NaN
```

### Сценарий 3: Очень короткий сегмент
```
Сегмент → < 0.5 сек → Мало данных → Статистика неустойчива → NaN
```

### Сценарий 4: Шум без периодичности
```
Аудио → Только шум → Нет F0 → NaN в F0 → NaN в jitter/shimmer
```

---

## Как предотвратить NaN

### 1. **Всегда проверяйте данные перед вычислениями:**

```python
def safe_mean(values):
    if len(values) == 0:
        return 0.0
    values_clean = [v for v in values if np.isfinite(v)]
    if len(values_clean) == 0:
        return 0.0
    result = np.mean(values_clean)
    return float(result) if np.isfinite(result) else 0.0
```

### 2. **Используйте защиту от деления на ноль:**

```python
denominator = value + 1e-10  # Малое значение вместо 0
result = numerator / denominator
```

### 3. **Фильтруйте NaN сразу после получения:**

```python
f0_values = f0_values[~np.isnan(f0_values)]  # Удаляем NaN
f0_values = f0_values[np.isfinite(f0_values)]  # Удаляем inf тоже
```

### 4. **Используйте функцию очистки перед JSON:**

```python
def clean_json_values(obj):
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return 0.0  # Или None
    # ... рекурсивная обработка
```

### 5. **Проверяйте результаты перед сохранением:**

```python
result = calculate_feature()
if not np.isfinite(result):
    result = default_value  # Значение по умолчанию
```

---

## Где в коде уже есть защита

✅ **feature_extractor.py:**
- Строка 730: `f0_values = f0_values[~np.isnan(f0_values)]`
- Строка 385: `rms_db = rms_db[np.isfinite(rms_db)]`
- Строка 395: Проверка `if not np.isfinite(db_variation)`

✅ **parkinson_analyzer.py:**
- Функция `_clean_json_values()` очищает NaN перед сохранением

✅ **api.py:**
- Функция `clean_json_values()` очищает NaN перед отправкой
- Фильтрация в `/api/visualization/` эндпоинте

---

## Рекомендации

1. **Всегда фильтруйте NaN сразу после получения данных**
2. **Используйте `np.isfinite()` для проверки**
3. **Проверяйте длину массивов перед статистическими операциями**
4. **Используйте epsilon (1e-10) для защиты от деления на ноль**
5. **Очищайте данные перед сериализацией в JSON**

---

## Пример правильной обработки

```python
# ❌ ПЛОХО:
f0_values = librosa.pyin(audio)[0]
f0_mean = np.mean(f0_values)  # Может быть NaN!

# ✅ ХОРОШО:
f0 = librosa.pyin(audio, fmin=50, fmax=500)
f0_values = f0[0]
f0_values = f0_values[~np.isnan(f0_values)]  # Фильтруем NaN
f0_values = f0_values[f0_values > 0]  # Убираем невалидные

if len(f0_values) > 0:
    f0_mean = float(np.mean(f0_values))
    if not np.isfinite(f0_mean):
        f0_mean = 0.0
else:
    f0_mean = 0.0
```
