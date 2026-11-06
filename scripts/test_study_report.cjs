const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');
const context = vm.createContext({});
vm.runInContext(fs.readFileSync(path.join(__dirname, '../web/assets/study-report.js'), 'utf8'), context);
const render = result => context.renderStudyReport(result);
const result = {
    dsi: {dsi_score: -2.32, approximate: true, uncertainty: 1.3,
        dsi_range: 'Выраженные нарушения голоса', i_low_method: 'self_reference',
        dsi_breakdown: {mpt_sec: 12.05, f0_high_hz: 240.3, i_low_db: 65, jitter_percent: 0.56}},
    quality: {verdict: 'warning', issues: [{message: 'Повышенный шум <test>'}]},
    calibration: {sweep_detected: true, noise_floor_dbfs: -62.5},
    report: ['⚠️ Качество записи снижено', '- Повышенный шум <test>', '- Охриплость', '\n=== DSI ===', 'микрофон не откалиброван']
};
const html = render(result);
assert(html.includes('-2.3'));
assert(html.includes('± 1.3 · оценка'));
assert(html.includes('Тестовый сигнал: обнаружен'));
assert(html.includes('Повышенный шум &lt;test&gt;'));
assert(!html.includes('<test>'));
assert(!html.includes('микрофон не откалиброван'));
assert.equal((html.match(/<li>Охриплость<\/li>/g) || []).length, 1);
assert.equal((html.match(/<li>Повышенный шум/g) || []).length, 1);
const legacy = render({dsi: {dsi_score: null, reason: '<нет данных>'}});
assert(legacy.includes('DSI не рассчитан'));
assert(legacy.includes('&lt;нет данных&gt;'));
assert(legacy.includes('нельзя установить, проводилась ли она'));
assert(!legacy.includes('NaN'));
const calibrated = render({dsi: {dsi_score: 2, approximate: false}});
assert(!calibrated.includes('±'));
assert(!html.includes('Как получена оценка'));
assert(!html.includes('Тишина и тестовый звук помогают'));
assert(render({...result, quality: {verdict: 'reject'}}).includes('Повторите запись: её качество'));
assert(html.includes('<summary>Точность и проверка микрофона'));
assert(!html.includes('<details open'));
assert(!html.includes('Низкий (признаков вне нормы'));
console.log('study report: approximate/calibrated/missing results, legacy calibration, escaping and deduplication: ok');
if (process.argv.includes('--preview')) {
    const demo = {...result, quality: {verdict: 'warning', issues: [{message: 'Повышенный шум (SNR 30 dB): jitter, shimmer и HNR менее надёжны.'}]},
        dsi: {...result.dsi, interpretation: {mpt_status: 'Снижен', f0_high_status: 'Низкий', i_low_status: 'Повышен', jitter_status: 'Нормальный'}},
        report: [
            '⚠️ Качество записи снижено, часть значений может быть неточной.',
            '- Повышенный шум (SNR 30 dB): jitter, shimmer и HNR менее надёжны.',
            '- Охриплость (лёгкая): HNR 17.5 дБ.',
            '- Неточная артикуляция (лёгкая): темп 3.8 слогов/сек без пауз, паузы 28.3%.',
            '- Отдельные показатели вне нормы. Имеет смысл повторить запись.',
            '\n=== DSI (Dysphonia Severity Index) ===',
            'DSI (оценка): -2.3 ± 1.3',
            'Параметры:',
            '  - MPT: 12.05 с (Снижен)',
            '  - F0-High: 240.3 Гц (Низкий)',
            '  - I-Low: 65.0 дБ SPL (оценка) (Повышен)',
            '  - Jitter: 0.56% (Нормальный)',
            'DSI = 0.13 × MPT + 0.0053 × F0-High − 0.26 × I-Low − 1.18 × Jitter(%) + 12.4',
            'DSI оценивает качество голоса, а не болезнь Паркинсона, и не является диагнозом.',
            'Абсолютная громкость в дБ SPL не откалибрована. Самый тихий голос оценён по обычной громкости «а», принятой примерно за 70 дБ SPL на расстоянии 30 см. Неопределённость DSI из-за пересчёта громкости — примерно ±1.3.',
            '\n=== Речевые задания ===',
            '- Долгая «а» (MPT): 12.1 с, лучшая из 3',
            '- Чтение текста: 52 с, темп 3.8 слог/с, паузы 28%, разброс тона 6.8 полутона',
            '- Слоги, свистящие и шипящие (са-ша-за-жа): 2.7 слог/с, неровность 44%',
            '- Слоги, сонорные (ла-ра-ра-ла): 2.5 слог/с, неровность 45%',
            '- Слоги, звонкие и глухие (па-ба-па, та-да-та, ка-га-ка): 2.3 слог/с, неровность 38%',
            '- Слоги, твёрдые и мягкие (са-ся, ла-ля, та-тя): 2.1 слог/с, неровность 42%',
            '- Слоги, тонус и воздушный поток (ша ша ша, са са са, са-сы-су-се): 2.1 слог/с, неровность 13%',
            '- Темп фраз, слог/с: медленно 2.3, обычно 4.6, быстро 4.7',
            '- Интонация вопросов: размах тона 23.2 полутона'
        ]};
    const preview = render(demo);
    const fullText = preview.split('<div class="study-report-full">')[1].split('</div>')[0];
    // The expanded report must retain every source line through the final speech task.
    for (const line of demo.report) assert(fullText.includes(line), `Missing report line: ${line}`);
    const css = fs.readFileSync(path.join(__dirname, '../web/assets/theme.css'), 'utf8');
    const destination = path.join(__dirname, '../output/study-report-preview.html');
    fs.mkdirSync(path.dirname(destination), {recursive: true});
    fs.writeFileSync(destination, `<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Предпросмотр отчёта</title><style>${css}\nbody{padding:40px 20px;}main{max-width:980px;margin:auto;}p{margin-top:0;line-height:1.6;}h2,h3,h4{font-weight:500;}</style><main>${preview}<p style="font-size:12px;color:#78838e">Предпросмотр оформления · демонстрационные данные</p></main></html>`);
    console.log(destination);
}
