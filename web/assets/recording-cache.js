/* Incremental PCM checkpoints. Audio and recorder metadata commit in one transaction. */
const RecordingCache = (() => {
    let retainedState=null;
    function samples(state, start=0, end=state.nSamples) {
        const out=new Float32Array(end-start); let offset=0;
        for(const chunk of state.chunks) {
            const from=Math.max(start,offset), to=Math.min(end,offset+chunk.length);
            if(to>from) out.set(chunk.subarray(from-offset,to-offset),from-start);
            offset+=chunk.length;
            if(offset>=end) break;
        }
        return out;
    }
    function metadata(state) {
        const test=state.test;
        return {sr:state.sr,nSamples:state.nSamples,speechStart:state.speechStart,
            calibrate:state.calibrate,noiseFloor:state.noiseFloor,silenceEnd:state.silenceEnd,plan:state.plan,phase:state.phase,paused:!!state.paused,
            settings:{...state.settings},deviceInfo:state.deviceInfo || buildDeviceInfo(state,null),
            test:test?{task:test.task,step:test.step,tries:test.tries,tryStart:test.tryStart,
                segments:test.segments.map(s=>({...s})),redo:!!test.redo,pendingReplace:test.pendingReplace,resumedAt:test.resumedAt}:null};
    }
    async function begin(state, draft) {
        const user=await AudioPark.ready;
        if(!user) throw new Error('Требуется вход');
        const cache={id:draft?.id || crypto.randomUUID(),owner:String(user.id),createdAt:draft?.createdAt || new Date().toISOString(),
            savedSamples:draft?.recorder.nSamples || 0,revision:draft?.revision || 0,tail:Promise.resolve()};
        if(draft) {
            if(String(draft.owner)!==String(user.id)) throw new Error('Запись принадлежит другой учётной записи');
            if(draft.recorder.sr!==state.sr) throw new Error('Частота микрофона изменилась. Откройте запись для прослушивания и скачайте копию');
            const raw=draft.recorder;
            if(raw.deviceInfo?.device_id && raw.deviceInfo.device_id!==buildDeviceInfo(state,null).device_id) throw new Error('Микрофон изменился. Скачайте сохранённую часть и начните новое обследование с калибровкой');
            if(raw.settings?.deviceId && state.settings.deviceId && raw.settings.deviceId!==state.settings.deviceId) throw new Error('Микрофон изменился. Скачайте сохранённую часть и начните новое обследование с калибровкой');
            state.chunks=[draft.samples];state.nSamples=raw.nSamples;state.speechStart=raw.speechStart;
            state.calibrate=raw.calibrate;state.noiseFloor=raw.noiseFloor;state.silenceEnd=raw.silenceEnd;state.plan=raw.plan;
            state.paused=!!raw.paused;state.phase=raw.phase;
            state.deviceInfo=raw.deviceInfo;
            if(raw.test) {
                Object.assign(state.test,structuredClone(raw.test),{tryStart:null,resumedAt:raw.nSamples});
                if(state.test.step>=state.test.steps.length) state.test.step=state.test.steps.length-1;
            }
        }
        state.cache=cache;
        retainedState=state;
        await checkpoint(state);
        state.cache.timer=setInterval(()=>checkpoint(state).catch(()=>{}),2000);
    }
    function checkpoint(state) {
        if(!state.cache || state.cache.finalizing) return Promise.resolve();
        // Serialize snapshots too: queued snapshots must start at the last committed sample.
        const save=state.cache.tail.catch(()=>{}).then(async()=>{
            if(state.cache.finalizing) return;
            const raw=metadata(state), offset=state.cache.savedSamples;
            const revision=await ExamFiles.cache('checkpoint',{
                id:state.cache.id,owner:state.cache.owner,createdAt:state.cache.createdAt,
                revision:state.cache.revision,status:'recording',incomplete:true,
                patientId:state.patientId,patientUuid:state.patientUuid,recorder:raw,
                offset,chunk:samples(state,offset,raw.nSamples)
            });
            state.cache.savedSamples=raw.nSamples;state.cache.revision=revision;
            fileElement('recording-cache-status').textContent=`Сохранено в браузере: ${(raw.nSamples/raw.sr).toFixed(1)} с. Последние 2 секунды могут ещё сохраняться.`;
        });
        state.cache.tail=save;
        return save.catch(error=>{
            if(!state.cache.failed) state.cache.wasPaused=!!state.paused;
            if(state.test?.tryStart!==null && state.test) {state.test.tryStart=null;state.test.resumedAt=state.nSamples;showTestStep(state);}
            state.cache.failed=true;state.paused=true;
            fileElement('recording-cache-status').textContent='Запись приостановлена: '+storageErrorMessage(error)+'. Скачайте ZIP или повторите сохранение.';
            fileElement('retry-local-save').hidden=false;
            fileElement('download-actions').hidden=false;
            fileElement('download-exam').disabled=false;fileElement('download-wav').disabled=false;
            throw error;
        });
    }
    function detach(state) {
        if(!state?.cache) return;
        clearInterval(state.cache.timer);
        if(!state.cache.finalizing) checkpoint(state).then(()=>refreshLocalExams()).catch(showFileError);
    }
    function finish(state) {if(!state || retainedState===state) retainedState=null;}
    async function load(exam) {
        const snapshot=await ExamFiles.cache('draft',exam),current=snapshot.exam;
        if(!current?.recorder) throw new Error('Черновик уже завершён или удалён. Обновите список');
        const chunks=snapshot.chunks, raw=current.recorder;
        const data=new Float32Array(raw.nSamples);let offset=0;
        for(const chunk of chunks) {
            if(chunk.offset!==offset || chunk.data.length+offset>data.length) throw new Error('Неполный локальный черновик. Сохранённые фрагменты не согласованы');
            data.set(chunk.data,offset);offset+=chunk.data.length;
        }
        if(offset!==data.length) throw new Error('Аудиофрагменты черновика отсутствуют');
        return {...current,samples:data};
    }
    function preview(state) {
        const raw=metadata(state), all=samples(state), start=raw.speechStart ?? 0;
        if(all.length<=start) throw new Error('Аудио ещё не записано');
        const buffer=audioContext.createBuffer(1,all.length-start,state.sr);buffer.getChannelData(0).set(all.subarray(start));
        const deviceInfo={...raw.deviceInfo,task:raw.test?.task || 'exam',dsi_segments:[],protocol_segments:[]};
        for(const [key,tasks] of Object.entries(state.test?.segmentSets || TESTS.exam.segmentSets)) {
            deviceInfo[key]=recordingSegments(state,tasks);
        }
        let calibration=null,calibrationWav=null;
        if(raw.calibrate && raw.speechStart!==null && start>0) {
            calibration=analyzeCalibration(all.subarray(0,start),state);
            if(calibration.noise_floor_dbfs!==null) deviceInfo.noise_floor_dbfs=calibration.noise_floor_dbfs;
            const cal=audioContext.createBuffer(1,start,raw.sr);cal.getChannelData(0).set(all.subarray(0,start));calibrationWav=audioBufferToWav(cal,raw.sr);
        }
        return {id:state.cache.id,owner:state.cache.owner,version:1,createdAt:state.cache.createdAt,status:'recording',incomplete:true,
            patientId:state.patientId,patientUuid:state.patientUuid,audio:audioBufferToWav(buffer,raw.sr),buffer,deviceInfo,calibration,calibrationWav};
    }
    async function materialize(exam) {
        if(!exam.recorder) return exam;
        const draft=await load(exam), raw=draft.recorder;
        const state={...raw,chunks:[draft.samples],cache:draft,patientId:draft.patientId,patientUuid:draft.patientUuid,
            test:raw.test?{...TESTS.exam,...raw.test}:null};
        return {...preview(state),draft};
    }
    async function resume(exam) {
        if(isRecording || analyzeProgress || startingRecording) return;
        const draft=await load(exam);
        if(String((await AudioPark.ready).id)!==String(draft.owner)) throw new Error('Запись принадлежит другой учётной записи');
        if(draft.recorder.speechStart===null) {
            await startRecording({test:'exam',restartingDraft:true});
            fileElement('file-status').textContent='Калибровка была прервана и начата заново. Предыдущие фрагменты остаются в локальных копиях.';
            return;
        }
        await startRecording({test:'exam',draft});
        if(rec && rec.cache?.id===draft.id) fileElement('file-status').textContent='Запись продолжена с сохранённого фрагмента. Прерванную попытку запишите заново.';
    }
    async function retry() {
        const state=rec || retainedState;
        if(!state?.cache) return;
        try {
            await checkpoint(state);state.cache.failed=false;state.paused=state.cache.wasPaused;
            fileElement('retry-local-save').hidden=true;
            await refreshLocalExams();
        } catch(error) {showFileError(error);}
    }
    return {begin,checkpoint,detach,finish,load,preview,materialize,resume,retry,current:()=>retainedState};
})();
