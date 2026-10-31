/* Patient names are stored by the API; UUIDs preserve existing study links. */
let patientRecords = [];
let editingPatient = null;
let patientPickerBusy = false;
const patientElement = id => document.getElementById(id);
const normalizedPatientName = value => value.normalize('NFKC').toLocaleLowerCase('ru-RU').replaceAll('ё', 'е').trim().replace(/\s+/g, ' ');

async function initPatientPicker() {
    patientElement('patient-search').addEventListener('input', renderPatients);
    patientElement('add-patient-btn').addEventListener('click', () => openPatientEditor());
    patientElement('change-patient-btn').addEventListener('click', clearPatientSelection);
    patientElement('retry-patients-btn').addEventListener('click', loadPatients);
    patientElement('cancel-patient-btn').addEventListener('click', () => patientElement('patient-editor').close());
    patientElement('patient-editor-form').addEventListener('submit', savePatient);
    for (const id of ['patient-surname', 'patient-name', 'patient-patronymic']) {
        patientElement(id).addEventListener('input', () => {
            patientElement('patient-duplicate-confirm').checked = false;
            updatePatientDuplicate();
        });
    }
    patientElement('patient-editor').addEventListener('cancel', event => {
        if (patientPickerBusy) event.preventDefault();
    });
    try { await AudioPark.ready; await loadPatients(); }
    catch (error) { showPatientError(error.message); }
}

async function loadPatients() {
    patientElement('retry-patients-btn').hidden = true;
    showPatientError('');
    try {
        const response = await fetch(`${API_URL}/api/patients`);
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || 'Не удалось загрузить пациентов');
        patientRecords = data.patients || [];
        renderPatients();
    } catch (error) {
        patientElement('patient-list').replaceChildren();
        patientElement('patient-list-count').textContent = 'Список пациентов не загружен';
        patientElement('retry-patients-btn').hidden = false;
        showPatientError(error.message);
    }
}

function patientVisitText(patient) {
    const visit = patient.last_visit ? new Date(patient.last_visit).toLocaleDateString('ru-RU') : '—';
    return patient.study_count ? `Последний визит: ${visit} · Обследований: ${patient.study_count}` : 'Обследований пока нет';
}

function renderPatients() {
    const query = normalizedPatientName(patientElement('patient-search').value);
    const terms = query.split(' ').filter(Boolean);
    const visible = patientRecords.filter(patient => terms.every(term => normalizedPatientName(patient.full_name || '').includes(term)))
        .sort((a, b) => (a.full_name || 'я').localeCompare(b.full_name || 'я', 'ru') || a.local_number - b.local_number);
    patientElement('patient-list-count').textContent = query ? `Найдено: ${visible.length}` : `Всего пациентов: ${patientRecords.length}`;
    const list = patientElement('patient-list');
    list.replaceChildren();
    if (!visible.length) {
        const message = document.createElement('p'); message.className = 'hint py-3 mb-0';
        message.textContent = patientRecords.length ? 'По этому ФИО никого не найдено. Проверьте написание или добавьте пациента.' : 'Пациентов пока нет. Нажмите «Добавить пациента», чтобы создать первую карточку.';
        list.append(message); return;
    }
    for (const patient of visible) {
        const row = document.createElement('div'); row.className = 'patient-row';
        const description = document.createElement('div');
        const name = document.createElement('strong'); name.textContent = patient.full_name || 'ФИО не указано';
        const meta = document.createElement('small');
        meta.textContent = `${patientVisitText(patient)} · Карточка №${patient.local_number}`;
        description.append(name, meta);
        const actions = document.createElement('div'); actions.className = 'patient-row-actions';
        const edit = document.createElement('button'); edit.type = 'button'; edit.className = 'btn btn-sm btn-outline-secondary';
        edit.textContent = patient.full_name ? 'Изменить ФИО' : 'Указать ФИО';
        edit.addEventListener('click', () => openPatientEditor(patient)); actions.append(edit);
        if (patient.full_name) {
            const choose = document.createElement('button'); choose.type = 'button'; choose.className = 'btn btn-sm btn-primary'; choose.textContent = 'Выбрать';
            choose.addEventListener('click', () => selectPatient(patient)); actions.append(choose);
        }
        row.append(description, actions); list.append(row);
    }
}

