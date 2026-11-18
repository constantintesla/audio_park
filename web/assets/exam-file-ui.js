/* UI adapter for the shared examination format and account-scoped local copies. */
let previewURL = null;
let fileImportBusy = false;
let localSaveFailed = false;
let localSaveQueue = Promise.resolve();
const fileElement = id => document.getElementById(id);
function storageErrorMessage(error) {
    if(error?.name==='QuotaExceededError') return 'Недостаточно места в браузере. Скачайте ZIP и освободите место, удалив ненужные локальные копии';
    if(error?.name==='SecurityError') return 'Браузер запретил локальное хранение. Скачайте ZIP и разрешите хранение данных для этого сайта';
    return error?.message || 'Локальное сохранение не удалось';
}
function showFileError(error) { fileElement('file-status').textContent = 'Ошибка: '+storageErrorMessage(error)+'. При ошибке хранения скачайте ZIP перед закрытием страницы.'; }
async function currentExam(recording=lastRecording,includeDraft=true) {
    const draft=typeof RecordingCache!=='undefined'?RecordingCache.current():null;
    if(includeDraft && draft?.cache) return RecordingCache.preview(draft);
    if(!recording || !audioBuffer) throw new Error('Нет записи');
    await recording.cachePending;
    recording.id ||= crypto.randomUUID();
    recording.createdAt ||= new Date().toISOString();
    recording.audio ||= audioBufferToWav(recording.buffer || audioBuffer,(recording.buffer || audioBuffer).sampleRate);
    return {version:1,id:recording.id,owner:recording.owner,createdAt:recording.createdAt,status:recording.status || 'saved',
        incomplete:!!recording.incomplete,submissionSignature:recording.submissionSignature,studyId:recording.studyId,lastError:recording.lastError,
        audio:recording.audio,deviceInfo:recording.deviceInfo,
        calibration:recording.calibration,calibrationWav:recording.calibrationWav,
        patientId:recording.patientId,patientUuid:recording.patientUuid};
}
function downloadFile(blob,name) { const url=URL.createObjectURL(blob), a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),30000); }
async function persistCurrentExam() {
    const recording=lastRecording;
    const save=localSaveQueue.catch(()=>{}).then(()=>persistExamRecord(recording));
    localSaveQueue=save;
    return save;
}
async function persistExamRecord(recording) {
    fileElement('download-actions').hidden=false;
    fileElement('download-wav').disabled=false; fileElement('download-exam').disabled=false;
    const exam=await currentExam(recording,false);
    if(recording?.draft) throw new Error('Это незавершённая запись. Продолжите её из списка локальных обследований или скачайте копию');
    let stored;
    try {
        await ExamFiles.cache(recording.cacheState?'finalize':'put',{...exam,expectedRevision:recording.cacheState?.revision});
        recording.cacheState=null;
        stored=await ExamFiles.cache('get',exam);
        if(!stored?.audio || stored.audio.size!==exam.audio.size || (stored.calibrationWav?.size || 0)!==(exam.calibrationWav?.size || 0)) throw new Error('Не удалось подтвердить сохранение полного комплекта обследования');
        localSaveFailed=false;
    } catch(error) {localSaveFailed=true;fileElement('retry-local-save').hidden=false;throw error;}
    exam.owner=stored.owner;
    recording.owner=stored.owner;
    fileElement('file-status').textContent='Запись сохранена в этом браузере. Можно скачать WAV или ZIP.';
    await refreshLocalExams(); return exam;
}
async function restoreExam(exam, buffer) {
    if(exam.version && exam.version!==1) throw new Error('Неподдерживаемая версия локального обследования');
    if(exam.recorder) exam=await RecordingCache.materialize(exam);
    const restored=buffer || await audioContext.decodeAudioData(await exam.audio.arrayBuffer());
    ExamFiles.validate(exam.deviceInfo,restored.duration,{incomplete:!!exam.incomplete});
    if(typeof RecordingCache!=='undefined') RecordingCache.finish();
    audioBuffer=restored;
    lastRecording={...exam,buffer:audioBuffer,imported:true};
    if(typeof patientRecords !== 'undefined') {
        const patient=patientRecords.find(p=>p.uuid===exam.patientUuid);
        if(patient) selectPatient(patient);
    }
    displayAudioInfo('Восстановленное обследование',audioBuffer); initWaveform(audioBuffer);
    fileElement('calibration-info').style.display='none';
    if(previewURL) URL.revokeObjectURL(previewURL);
    fileElement('file-preview').pause();fileElement('file-preview').ontimeupdate=null;
    previewURL=URL.createObjectURL(exam.audio); fileElement('file-preview').src=previewURL;fileElement('file-preview').hidden=false;
    fileElement('analyze-btn').hidden=!!exam.incomplete;fileElement('analyze-btn').disabled=!!exam.incomplete;fileElement('analyze-btn').textContent=exam.status==='sent'?'Проверить результат':'Анализировать';
    fileElement('download-actions').hidden=false;fileElement('download-wav').disabled=false;fileElement('download-exam').disabled=false;
    if(!exam.draft) await persistCurrentExam();
    const segments=[...(exam.deviceInfo.dsi_segments||[]),...(exam.deviceInfo.protocol_segments||[])];
    const contents=fileElement('file-contents');contents.replaceChildren();
    const attempts=[...new Map(segments.map(s=>[`${s.task}:${s.start_sec}:${s.end_sec}`,s])).values()].sort((a,b)=>a.start_sec-b.start_sec);
    const counts=new Map();
    for(const segment of attempts) {
        counts.set(segment.task,(counts.get(segment.task)||0)+1);
        const row=document.createElement('p');
        row.textContent=`${EXAM_STEPS[segment.task]?.title || segment.task} · попытка ${counts.get(segment.task)} · ${segment.start_sec.toFixed(2)}–${segment.end_sec.toFixed(2)} с `;
        const play=document.createElement('button');play.className='btn btn-sm btn-outline-secondary';play.textContent='Прослушать попытку';
        play.onclick=()=>{const player=fileElement('file-preview');player.currentTime=segment.start_sec;player.ontimeupdate=()=>{if(player.currentTime>=segment.end_sec) {player.pause();player.ontimeupdate=null;}};player.play().catch(showFileError);};
        row.append(play);contents.append(row);
    }
    fileElement('file-status').textContent=(exam.incomplete?'Незавершённая запись: восстановлена только сохранённая часть. Продолжите запись из списка локальных обследований или скачайте копию. ':'')+`Восстановлено: ${audioBuffer.duration.toFixed(1)} с; заданий: ${new Set(segments.map(s=>s.task)).size}; калибровка: ${exam.calibrationWav?'есть':'отсутствует'}. Выберите или подтвердите пациента перед анализом.`;
}
const EXAM_STATUS={recording:'Записывается · незавершённая запись',saved:'Сохранено',sending:'Отправляется · можно проверить результат',error:'Ошибка · можно повторить отправку',sent:'Отправлено'};
function forgetLocalExam(id) {
    if(typeof RecordingCache!=='undefined' && (!id || RecordingCache.current()?.cache.id===id)) RecordingCache.finish();
    if(id && lastRecording?.id!==id) return;
    lastRecording=null;audioBuffer=null;localSaveFailed=false;
    fileElement('file-preview').pause();fileElement('file-preview').removeAttribute('src');fileElement('file-preview').hidden=true;
    if(previewURL) {URL.revokeObjectURL(previewURL);previewURL=null;}
    fileElement('file-contents').replaceChildren();fileElement('download-actions').hidden=true;fileElement('analyze-btn').hidden=true;
    fileElement('retry-local-save').hidden=true;fileElement('audio-info').style.display='none';
    fileElement('recording-cache-status').textContent='';
    if(typeof wavesurfer!=='undefined' && wavesurfer) {wavesurfer.destroy();wavesurfer=null;}
    fileElement('waveform-card').style.display='none';
}
async function refreshLocalExams() {
    const list=fileElement('local-exams');list.replaceChildren();
    const exams=await ExamFiles.cache('list');
    exams.sort((a,b)=>(a.status==='sent')-(b.status==='sent') || String(b.createdAt).localeCompare(String(a.createdAt)));
    for(const exam of exams) {
        const row=document.createElement('div'), label=document.createElement('span');
        const patient=typeof patientRecords!=='undefined'?patientRecords.find(item=>item.uuid===exam.patientUuid):null;
        const patientName=document.createElement('strong');
        patientName.textContent=patient?.full_name || exam.patientId || 'Пациент не указан';
        row.append(patientName);
        label.textContent=`${new Date(exam.createdAt).toLocaleString()} · ${EXAM_STATUS[exam.status] || 'Сохранено'}${exam.lastError?' · '+exam.lastError:''} `;row.append(label);
        row.className='local-exam-row';
        const controls=document.createElement('div');controls.className='local-exam-actions';row.append(controls);
        const materialize=()=>exam.recorder?RecordingCache.materialize(exam):Promise.resolve(exam);
        const actions=[['Открыть / прослушать',()=>restoreExam(exam)],
            ['WAV',async()=>downloadFile((await materialize()).audio,'recording.wav')],
            ['ZIP',async()=>downloadFile(await ExamFiles.exportExam(await materialize()),'examination.zip')],
            ['Удалить',async()=>{if(confirm('Удалить локальную копию?')) {await ExamFiles.cache('delete',exam);forgetLocalExam(exam.id);await refreshLocalExams();}}]];
        if(exam.recorder) actions.splice(1,0,['Продолжить запись',()=>RecordingCache.resume(exam)]);
        else actions.splice(1,0,[exam.status==='sent'?'Проверить результат':'Проверить / повторить отправку',async()=>{
            await restoreExam(exam);fileImportBusy=false;await analyzeAudio();
        }]);
        for(const [text,action] of actions) {const b=document.createElement('button');b.className='btn btn-sm btn-outline-secondary';b.textContent=text;b.onclick=()=>{if(isRecording || analyzeProgress || fileImportBusy) return;fileImportBusy=true;Promise.resolve().then(action).catch(showFileError).finally(()=>{fileImportBusy=false;});};controls.append(b);} list.append(row);
    }
    if(!exams.length) list.textContent='Локальных обследований нет.';
}
async function submissionExam() {
    const exam=await currentExam();
    if(exam.incomplete || lastRecording.draft) throw new Error('Незавершённое обследование нельзя отправить как полное. Продолжите запись');
    ExamFiles.validate(exam.deviceInfo,audioBuffer.duration);
    const signature=await ExamFiles.hash(new TextEncoder().encode(JSON.stringify({
        patient:exam.patientUuid || exam.patientId,deviceInfo:exam.deviceInfo,calibration:exam.calibration,
        audio:await ExamFiles.hash(await exam.audio.arrayBuffer()),
        calibrationAudio:exam.calibrationWav?await ExamFiles.hash(await exam.calibrationWav.arrayBuffer()):null
    })));
    if(exam.submissionSignature && exam.submissionSignature!==signature) {
        lastRecording.id=crypto.randomUUID();lastRecording.status='saved';lastRecording.studyId=undefined;
    }
    lastRecording.submissionSignature=signature;
    return persistCurrentExam();
}
async function setExamStatus(exam,status,extra={}) {
    const updated={...exam,...extra,status};await ExamFiles.cache('put',updated);
    if(lastRecording?.id===exam.id) Object.assign(lastRecording,{...extra,status});
    await refreshLocalExams();return updated;
}
function analysisForm(exam) {
    const form=new FormData();
    form.append('request_id',exam.id);form.append('file',exam.audio,'examination.wav');
    form.append('patient_id',exam.patientId);
    if(exam.patientUuid) form.append('patient_uuid',exam.patientUuid);
    form.append('device_info',JSON.stringify(exam.deviceInfo));
    if(exam.calibration) form.append('calibration',JSON.stringify(exam.calibration));
    if(exam.calibrationWav) form.append('calibration_file',exam.calibrationWav,'calibration.wav');
    return form;
}
async function checkExamResult(exam,form) {
    const fingerprint=await ExamFiles.requestFingerprint(form);
    const response=await fetch(`${API_URL}/api/analysis-requests/${encodeURIComponent(exam.id)}?fingerprint=${fingerprint}`);
    // A legacy or changed payload must pass the POST's full fingerprint check.
    if(response.status===404 || response.status===409) return null;
    const result=await response.json();
    if(!response.ok) throw new Error(result.error || 'Не удалось проверить результат');
    return result;
}
async function withExamSendLock(exam,action,recover=false) {
    if(typeof navigator==='undefined' || !navigator.locks) return action();
    return navigator.locks.request(`audio-park-send:${exam.owner}:${exam.id}`,{ifAvailable:true},lock=>{
        if(lock) return action();
        if(recover) return;
        throw Object.assign(new Error('Это обследование уже отправляется в другой вкладке'),{busy:true});
    });
}
async function recoverLocalExams() {
    const exams=await ExamFiles.cache('list');
    for(const exam of exams) if(exam.status==='sending') {
        await withExamSendLock(exam,()=>ExamFiles.cache('put',{...exam,status:'error',lastError:'Предыдущая отправка не подтверждена. Проверьте результат или повторите отправку'}),true);
    }
    await refreshLocalExams();
    const pending=exams.filter(exam=>exam.status!=='sent').length;
    if(pending && !lastRecording && !isRecording) {
        fileElement('local-exams-panel').open=true;
        fileElement('file-status').textContent=`Найдено неотправленных обследований: ${pending}. Откройте запись или продолжите её из списка ниже.`;
    }
}
function openBeforeSend() {
    fileElement('before-send-status').textContent='';
    fileElement('before-send').showModal();
}
function initExamFiles() {
    AudioPark.onSessionExpired=()=>showFileError(new Error('Сессия истекла. Скачайте копию и войдите в ту же учётную запись в другой вкладке, затем повторите отправку'));
    AudioPark.beforeLogout=async()=>{
        if(analyzeProgress) {alert('Дождитесь завершения отправки или сохраните копию перед выходом');return false;}
        const draft=RecordingCache.current();
        if(draft?.cache) {
            try {await RecordingCache.checkpoint(draft);}
            catch(error) {showFileError(error);return confirm('Последние фрагменты не удалось сохранить. Сначала скачайте ZIP, нажав «Отмена». Выйти с потерей несохранённых фрагментов?');}
        }
        if(localSaveFailed) return confirm('Запись не сохранена в браузере. Сначала скачайте ZIP, нажав «Отмена». Выйти с потерей несохранённой записи?');
        return true;
    };
    fileElement('retry-local-save').onclick=()=>{
        if(RecordingCache.current()?.cache) return RecordingCache.retry();
        persistCurrentExam().then(()=>{fileElement('retry-local-save').hidden=true;}).catch(showFileError);
    };
    window.addEventListener('beforeunload',event=>{
        if(isRecording || analyzeProgress || localSaveFailed) {event.preventDefault();event.returnValue='';}
    });
    document.addEventListener('visibilitychange',()=>{
        if(document.hidden && rec?.cache) RecordingCache.checkpoint(rec).catch(showFileError);
    });
    fileElement('refresh-local-exams').onclick=()=>refreshLocalExams().catch(showFileError);
    fileElement('clear-local-exams').onclick=async()=>{
        if(isRecording || analyzeProgress || fileImportBusy) return;
        if(!confirm('Удалить все локальные копии вашей учётной записи из этого браузера? Сначала скачайте необходимые ZIP. Серверные результаты сохранятся.')) return;
        fileImportBusy=true;
        try {await ExamFiles.cache('clear');forgetLocalExam();await refreshLocalExams();}
        catch(error) {showFileError(error);} finally {fileImportBusy=false;}
    };
    for(const [id,zip] of [['before-send-zip',true],['before-send-wav',false]]) {
        fileElement(id).onclick=async()=>{
            const button=fileElement(id);button.disabled=true;fileElement('before-send-submit').disabled=true;
            try {
                const exam=await currentExam();
                const blob=zip?await ExamFiles.exportExam(exam):exam.audio;
                downloadFile(blob,zip?'examination.zip':'recording.wav');
                fileElement('before-send-status').textContent='Файл передан браузеру для скачивания. Теперь можно отправить обследование на анализ.';
            } catch(error) {fileElement('before-send-status').textContent='Не удалось скачать: '+error.message;}
            finally {button.disabled=false;fileElement('before-send-submit').disabled=false;}
        };
    }
    fileElement('before-send-cancel').onclick=()=>fileElement('before-send').close();
    fileElement('before-send-submit').onclick=()=>{fileElement('before-send').close();analyzeAudio({backupConfirmed:true});};
    for(const step of Object.values(EXAM_STEPS)) {const option=document.createElement('option');option.value=step.task;option.textContent=step.title;fileElement('import-task').append(option);}
    fileElement('download-wav').onclick=()=>currentExam().then(e=>downloadFile(e.audio,'recording.wav')).catch(showFileError);
    fileElement('download-exam').onclick=()=>currentExam().then(ExamFiles.exportExam).then(b=>downloadFile(b,'examination.zip')).catch(showFileError);
    fileElement('import-exam').onchange=async event=>{
        const file=event.target.files[0];event.target.value='';if(!file) return;
        if(fileImportBusy) return;
        fileImportBusy=true;
        fileElement('file-status').textContent='Проверка и загрузка файла…';
        try {
            if(isRecording || analyzeProgress) throw new Error('Дождитесь завершения записи или анализа');
            if(file.size>ExamFiles.MAX) throw new Error('Размер файла превышает 128 МиБ');
            let exam;
            if(/\.zip$/i.test(file.name)) exam=await ExamFiles.importExam(file,audioContext);
            else {
                if(!/\.(wav|mp3|ogg|oga|webm|m4a|flac|aac)$/i.test(file.name)) throw new Error('Неподдерживаемый формат аудио');
                const buffer=await audioContext.decodeAudioData(await file.arrayBuffer()), task=fileElement('import-task').value;
                const dsi=['mpt','glide','soft','vowel'].includes(task), speech=task!=='vowel' && !['glide','soft'].includes(task);
                const seg={task,start_sec:0,end_sec:buffer.duration};
                exam={id:crypto.randomUUID(),audio:audioBufferToWav(buffer,buffer.sampleRate),buffer,deviceInfo:{task:dsi?'dsi':'speech_protocol',dsi_segments:dsi?[seg]:[],protocol_segments:speech?[seg]:[]}};
                if(fileElement('import-append').checked) {
                    if(!lastRecording || !audioBuffer || lastRecording.deviceInfo.task !== 'exam') throw new Error('Для добавления попытки сначала откройте полное обследование ZIP');
                    const sr=audioBuffer.sampleRate, extra=Math.round(buffer.duration*sr), combined=audioContext.createBuffer(1,audioBuffer.length+extra,sr), data=combined.getChannelData(0), source=buffer.getChannelData(0), offset=audioBuffer.duration;
                    data.set(audioBuffer.getChannelData(0));
                    for(let i=0;i<extra;i++) { const x=i*buffer.sampleRate/sr, k=Math.floor(x), f=x-k;data[audioBuffer.length+i]=(source[k]||0)*(1-f)+(source[Math.min(k+1,source.length-1)]||0)*f; }
                    const info=structuredClone(lastRecording.deviceInfo);
                    for(const key of ['dsi_segments','protocol_segments']) if(exam.deviceInfo[key].length) (info[key] ||= []).push({...seg,start_sec:offset,end_sec:combined.duration});
                    exam={...await currentExam(),id:crypto.randomUUID(),status:'saved',studyId:undefined,submissionSignature:undefined,audio:audioBufferToWav(combined,sr),buffer:combined,deviceInfo:info};
                    // Appending all missing tasks can complete an imported partial backup.
                    try {ExamFiles.validate(info,combined.duration);exam.incomplete=false;} catch(error) {if(!exam.incomplete) throw error;}
                }
                ExamFiles.validate(exam.deviceInfo,exam.buffer.duration,{incomplete:!!exam.incomplete});
            }
            // The local format always stores WAV, even when a legacy ZIP used another codec.
            const restoredBuffer=exam.buffer || await audioContext.decodeAudioData(await exam.audio.arrayBuffer());
            // The chunked transport expects the recorder's standard 44-byte PCM header.
            // Convert once on import; local restores retain these exact bytes for retries.
            exam.audio=audioBufferToWav(restoredBuffer,restoredBuffer.sampleRate);
            if(exam.calibrationWav) {
                const calibrationBuffer=await audioContext.decodeAudioData(await exam.calibrationWav.arrayBuffer());
                exam.calibrationWav=audioBufferToWav(calibrationBuffer,calibrationBuffer.sampleRate);
            }
            await restoreExam(exam,restoredBuffer);
        } catch(error) {showFileError(error);} finally {fileImportBusy=false;}
    };
    recoverLocalExams().catch(showFileError);
}
