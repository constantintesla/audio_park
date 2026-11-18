"""Persistent, structured upload events. Never store request bodies or headers."""
import errno
import logging
import re
import sqlite3
import time

from flask import g, has_request_context, jsonify, request
from app.auth import connection

log = logging.getLogger(__name__)
EVENTS = {'snapshot', 'created', 'part_received', 'submitted', 'cancelled',
          'rejected', 'assembling', 'queued', 'analyzing', 'completed', 'failed'}


def client_platform():
    if not has_request_context():
        return None, None
    agent = request.user_agent.string
    platform = next((name for token, name in [('Android', 'Android'), ('iPhone', 'iOS'),
                    ('iPad', 'iOS'), ('Windows', 'Windows'), ('Macintosh', 'macOS'),
                    ('Linux', 'Linux')] if token in agent), 'Другое')
    for token, name in [('YaBrowser', 'Яндекс'), ('EdgA', 'Edge'), ('EdgiOS', 'Edge'),
                        ('Edg', 'Edge'), ('OPR', 'Opera'), ('FxiOS', 'Firefox'),
                        ('Firefox', 'Firefox'), ('CriOS', 'Chrome'), ('Chrome', 'Chrome'),
                        ('Version', 'Safari')]:
        match = re.search(re.escape(token) + r'/(\d{1,4})\b', agent)
        if match:
            return platform, name + ' ' + match[1]
    return platform, 'Другой браузер'


def record_event(event, key=None, *, user_id=None, stage=None, status=None, message=None,
                 part=None, byte_count=None, elapsed=None, db=None, source='live'):
    from app.uploads import database
    if event not in EVENTS:
        raise ValueError('Unknown upload diagnostic event')
    # Route parameters and user-agent strings can contain arbitrary input. Persist
    # only server-generated IDs and a classified platform/browser, never raw values.
    key = key if isinstance(key, str) and re.fullmatch('[0-9a-f]{32}', key) else None
    if user_id is None and has_request_context():
        user_id = (getattr(g, 'user', None) or {}).get('id')
    platform, browser = client_platform()
    def write(conn):
        conn.execute('DELETE FROM upload_events WHERE created_at < ?', (time.time() - 90 * 86400,))
        conn.execute('''INSERT INTO upload_events
            (created_at,upload_id,user_id,event,stage,http_status,message,part_number,byte_count,
             elapsed_seconds,platform,browser,source) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (time.time(), key, user_id, event, stage, status, (message or '')[:500],
             part, byte_count, elapsed, platform, browser, source))
    if db is not None:
        write(db)
    else:
        try:
            with database() as conn:
                write(conn)
        except (OSError, sqlite3.Error):
            # A diagnostic write must not replace the original response or fail a job.
            log.exception('upload_diagnostics_unavailable')


def worker_error(error):
    known = {'Повреждена часть WAV', 'Хэш собранного WAV не совпадает',
             'Параметры WAV не совпадают с манифестом', 'WAV обрезан',
             'Учётная запись недоступна', 'Пациент недоступен',
             'Не подтверждено сохранение исходного WAV'}
    if isinstance(error, OSError) and error.errno == errno.ENOSPC:
        return 'Недостаточно места на диске сервера'
    if isinstance(error, ValueError) and str(error) in known:
        return str(error)
    return 'Ошибка обработки записи (' + type(error).__name__ + '). Подробности — в серверном журнале.'


def init_diagnostics(app):
    from app.uploads import database
    with app.app_context(), database() as db:
        db.executescript('''CREATE TABLE IF NOT EXISTS upload_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, created_at REAL NOT NULL,
            upload_id TEXT, user_id INTEGER, event TEXT NOT NULL, stage TEXT,
            http_status INTEGER, message TEXT, part_number INTEGER, byte_count INTEGER,
            elapsed_seconds REAL, platform TEXT, browser TEXT, source TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS upload_events_upload ON upload_events(upload_id,id);
            CREATE INDEX IF NOT EXISTS upload_events_created ON upload_events(created_at);''')
        db.execute('BEGIN IMMEDIATE')
        # Existing uploads have no recoverable detailed history. Label their first
        # entry as a snapshot rather than inventing events from their current state.
        for row in db.execute('SELECT id,owner,state FROM uploads WHERE NOT EXISTS '
                              '(SELECT 1 FROM upload_events e WHERE e.upload_id=uploads.id)').fetchall():
            record_event('snapshot', row['id'], user_id=row['owner'], stage=row['state'],
                         message='Состояние при включении журнала. Более ранние события не сохранялись.',
                         db=db, source='snapshot')

    @app.get('/api/admin/upload-logs')
    def upload_logs():
        before = request.args.get('before', type=int)
        query = request.args.get('q', '').strip()[:200]
        errors_only = request.args.get('errors') == '1'
        with connection() as auth:
            users = {row['id']: row['login'] for row in auth.execute('SELECT id,login FROM users')}
        matches = [key for key, login in users.items() if query.casefold() in login.casefold()] if query else []
        where, params = ['e.created_at >= ?', '(? IS NULL OR e.id < ?)'], [time.time() - 90 * 86400, before, before]
        if query:
            # Literal substring matching: '%' and '_' in search text are not wildcards.
            term = '%' + query.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
            placeholders = ','.join('?' for _ in matches) or 'NULL'
            where.append(f"(e.upload_id LIKE ? ESCAPE '\\' OR u.result_id LIKE ? ESCAPE '\\' OR e.user_id IN ({placeholders}))")
            params.extend([term, term, *matches])
        if errors_only:
            where.append("(e.http_status >= 400 OR e.event='failed' OR (e.event='snapshot' AND u.state='failed'))")
        with database() as db:
            rows = db.execute('''SELECT e.*,u.state AS current_state,u.result_id,
                u.updated AS last_activity,
                (SELECT count(*) FROM parts p WHERE p.upload_id=e.upload_id) AS received_parts,
                (SELECT coalesce(sum(size),0) FROM parts p WHERE p.upload_id=e.upload_id) AS received_bytes
                FROM upload_events e LEFT JOIN uploads u ON u.id=e.upload_id WHERE '''
                + ' AND '.join(where) + ' ORDER BY e.id DESC LIMIT 101', params).fetchall()
            counts = {row['state']: row['n'] for row in db.execute('SELECT state,count(*) AS n FROM uploads GROUP BY state')}
        events = [{**dict(row), 'login': users.get(row['user_id'])} for row in rows[:100]]
        return jsonify(events=events, has_more=len(rows) > 100,
                       next_before=events[-1]['id'] if len(rows) > 100 else None,
                       upload_counts=counts, retention_days=90)
