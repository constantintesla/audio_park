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
            assert saved['patient_id'] == 'ARCHIVE-TEST'
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
