// Карточка результатов речевых заданий (speech_protocol.py). Общая для главной и /results
function renderSpeechProtocolCard(sp) {
    if (!sp) return '';
    const num = (v, digits = 1) => (v === null || v === undefined) ? '—' : Number(v).toFixed(digits);
    const row = (name, value, hint) => `
        <tr><td><strong>${name}</strong></td><td>${value}</td><td><small>${hint || ''}</small></td></tr>`;
    const rows = [];
    const mpt = sp.mpt || {};
    if (mpt.mpt_sec !== null && mpt.mpt_sec !== undefined) {
        rows.push(row('Долгая «а» (MPT), с', num(mpt.mpt_sec),
            `лучшая из ${(mpt.attempts || []).length}; у взрослых обычно 15–25 с и больше`));
    }
    const reading = sp.reading;
    if (reading) {
        rows.push(row('Чтение: темп, слог/с', num(reading.rate_syl_sec), `текст прочитан за ${num(reading.duration_sec, 0)} с`));
        rows.push(row('Чтение: паузы, %', num(100 * (reading.pause_ratio || 0), 0), ''));
        rows.push(row('Чтение: разброс тона, полутона', num(reading.f0_sd_st), 'меньше 2 — речь монотонная'));
    }
    (sp.syllables || []).forEach(item => {
        rows.push(row(`Слоги: ${item.name}`,
            item.rate_syl_sec !== null ? `${num(item.rate_syl_sec)} слог/с` : 'не распознаны',
            item.cv_percent !== null ? `неровность ${num(item.cv_percent, 0)}%` : ''));
    });
    const phrases = sp.phrases || {};
    [['slow', 'медленно'], ['normal', 'в обычном темпе'], ['fast', 'быстро']].forEach(([key, label]) => {
        const p = phrases[key];
        if (p && p.rate_syl_sec !== null) rows.push(row(`Фразы ${label}, слог/с`, num(p.rate_syl_sec), ''));
    });
    if (phrases.fast_to_slow_ratio) {
        rows.push(row('Во сколько раз «быстро» быстрее «медленно»', num(phrases.fast_to_slow_ratio, 2),
            'меньше 1.3 — темп почти не меняется'));
    }
    const intonation = sp.intonation;
    if (intonation && intonation.f0_range_st !== null) {
        rows.push(row('Интонация вопросов: размах тона, полутона', num(intonation.f0_range_st), 'меньше 4 — интонация бедная'));
    }
    const notes = (sp.notes || []).map(n => `<li>${n}</li>`).join('');
    const missing = (sp.missing || []).length
        ? `<p class="text-muted mb-2"><small>Не выполнены: ${sp.missing.join(', ')}</small></p>` : '';
    return `
        <div class="card mt-3 mb-3">
            <div class="card-header bg-primary text-white">
                <h5 class="mb-0">Речевые задания</h5>
            </div>
            <div class="card-body">
                <table class="table table-sm table-bordered">
                    <thead class="table-light"><tr><th>Показатель</th><th>Значение</th><th>Ориентир</th></tr></thead>
                    <tbody>${rows.join('')}</tbody>
                </table>
                ${notes ? `<ul class="mb-2">${notes}</ul>` : ''}
                ${missing}
                <div class="alert alert-info mb-0" role="alert">
                    <small>Интонация, громкость и темп в общем отчете оценены по чтению текста, охриплость — по спокойной «а».
                    Ориентиры примерные, это измерения, а не диагноз.</small>
                </div>
            </div>
        </div>`;
}

// Записи заданий, карточки DSI и речевых заданий. renderDsiCard объявлена на каждой странице,
// renderTaskRecordings - в exam_tasks.js
function renderTestCards(result) {
    const sp = result.speech_protocol;
    const recordings = renderTaskRecordings(result);
    // В записи только с речевыми заданиями DSI не измерялся
    if (sp && result.recording_task !== 'exam') return recordings + renderSpeechProtocolCard(sp);
    return recordings + renderDsiCard(result.dsi || {}) + renderSpeechProtocolCard(sp);
}
