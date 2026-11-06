"""Run one durable queue consumer: python -m app.upload_worker.

The OS lock is released on process death; the next consumer recovers interrupted
jobs. API processes never claim jobs. One analysis at a time per shared volume.
"""
import json
import logging
import time

from dotenv import load_dotenv
load_dotenv()

from app import api
from app.auth import connection, get_patient, public_user
from app.storage_lock import file_lock
from app.uploads import assemble, database, digest, root
from flask import g
from werkzeug.datastructures import FileStorage

log = logging.getLogger(__name__)


def process_one():
    with database() as db:
        row = db.execute("SELECT * FROM uploads WHERE state IN ('assembling','queued','analyzing') ORDER BY updated LIMIT 1").fetchone()
    if not row:
        return False
    key, began = row['id'], time.monotonic()
    try:
        manifest = json.loads(row['manifest'])
        with connection() as db:
            user = db.execute('SELECT * FROM users WHERE id=? AND active=1', (row['owner'],)).fetchone()
        if not user:
            raise ValueError('Учётная запись недоступна')
        g.user = public_user(user)
        patient = get_patient(row['patient'], row['owner'])
        if not patient:
            raise ValueError('Пациент недоступен')
        # A crash after committing results but before updating queue must not
        # analyze again. The original WAV is checked before releasing parts.
        existing = next((r for r in api.load_results() if r.get('study_id') == row['result_id']), None)
        if existing:
            result = existing
        else:
            directory = assemble(key, manifest)
            for state in ('queued', 'analyzing'):
                with database() as db:
                    db.execute('UPDATE uploads SET state=?,updated=? WHERE id=?', (state, time.time(), key))
            metadata = {field: json.dumps(manifest.get(field, {})) for field in ('device_info', 'calibration')}
            calibration = None
            try:
                if 'calibration' in manifest['files']:
                    calibration = FileStorage(open(directory / 'calibration.wav', 'rb'), filename='calibration.wav')
                result = api.analyze_and_save(str(directory / 'recording.wav'), row['result_id'],
                                              patient['label'], patient, g.user, metadata,
                                              calibration_file=calibration)
            finally:
                if calibration:
                    calibration.close()
        files = result.get('raw_data', {}).get('files', {})
        for name, field in [('recording', 'original_audio'), ('calibration', 'calibration_audio')]:
            if name in manifest['files'] and (not files.get(field) or digest(files[field]) != manifest['files'][name]['sha256']):
                raise ValueError('Не подтверждено сохранение исходного WAV')
        with database() as db:
            db.execute("UPDATE uploads SET state='completed',error=NULL,updated=? WHERE id=?", (time.time(), key))
        # Success cleanup only after verifying saved WAVs and committing status.
        try:
            cleanup_completed(key)
        except OSError:
            log.warning('upload_cleanup_deferred upload_id=%s', key)
        log.info('upload_completed upload_id=%s seconds=%.2f', key, time.monotonic() - began)
    except Exception:
        log.exception('upload_failed upload_id=%s', key)
        with database() as db:
            db.execute("UPDATE uploads SET state='failed',error=?,updated=? WHERE id=?",
                       ('Не удалось собрать или проанализировать запись. Исходная запись сохранена локально; обратитесь к администратору.', time.time(), key))
    finally:
        if api._malloc_trim:
            import gc
            gc.collect()
            api._malloc_trim(0)
    return True


def cleanup_completed(key):
    import shutil
    directory = root() / key
    if directory.exists():
        shutil.rmtree(directory)


def cleanup_finished():
    """Recover a crash after committing completed but before deleting parts."""
    with database() as db:
        rows = db.execute("SELECT * FROM uploads WHERE state='completed'").fetchall()
    saved = {r.get('study_id'): r for r in api.load_results()}
    for row in rows:
        if not (root() / row['id']).exists():
            continue
        try:
            manifest = json.loads(row['manifest'])
            result = saved[row['result_id']]
            if result.get('owner_id') != row['owner']:
                raise ValueError('Result owner mismatch')
            files = result['raw_data']['files']
            for name, field in [('recording', 'original_audio'), ('calibration', 'calibration_audio')]:
                if name in manifest['files'] and digest(files[field]) != manifest['files'][name]['sha256']:
                    raise ValueError('Saved WAV checksum mismatch')
            cleanup_completed(row['id'])
        except (OSError, ValueError, KeyError):
            log.warning('upload_cleanup_deferred upload_id=%s', row['id'])


def main():
    with api.app.app_context(), file_lock(root() / 'worker.lock', blocking=False):
        cleanup_finished()
        while True:
            if not process_one():
                time.sleep(2)


if __name__ == '__main__':
    main()
