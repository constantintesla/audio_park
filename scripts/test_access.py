"""Access regression tests; temporary storage, no production data or credentials."""
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
TEMP = tempfile.TemporaryDirectory()
os.environ['AUTH_DATA_DIR'] = str(Path(TEMP.name) / 'auth')
from app import api
from app.auth import create_user, password_matches


class AccessTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        api.app.config['TESTING'] = True
        api.RESULTS_FILE = str(Path(TEMP.name) / 'results.json')
        api.RESULTS_DIR = str(Path(TEMP.name) / 'results')
        Path(api.RESULTS_DIR).mkdir()
        with api.app.app_context():
            cls.users = {role: create_user(role, 'test-password', role=role if role != 'other' else 'expert')
                         for role in ['expert', 'other', 'moderator', 'admin']}
            cls.users['patient-a'] = create_user('patient-a', 'test-password', role='expert')
            cls.users['patient-b'] = create_user('patient-b', 'test-password', role='expert')

    def setUp(self):
        self.records = [self.record('expert', 'AP-own', 'P-001'), self.record('other', 'AP-other', 'P-002')]
        api.save_results(self.records)

    def record(self, role, study_id, patient):
        audio = Path(api.RESULTS_DIR) / (study_id + '.wav')
        audio.write_bytes(b'RIFF-test-audio')
        return {'study_id':study_id, 'patient_id':patient, 'owner_id':self.users[role]['id'],
                'user_info':{'tg_user_id':self.users[role]['id'], 'tg_username':role, 'timestamp':'2026-10-29T12:00:00'},
                'features':{}, 'dsi':{}, 'symptom_scores':{}, 'audio_summary':{},
                'raw_data':{'files':{'original_audio':str(audio)}}}

    def login(self, role):
        client = api.app.test_client()
        token = client.get('/api/auth/csrf').get_json()['csrf_token']
        response = client.post('/api/auth/login', json={'login':role,'password':'test-password'}, headers={'X-CSRF-Token':token})
        self.assertEqual(response.status_code, 200)
        return client, {'X-CSRF-Token':response.get_json()['csrf_token']}

    def test_guest_has_no_data_access(self):
        client = api.app.test_client()
        for path in ['/api/results', '/api/patients', '/api/stats', '/api/results/AP-own', '/api/audio/AP-own', '/api/visualization/AP-own', '/api/users', '/api/export/csv', '/api/export/html', '/api/export/json']:
            self.assertEqual(client.get(path).status_code, 401, path)
        self.assertEqual(client.get('/').status_code, 302)
        self.assertEqual(client.get('/healthz').get_json(), {'status':'ok'})
        self.assertEqual(client.get('/login').status_code, 200)

    def test_expert_ownership_cannot_be_overridden(self):
        client, headers = self.login('expert')
        for query in ['', '?scope=all', '?scope=all&user_id=' + str(self.users['other']['id'])]:
            data = client.get('/api/results' + query).get_json()
            self.assertTrue(all(r['owner_id'] == self.users['expert']['id'] for r in data['results']))
        for suffix in ['AP-other', '1']:
            for route in ['results', 'audio', 'visualization', 'archive']:
                self.assertEqual(client.get(f'/api/{route}/{suffix}').status_code, 404)
            self.assertEqual(client.get(f'/api/audio/{suffix}/task/0').status_code, 404)
        self.assertEqual(client.get('/api/results/AP-own').status_code, 200)
        self.assertEqual(client.get('/api/audio/AP-own').status_code, 200)
        self.assertEqual(client.get('/api/stats?scope=all').get_json()['total_analyses'], 1)
        for path in ['/users', '/all-results', '/api/users']:
            self.assertEqual(client.get(path).status_code, 403)
        self.assertEqual(client.post('/api/recalculate-all', headers=headers).status_code, 403)
        self.assertEqual(client.post('/api/users', json={'login':'hacker','password':'test-password','role':'admin'}, headers=headers).status_code, 403)

    def test_scoped_exports_and_filters(self):
        client, _ = self.login('expert')
        for fmt in ['json','csv','html']:
            response = client.get('/api/export/' + fmt + '?scope=all')
            self.assertEqual(response.status_code, 200)
            if fmt == 'json':
                self.assertEqual([r['study_id'] for r in response.get_json()['results']], ['AP-own'])
            self.assertNotIn('other', response.get_data(as_text=True))
        self.assertEqual(client.get('/api/results?patient_id=P-002').get_json()['count'], 0)
        self.assertEqual(client.get('/api/results?q=AP-own').get_json()['count'], 1)
        self.assertEqual(client.get('/api/results?date_from=2026-10-30').get_json()['count'], 0)

    def test_both_manager_roles_have_full_access(self):
        for role in ['admin','moderator']:
            client, headers = self.login(role)
            self.assertEqual(client.get('/api/results?scope=all').get_json()['count'], 2)
            self.assertEqual(client.get('/api/results').get_json()['count'], 0)
            self.assertEqual(client.get('/api/audio/AP-other').status_code, 200)
            self.assertEqual(client.get('/all-results').status_code, 200)
            self.assertEqual(client.get('/users').status_code, 200)
            users = client.get('/api/users').get_json()['users']
            self.assertNotIn('password_hash', json.dumps(users))
            response = client.post('/api/users', json={'login':role+'-new','password':'new-test-password','role':'expert'}, headers=headers)
            self.assertEqual(response.status_code, 201)
            self.assertEqual(client.post('/api/users', json={'login':role+'-new','password':'new-test-password'}, headers=headers).status_code, 409)
            data = client.get('/api/export/json?scope=all&owner_id=' + str(self.users['other']['id'])).get_json()
            self.assertEqual([r['study_id'] for r in data['results']], ['AP-other'])

    def test_csrf_logout_and_static_files(self):
        client, headers = self.login('moderator')
        self.assertEqual(client.post('/api/users', json={'login':'bad','password':'test-password'}).status_code, 403)
        for path in ['/results.json', '/.env', '/api.py', '/auth_data/users.sqlite3', '/results/AP-own.wav', '/index.html', '/assets/../auth.py']:
            self.assertEqual(client.get(path).status_code, 404, path)
        self.assertEqual(client.get('/assets/auth.js').status_code, 200)
        with client.session_transaction() as saved_session:
            captured = dict(saved_session)
        self.assertEqual(client.post('/api/auth/logout', headers=headers).status_code, 200)
        self.assertEqual(client.get('/api/results').status_code, 401)
        replay = api.app.test_client()
        with replay.session_transaction() as replay_session:
            replay_session.update(captured)
        self.assertEqual(replay.get('/api/results').status_code, 401)

    def test_scrypt_compatibility(self):
        salt = 'knownSalt'
        hashed = hashlib.scrypt(b'known-password', salt=salt.encode(), n=32768, r=8, p=1, maxmem=64*1024*1024).hex()
        stored = f'scrypt:32768:8:1${salt}${hashed}'
        self.assertTrue(password_matches(stored, 'known-password'))
        self.assertFalse(password_matches(stored, 'wrong-password'))

    def test_patient_numbers_are_local_to_each_doctor(self):
        doctor_a, headers_a = self.login('patient-a')
        doctor_b, headers_b = self.login('patient-b')
        patient_a = doctor_a.post('/api/patients', headers=headers_a).get_json()['patient']
        patient_b = doctor_b.post('/api/patients', headers=headers_b).get_json()['patient']
        self.assertEqual(patient_a['local_number'], 1)
        self.assertEqual(patient_b['local_number'], 1)
        self.assertNotEqual(patient_a['uuid'], patient_b['uuid'])
        self.assertEqual(doctor_a.post('/api/patients', headers=headers_a).get_json()['patient']['local_number'], 2)
        self.assertEqual(doctor_b.post('/api/patients', headers=headers_b).get_json()['patient']['local_number'], 2)
        self.assertEqual(doctor_a.post(f"/api/patients/{patient_b['uuid']}/archive", headers=headers_a).status_code, 404)
        key_a = doctor_a.get('/api/patients/identity-key').get_json()['hmac_key']
        key_b = doctor_b.get('/api/patients/identity-key').get_json()['hmac_key']
        self.assertRegex(key_a, r'^[0-9a-f]{64}$')
        self.assertNotEqual(key_a, key_b)
        lookup_token = 'a' * 64
        self.assertFalse(doctor_a.post('/api/patients/resolve', json={'lookup_token':lookup_token}, headers=headers_a).get_json()['found'])
        resolved_a = doctor_a.post('/api/patients', json={'lookup_token':lookup_token}, headers=headers_a).get_json()['patient']
        self.assertEqual(doctor_a.post('/api/patients/resolve', json={'lookup_token':lookup_token}, headers=headers_a).get_json()['patients'][0]['uuid'], resolved_a['uuid'])
        resolved_b = doctor_b.post('/api/patients', json={'lookup_token':lookup_token}, headers=headers_b).get_json()['patient']
        self.assertNotEqual(resolved_a['uuid'], resolved_b['uuid'])
        linked = doctor_a.post(f"/api/patients/{patient_a['uuid']}/identity", json={'lookup_token':lookup_token}, headers=headers_a)
        self.assertEqual(linked.status_code, 200)
        self.assertEqual(doctor_a.post('/api/patients/resolve', json={'lookup_token':lookup_token}, headers=headers_a).get_json()['count'], 2)

    def test_recalculation_keeps_ids_and_ownership(self):
        client, headers = self.login('moderator')
        class FakeAnalyzer:
            def __init__(self, **kwargs): pass
            def analyze_audio_file(self, *args, **kwargs):
                return {'features':{}, 'report':[]}
        with patch('app.parkinson_analyzer.ParkinsonAnalyzer', FakeAnalyzer):
            response = client.post('/api/recalculate-all', headers=headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['recalculated'], 2)
        after = api.load_results()
        for old, new in zip(self.records, after):
            for key in ['study_id','owner_id','patient_id']:
                self.assertEqual(old[key], new[key])

    def test_analysis_ignores_spoofed_owner_and_saves_once(self):
        client, headers = self.login('expert')
        patient = client.post('/api/patients', headers=headers).get_json()['patient']
        class FakeAnalyzer:
            def __init__(self, **kwargs): pass
            def analyze_audio_file(self, *args, **kwargs):
                return {'features':{},'report':[]}
        with patch('app.parkinson_analyzer.ParkinsonAnalyzer', FakeAnalyzer):
            response = client.post('/api/analyze', data={'file':(io.BytesIO(b'fake'),'test.wav'), 'patient_uuid':patient['uuid'], 'patient_id':'SPOOFED', 'user_id':str(self.users['admin']['id']), 'username':'admin'}, headers=headers)
        self.assertEqual(response.status_code, 200)
        result = response.get_json()
        self.assertEqual(result['owner_id'], self.users['expert']['id'])
        self.assertEqual(result['patient_uuid'], patient['uuid'])
        self.assertEqual(result['patient_id'], patient['label'])
        self.assertEqual(result['user_info']['tg_username'], 'expert')
        self.assertTrue(result['study_id'].startswith('AP-'))
        self.assertEqual(len(api.load_results()), 3)
        self.assertEqual(client.post('/api/results', json=result, headers=headers).status_code, 405)
        self.assertEqual(len(api.load_results()), 3)
        self.assertEqual(client.post('/api/analyze', data={'file':(io.BytesIO(b'x'),'test.wav')}, headers=headers).status_code, 400)


if __name__ == '__main__':
    try:
        unittest.main()
    finally:
        TEMP.cleanup()
