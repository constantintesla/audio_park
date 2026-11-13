/* Portable examination format v1. ZIP writer uses STORE; reader accepts STORE/DEFLATE. */
(() => {
    const MAX = 128 * 1024 * 1024, TOTAL = 256 * 1024 * 1024, COUNT = 256;
    const enc = new TextEncoder(), dec = new TextDecoder('utf-8', {fatal:true});
    const fail = message => { throw new Error(message); };
    const safe = name => /^[a-zA-Z0-9_./-]+$/.test(name) && !name.startsWith('/') && !name.split('/').some(p => !p || p === '.' || p === '..');
    const hash = async data => Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', data)), b => b.toString(16).padStart(2,'0')).join('');
    function crc(data) { let c = -1; for (const b of data) { c ^= b; for(let i=0;i<8;i++) c = (c >>> 1) ^ (0xedb88320 & -(c & 1)); } return (c ^ -1) >>> 0; }
    function header(size) { const bytes = new Uint8Array(size); return {bytes, view:new DataView(bytes.buffer)}; }
    function zip(files) {
        const parts=[], directory=[]; let offset=0, total=0;
        for (const [name, data] of files) {
            total += data.length; if(total > TOTAL || !safe(name)) fail('Превышен размер архива или недопустимое имя');
            const n=enc.encode(name), checksum=crc(data), h=header(30+n.length), d=header(46+n.length);
            h.view.setUint32(0,0x04034b50,true); h.view.setUint16(4,20,true);
            h.view.setUint32(14,checksum,true); h.view.setUint32(18,data.length,true); h.view.setUint32(22,data.length,true); h.view.setUint16(26,n.length,true); h.bytes.set(n,30);
            d.view.setUint32(0,0x02014b50,true); d.view.setUint16(4,20,true); d.view.setUint16(6,20,true);
            d.view.setUint32(16,checksum,true); d.view.setUint32(20,data.length,true); d.view.setUint32(24,data.length,true); d.view.setUint16(28,n.length,true); d.view.setUint32(42,offset,true); d.bytes.set(n,46);
            parts.push(h.bytes,data); directory.push(d.bytes); offset += h.bytes.length+data.length;
        }
        const end=header(22), size=directory.reduce((a,b)=>a+b.length,0);
        end.view.setUint32(0,0x06054b50,true); end.view.setUint16(8,files.size,true); end.view.setUint16(10,files.size,true); end.view.setUint32(12,size,true); end.view.setUint32(16,offset,true);
        const result = new Blob([...parts,...directory,end.bytes],{type:'application/zip'});
        if(result.size > MAX) fail('ZIP превышает 128 МиБ'); return result;
    }
    async function unzip(blob) {
        if(blob.size > MAX) fail('Архив превышает 128 МиБ');
        const bytes=new Uint8Array(await blob.arrayBuffer()), v=new DataView(bytes.buffer); let end=-1;
        for(let i=bytes.length-22;i>=Math.max(0,bytes.length-65557);i--) if(v.getUint32(i,true)===0x06054b50 && i+22+v.getUint16(i+20,true)===bytes.length) {end=i;break;}
        if(end<0) fail('Повреждён ZIP');
        const count=v.getUint16(end+10,true), out=new Map(); let p=v.getUint32(end+16,true), total=0;
        if(count>COUNT || v.getUint16(end+4,true) || v.getUint16(end+6,true) || v.getUint16(end+8,true)!==count || p+v.getUint32(end+12,true)!==end) fail('Неподдерживаемая структура ZIP');
        for(let i=0;i<count;i++) {
            if(p+46>end || v.getUint32(p,true)!==0x02014b50) fail('Повреждён каталог ZIP');
            const flags=v.getUint16(p+8,true), method=v.getUint16(p+10,true), checksum=v.getUint32(p+16,true), size=v.getUint32(p+24,true), packed=v.getUint32(p+20,true), n=v.getUint16(p+28,true), at=v.getUint32(p+42,true);
            if(p+46+n>end) fail('Повреждён каталог ZIP');
            const name=dec.decode(bytes.subarray(p+46,p+46+n));
            total+=size;
            if(!safe(name) || out.has(name) || flags & 1 || ![0,8].includes(method) || size>MAX || total>TOTAL || !/^(manifest\.json|results\.json|recording\.(wav|mp3|ogg|oga|webm|m4a|flac|aac)|calibration\.wav|tasks\/[\w-]+\.wav)$/.test(name)) fail('Недопустимое содержимое или превышены лимиты ZIP');
            if(at+30>p || v.getUint32(at,true)!==0x04034b50 || v.getUint16(at+8,true)!==method) fail('Повреждён файл ZIP');
            const start=at+30+v.getUint16(at+26,true)+v.getUint16(at+28,true);
            if(start+packed>p || dec.decode(bytes.subarray(at+30,at+30+v.getUint16(at+26,true)))!==name) fail('Повреждён файл ZIP');
            let data=bytes.slice(start,start+packed);
            if(method===8) {
                const reader=new Blob([data]).stream().pipeThrough(new DecompressionStream('deflate-raw')).getReader();
                const chunks=[]; let length=0;
                try { while(true) { const r=await reader.read(); if(r.done) break; length+=r.value.length; if(length>size) { await reader.cancel(); fail('Превышен размер распаковки'); } chunks.push(r.value); } }
                finally { reader.releaseLock(); }
                data=new Uint8Array(await new Blob(chunks).arrayBuffer());
            }
            if(data.length!==size || crc(data)!==checksum) fail('Контрольная сумма ZIP не совпадает');
            out.set(name,data); p+=46+n+v.getUint16(p+30,true)+v.getUint16(p+32,true);
        }
        if(p!==end) fail('Повреждён каталог ZIP'); return out;
    }
    const allowed=['task','sample_rate','input_format','mic_mode','noise_floor_dbfs','dsi_segments','protocol_segments'];
    function cleanInfo(info) { return Object.fromEntries(allowed.filter(k=>Object.hasOwn(info || {},k)).map(k=>[k,info[k]])); }
    function validate(info,duration,{incomplete=false}={}) {
        if(!info || typeof info!=='object' || !Number.isFinite(duration) || duration<=0 || duration>1800) fail('Недопустимая длительность записи (максимум 30 минут)');
        const tasks=new Set(Object.keys(EXAM_STEPS));
        for(const key of ['dsi_segments','protocol_segments']) {
            const segments=info[key] || [];
            if(!Array.isArray(segments) || segments.length>(key==='dsi_segments'?20:40)) fail('Недопустимая разметка заданий');
            let previous=0;
            for(const s of segments) {
                if(!s || !(key==='dsi_segments'?['mpt','glide','soft','vowel'].includes(s.task):tasks.has(s.task) && !['glide','soft','vowel'].includes(s.task)) || !Number.isFinite(s.start_sec) || !Number.isFinite(s.end_sec) || s.start_sec<previous || s.end_sec-s.start_sec<0.2 || s.end_sec>duration+0.002) fail('Границы заданий не согласованы с аудио');
                previous=s.end_sec;
            }
        }
        if(info.task==='exam') {
            const present=new Set([...(info.dsi_segments||[]),...(info.protocol_segments||[])].map(s=>s.task));
            const missing=[...tasks].filter(t=>!present.has(t));
            if(missing.length && !incomplete) fail('Для полного обследования отсутствует разметка: '+missing.join(', ')+'. Можно извлечь recording.wav и загрузить как отдельное задание.');
        } else if(!tasks.has(info.task) && !['dsi','speech_protocol'].includes(info.task)) fail('Выберите задание для аудиофайла');
    }
    async function exportExam(exam) {
        const files=new Map([['recording.wav',new Uint8Array(await exam.audio.arrayBuffer())]]);
        if(exam.calibrationWav) files.set('calibration.wav',new Uint8Array(await exam.calibrationWav.arrayBuffer()));
        const checksums={}; for(const [name,data] of files) checksums[name]=await hash(data);
        const manifest={format:'audio-park-exam',version:1,protocol_version:1,local_id:exam.id,incomplete:!!exam.incomplete,device_info:cleanInfo(exam.deviceInfo),calibration:exam.calibration,files:checksums};
        files.set('manifest.json',enc.encode(JSON.stringify(manifest))); return zip(files);
    }
    function parseJSON(bytes, name) {
        try { const data=JSON.parse(dec.decode(bytes)); if(!data || Array.isArray(data) || typeof data!=='object') fail(''); return data; }
        catch { fail('Некорректный '+name); }
    }
    async function importExam(blob, context) {
        const files=await unzip(blob); let manifest;
        if(files.has('manifest.json')) {
            manifest=parseJSON(files.get('manifest.json'),'manifest.json');
            if(manifest.format!=='audio-park-exam' || manifest.version!==1 || manifest.protocol_version!==1) fail('Неподдерживаемая версия обследования');
            if(!manifest.files || !Object.hasOwn(manifest.files,'recording.wav')) fail('В манифесте нет основной записи');
            for(const [name,digest] of Object.entries(manifest.files)) if(!files.has(name) || await hash(files.get(name))!==digest) fail('Файл отсутствует или повреждён: '+name);
            for(const name of files.keys()) if(name!=='manifest.json' && !Object.hasOwn(manifest.files,name)) fail('Файл не описан в манифесте: '+name);
        } else if(files.has('results.json')) {
            const result=parseJSON(files.get('results.json'),'results.json');
            manifest={device_info:{...result.device_info,task:result.device_info?.task || result.recording_task},calibration:result.calibration};
            if(!manifest.device_info?.task) fail('В старом архиве нет параметров задания. Извлеките recording и загрузите как отдельное задание.');
        } else fail('В архиве нет manifest.json или results.json');
        if(manifest.calibration && (typeof manifest.calibration!=='object' || Array.isArray(manifest.calibration) || JSON.stringify(manifest.calibration).length>20000)) fail('Некорректные данные калибровки');
        const name=[...files.keys()].find(n=>n.startsWith('recording.'));
        if(!name) fail('В архиве нет основной записи');
        const audio=new Blob([files.get(name)],{type:name.endsWith('.wav')?'audio/wav':'application/octet-stream'}), buffer=await context.decodeAudioData(await audio.arrayBuffer());
        const info=cleanInfo(manifest.device_info); validate(info,buffer.duration,{incomplete:manifest.incomplete===true});
        if(files.has('calibration.wav')) await context.decodeAudioData(files.get('calibration.wav').slice().buffer);
        return {id:crypto.randomUUID(),audio,buffer,incomplete:manifest.incomplete===true,deviceInfo:info,calibration:manifest.calibration || null,calibrationWav:files.has('calibration.wav')?new Blob([files.get('calibration.wav')],{type:'audio/wav'}):null};
    }
    async function db() {
        if(!globalThis.indexedDB) fail('Браузер не предоставляет локальное хранилище');
        return new Promise((resolve,reject)=>{
            let blocked=false;
            const r=indexedDB.open('audio-park-exams',2);
            r.onupgradeneeded=()=>{
                if(!r.result.objectStoreNames.contains('exams')) r.result.createObjectStore('exams',{keyPath:'key'});
                const exams=r.transaction.objectStore('exams');
                if(!exams.indexNames.contains('owner')) exams.createIndex('owner','owner');
                if(!r.result.objectStoreNames.contains('chunks')) r.result.createObjectStore('chunks',{keyPath:['key','offset']}).createIndex('exam','key');
            };
            r.onsuccess=()=>{if(blocked){r.result.close();return;}r.result.onversionchange=()=>r.result.close();resolve(r.result);};
            r.onerror=()=>reject(r.error);
            r.onblocked=()=>{blocked=true;reject(new Error('Закройте другие вкладки обследования и повторите сохранение'));};
        });
    }
    async function cache(action,exam) {
        const user=await AudioPark.ready; if(!user) fail('Требуется вход');
        const owner=String(user.id), key=exam && owner+':'+exam.id;
        if(exam?.owner && String(exam.owner)!==owner) fail('Обследование принадлежит другой учётной записи');
        const connection=await db();
        try { return await new Promise((resolve,reject)=>{
            const mode=['list','get','chunks','draft'].includes(action)?'readonly':'readwrite';
            let tx;
            try {tx=connection.transaction(['exams','chunks'],mode,{durability:'strict'});}
            catch(error) {if(error.name!=='TypeError') throw error;tx=connection.transaction(['exams','chunks'],mode);}
            const store=tx.objectStore('exams'), chunks=tx.objectStore('chunks'); let result, error;
            const removeChunks=()=>{
                const req=chunks.index('exam').openKeyCursor(IDBKeyRange.only(key));
                req.onsuccess=()=>{const cursor=req.result;if(cursor){chunks.delete(cursor.primaryKey);cursor.continue();}};
            };
            if(action==='list') {const r=store.index('owner').getAll(IDBKeyRange.only(owner));r.onsuccess=()=>{result=r.result;};}
            else if(action==='get') {const r=store.get(key);r.onsuccess=()=>{result=r.result;};}
            else if(action==='chunks') {const r=chunks.index('exam').getAll(IDBKeyRange.only(key));r.onsuccess=()=>{result=r.result.sort((a,b)=>a.offset-b.offset);};}
            else if(action==='draft') {
                result={};const r=store.get(key),c=chunks.index('exam').getAll(IDBKeyRange.only(key));
                r.onsuccess=()=>{result.exam=r.result;};c.onsuccess=()=>{result.chunks=c.result.sort((a,b)=>a.offset-b.offset);};
            }
            else if(action==='delete') {store.delete(key);removeChunks();}
            else if(action==='clear') {
                const r=store.index('owner').getAll(IDBKeyRange.only(owner));r.onsuccess=()=>{for(const e of r.result) {
                    store.delete(e.key);
                    const c=chunks.index('exam').openKeyCursor(IDBKeyRange.only(e.key));
                    c.onsuccess=()=>{if(c.result){chunks.delete(c.result.primaryKey);c.result.continue();}};
                }};
            } else if(action==='checkpoint') {
                const r=store.get(key);r.onsuccess=()=>{
                    const previous=r.result;
                    if((previous?.revision || 0)!==(exam.revision || 0) || (previous && !previous.recorder)) {
                        error=new Error('Эта запись уже изменена в другой вкладке. Скачайте копию текущей записи');tx.abort();return;
                    }
                    const {chunk,offset,...metadata}=exam;
                    result=(exam.revision || 0)+1;
                    if(chunk?.length) chunks.put({key,offset,data:chunk});
                    store.put({...metadata,version:1,owner,key,revision:result,updatedAt:new Date().toISOString()});
                };
            } else if(action==='put' || action==='finalize') {
                const {buffer,cachePending,expectedRevision,...metadata}=exam;
                const put=()=>{
                    store.put({...metadata,version:1,owner,key,updatedAt:new Date().toISOString()});
                    if(!exam.recorder) removeChunks();
                };
                if(action==='finalize') {
                    const r=store.get(key);r.onsuccess=()=>{
                        if(!r.result?.recorder || r.result.revision!==expectedRevision) {
                            error=new Error('Черновик изменён в другой вкладке. Скачайте текущую запись отдельным ZIP');tx.abort();return;
                        }
                        put();
                    };
                } else put();
            } else {error=new Error('Неизвестная операция хранилища');tx.abort();}
            tx.oncomplete=()=>resolve(result);
            tx.onerror=event=>{error ||= event.target.error || tx.error;};
            tx.onabort=()=>reject(error || tx.error || new Error('Локальное сохранение отменено'));
        }); } finally {connection.close();}
    }
    async function requestFingerprint(form) {
        // Match analysis_requests.py exactly, including Python's default JSON separators.
        const byKey=([a],[b])=>a<b?-1:a>b?1:0;
        const fields=[...form].filter(([,value])=>typeof value==='string').sort(byKey);
        const canonical=fields.filter(([key])=>key!=='patient_id' || !form.get('patient_uuid'));
        const parts=[enc.encode('['+canonical.map(([key,value])=>'['+JSON.stringify(key)+', '+JSON.stringify(value)+']').join(', ')+']')];
        for(const [key,file] of [...form].filter(([,value])=>typeof value!=='string').sort(byKey)) {
            parts.push(enc.encode(key),enc.encode(file.name || ''),file);
        }
        return hash(await new Blob(parts).arrayBuffer());
    }
    window.ExamFiles={MAX,validate,cleanInfo,exportExam,importExam,cache,zip,unzip,hash,requestFingerprint};
})();
