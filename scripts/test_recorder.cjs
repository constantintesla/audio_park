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
for (const name of ['exam_tasks.js','speech_protocol.js','patient-picker.js']) vm.runInContext(fs.readFileSync(path.join(root,'web/assets',name),'utf8'),context);
for (const [,script] of html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/g)) if(script.trim())vm.runInContext(script,context);
async function run(code) { return vm.runInContext(code,context); }
(async()=>{
    await run(`assert.equal(normalizedPatientName('  Ёлкин  Иван  '), 'елкин иван');
        patientRecords=[{uuid:'a',full_name:'Ёлкин Иван Иванович',local_number:1,study_count:1,last_visit:'2026-10-31'},
            {uuid:'b',full_name:'Петров Пётр',local_number:2,study_count:0,last_visit:null}];
        document.getElementById('patient-search').value='ИВАН елк';renderPatients();
        assert.equal(document.getElementById('patient-list').children.length,1);
        selectPatient(patientRecords[0]);
        assert.equal(document.getElementById('patient-selected-label').textContent,'Ёлкин Иван Иванович');
        assert.equal(document.getElementById('patient-uuid').value,'a');
        assert.equal(document.getElementById('patient-picker').hidden,true);
        clearPatientSelection();assert.equal(document.getElementById('patient-picker').hidden,false);
        document.getElementById('patient-id').value='TEST-PATIENT';`);
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
    const starting = run('startRecording({test:"exam"})');
    await new Promise(r=>setImmediate(r));
    await run(`function feed(n, value=0.05) {
        rec.processor.onaudioprocess({inputBuffer:{getChannelData:()=>new Float32Array(n).fill(value)}});
    }
    feed(3000, 0.0001);`);
    await starting;
    await run('assert.equal(rec.test.steps.length,14);assert(document.getElementById("patient-id").disabled);assert(document.body.classList.contains("exam-focus-mode"));');
    await run(`(async()=>{for(let i=0;i<27;i++) {
        assert(rec.paused);
        const before=rec.nSamples; feed(60000, .9); assert.equal(rec.nSamples,before);
        await onTestTryButton(); feed(2000);
        const finishing=onTestTryButton(); await onTestTryButton();
        feed(1000); // Only the 300 ms tail is retained, even if a larger block arrives.
        await finishing;
        assert.equal(rec.nSamples,before+2300);
    }})()`);
    await new Promise(r=>setTimeout(r,50));
    await run('assert(rec.paused);assert(document.getElementById("exam-review").open);assert.equal(rec.test.segments.length,27);');
    await run(`(async()=>{redoExamStep(rec,0); assert(rec.paused);
        const before=rec.nSamples;feed(60000);assert.equal(rec.nSamples,before);
        await onTestTryButton();feed(2000);
        const finishing=onTestTryButton();feed(1000);await finishing;nextTestStep(rec);})()`);
    await new Promise(r=>setTimeout(r,50));
    await run(`assert(rec.paused);assert.equal(rec.test.segments.filter(s=>s.task==='mpt').length,1);
        assert(stopRecording());assert.equal(lastRecording.patientId,'TEST-PATIENT');assert.equal(lastRecording.deviceInfo.task,'exam');
        const d=lastRecording.deviceInfo;
        assert.equal(d.dsi_segments.length,7);assert.equal(d.protocol_segments.length,19);
        assert(d.dsi_segments.every(s=>s.end_sec>s.start_sec && s.end_sec<=audioBuffer.duration));
        assert.equal(d.noise_floor_dbfs,-80);
        assert.equal(audioBuffer.getChannelData(0).length,28*2300);
        assert(tracks.every(t=>t.readyState==='ended'));assert(!document.body.classList.contains('exam-focus-mode'));`);
    // Transport keeps attempts and replaced takes, excluding instruction gaps.
    await run(`(async()=>{const exported = audioBufferToWav(audioBuffer, audioBuffer.sampleRate);
        const bytes = await exported.arrayBuffer(); const header = new DataView(bytes);
        assert.equal(header.getUint32(40,true), audioBuffer.getChannelData(0).length * 2);
        assert.equal(bytes.byteLength, 44 + audioBuffer.getChannelData(0).length * 2);
        assert(audioBuffer.duration >= Math.max(...lastRecording.deviceInfo.dsi_segments.map(s=>s.end_sec)));})()`);
    // Browser permission rejection leaves controls usable and no active microphone.
    await run(`document.getElementById('calibrate-check').checked=true;
        const savedSignal=playCalibrationSignal, savedBeep=playBeep;
        playCalibrationSignal=state=>({end:state.nSamples});
        playBeep=state=>({end:state.nSamples});`);
    const calibrated=run('startRecording({test:"exam"})');
    await new Promise(r=>setImmediate(r));
    await run('feed(3000,0.0001)');
    await new Promise(r=>setTimeout(r,40));
    await run('feed(1000)');
    await new Promise(r=>setTimeout(r,40));
    await run('feed(1000)');
    await calibrated;
    await run(`assert(rec.paused);assert.equal(rec.speechStart,5000);
        feed(60000);assert.equal(rec.nSamples,5000);cleanupRecording();
        playCalibrationSignal=savedSignal;playBeep=savedBeep;`);
    await run(`navigator.mediaDevices.getUserMedia=async()=>{throw Object.assign(new Error('denied'),{name:'NotAllowedError'});};`);
    await run('startRecording({check:true})');
    await run('assert.equal(rec,null);assert(!startingRecording);assert(!micReady);assert(!document.getElementById("record-btn").disabled);');
    console.log('recorder: microphone signal/silence/denial, 14 tasks, 27 attempts, review, redo, segment boundaries: ok');
})().catch(error=>{console.error(error);process.exitCode=1;});
