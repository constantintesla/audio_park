// Structured presentation of saved results, including older examinations.
function renderStudyReport(result) {
    const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    const num = (value, digits = 1) => typeof value === 'number' && Number.isFinite(value) ? value.toFixed(digits) : '—';
    const dsi = result.dsi || {}, quality = result.quality || {};
    const measured = typeof dsi.dsi_score === 'number' && Number.isFinite(dsi.dsi_score);
    const verdict = {ok:'Запись пригодна', warning:'Есть замечания к записи', reject:'Нужна повторная запись'}[quality.verdict] || 'Качество записи не указано';
    const issueMessages = (quality.issues || []).map(issue => issue.message);
    const issues = issueMessages.map(message => `<li>${esc(message)}</li>`).join('');
    const parameters = [
        ['mpt_sec', 'Длительность «а»', 'с', 'mpt_status', 2],
        ['f0_high_hz', 'Самая высокая нота', 'Гц', 'f0_high_status', 1],
        ['i_low_db', 'Самый тихий голос', 'дБ SPL', 'i_low_status', 1],
        ['jitter_percent', 'Нестабильность частоты', '%', 'jitter_status', 2]
    ].map(([key, name, unit, status, digits]) => `<div class="study-metric"><dt>${name}</dt>
        <dd>${num(dsi.dsi_breakdown?.[key], digits)} <small>${unit}${key === 'i_low_db' && dsi.approximate ? ' · оценка' : ''}</small></dd>
        <span>${esc(dsi.interpretation?.[status] || 'Нет оценки')}</span></div>`).join('');
    const report = result.report || [];
    const findings = [];
    for (const line of report) {
        if (line.includes('===') || line.trim().startsWith('DSI не рассчитан:')) break;
        const text = line.trim().replace(/^[-–]\s*/, '');
        if (!text || text.startsWith('⚠') || issueMessages.includes(text)) continue;
        findings.push(`<li>${esc(text)}</li>`);
    }
    const cal = result.calibration;
    const calibration = cal
        ? `Данные проверки перед записью сохранены. Тестовый сигнал: ${cal.sweep_detected === true ? 'обнаружен' : cal.sweep_detected === false ? 'не обнаружен' : 'нет данных'}. Уровень шума: ${num(cal.noise_floor_dbfs)} dBFS.`
        : 'В этом результате нет сохранённых данных проверки перед записью. По отчёту нельзя установить, проводилась ли она.';
    const fullReport = report.map(line => esc(line.replace('микрофон не откалиброван', 'абсолютная громкость в дБ SPL не откалибрована'))).join('\n');
    const limited = dsi.approximate || quality.verdict === 'warning' || quality.verdict === 'reject';
    const nextStep = quality.verdict === 'reject' ? 'Повторите запись: её качество не позволяет надёжно оценить показатели.'
        : quality.verdict === 'warning' ? 'Повторите запись в тихом помещении, чтобы проверить устойчивость результата.'
        : 'Сравнивайте показатели с предыдущими записями на том же устройстве.';
    return `<article class="study-report-card" aria-label="Результат обследования">
        <header class="study-report-heading"><div><p class="study-eyebrow">Результат обследования</p><h2>Голос и речь</h2></div>
            <span class="study-status ${limited ? 'is-limited' : ''}">${!measured ? 'Недостаточно данных' : limited ? 'Оценка с ограничениями' : 'Расчёт выполнен'}</span></header>
        <div class="study-report-body">
            <div class="study-report-overview">
                <section class="study-score-panel"><h3 class="study-report-label">Индекс голоса <span>DSI</span></h3>
                    <div class="study-report-score">${measured ? num(dsi.dsi_score, dsi.approximate ? 1 : 2) : '—'}
                        </div>
                    <p class="study-score-caption">${measured && dsi.approximate ? `± ${num(dsi.uncertainty)} · оценка` : measured ? 'По выполненным заданиям' : 'DSI не рассчитан'}</p>
                </section>
                <section class="study-conclusion"><p class="study-eyebrow">Что показывает расчёт</p>
                    <h3>${esc(measured ? dsi.dsi_range || 'Индекс рассчитан' : 'Пока недостаточно данных')}</h3>
                    <p>${!measured ? esc(dsi.reason || dsi.error || 'Для расчёта нужны измерения голосовых заданий.') : dsi.approximate ? 'Громкость оценена приблизительно. Это ограничивает точность индекса и его интерпретации, даже при успешной проверке микрофона.' : 'Индекс объединяет длительность звука, высоту и громкость голоса, а также стабильность частоты.'}</p>
                    <div class="study-next-step"><span aria-hidden="true">↗</span><p>${nextStep}</p></div>
                </section>
            </div>
            <p class="study-report-note">DSI описывает качество голоса. Он не определяет вероятность болезни Паркинсона и не является диагнозом.</p>
            ${dsi.dsi_breakdown ? `<section class="study-parameters"><h3 class="study-section-title">Показатели голоса</h3><dl class="study-metrics">${parameters}</dl></section>` : ''}
            <section class="study-quality-row ${quality.verdict === 'warning' || quality.verdict === 'reject' ? 'is-warning' : ''}">
                <span class="study-quality-dot" aria-hidden="true"></span><div><h3>${verdict}</h3>
                <p>${quality.verdict === 'ok' ? 'Замечаний к качеству не обнаружено.' : quality.verdict === 'reject' ? 'Показатели этой записи ненадёжны.' : quality.verdict === 'warning' ? 'Шум или другие особенности записи могут влиять на точность измерений.' : 'Данные оценки качества отсутствуют.'}</p></div>
            </section>
            ${findings.length ? `<section class="study-observations"><h3 class="study-section-title">Наблюдения по речи</h3><ul>${findings.join('')}</ul></section>` : ''}
            <details class="study-report-details"><summary>Точность и проверка микрофона<span aria-hidden="true">+</span></summary><div class="study-details-content">
                <h4>Проверка перед записью</h4><p>${esc(calibration)}</p>
                ${issues ? `<h4>Замечания к записи</h4><ul>${issues}</ul>` : ''}
                <p>Для сравнения результатов используйте то же устройство и расстояние до микрофона.</p>
            </div></details>
            ${fullReport ? `<details class="study-report-details"><summary>Полный текст расчёта<span aria-hidden="true">+</span></summary><div class="study-report-full">${fullReport}</div></details>` : ''}
        </div></article>`;
}
