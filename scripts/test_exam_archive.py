"""Re-analyze a reference examination ZIP in isolated storage (no import into real accounts)."""
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main(filename):
    with zipfile.ZipFile(filename) as archive, tempfile.TemporaryDirectory() as tmp:
        expected = json.loads(archive.read('results.json'))
        os.environ['AUTH_DATA_DIR'] = str(Path(tmp) / 'auth')
        from app import api
        from app.auth import create_user
        api.RESULTS_FILE = str(Path(tmp) / 'results.json')
        api.RESULTS_DIR = str(Path(tmp) / 'results')
        Path(api.RESULTS_DIR).mkdir()
        api.app.config['TESTING'] = True
        with api.app.app_context():
            for login, role in [('owner', 'expert'), ('other', 'expert'), ('manager', 'moderator')]:
                create_user(login, 'archive-test-password', role)

        def login(name):
            client = api.app.test_client()
            token = client.get('/api/auth/csrf').get_json()['csrf_token']
            response = client.post('/api/auth/login', json={'login': name, 'password': 'archive-test-password'},
                                   headers={'X-CSRF-Token': token})
            assert response.status_code == 200
            return client, {'X-CSRF-Token': response.get_json()['csrf_token']}

        client, headers = login('owner')
        response = client.post('/api/analyze', headers=headers, data={
            'patient_id': 'ARCHIVE-TEST',
            'file': (io.BytesIO(archive.read('recording.wav')), 'examination.wav'),
            'calibration_file': (io.BytesIO(archive.read('calibration.wav')), 'calibration.wav'),
            'device_info': json.dumps(expected['device_info']),
            'calibration': json.dumps(expected['calibration']),
        })
        assert response.status_code == 200, response.get_data(as_text=True)[:1000]
        result = response.get_json()
        assert result['recording_task'] == 'exam'
        assert len(api.load_results()) == 1
        # Same archived bytes and boundaries through the durable transport.
        import hashlib
        import wave
        from app.upload_worker import process_one
        patient_response = client.post('/api/patients', json={}, headers=headers)
        patient_uuid = patient_response.json['patient']['uuid']
        upload = client.post('/api/uploads', json={'version': 1, 'patient_uuid': patient_uuid}, headers=headers).json['upload_id']
        manifest = {'version': 1, 'files': {}, 'device_info': expected['device_info'], 'calibration': expected['calibration']}
        number = 0
        for name, filename in [('recording', 'recording.wav'), ('calibration', 'calibration.wav')]:
            audio = archive.read(filename)
            with wave.open(io.BytesIO(audio), 'rb') as wav:
                assert wav.getsampwidth() == 2, 'Reference must contain PCM 16-bit WAV'
                spec = {'format': 'pcm_s16le', 'channels': wav.getnchannels(), 'sample_rate': wav.getframerate(),
                        'samples': wav.getnframes(), 'size': len(audio), 'sha256': hashlib.sha256(audio).hexdigest(), 'parts': []}
            for offset in range(0, len(audio), 4 * 1024 * 1024):
                part = audio[offset:offset + 4 * 1024 * 1024]
                checksum = hashlib.sha256(part).hexdigest()
                response = client.put(f'/api/uploads/{upload}/parts/{number}', data=part,
                                      headers={**headers, 'X-Part-Size': str(len(part)), 'X-Part-SHA256': checksum})
                assert response.status_code == 200, response.data
                spec['parts'].append({'number': number, 'size': len(part), 'sha256': checksum})
                number += 1
            manifest['files'][name] = spec
        response = client.post(f'/api/uploads/{upload}/complete', json=manifest, headers=headers)
        assert response.status_code == 202, response.data
        with api.app.app_context():
            assert process_one()
        status = client.get(f'/api/uploads/{upload}').json
        assert status['state'] == 'completed', status
        transported = client.get(f"/api/results/{status['study_id']}").json
        import numpy as np
        # Fixed tolerances: numerical features rtol=1e-5, atol=1e-6;
        # DSI absolute tolerance 0.05 (same as the existing archive check).
        for feature, value in result['features'].items():
            if isinstance(value, (int, float)):
                assert np.isclose(value, transported['features'][feature], rtol=1e-5, atol=1e-6), feature
        assert abs(result['dsi']['dsi_score'] - transported['dsi']['dsi_score']) <= 0.05
        assert transported['task_recordings'] == result['task_recordings']
        original = Path(transported['raw_data']['files']['original_audio']).read_bytes()
        assert original == archive.read('recording.wav')
        attempts = result['task_recordings']
        assert len(attempts) == len(expected['task_recordings']) == 27
        assert len({a['task'] for a in attempts}) == 14
        assert result['speech_protocol']['missing'] == expected['speech_protocol']['missing'] == []
        for key in ['dsi_score']:
            assert abs(result['dsi'][key] - expected['dsi'][key]) <= 0.05, (key, result['dsi'][key], expected['dsi'][key])
        key = result['study_id']
        import soundfile as sf
        for attempt in attempts:
            clip = client.get(f'/api/audio/{key}/task/{attempt["n"]}')
            assert clip.status_code == 200
            samples, sr = sf.read(io.BytesIO(clip.data))
            duration = attempt['end_sec'] - attempt['start_sec']
            assert abs(len(samples) / sr - duration) < 0.002, (attempt['n'], len(samples) / sr, duration)
        response = client.get(f'/api/archive/{key}')
        assert response.status_code == 200
        with zipfile.ZipFile(io.BytesIO(response.data)) as output:
            assert len([n for n in output.namelist() if n.startswith('tasks/')]) == 27
            saved = json.loads(output.read('results.json'))
            assert 'patient_id' not in saved and 'owner_id' not in saved
            assert json.loads(output.read('manifest.json'))['version'] == 1
        for role in ['other', 'manager']:
            actor, _ = login(role)
            expected_status = 404 if role == 'other' else 200
            for route in [f'/api/results/{key}', f'/api/audio/{key}/task/1', f'/api/archive/{key}',
                          f'/api/visualization/{key}?part=waveform']:
                assert actor.get(route).status_code == expected_status, (role, route)
        print(json.dumps({'status': 'ok', 'tasks': 14, 'attempts': len(attempts),
                          'dsi': result['dsi']['dsi_score'], 'reference_dsi': expected['dsi']['dsi_score'],
                          'missing_tasks': result['speech_protocol']['missing']}, ensure_ascii=False))


if __name__ == '__main__':
    main(sys.argv[1])
