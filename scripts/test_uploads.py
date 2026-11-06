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

    def prepare(self, sr=48000, calibration=False):
        key = self.create()
        files, blobs, number = {}, {}, 0
        for name in ['recording'] + (['calibration'] if calibration else []):
            data = wav_bytes(sr)
            parts = []
            for offset in range(0, len(data), 32768):
                block = data[offset:offset + 32768]
                parts.append({'number': number, 'size': len(block), 'sha256': sha(block)})
                blobs[number] = block
                number += 1
            files[name] = {'format': 'pcm_s16le', 'sample_rate': sr, 'channels': 1, 'samples': sr, 'size': len(data), 'sha256': sha(data), 'parts': parts}
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


if __name__ == '__main__':
    unittest.main()
