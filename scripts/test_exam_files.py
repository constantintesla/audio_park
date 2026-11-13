"""Self-contained archive interoperability and idempotent API regression tests."""
import gc
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main():
    with tempfile.TemporaryDirectory() as directory:
        tmp = Path(directory)
        os.environ['AUTH_DATA_DIR'] = str(tmp / 'auth')
        from app import api
        from app.auth import create_user
        from app.task_recordings import TASK_NAMES, build_archive
        from app.exam_archive import validate_segments
        wav = tmp / 'recording.wav'
        sf.write(wav, np.sin(np.arange(14 * 16000) * .05) * .2, 16000)
        segments = [{'task': task, 'start_sec': i, 'end_sec': i + 1} for i, task in enumerate(TASK_NAMES)]
        info = {'task': 'exam', 'device_id': 'private-device',
                'dsi_segments': [s for s in segments if s['task'] in ('mpt', 'glide', 'soft', 'vowel')],
                'protocol_segments': [s for s in segments if s['task'] not in ('glide', 'soft', 'vowel')]}
        validate_segments(info, 14)
        result = {'study_id': 'fixture', 'device_info': info, 'calibration': {'noise_floor_dbfs': -70},
                  'owner_id': 123, 'patient_id': 'PRIVATE NAME', 'user_info': {'secret': 'PRIVATE'},
                  'raw_data': {'files': {'original_audio': str(wav), 'calibration_audio': str(wav)}}}
        (tmp / 'server.zip').write_bytes(build_archive(result))
        with zipfile.ZipFile(tmp / 'server.zip') as archive:
            data = json.loads(archive.read('results.json'))
            assert 'patient_id' not in data and 'owner_id' not in data and 'raw_data' not in data
            assert 'device_id' not in data['device_info']
            with zipfile.ZipFile(tmp / 'legacy.zip', 'w', zipfile.ZIP_DEFLATED) as legacy:
                for name in archive.namelist():
                    if name != 'manifest.json':
                        legacy.writestr(name, archive.read(name))
        with zipfile.ZipFile(tmp / 'unsafe.zip', 'w') as unsafe:
            unsafe.writestr('../recording.wav', b'bad')
        subprocess.run(['node', str(ROOT / 'scripts/test_exam_files.cjs'), str(tmp)], check=True)
        fingerprint = json.loads((tmp / 'fingerprint.json').read_text(encoding='utf-8'))
        fields = dict(fingerprint['fields'])
        fields.pop('patient_id')  # Authorized UUID replaces the mutable patient label.
        digest = hashlib.sha256(json.dumps(sorted(fields.items()), ensure_ascii=False).encode())
        with zipfile.ZipFile(tmp / 'server.zip') as archive:
            for key, filename, source in [('calibration_file', 'calibration.wav', 'calibration.wav'),
                                          ('file', 'examination.wav', 'recording.wav')]:
                digest.update(key.encode()); digest.update(filename.encode()); digest.update(archive.read(source))
        assert digest.hexdigest() == fingerprint['hash'], 'Browser/server fingerprint mismatch'
        with zipfile.ZipFile(tmp / 'browser.zip') as archive:
            manifest = json.loads(archive.read('manifest.json'))
            assert manifest['version'] == 1
            samples, sr = sf.read(io.BytesIO(archive.read('recording.wav')))
            assert len(samples) / sr == 14
        api.RESULTS_FILE = str(tmp / 'results.json')
        api.RESULTS_DIR = str(tmp / 'results')
        api.app.config['TESTING'] = True
        with api.app.app_context():
            create_user('owner', 'archive-test-password', 'expert')
            create_user('other', 'archive-test-password', 'expert')
        def login(name):
            client = api.app.test_client()
            token = client.get('/api/auth/csrf').get_json()['csrf_token']
            response = client.post('/api/auth/login', json={'login': name, 'password': 'archive-test-password'}, headers={'X-CSRF-Token': token})
            assert response.status_code == 200
            return client, {'X-CSRF-Token': response.get_json()['csrf_token']}
        # Exercise the real analysis pipeline, then repeat with a lost-response style retry.
        client, headers = login('owner')
        def send(client=client, headers=headers, patient='TEST', metadata=None):
            return client.post('/api/analyze', headers=headers, data={
                'request_id': 'archive-request-123456', 'patient_id': patient,
                'file': (io.BytesIO(wav.read_bytes()), 'examination.wav'),
                'device_info': json.dumps(metadata or {'task': 'vowel'})})
        # Both first requests arrive before either has a stored result.
        actors = [login('owner') for _ in range(2)]
        barrier = Barrier(2)
        def cold_request(index):
            barrier.wait(timeout=10)
            actor, auth = actors[index]
            response = send(actor, auth)
            assert response.status_code == 200, response.get_data(as_text=True)[:1000]
            return response.get_json()['study_id']
        with ThreadPoolExecutor(max_workers=2) as pool:
            studies = list(pool.map(cold_request, range(2)))
        study = studies[0]
        assert studies == [study, study]
        lookup = client.get('/api/analysis-requests/archive-request-123456')
        assert lookup.status_code == 200 and lookup.get_json()['study_id'] == study
        stored_fingerprint = api.load_results()[0]['request_fingerprint']
        assert client.get('/api/analysis-requests/archive-request-123456?fingerprint=' + stored_fingerprint).status_code == 200
        assert client.get('/api/analysis-requests/archive-request-123456?fingerprint=' + '0' * 64).status_code == 409
        assert send().get_json()['study_id'] == study
        assert send(patient='CHANGED').status_code == 409
        assert len(api.load_results()) == 1
        with ThreadPoolExecutor(max_workers=2) as pool:
            def retry(_):
                actor, auth = login('owner')
                return send(actor, auth).get_json()['study_id']
            assert list(pool.map(retry, range(2))) == [study, study]
        actor, auth = login('other')
        assert actor.get('/api/analysis-requests/archive-request-123456').status_code == 404
        assert actor.get('/api/analysis-requests/archive-request-123456',
                         headers={'X-AudioPark-Owner': str(1)}).status_code == 409
        assert send(actor, auth).get_json()['study_id'] != study
        assert len(api.load_results()) == 2
        assert api.app.test_client().get('/api/analysis-requests/archive-request-123456').status_code == 401
        invalid = client.post('/api/analyze', headers=headers, data={
            'request_id': 'invalid-segments-12345', 'patient_id': 'TEST',
            'file': (io.BytesIO(wav.read_bytes()), 'examination.wav'),
            'device_info': json.dumps({'task': 'exam'})})
        assert invalid.status_code == 400
        assert len(api.load_results()) == 2
        print('archive API: real analysis, retries, changed payload, concurrent retries, owner isolation, invalid boundaries: ok')
        gc.collect()  # sqlite3 contexts commit but rely on collection to close on Windows.


if __name__ == '__main__':
    main()
