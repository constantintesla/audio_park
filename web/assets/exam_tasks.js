// Задания обследования: что просим сделать (для записи на главной и для блоков «задание — запись» в результатах)

// Упражнения DSI (Wuyts 2000) кроме долгой «а»: она общая с речевыми заданиями (MPT)
const DSI_STEPS = [
    {
        task: 'glide', tries: 2,
        title: 'Голос вверх до самой высокой ноты',
        text: 'Начните «а» низким удобным голосом и плавно поднимайтесь как можно выше, как сирена вверх. ' +
              'Можно уйти в фальцет (тонкий голос). 2 попытки, засчитается самая высокая нота.',
        say: '«А-а-а↗» снизу вверх до самой высокой ноты'
    },
    {
        task: 'soft', tries: 3,
        title: 'Самый тихий голос',
        text: 'Скажите «а» так тихо, как можете, но голосом, а не шепотом. Держите телефон или ноутбук на том же расстоянии (20–30 см). ' +
              '3 попытки по 2–3 секунды, засчитается самая тихая.',
        say: '«а-а-а» очень тихо, но не шепотом'
    },
    {
        task: 'vowel', tries: 1,
        title: 'Спокойная «а»',
        text: 'Скажите «а» обычной громкостью, как в спокойном разговоре, ровно, 4–5 секунд. Держите устройство на том же расстоянии (30 см). ' +
              'По этой записи оценивается стабильность голоса (jitter), а еще она служит меркой громкости для самого тихого голоса.',
        say: '«А-а-а-а» спокойно, 4–5 секунд'
    }
];

// Речевые задания из документа «Задания по голосу». В items - по одной фразе на попытку,
// *слово* - выделенное слово (логическое ударение)
const SPEECH_STEPS = [
    {
        task: 'mpt', tries: 3,
        title: 'Долгая «а» (время максимальной фонации)',
        text: 'Сделайте нормальный вдох, а затем произнесите звук «а» так долго и так ровно, как сможете, на вашей обычной громкости. ' +
              'Когда понадобится вдохнуть, просто остановитесь и нажмите «Готово». 3 попытки, засчитается лучшая.',
        say: '«А-а-а-а-а…» ровно, обычной громкостью'
    },
    {
        task: 'reading', tries: 1,
        title: 'Чтение текста',
        text: 'Нажмите «Начать попытку» и прочитайте текст с выражением. В конце нажмите «Готово».',
        say: 'Буря на реке\n' +
             'В тихом селе жил рыбак Григорий. Как-то раз бежал он к реке и медленно, тихо бормотал: «Боже, дай удачи!» ' +
             'Вдруг взревел ветер, грянул гром, ударила молния. Григорий громко крикнул: «Вот это буря! Берегитесь!»\n' +
             'Он быстро закричал рыбакам: «Плывите, гребите, не зевайте! Скорее — к берегу!» Волны бились. ' +
             'Рыбаки воскликнули: «Кто нам поможет?». Тут примчались соседи: «Мы здесь! Держитесь!»\n' +
             'Буря утихла. Григорий улыбнулся и задумчиво сказал: «Жизнь — как река, полна вихрей».'
    },
    {
        task: 'syl_sibilant', tries: 1,
        title: 'Слоги: свистящие и шипящие',
        text: 'Произнесите слоги в обычном темпе и повторите всю серию 3 раза подряд.',
        say: 'са — ша — за — жа'
    },
    {
        task: 'syl_sonorant', tries: 1,
        title: 'Слоги: сонорные',
        text: 'Произнесите слоги в обычном темпе и повторите всю серию 3 раза подряд.',
        say: 'ла — ра — ра — ла'
    },
    {
        task: 'syl_voicing', tries: 1,
        title: 'Слоги: звонкие и глухие',
        text: 'Произнесите слоги в обычном темпе, каждую тройку по одному разу.',
        say: 'па — ба — па, та — да — та, ка — га — ка'
    },
    {
        task: 'syl_palatal', tries: 1,
        title: 'Слоги: твердые и мягкие',
        text: 'Произнесите пары слогов в обычном темпе и повторите всю серию 2 раза подряд.',
        say: 'са — ся, ла — ля, та — тя'
    },
    {
        task: 'syl_airflow', tries: 1,
        title: 'Слоги: тонус и воздушный поток',
        text: 'Произнесите слоги в обычном темпе, четко, по порядку.',
        say: 'ша ша ша\nса са са\nса — сы — су — се'
    },
    {
        task: 'phrase_slow',
        title: 'Фразы: читайте медленно',
        text: 'Прочитайте фразу медленно. Одна попытка — одна фраза.',
        items: ['Сегодня и нам необходимо вдумчиво и планомерно обсудить все детали.',
                'Тёплый солнечный луч медленно скользил по подоконнику тихой комнаты.',
                'Капля дождя повисла на кончике листа и долго не решалась упасть.']
    },
    {
        task: 'phrase_fast',
        title: 'Фразы: читайте быстро',
        text: 'Прочитайте фразу быстро, но разборчиво. Одна попытка — одна фраза.',
        items: ['Он быстро закрыл дверь, схватил ключи и выбежал наружу.',
                'Поезд резко остановился, пассажиры встали и зашевелились.',
                'Она включила телефон, прочитала сообщение и начала быстро собираться на утренний рейс.']
    },
    {
        task: 'phrase_normal',
        title: 'Фразы: читайте в обычном темпе',
        text: 'Прочитайте фразу в обычном темпе. Одна попытка — одна фраза.',
        items: ['Утром город просыпался, наполняясь звуками машин и голосами людей.',
                'Она закрыла книгу и задумалась о прочитанном.',
                'Вечером мы вышли на прогулку и долго разговаривали.']
    },
    {
        task: 'intonation',
        title: 'Интонация: вопрос к выделенному слову',
        text: 'Задайте вопрос, выделив голосом подчеркнутое слово, затем прочитайте ответ.',
        items: ['*Сергей* купил фрукты? — Да, Сергей.',
                'Сергей *купил* фрукты? — Нет, их купил Вячеслав.',
                'Сергей купил *фрукты*? — Нет, он купил овощи.']
    }
];

