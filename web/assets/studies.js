document.addEventListener('DOMContentLoaded', async () => {
    const all = location.pathname.replace(/\/$/, '').endsWith('/all-results');
    const error = document.querySelector('#study-error');
    const ownerFilter = document.querySelector('#owner-filter');
    const recalculate = document.querySelector('#recalculate-btn');
    const params = () => {
        const query = new URLSearchParams();
        if (all) query.set('scope', 'all');
        for (const [id, key] of [['study-search','q'],['owner-filter','owner_id'],['date-from','date_from'],['date-to','date_to']]) {
            const value = document.getElementById(id).value.trim(); if (value) query.set(key, value);
        }
        return query.toString();
    };
    async function load() {
        error.hidden = true;
        document.querySelector('#results-count').textContent = 'Загрузка…';
        try {
            const query = params();
            const response = await fetch(AudioPark.url('/api/results?' + query));
            const data = await response.json(); if (!response.ok) throw new Error(data.error);
            const body = document.querySelector('#study-list'); body.replaceChildren();
            for (const study of data.results) {
                const row = document.createElement('tr');
                const verdict = study.quality?.verdict;
                const values = [study.study_id || '—', study.patient_id || '—', new Date(study.user_info?.timestamp).toLocaleString('ru-RU'), study.user_info?.tg_username || '—', study.dsi?.dsi_score?.toFixed(2) ?? '—', {ok:'Пригодна',warning:'Есть замечания',reject:'Перезаписать'}[verdict] || '—'];
                values.forEach((value, index) => { const cell = document.createElement('td'); cell.textContent = value; if (index === 0) cell.className = 'study-id'; row.append(cell); });
                const cell = document.createElement('td');
                const actions = document.createElement('div'); actions.className = 'study-actions'; cell.append(actions);
                const link = document.createElement('a');
                link.className = 'btn btn-sm btn-outline-secondary'; link.textContent = 'Открыть';
                link.href = AudioPark.url('/visualization?study=' + encodeURIComponent(study.study_id)); actions.append(link);
                const archive = document.createElement('a');
                archive.className = 'btn btn-sm btn-outline-primary'; archive.textContent = 'Скачать ZIP';
                archive.href = AudioPark.url('/api/archive/' + encodeURIComponent(study.study_id || study._index));
                archive.setAttribute('download', '');
                archive.title = 'Аудио, калибровка, задания и результаты анализа';
                actions.append(archive);
                const remove = document.createElement('button'); remove.type = 'button';
                remove.className = 'btn btn-sm btn-outline-danger'; remove.textContent = 'Удалить';
                remove.addEventListener('click', async () => {
                    if (!confirm(`Удалить исследование ${study.study_id} пациента «${study.patient_id || 'не указан'}»? Оно исчезнет из списка, результаты и запись станут недоступны. Локальные копии в браузере сохранятся.`)) return;
                    remove.disabled = true; error.hidden = true;
                    try {
                        const response = await fetch(AudioPark.url('/api/results/' + encodeURIComponent(study.study_id)), {method: 'DELETE', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({confirmed: true})});
                        const data = await response.json();
                        if (!response.ok) throw new Error(data.error || 'Не удалось удалить исследование');
                        await load();
                    } catch (exception) { error.textContent = exception.message; error.hidden = false; }
                    finally { remove.disabled = false; }
                });
                actions.append(remove); row.append(cell); body.append(row);
            }
            document.querySelector('#results-count').textContent = `Найдено: ${data.count}`;
            document.querySelector('#empty-results').hidden = data.count !== 0;
            document.querySelector('#studies-table').hidden = data.count === 0;
            for (const link of document.querySelectorAll('.export-link')) link.href = AudioPark.url(`/api/export/${link.dataset.format}?${query}`);
        } catch (exception) { error.textContent = exception.message; error.hidden = false; document.querySelector('#results-count').textContent = ''; }
    }
    try {
        await AudioPark.ready;
        if (all) {
            document.querySelector('#page-title').textContent = 'Все исследования';
            document.querySelector('#page-description').textContent = 'Обследования всех пользователей';
            ownerFilter.hidden = false; recalculate.hidden = false;
            const response = await fetch(AudioPark.url('/api/users')); const data = await response.json();
            if (!response.ok) throw new Error(data.error);
            for (const user of data.users) { const option = document.createElement('option'); option.value = user.id; option.textContent = user.login; ownerFilter.append(option); }
        }
        document.querySelector('#study-filters').addEventListener('submit', event => {event.preventDefault(); load();});
        document.querySelector('#refresh-btn').addEventListener('click', load);
        recalculate.addEventListener('click', async () => {
            if (!confirm('Пересчитать все исследования с текущими алгоритмами? Это может занять несколько минут.')) return;
            recalculate.disabled = true; recalculate.textContent = 'Выполняется пересчёт…'; error.hidden = true;
            try {const response = await fetch(AudioPark.url('/api/recalculate-all'), {method:'POST'}); const data = await response.json(); if(!response.ok) throw new Error(data.error); await load(); alert(data.message);}
            catch(exception){error.textContent=exception.message; error.hidden=false;}
            finally {recalculate.disabled=false; recalculate.textContent='Пересчитать все';}
        });
        await load();
    } catch (exception) { error.textContent = exception.message; error.hidden = false; }
});
