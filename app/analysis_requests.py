"""Durable, owner-scoped retry serialization. A committed study survives a lost response."""
import hashlib
import json
import os
import re
import sqlite3
from functools import wraps
from flask import g, jsonify, request


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
        for key, file in sorted(request.files.items()):
            digest.update(key.encode())
            digest.update((file.filename or '').encode())
            while True:
                chunk = file.stream.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
            file.stream.seek(0)
        fingerprint = digest.hexdigest()
        # SQLite serializes cooperating worker processes, not just Python threads.
        os.makedirs(api.RESULTS_DIR, exist_ok=True)
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
            g.analysis_request_id = request_id
            g.analysis_fingerprint = fingerprint
            response = func(*args, **kwargs)
            db.commit()
            return response
        finally:
            db.close()
    return wrapped