function escapeStepText(text) {
    return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/\*([^*]+)\*/g, '<strong class="step-emphasis">$1</strong>');
}

const EXAM_STEPS = Object.fromEntries([...SPEECH_STEPS, ...DSI_STEPS].map(step => [step.task, step]));

// Блок «задание — запись»: каждое задание с инструкцией и записями его попыток
// (послушать и скачать), плюс вся запись и архив прогона. Попытки (task_recordings)
// присылает сервер, см. task_recordings.py. API_URL объявлен на каждой странице
function renderTaskRecordings(result) {
    const index = encodeURIComponent(result.study_id ?? result._index ?? '');
    const files = (result.raw_data || {}).files || {};
    if (index === undefined || index === null || !(files.original_audio || files.processed_audio)) return '';
    const base = `${API_URL}/api/audio/${index}`;
    const groups = [];
    (result.task_recordings || []).forEach(item => {
        let group = groups.find(g => g.task === item.task);
        if (!group) groups.push(group = {task: item.task, name: item.name, items: []});
        group.items.push(item);
    });
    const tasks = groups.map((group, i) => {
        const step = EXAM_STEPS[group.task] || {};
        const say = step.items ? '' : (step.say || '');
        const attempts = group.items.map(item => {
            const phrase = step.items && step.items[item.attempt - 1];
            const label = step.items ? `Фраза ${item.attempt}` : `Попытка ${item.attempt}`;
            return `
                <li class="task-rec-try">
                    <div class="task-rec-try-head">
                        <span class="task-rec-try-label">${label}<span class="text-muted"> · ${item.duration_sec} с</span></span>
                        ${item.value ? `<span class="task-rec-value">${item.value}</span>` : ''}
                        ${item.best ? '<span class="table-badge risk-low">засчитана</span>' : ''}
                    </div>
                    ${phrase ? `<p class="task-rec-phrase">${escapeStepText(phrase)}</p>` : ''}
                    <div class="task-rec-player">
                        <audio controls preload="none" src="${base}/task/${item.n}"></audio>
                        <a class="btn btn-sm btn-outline-secondary" href="${base}/task/${item.n}?download=1">Скачать</a>
                    </div>
                </li>`;
        }).join('');
        return `
            <section class="task-rec">
                <div class="task-rec-head">
                    <span class="task-rec-num">${i + 1}</span>
                    <div>
                        <h6>${step.title || group.name}</h6>
                        ${say ? `<p class="task-rec-say">${escapeStepText(say).replace(/\n/g, '<br>')}</p>` : ''}
                    </div>
                </div>
                <ul class="task-rec-tries">${attempts}</ul>
            </section>`;
    }).join('');
    const count = groups.length
        ? `${groups.length} ${plural(groups.length, 'задание', 'задания', 'заданий')}, ${result.task_recordings.length} ${plural(result.task_recordings.length, 'запись', 'записи', 'записей')}`
        : 'Запись без заданий';
    return `
        <div class="card mt-3 mb-3 task-rec-card">
            <div class="card-header d-flex justify-content-between align-items-center flex-wrap gap-2">
                <div>
                    <h5 class="mb-0">Задания и записи</h5>
                    <small class="text-muted">${count}</small>
                </div>
                <a class="btn btn-sm btn-primary" href="${API_URL}/api/archive/${index}"
                   title="ZIP: вся запись, калибровка, записи заданий и results.json">Скачать архив прогона</a>
            </div>
            <div class="card-body">
                <div class="task-rec-full">
                    <span class="task-rec-try-label">Вся запись</span>
                    <audio controls preload="none" src="${base}"></audio>
                </div>
                ${tasks}
            </div>
        </div>`;
}

function plural(n, one, few, many) {
    const mod10 = n % 10, mod100 = n % 100;
    if (mod10 === 1 && mod100 !== 11) return one;
    if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return few;
    return many;
}
