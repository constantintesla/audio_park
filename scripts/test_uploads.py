"""Transport, isolation and crash recovery checks, entirely in temporary storage."""
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
import wave
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
TEMP = tempfile.TemporaryDirectory()
os.environ['AUTH_DATA_DIR'] = str(Path(TEMP.name) / 'auth')
from app import api
from app.auth import create_user
from app.uploads import assemble, database, digest
from app.upload_worker import process_one


def wav_bytes(sr=48000, seconds=1):
    out = io.BytesIO()
    with wave.open(out, 'wb') as wav:
        wav.setparams((1, 2, sr, 0, 'NONE', 'not compressed'))
        # Includes silence, quiet samples and a replaced attempt; all preserved.
        wav.writeframes((b'\0\0' * (sr // 2) + b'\x01\0' * (sr - sr // 2)) * seconds)
    return out.getvalue()


def sha(data):
    return hashlib.sha256(data).hexdigest()


class UploadTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        api.app.config['TESTING'] = True
        api.RESULTS_FILE = str(Path(TEMP.name) / 'results.json')
        api.RESULTS_DIR = str(Path(TEMP.name) / 'results')
        Path(api.RESULTS_DIR).mkdir()
        with api.app.app_context():
            create_user('owner', 'upload-test-password', 'expert')
            create_user('other', 'upload-test-password', 'expert')
            create_user('admin', 'upload-test-password', 'admin')
            create_user('moderator', 'upload-test-password', 'moderator')

    def login(self, name='owner'):
        client = api.app.test_client()
        token = client.get('/api/auth/csrf').json['csrf_token']
        response = client.post('/api/auth/login', json={'login': name, 'password': 'upload-test-password'}, headers={'X-CSRF-Token': token})
        return client, {'X-CSRF-Token': response.json['csrf_token']}

    def setUp(self):
        self.client, self.headers = self.login()
        response = self.client.post('/api/patients', json={}, headers=self.headers)
        self.patient = response.json['patient']['uuid']
        with api.app.app_context(), database() as db:
            db.execute('DELETE FROM upload_events')
            db.execute('DELETE FROM parts')
            db.execute('DELETE FROM uploads')
        api.save_results([])

    def create(self, **extra):
        response = self.client.post('/api/uploads', headers=self.headers, json={'version': 1, 'patient_uuid': self.patient, **extra})
        self.assertIn(response.status_code, (200, 201), response.data)
        return response.json['upload_id']

    def put(self, key, number, data, **extra):
        return self.client.put(f'/api/uploads/{key}/parts/{number}', data=data,
                               headers={**self.headers, 'X-Part-Size': str(len(data)), 'X-Part-SHA256': sha(data), **extra})

    def prepare(self, sr=48000, calibration=False, extra_frames=0):
        key = self.create()
        files, blobs, number = {}, {}, 0
        for name in ['recording'] + (['calibration'] if calibration else []):
            data = wav_bytes(sr)
            if extra_frames:
                out = io.BytesIO()
                with wave.open(out, 'wb') as wav:
                    wav.setparams((1, 2, sr, 0, 'NONE', 'not compressed'))
                    wav.writeframes(data[44:] + b'\0\0' * extra_frames)
                data = out.getvalue()
            parts = []
            for offset in range(0, len(data), 32768):
                block = data[offset:offset + 32768]
                parts.append({'number': number, 'size': len(block), 'sha256': sha(block)})
                blobs[number] = block
                number += 1
            files[name] = {'format': 'pcm_s16le', 'sample_rate': sr, 'channels': 1, 'samples': sr + extra_frames, 'size': len(data), 'sha256': sha(data), 'parts': parts}
        for n in reversed(blobs):
            self.assertEqual(self.put(key, n, blobs[n]).status_code, 200)
        return key, {'version': 1, 'files': files, 'device_info': {'task': 'exam', 'dsi_segments': [{'task': 'mpt', 'start_sec': 0, 'end_sec': 1}], 'protocol_segments': [{'task': 'mpt', 'start_sec': 0, 'end_sec': 1}]}}, blobs

    def complete(self, key, manifest):
        return self.client.post(f'/api/uploads/{key}/complete', json=manifest, headers=self.headers)

    def test_access_csrf_and_idempotent_creation(self):
        key = self.create(client_token='test-idempotency-token')
        self.assertEqual(key, self.create(client_token='test-idempotency-token'))
        other, headers = self.login('other')
        for method, suffix in [('get', ''), ('put', '/parts/0'), ('post', '/complete'), ('post', '/cancel')]:
            response = getattr(other, method)(f'/api/uploads/{key}{suffix}', headers=headers, json={} if method == 'post' else None)
            self.assertEqual(response.status_code, 404)
        self.assertEqual(self.client.post(f'/api/uploads/{key}/cancel').status_code, 403)
        self.assertEqual(api.app.test_client().get(f'/api/uploads/{key}').status_code, 401)

    def test_parts_conflicts_limits_and_cancel(self):
        key = self.create()
        self.assertEqual(self.put(key, 0, b'abc').status_code, 200)
        self.assertEqual(self.put(key, 0, b'abc').status_code, 200)
        self.assertEqual(self.put(key, 0, b'def').status_code, 409)
        self.assertEqual(self.put(key, 1, b'abc', **{'X-Part-SHA256': '0' * 64}).status_code, 400)
        self.assertEqual(self.put(key, 1, b'abc', **{'X-Part-Size': '2'}).status_code, 400)
        self.assertEqual(self.put(key, 128, b'abc').status_code, 400)
        with patch.dict(api.app.config, UPLOAD_PART_BYTES=2):
            response = self.put(key, 1, b'abc')
            self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.post(f'/api/uploads/{key}/cancel', headers=self.headers).status_code, 200)
        self.assertEqual(self.put(key, 1, b'abc').status_code, 409)

    def test_admin_diagnostics_access_privacy_and_rejected_request(self):
        headers = {**self.headers, 'User-Agent': 'Android Chrome/130.0 YaBrowser/25.1 private-agent-secret',
                   'Authorization': 'Bearer private-auth-secret'}
        response = self.client.post('/api/uploads', headers=headers,
                                    json={'version': 1, 'patient_uuid': self.patient,
                                          'client_token': 'private-client-token-123456'})
        key = response.json['upload_id']
        self.assertEqual(self.put(key, 0, b'abc').status_code, 200)
        rejected = self.complete(key, {'version': 999, 'private_metadata': 'never-store-this-payload'})
        self.assertEqual(rejected.status_code, 400)
        for role in ('owner', 'other', 'moderator'):
            client, _ = self.login(role)
            self.assertEqual(client.get('/upload-logs').status_code, 403)
            self.assertEqual(client.get('/api/admin/upload-logs').status_code, 403)
        guest = api.app.test_client()
        self.assertEqual(guest.get('/api/admin/upload-logs').status_code, 401)
        admin, _ = self.login('admin')
        self.assertEqual(admin.get('/upload-logs').status_code, 200)
        result = admin.get('/api/admin/upload-logs?q=' + key).json
        self.assertEqual([row['event'] for row in result['events']], ['rejected', 'part_received', 'created'])
        self.assertEqual(result['events'][0]['message'], 'Неизвестная версия протокола')
        self.assertEqual(result['events'][0]['stage'], 'validating')
        self.assertEqual(result['events'][-1]['platform'], 'Android')
        self.assertEqual(result['events'][-1]['browser'], 'Яндекс 25')
        self.assertEqual(result['events'][0]['current_state'], 'uploading')
        self.assertEqual(result['events'][0]['received_bytes'], 3)
        self.assertTrue(all(row['login'] == 'owner' for row in result['events']))
        serialized = json.dumps(result)
        for secret in ('private-client-token', 'private-agent-secret', 'private-auth-secret',
                       'never-store-this-payload', self.patient, 'session_token', 'password_hash', 'manifest'):
            self.assertNotIn(secret, serialized)
        errors = admin.get('/api/admin/upload-logs?errors=1&q=owner').json['events']
        self.assertEqual([row['event'] for row in errors], ['rejected'])
        self.assertEqual(admin.get('/api/admin/upload-logs?q=%').json['events'], [])

    def test_diagnostics_pagination_retention_and_cleaned_upload(self):
        import time
        from app.upload_diagnostics import record_event
        key = self.create()
        with api.app.app_context(), database() as db:
            user_id = db.execute('SELECT owner FROM uploads WHERE id=?', (key,)).fetchone()['owner']
            db.execute('UPDATE upload_events SET created_at=?', (time.time() - 91 * 86400,))
            for _ in range(105):
                record_event('submitted', key, user_id=user_id, status=202, db=db)
            self.assertEqual(db.execute('SELECT count(*) FROM upload_events').fetchone()[0], 105)
            db.execute('DELETE FROM uploads WHERE id=?', (key,))
        admin, _ = self.login('admin')
        first = admin.get('/api/admin/upload-logs').json
        self.assertEqual(len(first['events']), 100)
        self.assertTrue(first['has_more'])
        second = admin.get('/api/admin/upload-logs?before=' + str(first['next_before'])).json
        self.assertEqual(len(second['events']), 5)
        self.assertFalse(second['has_more'])
        self.assertFalse({row['id'] for row in first['events']} & {row['id'] for row in second['events']})
        self.assertTrue(all(row['current_state'] is None for row in first['events']))

    def test_diagnostics_migration_snapshot_is_honest_and_idempotent(self):
        from flask import Flask
        from app.upload_diagnostics import init_diagnostics
        key = self.create()
        with api.app.app_context(), database() as db:
            db.execute('DELETE FROM upload_events')
        for _ in range(2):
            restarted = Flask('diagnostics-migration')
            restarted.config.update(api.app.config)
            init_diagnostics(restarted)
        with api.app.app_context(), database() as db:
            rows = db.execute('SELECT * FROM upload_events WHERE upload_id=?', (key,)).fetchall()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]['source'], 'snapshot')
            self.assertEqual(rows[0]['event'], 'snapshot')
            self.assertEqual(rows[0]['stage'], 'uploading')

    def test_byte_equivalence_and_complete_race(self):
        for sr in (44100, 48000):
            key, manifest, blobs = self.prepare(sr, calibration=True)
            with ThreadPoolExecutor(2) as pool:
                def finish(_):
                    client, headers = self.login()
                    return client.post(f'/api/uploads/{key}/complete', headers=headers, json=manifest)
                responses = list(pool.map(finish, range(2)))
            self.assertTrue(all(r.status_code == 202 for r in responses))
            self.assertEqual(responses[0].json['job_id'], responses[1].json['job_id'])
            self.assertEqual(self.put(key, 0, blobs[0]).status_code, 409)
            with api.app.app_context():
                directory = assemble(key, manifest)
                self.assertEqual((directory / 'recording.wav').read_bytes(), wav_bytes(sr))
                self.assertEqual((directory / 'calibration.wav').read_bytes(), wav_bytes(sr))
            manifest['device_info']['task'] = 'changed'
            self.assertEqual(self.complete(key, manifest).status_code, 409)

    def test_invalid_manifest_and_corrupt_wav(self):
        key, manifest, _ = self.prepare()
        with api.app.app_context(), database() as db:
            db.execute('DELETE FROM parts WHERE upload_id=? AND number=0', (key,))
        self.assertEqual(self.complete(key, manifest).status_code, 409)
        key, manifest, _ = self.prepare()
        manifest['device_info']['dsi_segments'][0]['end_sec'] = 2
        self.assertEqual(self.complete(key, manifest).status_code, 400)
        manifest['device_info']['dsi_segments'] = []
        manifest['files']['recording']['samples'] += 1
        self.assertEqual(self.complete(key, manifest).status_code, 202)
        with api.app.app_context():
            with self.assertRaises(ValueError):
                assemble(key, manifest)

    def test_worker_recovery_after_result_commit(self):
        key, manifest, _ = self.prepare(calibration=True)
        self.assertEqual(self.complete(key, manifest).status_code, 202)
        def fake_analysis(path, **kwargs):
            import shutil
            directory = Path(api.RESULTS_DIR) / kwargs['result_id']
            directory.mkdir(exist_ok=True)
            shutil.copyfile(path, directory / 'original.wav')
            return {'features': {}, 'raw_data': {'data_directory': str(directory), 'files': {'original_audio': str(directory / 'original.wav')}}}
        # Crash precisely between durable result save and queue commit.
        with api.app.app_context():
            with patch('app.parkinson_analyzer.ParkinsonAnalyzer.analyze_audio_file', side_effect=fake_analysis) as analyzer:
                self.assertTrue(process_one())
                self.assertEqual(analyzer.call_count, 1)
                with database() as db:
                    db.execute("UPDATE uploads SET state='analyzing' WHERE id=?", (key,))
                self.assertTrue(process_one())
                self.assertEqual(analyzer.call_count, 1)
            self.assertEqual(len(api.load_results()), 1)
        self.assertEqual(self.client.get(f'/api/uploads/{key}').json['state'], 'completed')
        self.assertEqual(self.complete(key, manifest).status_code, 202)

    def test_rounded_eof_draft_retries_and_worker(self):
        for sr in (44100, 48000):
            key, manifest, _ = self.prepare(sr, calibration=True, extra_frames=32)
            duration = manifest['files']['recording']['samples'] / sr
            rounded = round(duration, 3)
            self.assertGreater(rounded, duration)
            for field in ('dsi_segments', 'protocol_segments'):
                manifest['device_info'][field][0]['end_sec'] = rounded
            encoded = json.dumps(manifest, sort_keys=True)
            first = self.complete(key, manifest)
            self.assertEqual(first.status_code, 202, first.data)
            self.assertEqual(self.complete(key, manifest).json['job_id'], first.json['job_id'])
            def fake_analysis(path, **kwargs):
                import shutil
                directory = Path(api.RESULTS_DIR) / kwargs['result_id']
                directory.mkdir(exist_ok=True)
                shutil.copyfile(path, directory / 'original.wav')
                return {'features': {}, 'raw_data': {'data_directory': str(directory),
                        'files': {'original_audio': str(directory / 'original.wav')}}}
            with api.app.app_context(), patch('app.parkinson_analyzer.ParkinsonAnalyzer.analyze_audio_file', side_effect=fake_analysis):
                self.assertTrue(process_one())
                with database() as db:
                    row = db.execute('SELECT state,manifest FROM uploads WHERE id=?', (key,)).fetchone()
                self.assertEqual(row['state'], 'completed')
                self.assertEqual(row['manifest'], encoded)
                with database() as db:
                    stages = [r['event'] for r in db.execute('SELECT event FROM upload_events WHERE upload_id=? ORDER BY id', (key,))
                              if r['event'] in ('assembling', 'queued', 'analyzing', 'completed')]
                self.assertEqual(stages, ['assembling', 'queued', 'analyzing', 'completed'])
            self.assertEqual(self.complete(key, manifest).status_code, 202)
            manifest['device_info']['protocol_segments'][0]['end_sec'] = duration
            self.assertEqual(self.complete(key, manifest).status_code, 409)

    def test_invalid_bounds_keep_specific_error_and_allow_correction(self):
        key, manifest, _ = self.prepare(extra_frames=32)
        duration = manifest['files']['recording']['samples'] / 48000
        for segment in ({'start_sec': 0, 'end_sec': duration + 0.0006},
                        {'start_sec': duration, 'end_sec': duration + 0.0001},
                        {'start_sec': -0.001, 'end_sec': 1},
                        {'start_sec': 1, 'end_sec': 1},
                        {'start_sec': True, 'end_sec': 1},
                        {'start_sec': 0, 'end_sec': float('nan')},
                        {'start_sec': 0, 'end_sec': float('inf')}):
            manifest['device_info']['protocol_segments'] = [{'task': 'mpt', **segment}]
            with self.assertLogs('app.uploads', level='WARNING') as messages:
                response = self.complete(key, manifest)
            self.assertEqual(response.status_code, 400)
            self.assertIn('upload_rejected upload_id=' + key, messages.output[0])
            self.assertIn(response.json['error'], ('Границы попытки выходят за запись', 'Некорректный манифест'))
            self.assertEqual(self.client.get(f'/api/uploads/{key}').json['state'], 'uploading')
        manifest['device_info']['protocol_segments'][0] = {'task': 'mpt', 'start_sec': 0, 'end_sec': round(duration, 3)}
        self.assertEqual(self.complete(key, manifest).status_code, 202)

    def test_analysis_recalculation_and_charts_share_one_slot(self):
        import threading
        import time
        owner = self.client.get('/api/auth/me').json['user']['id']
        original = Path(api.RESULTS_DIR) / 'concurrency.wav'
        original.write_bytes(wav_bytes())
        api.save_results([{'study_id': 'AP-concurrency', 'owner_id': owner,
                          'patient_uuid': self.patient, 'patient_id': 'concurrency',
                          'user_info': {'tg_user_id': owner}, 'features': {},
                          'raw_data': {'data_directory': api.RESULTS_DIR, 'files': {'original_audio': str(original)}}}])
        admin, admin_headers = self.login('admin')
        started, mutex = threading.Event(), threading.Lock()
        active, peak = 0, 0
        def heavy():
            nonlocal active, peak
            with mutex:
                active += 1
                peak = max(peak, active)
            started.set()
            time.sleep(0.15)
            with mutex:
                active -= 1
        def analysis(*args, **kwargs):
            heavy()
            return {'features': {}, 'dsi': {}}
        def charts(path, parts):
            heavy()
            return {part: {'checked': True} for part in parts}
        def analyze():
            return self.client.post('/api/analyze', headers=self.headers,
                                    data={'patient_uuid': self.patient, 'file': (io.BytesIO(wav_bytes()), 'exam.wav')})
        chart_client, _ = self.login()
        with patch('app.parkinson_analyzer.ParkinsonAnalyzer.analyze_audio_file', side_effect=analysis), patch('app.api.chart_data', side_effect=charts), ThreadPoolExecutor(3) as pool:
            first = pool.submit(analyze)
            self.assertTrue(started.wait(5))
            second = pool.submit(chart_client.get, '/api/visualization/AP-concurrency?part=spectrogram')
            third = pool.submit(admin.post, '/api/recalculate-all', headers=admin_headers)
            responses = [f.result(timeout=10) for f in (first, second, third)]
        self.assertTrue(all(r.status_code == 200 for r in responses), [r.data for r in responses])
        self.assertEqual(peak, 1, 'Heavy operations overlapped across API requests')

    def test_over_50_mib_and_total_limit(self):
        import struct
        import tracemalloc
        key = self.create()
        total, part_size = 52 * 1024 * 1024, api.app.config['UPLOAD_PART_BYTES']
        header = bytearray(wav_bytes()[:44])
        struct.pack_into('<I', header, 4, total - 8)
        struct.pack_into('<I', header, 40, total - 44)
        block = bytes(part_size)
        h, parts = hashlib.sha256(), []
        for n in range(13):
            part = bytes(header) + block[44:] if n == 0 else block
            h.update(part)
            parts.append({'number': n, 'size': len(part), 'sha256': sha(part)})
            self.assertEqual(self.put(key, n, part).status_code, 200)
        status = self.client.get(f'/api/uploads/{key}').json
        self.assertEqual(sum(p['size'] for p in status['parts']), total)
        with patch.dict(api.app.config, UPLOAD_MAX_BYTES=total):
            response = self.put(key, 13, b'x')
            self.assertEqual(response.status_code, 413)
            self.assertTrue(response.is_json)
        manifest = {'version': 1, 'files': {'recording': {'format': 'pcm_s16le', 'sample_rate': 48000,
                    'channels': 1, 'samples': (total - 44) // 2, 'size': total, 'sha256': h.hexdigest(), 'parts': parts}}}
        self.assertEqual(self.complete(key, manifest).status_code, 202)
        with api.app.app_context():
            tracemalloc.start()
            directory = assemble(key, manifest)
            _, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            self.assertEqual(digest(directory / 'recording.wav'), h.hexdigest())
            self.assertLess(peak, 8 * 1024 * 1024)
            print(f'52 MiB WAV assembly: Python heap peak {peak / 1048576:.2f} MiB')

    def test_disk_full_and_bad_header_do_not_reach_analyzer(self):
        import errno
        key = self.create()
        with patch.dict(api.app.config, TESTING=False), patch('app.uploads.tempfile.mkstemp', side_effect=OSError(errno.ENOSPC, 'test disk full')):
            response = self.put(key, 0, b'abc')
        self.assertEqual(response.status_code, 500)
        self.assertTrue(response.is_json)
        self.assertEqual(self.client.get(f'/api/uploads/{key}').json['parts'], [])
        bad = b'BAD!' + wav_bytes()[4:]
        self.assertEqual(self.put(key, 0, bad).status_code, 200)
        manifest = {'version': 1, 'files': {'recording': {'format': 'pcm_s16le', 'sample_rate': 48000,
                    'channels': 1, 'samples': 48000, 'size': len(bad), 'sha256': sha(bad),
                    'parts': [{'number': 0, 'size': len(bad), 'sha256': sha(bad)}]}}}
        self.assertEqual(self.complete(key, manifest).status_code, 202)
        with api.app.app_context(), patch('app.parkinson_analyzer.ParkinsonAnalyzer.analyze_audio_file') as analyzer:
            self.assertTrue(process_one())
            analyzer.assert_not_called()
        self.assertEqual(self.client.get(f'/api/uploads/{key}').json['state'], 'failed')
        admin, _ = self.login('admin')
        events = admin.get('/api/admin/upload-logs?q=' + key).json['events']
        self.assertEqual(events[0]['event'], 'failed')
        self.assertEqual(events[0]['stage'], 'assembling')
        self.assertIn('Ошибка обработки записи', events[0]['message'])


if __name__ == '__main__':
    unittest.main()
