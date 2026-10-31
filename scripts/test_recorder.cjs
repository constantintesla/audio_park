// Exercise the real recorder state machine with simulated microphone samples; no hardware access.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const crypto = require('node:crypto').webcrypto;
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(root,'web/index.html'),'utf8');
const ids = new Set([...html.matchAll(/\bid="([^"]+)"/g)].map(m=>m[1]));
const elements = new Map();
const noop = () => {};
const bodyClasses = new Set();
function element() {
    return {style:{}, dataset:{}, children:[], checked:false, disabled:false, hidden:false, value:'TEST-PATIENT',
        classList:{add:noop,remove:noop,toggle:noop}, addEventListener:noop, scrollIntoView:noop,
        append(...children) { this.children.push(...children); }, appendChild(c) { this.children.push(c); },
        replaceChildren(...children) { this.children = children; }, focus:noop,
        showModal() { this.open=true; }, close() { this.open=false; },
        setAttribute:noop, getContext:() => new Proxy({}, {get:()=>noop}), clientWidth:600,clientHeight:100};
}
let tracks = [];
const stream = () => {
    const track = {readyState:'live', label:'simulated microphone',getSettings:()=>({}),stop(){this.readyState='ended';}};
    tracks.push(track); return {getTracks:()=>[track],getAudioTracks:()=>[track]};
};
const audioNode = () => ({connect:noop,disconnect:noop,gain:{value:0},getFloatTimeDomainData:noop});
const audioContext = {state:'running',sampleRate:1000,destination:{},
    createMediaStreamSource:audioNode,createAnalyser:audioNode,createScriptProcessor:audioNode,createGain:audioNode,
    createBuffer:(_,n,sr) => {const a=new Float32Array(n);return {sampleRate:sr,duration:n/sr,getChannelData:()=>a};}};
const context = vm.createContext({console,Blob,URL,FormData,Float32Array,ArrayBuffer,DataView,Int16Array,TextEncoder,crypto,
    setTimeout,clearTimeout,setInterval,clearInterval,performance,assert,
    document:{body:{classList:{add:c=>bodyClasses.add(c),remove:c=>bodyClasses.delete(c),toggle:(c,on)=>on?bodyClasses.add(c):bodyClasses.delete(c),contains:c=>bodyClasses.has(c)}},addEventListener:noop,querySelectorAll:()=>[],createElement:element,
        getElementById(id){assert(ids.has(id), `Missing DOM element: ${id}`);if(!elements.has(id))elements.set(id,element());return elements.get(id);}},
    window:{location:{protocol:'http:'},addEventListener:noop,devicePixelRatio:1},
    navigator:{mediaDevices:{getUserMedia:async()=>stream()},platform:'test'},
    AudioPark:{base:''},alert:noop,confirm:()=>true,requestAnimationFrame:()=>1,cancelAnimationFrame:noop,
    testAudioContext:audioContext,tracks,
});
for (const name of ['exam_tasks.js','speech_protocol.js']) vm.runInContext(fs.readFileSync(path.join(root,'web/assets',name),'utf8'),context);
for (const [,script] of html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/g)) if(script.trim())vm.runInContext(script,context);
async function run(code) { return vm.runInContext(code,context); }
(async()=>{
    await run(`(async()=>{patientIdentityKey='00'.repeat(32);
        document.getElementById('patient-surname').value='  Ёлкин  ';
        document.getElementById('patient-name').value='Иван';
        document.getElementById('patient-patronymic').value=' Иванович ';
        globalThis.patientTokenA=await patientLookupToken();
        document.getElementById('patient-surname').value='елкин';
        document.getElementById('patient-name').value='  иван  ';
        document.getElementById('patient-patronymic').value='иванович';
        globalThis.patientTokenB=await patientLookupToken();
        assert.equal(patientTokenA,patientTokenB);assert.match(patientTokenA,/^[0-9a-f]{64}$/);})()`);
    await run(`showPatientNextActions('Нет пациента','Создайте карточку','Создать пациента',true);
        assert.equal(document.getElementById('patient-next-actions').hidden,false);
        assert.match(document.getElementById('new-patient-btn').className,/btn-primary/);
        assert.equal(document.getElementById('choose-existing-btn').hidden,false);
        patientRecords=[{uuid:'a',label:'Пациент 1',study_count:1,last_visit:'2026-10-31'},
            {uuid:'b',label:'Пациент 2',study_count:0,last_visit:null}];
        matchedPatientUuids=new Set(['a','b']);openPatientBrowser('matches');
        assert.equal(document.getElementById('patient-browser').hidden,false);
        assert.equal(document.getElementById('patient-search').hidden,true);
        assert.equal(document.getElementById('patient-list').children.length,2);
        closePatientBrowser();assert.equal(document.getElementById('patient-browser').hidden,true);`);
    await run('audioContext=testAudioContext; initRecordButton();');
    let checking=run('startRecording({check:true})');
    await new Promise(r=>setImmediate(r));
    await run('rec.chunks=[new Float32Array(4000).fill(0.05)]; rec.nSamples=4000;');
    await checking;
    await run('assert(micReady); assert.equal(rec,null); assert.equal(lastRecording,null); assert(tracks.every(t=>t.readyState==="ended"));');
    // A silent check must disable examination and discard its samples.
    checking=run('startRecording({check:true})');
    await new Promise(r=>setImmediate(r));
    await run('rec.chunks=[new Float32Array(4000)];rec.nSamples=4000;');
    await checking;
    await run('assert(!micReady);assert(document.getElementById("exam-test-btn").disabled);');
    await run('micReady=true;');
    await run('startRecording({test:"exam"})');
    await run('assert.equal(rec.test.steps.length,14);assert(document.getElementById("patient-id").disabled);assert(document.body.classList.contains("exam-focus-mode"));');
    await run(`for(let i=0;i<27;i++) { onTestTryButton(); rec.chunks.push(new Float32Array(2000).fill(0.05)); rec.nSamples+=2000; onTestTryButton(); }
        rec.chunks.push(new Float32Array(1000));rec.nSamples+=1000;`);
    await new Promise(r=>setTimeout(r,50));
    await run('assert(rec.paused);assert(document.getElementById("exam-review").open);assert.equal(rec.test.segments.length,27);');
    await run(`redoExamStep(rec,0);onTestTryButton();rec.chunks.push(new Float32Array(2000));rec.nSamples+=2000;onTestTryButton();
        rec.chunks.push(new Float32Array(1000));rec.nSamples+=1000;nextTestStep(rec);`);
    await new Promise(r=>setTimeout(r,50));
    await run(`assert(rec.paused);assert.equal(rec.test.segments.filter(s=>s.task==='mpt').length,1);
        assert(stopRecording());assert.equal(lastRecording.patientId,'TEST-PATIENT');assert.equal(lastRecording.deviceInfo.task,'exam');
        const d=lastRecording.deviceInfo;
        assert.equal(d.dsi_segments.length,7);assert.equal(d.protocol_segments.length,19);
        assert(d.dsi_segments.every(s=>s.end_sec>s.start_sec && s.end_sec<=audioBuffer.duration));
        assert(tracks.every(t=>t.readyState==='ended'));assert(!document.body.classList.contains('exam-focus-mode'));`);
    // Browser permission rejection leaves controls usable and no active microphone.
    await run(`navigator.mediaDevices.getUserMedia=async()=>{throw Object.assign(new Error('denied'),{name:'NotAllowedError'});};`);
    await run('startRecording({check:true})');
    await run('assert.equal(rec,null);assert(!startingRecording);assert(!micReady);assert(!document.getElementById("record-btn").disabled);');
    console.log('recorder: microphone signal/silence/denial, 14 tasks, 27 attempts, review, redo, segment boundaries: ok');
})().catch(error=>{console.error(error);process.exitCode=1;});
