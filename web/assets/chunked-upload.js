/* WAV transport only: keep every byte and the recorder's original timeline. */
(() => {
    const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
    const hash = async blob => Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', await blob.arrayBuffer())), b => b.toString(16).padStart(2, '0')).join('');
    const api = path => AudioPark.url(path);
    async function json(path, options = {}, retry = true) {
        for (let attempt = 0; ; attempt++) {
            try {
                const response = await fetch(api(path), {...options, keepUploadOnAuthFailure: true});
                const isJSON = (response.headers.get('Content-Type') || '').includes('application/json');
                let data = null;
                if (isJSON) { try { data = await response.json(); } catch (_) { /* handled below */ } }
                if (!response.ok) {
                    const error = new Error([401, 403].includes(response.status)
                        ? 'Восстановите вход в отдельной вкладке, затем повторите отправку. Запись доступна для скачивания.'
                        : data?.error || `Сервер вернул ошибку ${response.status}. Повторите позже или скачайте запись.`);
                    error.status = response.status;
                    throw error;
                }
                if (!data) throw Object.assign(new Error('Непонятный ответ сервера. Повторите позже.'), {status: 502});
                return data;
            } catch (error) {
                if (!retry || attempt >= 3 || (error.status && ![408, 429, 500, 502, 503, 504].includes(error.status))) throw error;
                await sleep(500 * 2 ** attempt);
            }
        }
    }
    async function dbOperation(mode, operation) {
        const db = await new Promise((resolve, reject) => {
            const request = indexedDB.open('audio-park-uploads', 1);
            request.onupgradeneeded = () => request.result.createObjectStore('drafts');
            request.onsuccess = () => resolve(request.result);
            request.onerror = () => reject(request.error);
        });
        try {
            return await new Promise((resolve, reject) => {
                const tx = db.transaction('drafts', mode);
                const request = operation(tx.objectStore('drafts'));
                tx.oncomplete = () => resolve(request.result);
                tx.onerror = tx.onabort = () => reject(tx.error || new Error('Локальное хранилище недоступно'));
            });
        } finally { db.close(); }
    }
    let draft = null, busy = false, localAvailable = true;
    const key = () => String(AudioPark.user.id);
    async function persist() {
        try { await dbOperation('readwrite', store => store.put(draft, key())); }
        catch (_) {
            localAvailable = false;
            document.getElementById('upload-local-warning').textContent = 'Восстановление после закрытия вкладки недоступно. Скачайте исходную запись перед выходом.';
        }
    }
    function render() {
        document.getElementById('upload-recovery').hidden = !draft;
        document.getElementById('upload-resume').disabled = busy;
        document.getElementById('upload-cancel').disabled = busy;
    }
    function download(blob, filename) {
        const url = URL.createObjectURL(blob), link = document.createElement('a');
        link.href = url; link.download = filename; link.click();
        setTimeout(() => URL.revokeObjectURL(url), 60000);
    }
    async function makeDraft(input, config) {
        const files = {}, blobs = {}, manifest = {version: 1, files, device_info: input.deviceInfo, calibration: input.calibration || {}};
        let number = 0;
        for (const [name, blob] of Object.entries({recording: input.wav, calibration: input.calibrationWav})) {
            if (!blob) continue;
            // Browser-generated PCM WAVs have the standard 44-byte header.
            const header = new DataView(await blob.slice(0, 44).arrayBuffer());
            const channels = header.getUint16(22, true), sample_rate = header.getUint32(24, true);
            const parts = [];
            for (let offset = 0; offset < blob.size; offset += config.part_bytes) {
                const part = blob.slice(offset, offset + config.part_bytes);
                parts.push({number: number++, size: part.size, sha256: await hash(part)});
            }
            files[name] = {format: 'pcm_s16le', channels, sample_rate, samples: header.getUint32(40, true) / (channels * 2), size: blob.size, sha256: await hash(blob), parts};
            blobs[name] = blob;
        }
        return {patientUuid: input.patientUuid, token: crypto.randomUUID(), manifest, blobs, uploadId: null};
    }
    async function clear() {
        // Do not report cleanup success if sensitive local data could not be removed.
        try { await dbOperation('readwrite', store => store.delete(key())); }
        catch (error) { if (localAvailable) throw error; }
        draft = null; render();
    }
    async function transmit(progress) {
        busy = true; render();
        try {
            await AudioPark.refreshAuth();
            if (!draft.uploadId) {
                const created = await json('/api/uploads', {method: 'POST', headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({version: 1, patient_uuid: draft.patientUuid, client_token: draft.token})});
                draft.uploadId = created.upload_id; await persist();
            }
            const path = `/api/uploads/${draft.uploadId}`;
            let status = await json(path);
            if (status.state === 'cancelled') throw new Error('Загрузка отменена. Скачайте запись или начните новое обследование.');
            if (status.state === 'uploading') {
                const accepted = new Map(status.parts.map(part => [part.number, part]));
                let bytes = 0, count = 0;
                const all = [], total = Object.values(draft.manifest.files).reduce((n, f) => n + f.size, 0);
                for (const [name, file] of Object.entries(draft.manifest.files)) {
                    let offset = 0;
                    for (const part of file.parts) {
                        const blob = draft.blobs[name].slice(offset, offset + part.size); offset += part.size;
                        const saved = accepted.get(part.number);
                        if (saved && (saved.sha256 !== part.sha256 || saved.size !== part.size)) throw new Error('Принятая часть не совпадает с локальной записью');
                        if (saved) { bytes += part.size; count++; }
                        else all.push({part, blob});
                    }
                }
                const report = () => progress('uploading', `Сохранено частей: ${count}; ${(bytes / 1048576).toFixed(1)} из ${(total / 1048576).toFixed(1)} МиБ`, bytes / total);
                report();
                let cursor = 0, failure = null;
                const send = async () => {
                    while (!failure && cursor < all.length) {
                        const {part, blob} = all[cursor++];
                        try {
                            await json(`${path}/parts/${part.number}`, {method: 'PUT', headers: {'Content-Type': 'application/octet-stream', 'X-Part-Size': String(part.size), 'X-Part-SHA256': part.sha256}, body: blob});
                            bytes += part.size; count++; report();
                        } catch (error) { failure = error; }
                    }
                };
                await Promise.all([send(), send()]);
                if (failure) throw failure;
                await json(`${path}/complete`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(draft.manifest)});
            }
            const labels = {assembling: 'Сборка и проверка WAV', queued: 'Ожидание анализа', analyzing: 'Анализ обследования'};
            for (;;) {
                status = await json(path);
                if (status.state === 'completed') {
                    const study = status.study_id;
                    await clear();
                    return {study_id: study};
                }
                if (status.state === 'failed') throw new Error(status.error || 'Ошибка анализа');
                progress(status.state, labels[status.state] || 'Ожидание обработки', 1);
                await sleep(2000);
            }
        } finally { busy = false; render(); }
    }
    const client = window.ChunkedUpload = {json, hash, makeDraft,
        config: () => json('/api/uploads/config'),
        async start(input, config, progress) {
            await client.ready;
            if (draft) throw new Error('Есть незавершённая отправка. Используйте «Продолжить отправку» или скачайте и отмените её.');
            const size = input.wav.size + (input.calibrationWav?.size || 0);
            if (size > config.max_bytes || input.duration > config.max_seconds) {
                download(input.wav, 'examination.wav');
                throw new Error('Превышен безопасный лимит записи. Исходный WAV предложен для скачивания.');
            }
            draft = await makeDraft(input, config); render();
            try {
                const quota = await navigator.storage?.estimate();
                if (quota && quota.quota - quota.usage < size * 1.1) throw new Error('quota');
                await persist();
            } catch (_) {
                localAvailable = false;
                document.getElementById('upload-local-warning').textContent = 'Недостаточно места для восстановления после закрытия вкладки. Скачайте исходную запись.';
            }
            return transmit(progress);
        }
    };
    window.addEventListener('focus', () => { if (AudioPark.user) AudioPark.refreshAuth().catch(() => {}); });
    document.addEventListener('DOMContentLoaded', () => {
        client.ready = (async () => {
            await AudioPark.ready;
            try { draft = await dbOperation('readonly', store => store.get(key())); }
            catch (_) { localAvailable = false; }
            render();
        })();
        document.getElementById('upload-download').addEventListener('click', () => {
            if (draft) for (const [name, blob] of Object.entries(draft.blobs)) download(blob, `${name === 'recording' ? 'examination' : name}.wav`);
        });
        document.getElementById('upload-cancel').addEventListener('click', async () => {
            if (busy || !draft || !confirm('Отменить загрузку и удалить локальную копию записи?')) return;
            try {
                if (draft.uploadId) await json(`/api/uploads/${draft.uploadId}/cancel`, {method: 'POST'});
                await clear();
            } catch (error) { document.getElementById('upload-message').textContent = error.message; }
        });
        document.getElementById('upload-resume').addEventListener('click', async () => {
            if (busy || !draft) return;
            try {
                const result = await transmit((state, text) => { document.getElementById('upload-message').textContent = text; });
                location.assign(AudioPark.url(`/visualization?study=${encodeURIComponent(result.study_id)}`));
            } catch (error) { document.getElementById('upload-message').textContent = error.message; }
        });
    });
})();