function openPatientEditor(patient = null) {
    editingPatient = patient;
    patientElement('patient-editor-form').reset();
    const parts = (patient?.full_name || '').split(' ');
    ['patient-surname', 'patient-name', 'patient-patronymic'].forEach((id, index) => {
        patientElement(id).value = index === 2 ? parts.slice(2).join(' ') : (parts[index] || '');
    });
    patientElement('patient-editor-title').textContent = patient ? (patient.full_name ? 'Изменить ФИО' : 'Указать ФИО пациента') : 'Добавить пациента';
    patientElement('patient-editor-hint').textContent = patient ? `Карточка №${patient.local_number}. ${patientVisitText(patient)}. Все обследования сохранятся в этой карточке.` : 'ФИО сохраняется в вашей учётной записи.';
    patientElement('save-patient-btn').textContent = 'Сохранить и выбрать';
    patientElement('patient-editor-error').hidden = true;
    updatePatientDuplicate();
    patientElement('patient-editor').showModal();
    patientElement('patient-surname').focus();
}

function enteredPatientName() {
    return ['patient-surname', 'patient-name', 'patient-patronymic'].map(id => patientElement(id).value.trim().replace(/\s+/g, ' ')).filter(Boolean).join(' ');
}

function updatePatientDuplicate() {
    const duplicate = !editingPatient && patientRecords.some(patient => patient.full_name && normalizedPatientName(patient.full_name) === normalizedPatientName(enteredPatientName()));
    patientElement('patient-duplicate').hidden = !duplicate;
    patientElement('patient-duplicate-confirm').required = duplicate;
}

async function savePatient(event) {
    event.preventDefault();
    if (patientPickerBusy) return;
    patientPickerBusy = true;
    patientElement('patient-editor-error').hidden = true;
    const controls = [...patientElement('patient-editor-form').elements];
    controls.forEach(control => { control.disabled = true; });
    patientElement('save-patient-btn').textContent = 'Сохранение…';
    try {
        const response = await fetch(`${API_URL}/api/patients${editingPatient ? '/' + encodeURIComponent(editingPatient.uuid) : ''}`, {
            method: editingPatient ? 'PATCH' : 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({full_name: enteredPatientName()})
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || 'Не удалось сохранить пациента');
        const patient = {...(editingPatient || {study_count: 0, last_visit: null}), ...data.patient};
        patientRecords = patientRecords.filter(item => item.uuid !== patient.uuid);
        patientRecords.push(patient); renderPatients(); selectPatient(patient);
        patientElement('patient-editor').close();
        patientElement('change-patient-btn').focus();
    } catch (error) {
        patientElement('patient-editor-error').textContent = error.message;
        patientElement('patient-editor-error').hidden = false;
    } finally {
        patientPickerBusy = false;
        controls.forEach(control => { control.disabled = false; });
        patientElement('save-patient-btn').textContent = 'Сохранить и выбрать';
    }
}

function selectPatient(patient) {
    patientElement('patient-id').value = patient.full_name;
    patientElement('patient-uuid').value = patient.uuid;
    patientElement('patient-selected-label').textContent = patient.full_name;
    patientElement('patient-selected-meta').textContent = patientVisitText(patient);
    patientElement('patient-selected').hidden = false;
    patientElement('patient-picker').hidden = true;
    showPatientError('');
}

function clearPatientSelection() {
    if (isRecording) return;
    patientElement('patient-id').value = '';
    patientElement('patient-uuid').value = '';
    patientElement('patient-selected').hidden = true;
    patientElement('patient-picker').hidden = false;
    patientElement('patient-search').value = '';
    renderPatients(); patientElement('patient-search').focus();
}

function showPatientError(message) {
    patientElement('patient-error').textContent = message;
    patientElement('patient-error').hidden = !message;
}

function setPatientPickerDisabled(disabled) {
    for (const id of ['patient-id', 'patient-uuid', 'change-patient-btn']) patientElement(id).disabled = disabled;
    for (const element of document.querySelectorAll('#patient-picker input, #patient-picker button')) element.disabled = disabled;
}
