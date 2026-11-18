"""Durable, owner-scoped retry serialization. A committed study survives a lost response."""
import hashlib
import json
import os
import re
import sqlite3
from contextlib import contextmanager
from functools import wraps
from flask import current_app, g, jsonify, request


@contextmanager
def submission_guard(owner, token):
    """Serialize creation/legacy analysis of the same owner-scoped examination."""
    from app.storage_lock import file_lock
    key = hashlib.sha256(f'{owner}:{token}'.encode()).hexdigest()
    directory = os.path.join(os.path.dirname(current_app.config['AUTH_DB_PATH']), 'submission-locks')
    with file_lock(os.path.join(directory, key + '.lock')):
        yield


def upload_for_request(owner, token):
    from app.uploads import database
    with database() as db:
        row = db.execute('SELECT * FROM uploads WHERE owner=? AND client_token=?', (owner, token)).fetchone()
        return dict(row) if row else None


def completed_request(results, owner, token):
    """Also resolve studies created before chunked uploads stored request IDs."""
    result = next((r for r in results if str(r.get('owner_id')) == str(owner)
                   and r.get('request_id') == token), None)
    if result:
        return result
    upload = upload_for_request(owner, token)
    if not upload or upload['state'] != 'completed':
        return None
    return next((r for r in results if str(r.get('owner_id')) == str(owner)
                 and r.get('study_id') == upload['result_id']), None)


def matches_upload(upload, fields, files):
    """Compare verified whole-file bytes and original metadata, not a client claim."""
    try:
        manifest = json.loads(upload['manifest'])
        if fields.get('patient_uuid') != upload['patient']:
            return False
        if set(fields) - {'request_id', 'patient_uuid', 'patient_id', 'device_info', 'calibration'}:
            return False
        for key in ('device_info', 'calibration'):
            actual = json.loads(fields.get(key) or '{}')
            expected = manifest.get(key, {})
            if json.dumps(actual, sort_keys=True, allow_nan=False) != json.dumps(expected, sort_keys=True, allow_nan=False):
                return False
        expected_files = {'file': manifest['files']['recording']}
        if 'calibration' in manifest['files']:
            expected_files['calibration_file'] = manifest['files']['calibration']
        return (set(files) == set(expected_files) and all(
            files[key]['size'] == spec['size'] and files[key]['sha256'] == spec['sha256']
            for key, spec in expected_files.items()))
    except (ValueError, TypeError, KeyError):
        return False


def idempotent_analysis(func):
    @wraps(func)
    def wrapped(*args, **kwargs):
        from app import api
        request_id = request.form.get('request_id')
        if not request_id:
            return func(*args, **kwargs)
        if not re.fullmatch(r'[A-Za-z0-9-]{16,80}', request_id):
            return jsonify(error='Некорректный идентификатор запроса'), 400
        fields = dict(request.form)
        if fields.get('patient_uuid'):
            fields.pop('patient_id', None)  # The API derives the label from the authorized UUID.
        digest = hashlib.sha256(json.dumps(sorted(fields.items()), ensure_ascii=False).encode())
        files = {}
        for key, file in sorted(request.files.items()):
            digest.update(key.encode())
            digest.update((file.filename or '').encode())
            checksum, size = hashlib.sha256(), 0
            while True:
                chunk = file.stream.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
                checksum.update(chunk)
                size += len(chunk)
            file.stream.seek(0)
            files[key] = {'size': size, 'sha256': checksum.hexdigest()}
        fingerprint = digest.hexdigest()
        # SQLite serializes cooperating worker processes, not just Python threads.
        os.makedirs(api.RESULTS_DIR, exist_ok=True)
        with submission_guard(g.user['id'], request_id):
            return execute(func, args, kwargs, request_id, fields, files, fingerprint)
    return wrapped


def execute(func, args, kwargs, request_id, fields, files, fingerprint):
    from app import api
    db = sqlite3.connect(os.path.join(api.RESULTS_DIR, '.analysis-requests.sqlite3'), timeout=600)
    try:
        db.execute('CREATE TABLE IF NOT EXISTS requests (owner TEXT, request_id TEXT, PRIMARY KEY(owner, request_id))')
        db.execute('BEGIN IMMEDIATE')
        owner = str(g.user['id'])
        with api.RESULTS_LOCK:
            results = api.load_results()
        for result in results:
            if str(result.get('owner_id')) == owner and result.get('request_id') == request_id:
                if result.get('deleted_at'):
                    return jsonify(error='Исследование удалено. Создайте новое обследование'), 410
                if result.get('request_fingerprint') != fingerprint:
                    return jsonify(error='Данные запроса изменились. Создайте новое обследование'), 409
                return jsonify(result), 200
        upload = upload_for_request(g.user['id'], request_id)
        if upload:
            if upload['state'] != 'completed':
                return jsonify(error='Отправка этого обследования частями уже начата. Продолжите её из сохранённой копии.'), 409
            result = next((r for r in results if str(r.get('owner_id')) == owner
                           and r.get('study_id') == upload['result_id']), None)
            if result and result.get('deleted_at'):
                return jsonify(error='Исследование удалено. Создайте новое обследование'), 410
            if not result or not matches_upload(upload, fields, files):
                return jsonify(error='Данные запроса изменились или результат недоступен. Создайте новое обследование'), 409
            return jsonify(result), 200
        g.analysis_request_id = request_id
        g.analysis_fingerprint = fingerprint
        response = func(*args, **kwargs)
        db.commit()
        return response
    finally:
        db.close()
