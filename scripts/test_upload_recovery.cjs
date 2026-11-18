// Real transport module, persisted drafts and simulated server lifecycle. No real accounts/audio.
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),assert=require('node:assert/strict');
const crypto=require('node:crypto').webcrypto;
const source=fs.readFileSync(path.join(__dirname,'../web/assets/chunked-upload.js'),'utf8');
const response=(status,body)=>({status,ok:status<400,headers:{get:()=> 'application/json'},json:async()=>body});
function harness(mode='recover') {
    const data=new Map(),elements=new Map(),events={},requests=[],accepted=new Map();
    let completed=false;
    const idb={open(){const request={};setImmediate(()=>{request.result={close(){},transaction(){const tx={};tx.objectStore=()=>({
        get(key){const r={result:structuredClone(data.get(key))};setImmediate(()=>tx.oncomplete());return r;},
        put(value,key){data.set(key,structuredClone(value));setImmediate(()=>tx.oncomplete());return {};},
        delete(key){data.delete(key);setImmediate(()=>tx.oncomplete());return {};}
    });return tx;}};request.onsuccess();});return request;}};
    const context=vm.createContext({console,crypto,Blob,DataView,Uint8Array,Map,Promise,Array,
        setTimeout:callback=>setTimeout(callback,1),indexedDB:idb,navigator:{},
        AudioPark:{url:p=>p,user:{id:1},ready:Promise.resolve(),refreshAuth:async()=>{}},
        window:{addEventListener(){}},confirm:()=>true,
        document:{addEventListener:(name,callback)=>{events[name]=callback;},getElementById(id){
            if(!elements.has(id))elements.set(id,{hidden:true,disabled:false,textContent:'',addEventListener(name,callback){this[name]=callback;}});return elements.get(id);}},
        fetch:async(url,options)=>{
            requests.push({url,method:options.method||'GET',body:options.body});
            if(mode==='unauthorized')return response(401,{error:'Сессия истекла'});
            if(mode==='forbidden')return response(403,{error:'Нет доступа'});
            if(url==='/api/uploads')return mode==='patient-unavailable'?response(400,{error:'Выберите активного пациента'}):response(201,{upload_id:'new-id'});
            if(url.includes('/expired-id'))return response(404,{error:'Загрузка не найдена'});
            if(url.endsWith('/complete')){completed=true;return response(202,{});}
            if(url.includes('/parts/')){const number=Number(url.split('/').pop());accepted.set(number,options.body);return response(200,{});}
            return response(200,{state:completed?'completed':'uploading',parts:[],study_id:completed?'AP-restored':null});
        }
    });
    vm.runInContext(source,context);
    return {data,elements,events,requests,accepted,client:context.window.ChunkedUpload};
}
const header=new ArrayBuffer(44),view=new DataView(header);
view.setUint16(22,1,true);view.setUint32(24,48000,true);view.setUint32(40,96000,true);
const wav=new Blob([header,new Uint8Array(96000)]);
const input={wav,calibrationWav:wav,clientToken:'expired-upload-123456',patientUuid:'original-patient',duration:1,
    deviceInfo:{task:'dsi',dsi_segments:[{task:'vowel',start_sec:0,end_sec:1.001}]},calibration:{noise_floor_dbfs:-70}};
const config={part_bytes:32768,max_bytes:1e8,max_seconds:900};
async function setup(mode) {
    const h=harness(mode),draft=await h.client.makeDraft(input,config);
    draft.uploadId='expired-id';h.data.set('1',draft);h.data.set('2',{owner:'another account'});
    h.events.DOMContentLoaded();await h.client.ready;
    return {...h,draft};
}
(async()=>{
    const recovered=await setup('recover');
    assert(await recovered.client.hasPending(input.clientToken));
    const before=JSON.stringify(recovered.draft.manifest);
    // A pending upload must resume even after the UI feature flag is disabled.
    assert.equal((await recovered.client.start(input,{enabled:false},()=>{})).study_id,'AP-restored');
    const created=JSON.parse(recovered.requests.find(r=>r.url==='/api/uploads').body);
    assert.deepEqual(created,{version:1,patient_uuid:input.patientUuid,client_token:input.clientToken});
    assert.equal(recovered.requests.find(r=>r.url.endsWith('/complete')).body,before);
    for(const file of Object.values(recovered.draft.manifest.files))for(const part of file.parts){
        assert.equal(await recovered.client.hash(recovered.accepted.get(part.number)),part.sha256);
    }
    assert(!recovered.data.has('1'));assert(recovered.data.has('2'));
    const cancelled=await setup('recover');
    await cancelled.elements.get('upload-cancel').click();
    assert(!cancelled.data.has('1'));assert(cancelled.data.has('2'));
    assert(!cancelled.requests.some(r=>r.url==='/api/uploads'));
    assert.equal((await cancelled.client.start({...input,clientToken:'another-upload-123456'},config,()=>{})).study_id,'AP-restored');
    for(const mode of ['unauthorized','forbidden','patient-unavailable']) {
        const h=await setup(mode);
        await assert.rejects(h.client.start(input,config,()=>{}));
        assert(h.data.has('1'));assert.equal(h.data.get('1').uploadId,'expired-id');
        if(mode!=='patient-unavailable') {
            assert(!h.requests.some(r=>r.url==='/api/uploads'));
            await h.elements.get('upload-cancel').click();
            assert(h.data.has('1'));
        }
    }
    console.log('upload recovery: expired draft, byte-exact WAV/calibration/manifest, cancellation, flag rollback, auth and patient isolation: ok');
})().catch(error=>{console.error(error);process.exitCode=1;});
