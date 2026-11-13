// Real Chromium IndexedDB/reload tests. All HTTP traffic is a local fixture server.
const fs=require('node:fs'),path=require('node:path'),http=require('node:http');
const {spawn}=require('node:child_process');
const assert=require('node:assert/strict');
const root=path.resolve(__dirname,'..');
const chrome=process.env.CHROME_PATH || ['C:/Program Files/Google/Chrome/Application/chrome.exe','C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe','/usr/bin/google-chrome','/usr/bin/chromium','/usr/bin/chromium-browser'].find(p=>fs.existsSync(p));
if(!chrome) throw new Error('Set CHROME_PATH to a Chromium browser');
const backupRoot=path.join(root,'.local-backups');fs.mkdirSync(backupRoot,{recursive:true});
const profile=fs.mkdtempSync(path.join(backupRoot,'cache-browser-'));
let owner=1,authorized=true,uploads=0,mode='offline',receipts=new Map();
const patient={uuid:'patient-1',full_name:'Тестовый Пациент',local_number:1,study_count:0};
const server=http.createServer((req,res)=>{
    const url=new URL(req.url,'http://localhost');
    const json=(status,data)=>{res.writeHead(status,{'Content-Type':'application/json','Cache-Control':'no-store'});res.end(JSON.stringify(data));};
    if(url.pathname==='/api/auth/me') return json(authorized?200:401,authorized?{user:{id:owner,login:'test',role:'expert'},csrf_token:'fixture'}:{error:'Сессия истекла'});
    if(url.pathname==='/api/patients') return json(200,{patients:[patient]});
    if(url.pathname.startsWith('/api/analysis-requests/')) {
        if(!authorized) return json(401,{error:'Сессия истекла'});
        const key=owner+':'+url.pathname.split('/').pop();
        return receipts.has(key)?json(200,{study_id:receipts.get(key)}):json(404,{error:'Пока нет результата'});
    }
    if(url.pathname==='/api/analyze') {
        uploads++;
        let body='';req.on('data',c=>{body+=c.toString('latin1');});
        req.on('end',()=>{
            const id=body.match(/name="request_id"\r\n\r\n([^\r]+)/)?.[1];
            if(mode==='lost-response') receipts.set(owner+':'+id,'AP-test');
            if(mode!=='success') {
                // Interrupt a started response; Chromium may transparently retry an empty socket.
                res.writeHead(200,{'Content-Type':'application/json','Content-Length':'1000'});res.write('{');
                setImmediate(()=>req.socket.destroy());
            }
            else json(200,{study_id:'AP-test'});
        });return;
    }
    if(url.pathname==='/visualization') {res.end('<p>Saved result</p>');return;}
    const file=url.pathname==='/'?path.join(root,'web/index.html'):path.join(root,'web',url.pathname);
    if(!file.startsWith(path.join(root,'web')+path.sep) || !fs.existsSync(file)) {res.writeHead(404);res.end();return;}
    let data=fs.readFileSync(file);
    if(file.endsWith('index.html')) data=Buffer.from(data.toString().replace(/<script\b[^>]*src="https:[^"]+"[^>]*><\/script>/g,'').replace(/<link\b[^>]*href="https:[^"]+"[^>]*>/g,''));
    res.setHeader('Content-Type',file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':'text/html');res.end(data);
});
let browser,socket,seq=0;const pending=new Map();
async function cdp(method,params={}) {
    const id=++seq;
    const response=new Promise((resolve,reject)=>{const timer=setTimeout(()=>{pending.delete(id);reject(new Error('CDP timeout: '+method));},20000);pending.set(id,{resolve,reject,timer});});
    socket.send(JSON.stringify({id,method,params}));return response;
}
async function evaluate(expression) {
    const response=await cdp('Runtime.evaluate',{expression:`(async()=>{${expression}})()`,awaitPromise:true,returnByValue:true});
    if(response.exceptionDetails) throw new Error(response.exceptionDetails.exception?.description || response.exceptionDetails.text);
    return response.result.value;
}
async function open(base) {
    await cdp('Page.navigate',{url:base});
    for(let i=0;i<100;i++) {
        try {if(await evaluate(`return document.readyState==='complete' && typeof ExamFiles!=='undefined' && !!AudioPark.user;`)) break;} catch{}
        await new Promise(r=>setTimeout(r,30));
    }
    await evaluate(`await AudioPark.ready;await refreshLocalExams(); window.alert=message=>{window.lastAlert=message;};window.confirm=()=>true;`);
}
const assertBrowser=async expression=>{assert.equal(await evaluate(`return !!(${expression});`),true,expression);};
(async()=>{
    await new Promise(r=>server.listen(0,'127.0.0.1',r));
    const base=`http://127.0.0.1:${server.address().port}/`;
    browser=spawn(chrome,['--headless=new','--disable-gpu','--no-first-run','--no-default-browser-check','--autoplay-policy=no-user-gesture-required','--remote-debugging-port=0','--remote-allow-origins=*',`--user-data-dir=${profile}`,'about:blank'],{windowsHide:true,stdio:['ignore','ignore','pipe']});
    const debug=await new Promise((resolve,reject)=>{
        let stderr='';const timer=setTimeout(()=>reject(new Error('Chromium did not start: '+stderr.slice(-2000))),15000);
        browser.once('error',reject);browser.stderr.on('data',b=>{stderr+=b.toString();const m=stderr.match(/DevTools listening on (ws:\/\/[^\s]+)/);if(m){clearTimeout(timer);resolve(m[1]);}});
    });
    const debugURL=new URL(debug);
    const tabs=await (await fetch(`http://${debugURL.host}/json/list`)).json();
    socket=new WebSocket(tabs.find(t=>t.type==='page').webSocketDebuggerUrl);
    await new Promise((resolve,reject)=>{socket.onopen=resolve;socket.onerror=reject;});
    socket.onmessage=e=>{const m=JSON.parse(e.data),p=pending.get(m.id);if(p){clearTimeout(p.timer);pending.delete(m.id);m.error?p.reject(new Error(m.error.message)):p.resolve(m.result);}};
    await cdp('Page.enable');
    await cdp('Page.setDownloadBehavior',{behavior:'deny'});
    await cdp('Page.addScriptToEvaluateOnNewDocument',{source:"window.addEventListener('beforeunload',e=>e.stopImmediatePropagation(),true);"});
    await open(base);
    // Migrate a real version-1 database and preserve an older completed local copy.
    await evaluate(`await new Promise((resolve,reject)=>{const r=indexedDB.deleteDatabase('audio-park-exams');r.onsuccess=resolve;r.onerror=reject;});
        await new Promise((resolve,reject)=>{const r=indexedDB.open('audio-park-exams',1);r.onupgradeneeded=()=>r.result.createObjectStore('exams',{keyPath:'key'});r.onsuccess=()=>{
            const tx=r.result.transaction('exams','readwrite');tx.objectStore('exams').put({id:'legacy-recording-123456',key:'1:legacy-recording-123456',owner:'1',status:'saved',createdAt:new Date().toISOString(),audio:audioBufferToWav(audioContext.createBuffer(1,audioContext.sampleRate,audioContext.sampleRate),audioContext.sampleRate),deviceInfo:{task:'dsi',dsi_segments:[{task:'vowel',start_sec:0,end_sec:1}]}});
            tx.oncomplete=()=>{r.result.close();resolve();};tx.onerror=reject;
        };r.onerror=reject;});
        selectPatient(${JSON.stringify(patient)});document.getElementById('calibrate-check').checked=false;
        navigator.mediaDevices.getUserMedia=async()=>{const stream=audioContext.createMediaStreamDestination().stream,track=stream.getAudioTracks()[0];track.getSettings=()=>({deviceId:'fixture-microphone'});return stream;};
        await startRecording({test:'exam'});
        if(!rec) throw new Error(window.lastAlert || 'Recorder did not start');
        rec.processor.onaudioprocess=null;
        rec.chunks=[new Float32Array(rec.sr*4).fill(.05)];rec.nSamples=rec.sr*4;
        rec.test.segments=[{task:'mpt',start:0,end:rec.sr*2}];rec.test.step=1;rec.test.tries=0;
        await RecordingCache.checkpoint(rec);window.draftId=rec.cache.id;`);
    const draftId=await evaluate('return draftId;');
    await assertBrowser(`(await ExamFiles.cache('list')).some(e=>e.id==='legacy-recording-123456' && e.audio.size>44)`);
    await cdp('Page.navigate',{url:'about:blank'});await open(base);
    await assertBrowser(`(await ExamFiles.cache('list')).some(e=>e.id==='${draftId}' && e.version===1 && e.recorder.nSamples>0)`);
    await evaluate(`window.draft=(await ExamFiles.cache('list')).find(e=>e.id==='${draftId}');await restoreExam(draft);`);
    await assertBrowser(`audioBuffer.duration===4 && lastRecording.incomplete && document.getElementById('analyze-btn').hidden && document.getElementById('file-status').textContent.includes('Незавершённая')`);
    await evaluate(`const partial=await ExamFiles.importExam(await ExamFiles.exportExam(await currentExam()),audioContext);if(!partial.incomplete) throw new Error('Partial backup lost its marker');`);
    // Continue a real checkpoint; verify incremental chunks after another reload.
    await evaluate(`navigator.mediaDevices.getUserMedia=async()=>{const stream=audioContext.createMediaStreamDestination().stream,track=stream.getAudioTracks()[0];track.getSettings=()=>({deviceId:'fixture-microphone'});return stream;};
        await RecordingCache.resume(draft);if(!rec) throw new Error(window.lastAlert);
        rec.processor.onaudioprocess=null;rec.chunks.push(new Float32Array(rec.sr).fill(.08));rec.nSamples+=rec.sr;
        await RecordingCache.checkpoint(rec);if(rec.cache.savedSamples!==rec.sr*5) throw new Error('Resume lost samples');`);
    // Quota failure must pause recording and expose an independent backup.
    await evaluate(`window.realCache=ExamFiles.cache;ExamFiles.cache=async(action,exam)=>{if(action==='checkpoint')throw new DOMException('No space','QuotaExceededError');return realCache(action,exam);};
        rec.chunks.push(new Float32Array(rec.sr));rec.nSamples+=rec.sr;
        await RecordingCache.checkpoint(rec).catch(()=>{});
        if(!rec.paused || !rec.cache.failed) throw new Error('Quota failure did not pause');
        if((await currentExam()).audio.size<44) throw new Error('Emergency backup unavailable');
        ExamFiles.cache=realCache;await RecordingCache.retry();
        if(rec.cache.failed || rec.paused) throw new Error('Retry did not resume');
        await RecordingCache.checkpoint(rec);cleanupRecording();`);
    // Finalizing a complete recording keeps its ID and atomically replaces PCM chunks with WAV.
    await evaluate(`await new Promise(r=>setTimeout(r,100));document.getElementById('calibrate-check').checked=false;await startRecording({test:'exam'});
        if(!rec) throw new Error(window.lastAlert || 'Recorder did not restart');
        rec.processor.onaudioprocess=null;rec.chunks=[new Float32Array(rec.sr*45).fill(.03)];rec.nSamples=rec.sr*45;
        rec.calibrate=true;rec.speechStart=11*rec.sr;rec.silenceEnd=3*rec.sr;rec.plan={sweepStart:3*rec.sr,toneStart:8*rec.sr,end:10*rec.sr};
        rec.test.segments=TESTS.exam.steps.map((step,i)=>({task:step.task,start:(11+i*2)*rec.sr,end:(12+i*2)*rec.sr}));
        await RecordingCache.checkpoint(rec);const id=rec.cache.id;
        const draft=await ExamFiles.cache('get',{id}),preview=await RecordingCache.materialize(draft);
        if(!preview.calibrationWav || !preview.calibration.browser_settings) throw new Error('Calibration checkpoint was lost');
        let conflicts=0;
        try {await ExamFiles.cache('checkpoint',{...draft,revision:draft.revision-1,offset:0,chunk:new Float32Array(1)});} catch {conflicts++;}
        try {await ExamFiles.cache('finalize',{id,expectedRevision:draft.revision-1,status:'saved',audio:new Blob(['stale'])});} catch {conflicts++;}
        if(conflicts!==2 || !(await ExamFiles.cache('get',{id})).recorder) throw new Error('Stale writer overwrote a checkpoint');
        if(!stopRecording()) throw new Error('Finalization failed');await persistCurrentExam();
        const saved=await ExamFiles.cache('get',{id});ExamFiles.validate(saved.deviceInfo,audioBuffer.duration);
        if(saved.id!==id || saved.incomplete || !saved.audio || (await ExamFiles.cache('chunks',{id})).length) throw new Error('Finalization lost identity or retained chunks');`);
    await evaluate(`await new Promise(r=>setTimeout(r,100));window.complete={id:crypto.randomUUID(),status:'saved',createdAt:new Date().toISOString(),patientId:'Тестовый Пациент',patientUuid:'patient-1',deviceInfo:{task:'dsi',dsi_segments:[{task:'vowel',start_sec:0,end_sec:1}]},audio:audioBufferToWav(audioContext.createBuffer(1,audioContext.sampleRate,audioContext.sampleRate),audioContext.sampleRate)};
        await restoreExam(complete);window.completeId=lastRecording.id;`);
    const completeId=await evaluate('return completeId;');
    await evaluate(`ExamFiles.cache=async(action,exam)=>{if(action==='put')throw new DOMException('No space','QuotaExceededError');return realCache(action,exam);};
        await analyzeAudio({backupConfirmed:true});ExamFiles.cache=realCache;`);
    assert.equal(uploads,0,'Storage failure must block upload');
    await evaluate(`window.idbDescriptor=Object.getOwnPropertyDescriptor(window,'indexedDB');Object.defineProperty(window,'indexedDB',{value:null,configurable:true});
        await analyzeAudio({backupConfirmed:true});Object.defineProperty(window,'indexedDB',idbDescriptor);`);
    assert.equal(uploads,0,'Unavailable IndexedDB must block upload');
    await evaluate('await analyzeAudio({backupConfirmed:true});');
    assert.equal(uploads,1);
    await assertBrowser(`(await ExamFiles.cache('get',{id:'${completeId}'})).status==='error' && !!lastRecording.audio`);
    await cdp('Page.navigate',{url:'about:blank'});await open(base);
    await evaluate(`await restoreExam(await ExamFiles.cache('get',{id:'${completeId}'}));`);
    // Simulate a committed server result with a lost HTTP response.
    mode='lost-response';
    await evaluate('await analyzeAudio({backupConfirmed:true});');
    assert.equal(uploads,2);
    await assertBrowser(`lastRecording.id==='${completeId}' && (await ExamFiles.cache('get',{id:'${completeId}'})).status==='error'`);
    await evaluate('await analyzeAudio({backupConfirmed:true});');
    for(let i=0;i<100 && !(await evaluate("return location.pathname==='/visualization';"));i++) await new Promise(r=>setTimeout(r,20));
    assert.equal(uploads,2,'Lost-response reconciliation must not upload again');
    await open(base);
    await assertBrowser(`(await ExamFiles.cache('get',{id:'${completeId}'})).status==='sent'`);
    await evaluate(`await restoreExam(await ExamFiles.cache('get',{id:'${completeId}'}));lastRecording.deviceInfo={...lastRecording.deviceInfo,mic_mode:'changed'};
        const changed=await submissionExam();if(changed.id==='${completeId}') throw new Error('Changed data reused a submitted ID');
        if(!(await ExamFiles.cache('get',{id:'${completeId}'})).studyId) throw new Error('Mutation erased original receipt');`);
    // Stale sending state is recoverable after restarting the page.
    await evaluate(`let exam=await ExamFiles.cache('get',{id:'${completeId}'});await ExamFiles.cache('put',{...exam,status:'sending'});`);
    await cdp('Page.navigate',{url:'about:blank'});await open(base);
    await assertBrowser(`(await ExamFiles.cache('get',{id:'${completeId}'})).status==='error'`);
    await evaluate(`await restoreExam(await ExamFiles.cache('get',{id:'${completeId}'}));`);
    authorized=false;
    await evaluate('await analyzeAudio({backupConfirmed:true});');
    assert.equal(uploads,2);
    await assertBrowser(`location.pathname==='/' && !!lastRecording.audio && document.getElementById('analyze-stage').textContent.includes('Сессия')`);
    authorized=true;owner=2;
    await evaluate(`try {await AudioPark.verifyOwner('1');throw new Error('Owner mismatch accepted');} catch(e) {if(!e.message.includes('другая учётная')) throw e;}`);
    await cdp('Page.navigate',{url:'about:blank'});await open(base);
    await assertBrowser(`(await ExamFiles.cache('list')).length===0`);
    owner=1;await cdp('Page.navigate',{url:'about:blank'});await open(base);
    await assertBrowser(`(await ExamFiles.cache('list')).length>=2`);
    await evaluate(`await ExamFiles.cache('delete',{id:'${draftId}'});if((await ExamFiles.cache('chunks',{id:'${draftId}'})).length) throw new Error('Deleted draft leaked chunks');await ExamFiles.cache('clear');`);
    await assertBrowser(`(await ExamFiles.cache('list')).length===0`);
    console.log('recording cache browser: real IndexedDB migration, reload, partial ZIP, resume, quota failure/retry, blocked upload, network retry, lost-response lookup, interrupted send, expired session, owner isolation, chunk deletion: ok');
})().catch(error=>{console.error(error);process.exitCode=1;}).finally(async()=>{
    if(socket?.readyState===WebSocket.OPEN) {try {await cdp('Browser.close');}catch{}socket.close();}
    if(browser && browser.exitCode===null) {browser.kill();await new Promise(r=>browser.once('exit',r));}
    server.closeAllConnections();server.close();
    // Only the unique fixture profile inside this workspace can be removed.
    if(profile.startsWith(backupRoot+path.sep)) {try {fs.rmSync(profile,{recursive:true,force:true,maxRetries:4,retryDelay:100});}catch{}}
});
