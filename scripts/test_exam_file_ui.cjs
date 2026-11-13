const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(require('node:path').join(__dirname,'../web/assets/exam-file-ui.js'),'utf8');
const elements=new Map();const element=()=>({style:{},disabled:true,hidden:true,children:[],append(...items){this.children.push(...items);},replaceChildren(){this.children=[];},pause(){},textContent:''});
let saved;
const original=new Blob(['original WAV bytes'],{type:'audio/wav'});
const buffer={duration:1,sampleRate:48000};
const context=vm.createContext({console,Blob,URL,crypto,Date,Set,Map,setTimeout,structuredClone,
    EXAM_STEPS:{vowel:{title:'Спокойная а'}},
    document:{createElement:element,getElementById(id){if(!elements.has(id))elements.set(id,element());return elements.get(id);}},
    lastRecording:null,audioBuffer:null,audioContext:{decodeAudioData:async()=>buffer},
    audioBufferToWav(){throw new Error('Restored WAV must retain original bytes');},
    displayAudioInfo(){},initWaveform(){},
    ExamFiles:{validate(){},async cache(action,e){if(action==='put') saved={...e,owner:'1'};return action==='list'?[]:action==='get'?saved:undefined;}},
});
vm.runInContext(source,context);
(async()=>{
    context.exam={id:'fixed-retry-id',createdAt:'2026-11-12T00:00:00Z',status:'saved',audio:original,deviceInfo:{task:'dsi',dsi_segments:[{task:'vowel',start_sec:0,end_sec:1}]},patientUuid:'patient-1'};
    await vm.runInContext('restoreExam(exam)',context);
    assert.equal(saved.id,'fixed-retry-id');
    assert.equal(saved.audio,original);
    assert.equal(saved.patientUuid,'patient-1');
    assert.equal(elements.get('analyze-btn').hidden,false);
    assert.equal(elements.get('file-preview').hidden,false);
    assert.equal(elements.get('file-contents').children.length,1);
    assert.match(elements.get('file-contents').children[0].textContent,/попытка 1/);
    context.exam={...context.exam,status:'sent'};
    await vm.runInContext('restoreExam(exam)',context);
    assert.equal(saved.id,'fixed-retry-id');
    assert.equal(saved.status,'sent');
    console.log('archive UI: reload keeps retry ID and WAV bytes, preview, sent examination keeps its identity: ok');
})().catch(e=>{console.error(e);process.exitCode=1;});
