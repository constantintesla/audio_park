// Exercise the actual transport client with a fake server and transactional IDB.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const crypto = require('node:crypto').webcrypto;
const data = new Map(), elements = new Map(), events = {};
let navigated = null;
let failPart = true, accepted = new Map(), complete = false, requests = 0, active = 0, peak = 0;
const idb = {open() {
    const request = {};
    setImmediate(() => { request.result = {close() {}, transaction() {
        const tx = {};
        tx.objectStore = () => ({
            get(key) { const r = {result: structuredClone(data.get(key))}; setImmediate(() => tx.oncomplete()); return r; },
            put(value, key) { data.set(key, structuredClone(value)); const r = {}; setImmediate(() => tx.oncomplete()); return r; },
            delete(key) { data.delete(key); const r = {}; setImmediate(() => tx.oncomplete()); return r; }
        }); return tx;
    }}; request.onsuccess(); }); return request;
}};
function element() { return {hidden: true, disabled: false, textContent: '', addEventListener(name, fn) { this[name] = fn; }}; }
const response = (status, body, type = 'application/json') => ({status, ok: status < 400, headers: {get: () => type}, json: async () => body});
const context = {console, crypto, Blob, DataView, Uint8Array, Map, Promise, Array, indexedDB: idb,
    setTimeout: (fn, ms) => setTimeout(fn, Math.min(ms, 1)),
    AudioPark: {url: p => p, user: {id: 1}, ready: Promise.resolve(), refreshAuth: async () => {}},
    navigator: {storage: {estimate: async () => ({quota: 1e9, usage: 0})}},
    document: {addEventListener(name, fn) { events[name] = fn; }, getElementById(id) { if (!elements.has(id)) elements.set(id, element()); return elements.get(id); }},
    fetch: async (url, options) => {
        requests++;
        if (url === '/html') return response(502, null, 'text/html');
        if (url === '/denied') return response(403, {});
        if (url === '/api/uploads') return response(201, {upload_id: 'id'});
        if (url.endsWith('/complete')) { complete = true; return response(202, {}); }
        if (url.includes('/parts/')) {
            active++; peak = Math.max(peak, active);
            await new Promise(r => setImmediate(r)); active--;
            const number = Number(url.split('/').pop());
            accepted.set(number, {number, size: options.body.size, sha256: options.headers['X-Part-SHA256']});
            if (number === 0 && failPart) { failPart = false; throw new TypeError('lost response after accepting'); }
            return response(200, {});
        }
        return response(200, {state: complete ? 'completed' : 'uploading', parts: [...accepted.values()], study_id: complete ? 'AP-test' : null});
    },
    location: {assign(url) { navigated = url; }}, confirm: () => true,
};
context.window = {addEventListener() {}};
vm.createContext(context);
vm.runInContext(fs.readFileSync(require('node:path').join(__dirname, '../web/assets/chunked-upload.js'), 'utf8'), context);
(async () => {
    events.DOMContentLoaded();
    const client = context.window.ChunkedUpload;
    await client.ready;
    const header = new ArrayBuffer(44), view = new DataView(header);
    view.setUint16(22, 1, true); view.setUint32(24, 48000, true); view.setUint32(40, 96000, true);
    const wav = new Blob([header, new Uint8Array(96000)]);
    const input = {wav, duration: 1, patientUuid: 'patient', deviceInfo: {task: 'exam'}};
    const config = {part_bytes: 32768, max_bytes: 1e8, max_seconds: 900};
    const draft = await client.makeDraft(input, config);
    assert.equal(draft.manifest.files.recording.sha256, await client.hash(wav));
    const progress = [];
    const result = await client.start(input, config, (state, text, fraction) => progress.push(fraction));
    assert.equal(result.study_id, 'AP-test');
    assert.equal(data.size, 0, 'Sensitive draft must be removed after completion');
    assert.equal(peak, 2, 'Bounded parallelism');
    assert.equal(accepted.size, 3);
    assert(progress.every((value, i) => i === 0 || value >= progress[i - 1]));
    // Simulate closing the tab: only persisted Blob, manifest and upload ID survive.
    draft.uploadId = 'id';
    data.set('1', structuredClone(draft));
    complete = false;
    accepted = new Map([[0, draft.manifest.files.recording.parts[0]]]);
    const restored = {...context, window: {addEventListener() {}}};
    vm.createContext(restored);
    vm.runInContext(fs.readFileSync(require('node:path').join(__dirname, '../web/assets/chunked-upload.js'), 'utf8'), restored);
    events.DOMContentLoaded();
    await restored.window.ChunkedUpload.ready;
    assert.equal(elements.get('upload-recovery').hidden, false);
    await elements.get('upload-resume').click();
    assert.match(navigated, /AP-test/);
    assert.equal(data.size, 0);
    // A different logged-in user must not receive the first user's draft.
    data.set('1', structuredClone(draft));
    const other = {...context, AudioPark: {...context.AudioPark, user: {id: 2}}, window: {addEventListener() {}}};
    vm.createContext(other);
    vm.runInContext(fs.readFileSync(require('node:path').join(__dirname, '../web/assets/chunked-upload.js'), 'utf8'), other);
    events.DOMContentLoaded();
    await other.window.ChunkedUpload.ready;
    assert.equal(elements.get('upload-recovery').hidden, true);
    assert(data.has('1'));
    // Lack of quota must leave the in-memory recording usable and warn clearly.
    data.clear();
    other.navigator = {storage: {estimate: async () => ({quota: 1, usage: 1})}};
    complete = false; accepted = new Map();
    await other.window.ChunkedUpload.start(input, config, () => {});
    assert.match(elements.get('upload-local-warning').textContent, /Недостаточно места/);
    requests = 0;
    await assert.rejects(client.json('/denied'), /Восстановите вход/);
    assert.equal(requests, 1, 'Never retry authorization errors');
    requests = 0;
    await assert.rejects(client.json('/html'), /Сервер вернул ошибку/);
    assert.equal(requests, 4, 'Transient retries must be bounded');
    console.log('upload client: hashes, lost response, two senders, confirmed progress, IDB cleanup, auth and HTML errors: ok');
})().catch(error => { console.error(error); process.exitCode = 1; });
