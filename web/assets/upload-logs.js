/* Administrator-only structured events; all server strings are rendered as text. */
(() => {
    const labels = {snapshot:'Снимок состояния', created:'Загрузка создана', part_received:'Часть получена',
        submitted:'Передано в обработку', cancelled:'Загрузка отменена', rejected:'Запрос отклонён',
        assembling:'Сборка WAV', queued:'Ожидание анализа', analyzing:'Анализ', completed:'Результат сохранён', failed:'Ошибка обработки'};
    const states = {uploading:'Отправка', assembling:'Сборка WAV', queued:'В очереди', analyzing:'Анализ',
        completed:'Готово', failed:'Ошибка', cancelled:'Отменено'};
    const stages = {...states, creating:'Создание загрузки', validating:'Проверка разметки',
        cancelling:'Отмена', status:'Проверка состояния', processing:'Обработка', verifying:'Проверка сохранения'};
    const byId = id => document.getElementById(id);
    let cursor = null, events = [], busy = false, activeQuery = '', activeErrors = false;
    function text(tag, value, className) {
        const node = document.createElement(tag); node.textContent = value;
        if (className) node.className = className;
        return node;
    }
    const date = value => new Date(value * 1000).toLocaleString();
    function render(item) {
        const row = document.createElement('tr');
        if (item.event === 'failed' || item.http_status >= 400) row.className = 'diagnostic-error';
        const when = document.createElement('td');
        when.append(text('div', date(item.created_at)), text('small', [item.platform, item.browser].filter(Boolean).join(' · ') || 'Устройство не сохранено', 'text-muted'));
        const account = text('td', item.login || (item.user_id ? `Учётная запись #${item.user_id}` : 'Сессия не определена'));
        const upload = document.createElement('td');
        if (item.upload_id) {
            const filter = text('button', item.upload_id, 'diagnostic-id'); filter.type = 'button';
            filter.title = 'Показать всю историю этой загрузки';
            filter.addEventListener('click', () => {if (busy) return; byId('log-query').value = item.upload_id; byId('log-errors').checked = false; load(true);});
            upload.append(filter);
        } else upload.append(text('span', 'Загрузка ещё не создана'));
        if (item.current_state) upload.append(text('small', 'Сейчас: ' + (states[item.current_state] || item.current_state), 'd-block text-muted'));
        if (item.result_id) {
            if (item.current_state === 'completed') {
                const link = text('a', item.result_id, 'd-block diagnostic-id');
                link.href = AudioPark.url('/visualization?study=' + encodeURIComponent(item.result_id)); upload.append(link);
            } else upload.append(text('small', item.result_id, 'd-block text-muted diagnostic-id'));
        }
        const info = document.createElement('td');
        info.append(text('strong', labels[item.event] || item.event));
        const detail = [stages[item.stage] || item.stage, item.http_status ? `HTTP ${item.http_status}` : null];
        if (item.part_number !== null) detail.push(`Часть ${item.part_number + 1}`);
        if (item.byte_count !== null) detail.push(`${(item.byte_count / 1048576).toFixed(2)} МиБ`);
        if (item.elapsed_seconds !== null) detail.push(`${item.elapsed_seconds.toFixed(1)} с`);
        info.append(text('small', detail.filter(Boolean).join(' · '), 'd-block text-muted'));
        if (item.message) info.append(text('div', item.message));
        if (item.event === 'snapshot' && item.current_state === 'uploading') {
            info.append(text('small', `Принято частей: ${item.received_parts}, ${(item.received_bytes / 1048576).toFixed(1)} МиБ. Последняя активность: ${date(item.last_activity)}.`, 'd-block text-muted'));
        }
        row.append(when, account, upload, info); return row;
    }
    async function load(reset) {
        if (busy) return;
        busy = true; byId('log-refresh').disabled = true; byId('log-more').disabled = true;
        const status = byId('log-status'); status.textContent = 'Загрузка журнала…';
        const query = reset ? byId('log-query').value.trim() : activeQuery;
        const errors = reset ? byId('log-errors').checked : activeErrors;
        try {
            const params = new URLSearchParams({q:query, errors:errors ? '1' : '0'});
            if (!reset && cursor) params.set('before', cursor);
            const response = await fetch(AudioPark.url('/api/admin/upload-logs') + '?' + params);
            const data = await response.json();
            if (!response.ok) throw new Error(data.error || 'Не удалось загрузить журнал');
            if (reset) {events = []; byId('log-events').replaceChildren(); activeQuery = query; activeErrors = errors;}
            for (const item of data.events) {events.push(item); byId('log-events').append(render(item));}
            cursor = data.next_before;
            byId('log-more').hidden = !data.has_more;
            byId('log-copy').disabled = !events.length;
            byId('upload-counts').textContent = 'Все загрузки: ' + Object.entries(states).map(([state, label]) => `${label.toLowerCase()} — ${data.upload_counts[state] || 0}`).join(' · ');
            status.textContent = events.length ? `Показано событий: ${events.length}` : 'По выбранным условиям событий нет.';
        } catch (error) {status.textContent = error.message;}
        finally {busy = false; byId('log-refresh').disabled = false; byId('log-more').disabled = false;}
    }
    document.addEventListener('DOMContentLoaded', () => {
        byId('log-filters').addEventListener('submit', event => {event.preventDefault(); load(true);});
        byId('log-more').addEventListener('click', () => load(false));
        byId('log-copy').addEventListener('click', async () => {
            try {
                await navigator.clipboard.writeText(JSON.stringify({exported_at:new Date().toISOString(), events}, null, 2));
                byId('log-status').textContent = 'Журнал скопирован. Его можно приложить к сообщению об ошибке.';
            } catch (_) {byId('log-status').textContent = 'Браузер не разрешил копирование. Разрешите доступ к буферу обмена и повторите.';}
        });
        AudioPark.ready.then(() => load(true)).catch(error => {byId('log-status').textContent = error.message;});
    });
})();
