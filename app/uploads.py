"""Version 1: immutable WAV byte parts, SQLite queue, owner-scoped API."""
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import time
import uuid
import wave
from contextlib import contextmanager

from flask import current_app, g, jsonify, request
from werkzeug.exceptions import BadRequest, Conflict, NotFound, RequestEntityTooLarge
from app.auth import get_patient

log = logging.getLogger(__name__)
TERMINAL = ('completed', 'failed', 'cancelled')


def root():
    path = Path(current_app.config['UPLOAD_DIR'])
    path.mkdir(parents=True, exist_ok=True)
    return path


@contextmanager
def database():
    db = sqlite3.connect(root() / 'uploads.sqlite3', timeout=30)
    db.row_factory = sqlite3.Row
    try:
        with db:
            db.execute('BEGIN IMMEDIATE')
            yield db
    finally:
        db.close()


def owned(db, key):
    row = db.execute('SELECT * FROM uploads WHERE id=? AND owner=?', (key, g.user['id'])).fetchone()
    if not row:
        raise NotFound('Загрузка не найдена')
    if not get_patient(row['patient'], g.user['id']):
        raise NotFound('Пациент не найден')
    return row


def digest(path):
    h = hashlib.sha256()
    with open(path, 'rb') as file:
        for block in iter(lambda: file.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def integer(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise BadRequest('Недопустимое целое число в манифесте')
    return value


def sha(value):
    if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{64}', value):
        raise BadRequest('Некорректный SHA-256')
    return value


def validate_manifest(data, parts):
    cfg = current_app.config
    if not isinstance(data, dict) or type(data.get('version')) is not int or data.get('version') != 1:
        raise BadRequest('Неизвестная версия протокола')
    files = data.get('files')
    if not isinstance(files, dict) or 'recording' not in files or set(files) - {'recording', 'calibration'}:
        raise BadRequest('Необходим WAV обследования')
    expected, total = {}, 0
    for spec in files.values():
        if not isinstance(spec, dict) or spec.get('format') != 'pcm_s16le':
            raise BadRequest('Ожидается PCM 16 bit WAV')
        integer(spec.get('sample_rate'), 8000, 96000)
        integer(spec.get('channels'), 1, 2)
        integer(spec.get('samples'), 1, cfg['UPLOAD_MAX_SECONDS'] * spec['sample_rate'])
        total += integer(spec.get('size'), 44, cfg['UPLOAD_MAX_BYTES'])
        sha(spec.get('sha256'))
        if not isinstance(spec.get('parts'), list) or not spec['parts']:
            raise BadRequest('Отсутствует список частей')
        size = 0
        for part in spec['parts']:
            if not isinstance(part, dict):
                raise BadRequest('Некорректная часть')
            n = integer(part.get('number'), 0, cfg['UPLOAD_MAX_PARTS'] - 1)
            if n in expected:
                raise BadRequest('Повтор номера части')
            size += integer(part.get('size'), 1, cfg['UPLOAD_PART_BYTES'])
            expected[n] = (part['size'], sha(part.get('sha256')))
        if size != spec['size']:
            raise BadRequest('Размер файла не совпадает с частями')
    if total > cfg['UPLOAD_MAX_BYTES']:
        raise RequestEntityTooLarge('Превышен общий размер записи')
    actual = {p['number']: (p['size'], p['sha256']) for p in parts}
    if actual != expected:
        raise Conflict('Не все части получены или их хэши не совпадают')
    info = data.get('device_info', {})
    if not isinstance(info, dict) or not isinstance(data.get('calibration', {}), dict):
        raise BadRequest('Некорректные метаданные')
    duration = files['recording']['samples'] / files['recording']['sample_rate']
    for field in ('dsi_segments', 'protocol_segments'):
        segments = info.get(field, [])
        if not isinstance(segments, list) or len(segments) > 500:
            raise BadRequest('Некорректная разметка')
        for segment in segments:
            try:
                start, end = segment['start_sec'], segment['end_sec']
                if type(start) not in (int, float) or type(end) not in (int, float) or not 0 <= start < end <= duration:
                    raise ValueError()
            except (TypeError, KeyError, ValueError):
                raise BadRequest('Границы попытки выходят за запись')


def init_uploads(app):
    app.config.update(
        UPLOAD_DIR=os.getenv('UPLOAD_DIR', str(Path(app.config['AUTH_DB_PATH']).parent / 'uploads')),
        UPLOAD_PART_BYTES=4 * 1024 * 1024,
        UPLOAD_MAX_BYTES=int(os.getenv('UPLOAD_MAX_BYTES', 96 * 1024 * 1024)),
        UPLOAD_MAX_SECONDS=int(os.getenv('UPLOAD_MAX_SECONDS', 900)),
        UPLOAD_MAX_PARTS=128,
        UPLOAD_MAX_ACTIVE=3,
    )
    with app.app_context(), database() as db:
        db.executescript('''
            CREATE TABLE IF NOT EXISTS uploads (
                id TEXT PRIMARY KEY, owner INTEGER NOT NULL, patient TEXT NOT NULL,
                state TEXT NOT NULL, result_id TEXT NOT NULL UNIQUE,
                manifest TEXT, error TEXT, updated REAL NOT NULL, client_token TEXT);
            CREATE TABLE IF NOT EXISTS parts (
                upload_id TEXT NOT NULL, number INTEGER NOT NULL, size INTEGER NOT NULL,
                sha256 TEXT NOT NULL, PRIMARY KEY(upload_id, number));
        ''')
        if 'client_token' not in {r['name'] for r in db.execute('PRAGMA table_info(uploads)')}:
            db.execute('ALTER TABLE uploads ADD COLUMN client_token TEXT')
        db.execute('CREATE UNIQUE INDEX IF NOT EXISTS upload_client_token ON uploads(owner,client_token)')

    @app.get('/api/uploads/config')
    def upload_config():
        allowed = os.getenv('CHUNKED_UPLOAD_USERS', '').split(',')
        return jsonify(enabled=os.getenv('CHUNKED_UPLOAD_ENABLED', 'false').lower() == 'true' or g.user['login'] in allowed,
                       version=1, part_bytes=app.config['UPLOAD_PART_BYTES'],
                       max_bytes=app.config['UPLOAD_MAX_BYTES'], max_seconds=app.config['UPLOAD_MAX_SECONDS'])

    @app.errorhandler(RequestEntityTooLarge)
    def too_large(error):
        return jsonify(error='Превышен допустимый размер. Скачайте исходную запись.'), 413

    @app.post('/api/uploads')
    def create_upload():
        data = request.get_json()
        if not isinstance(data, dict) or type(data.get('version')) is not int or data.get('version') != 1:
            raise BadRequest('Неизвестная версия протокола')
        patient_uuid = data.get('patient_uuid')
        if not isinstance(patient_uuid, str) or len(patient_uuid) > 64:
            raise BadRequest('Выберите пациента')
        patient = get_patient(patient_uuid, g.user['id'])
        if not patient or patient['status'] != 'active':
            raise BadRequest('Выберите активного пациента')
        token = data.get('client_token')
        if token is not None and (not isinstance(token, str) or not re.fullmatch('[a-zA-Z0-9-]{16,64}', token)):
            raise BadRequest('Некорректный ключ повтора')
        key = uuid.uuid4().hex
        with database() as db:
            old = db.execute('SELECT * FROM uploads WHERE owner=? AND client_token=?', (g.user['id'], token)).fetchone() if token else None
            if old:
                if old['patient'] != patient['uuid']:
                    raise Conflict('Ключ повтора уже использован')
                return jsonify(upload_id=old['id']), 200
            active = db.execute("SELECT count(*) FROM uploads WHERE owner=? AND state NOT IN ('completed','failed','cancelled')", (g.user['id'],)).fetchone()[0]
            if active >= app.config['UPLOAD_MAX_ACTIVE']:
                raise Conflict('Слишком много незавершённых загрузок')
            db.execute('INSERT INTO uploads VALUES (?,?,?,?,?,?,?,?,?)',
                       (key, g.user['id'], patient['uuid'], 'uploading', 'AP-' + uuid.uuid4().hex, None, None, time.time(), token))
        log.info('upload_created upload_id=%s', key)
        return jsonify(upload_id=key), 201

    @app.get('/api/uploads/<key>')
    def upload_status(key):
        with database() as db:
            row = owned(db, key)
            parts = [dict(p) for p in db.execute('SELECT number,size,sha256 FROM parts WHERE upload_id=? ORDER BY number', (key,))]
            return jsonify(upload_id=key, state=row['state'], parts=parts, error=row['error'],
                           study_id=row['result_id'] if row['state'] == 'completed' else None)

    @app.put('/api/uploads/<key>/parts/<int:number>')
    def put_part(key, number):
        integer(number, 0, app.config['UPLOAD_MAX_PARTS'] - 1)
        with database() as db:
            row = owned(db, key)
            if row['state'] != 'uploading':
                raise Conflict('Загрузка уже зафиксирована')
            db.execute('UPDATE uploads SET updated=? WHERE id=?', (time.time(), key))
        expected_hash = sha(request.headers.get('X-Part-SHA256'))
        expected_size = request.headers.get('X-Part-Size', '')
        if not re.fullmatch('[0-9]{1,10}', expected_size):
            raise BadRequest('Укажите размер части')
        expected_size = integer(int(expected_size), 1, app.config['UPLOAD_PART_BYTES'])
        directory = root() / key
        directory.mkdir(exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=directory, suffix='.tmp')
        try:
            h, size = hashlib.sha256(), 0
            with os.fdopen(fd, 'wb') as out:
                while True:
                    block = request.stream.read(min(65536, app.config['UPLOAD_PART_BYTES'] + 1 - size))
                    if not block:
                        break
                    size += len(block)
                    if size > app.config['UPLOAD_PART_BYTES']:
                        raise RequestEntityTooLarge()
                    out.write(block)
                    h.update(block)
                out.flush()
                os.fsync(out.fileno())
            if size != expected_size or h.hexdigest() != expected_hash:
                raise BadRequest('Размер или SHA-256 части не совпадает')
            with database() as db:
                row = owned(db, key)
                if row['state'] != 'uploading':
                    raise Conflict('Загрузка уже зафиксирована')
                old = db.execute('SELECT * FROM parts WHERE upload_id=? AND number=?', (key, number)).fetchone()
                if old:
                    if (old['size'], old['sha256']) != (size, expected_hash):
                        raise Conflict('Часть с другим содержимым уже принята')
                else:
                    total = db.execute('SELECT coalesce(sum(size),0) FROM parts WHERE upload_id=?', (key,)).fetchone()[0]
                    if total + size > app.config['UPLOAD_MAX_BYTES']:
                        raise RequestEntityTooLarge()
                    os.replace(tmp, directory / str(number))
                    db.execute('INSERT INTO parts VALUES (?,?,?,?)', (key, number, size, expected_hash))
                db.execute('UPDATE uploads SET updated=? WHERE id=?', (time.time(), key))
            log.info('upload_part upload_id=%s number=%s bytes=%s duplicate=%s', key, number, size, bool(old))
            return jsonify(number=number, size=size, sha256=expected_hash)
        finally:
            Path(tmp).unlink(missing_ok=True)

    @app.post('/api/uploads/<key>/complete')
    def complete_upload(key):
        raw = request.stream.read(128 * 1024 + 1)
        if len(raw) > 128 * 1024:
            raise RequestEntityTooLarge()
        try:
            manifest = json.loads(raw)
            encoded = json.dumps(manifest, sort_keys=True, allow_nan=False)
        except (ValueError, TypeError):
            raise BadRequest('Некорректный манифест')
        with database() as db:
            row = owned(db, key)
            if row['manifest']:
                if row['manifest'] != encoded:
                    raise Conflict('Манифест уже зафиксирован')
            else:
                if row['state'] != 'uploading':
                    raise Conflict('Загрузка отменена')
                parts = db.execute('SELECT * FROM parts WHERE upload_id=?', (key,)).fetchall()
                validate_manifest(manifest, parts)
                db.execute("UPDATE uploads SET manifest=?,state='assembling',updated=? WHERE id=?", (encoded, time.time(), key))
            return jsonify(upload_id=key, job_id=row['result_id']), 202

    @app.post('/api/uploads/<key>/cancel')
    def cancel_upload(key):
        with database() as db:
            row = owned(db, key)
            if row['state'] == 'completed':
                return jsonify(state='completed')
            if row['state'] not in ('uploading', 'cancelled', 'failed'):
                raise Conflict('Сборка или анализ уже начались')
            db.execute("UPDATE uploads SET state='cancelled',updated=? WHERE id=?", (time.time(), key))
        return jsonify(state='cancelled')

    # Upload errors are JSON, including malformed requests and unexpected disk errors.
    @app.after_request
    def upload_errors(response):
        if request.path.startswith('/api/uploads') and response.status_code >= 400 and not response.is_json:
            messages = {400: 'Некорректный запрос загрузки', 404: 'Загрузка не найдена',
                        409: 'Конфликт загрузки: проверьте принятые части', 500: 'Не удалось сохранить запись. Повторите позже.'}
            response.set_data(json.dumps({'error': messages.get(response.status_code, 'Ошибка загрузки')}, ensure_ascii=False))
            response.content_type = 'application/json'
        return response


def assemble(key, manifest):
    """Streaming byte concatenation and PCM/header checks; never re-encode."""
    directory = root() / key
    for name, spec in manifest['files'].items():
        target = directory / (name + '.wav')
        tmp = directory / (name + '.assembling')
        with open(tmp, 'wb') as out:
            for part in spec['parts']:
                source = directory / str(part['number'])
                h, size = hashlib.sha256(), 0
                with open(source, 'rb') as inp:
                    for block in iter(lambda: inp.read(1024 * 1024), b''):
                        out.write(block)
                        h.update(block)
                        size += len(block)
                if (size, h.hexdigest()) != (part['size'], part['sha256']):
                    raise ValueError('Повреждена часть WAV')
            out.flush()
            os.fsync(out.fileno())
        if tmp.stat().st_size != spec['size'] or digest(tmp) != spec['sha256']:
            raise ValueError('Хэш собранного WAV не совпадает')
        with wave.open(str(tmp), 'rb') as wav:
            if (wav.getsampwidth(), wav.getframerate(), wav.getnchannels(), wav.getnframes(), wav.getcomptype()) != (
                    2, spec['sample_rate'], spec['channels'], spec['samples'], 'NONE'):
                raise ValueError('Параметры WAV не совпадают с манифестом')
            size = 0
            while True:
                block = wav.readframes(65536)
                if not block:
                    break
                size += len(block)
            if size != spec['samples'] * spec['channels'] * 2:
                raise ValueError('WAV обрезан')
        os.replace(tmp, target)
    return directory
