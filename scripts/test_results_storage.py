"""Atomic JSON persistence and existing production data, using isolated storage."""
import errno
import gc
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main():
    with tempfile.TemporaryDirectory() as temporary:
        directory = Path(temporary)
        data = directory / 'data'
        data.mkdir()
        results_file = data / 'results.json'
        # Production's host file already exists before the updated container starts.
        previous = [{'study_id': 'AP-existing', 'owner_id': 1, 'patient_id': 'TEST',
                     'request_id': 'existing-request-123456'}]
        results_file.write_text(json.dumps(previous), encoding='utf-8')
        os.environ['AUTH_DATA_DIR'] = str(data / 'auth')
        os.environ['RESULTS_FILE'] = str(results_file)
        from app import api

        class ResultsStorageTest(unittest.TestCase):
            def setUp(self):
                results_file.write_text(json.dumps(previous), encoding='utf-8')

            def test_startup_reads_existing_configured_file(self):
                self.assertEqual(api.RESULTS_FILE, str(results_file))
                self.assertEqual(api.load_results(), previous)

            def test_atomic_save_preserves_existing_study_and_retry_id(self):
                records = api.load_results() + [{'study_id': 'AP-new', 'owner_id': 1}]
                replace = os.replace
                calls = []

                def checked_replace(source, destination):
                    # Both names must be inside the same mounted parent directory.
                    self.assertEqual(Path(source).parent, data)
                    self.assertEqual(Path(destination), results_file)
                    calls.append((source, destination))
                    replace(source, destination)

                with patch.object(api.os, 'replace', side_effect=checked_replace):
                    api.save_results(records)
                self.assertEqual(len(calls), 1)
                self.assertEqual(json.loads(results_file.read_text(encoding='utf-8')), records)
                self.assertEqual(api.load_results()[0]['request_id'], 'existing-request-123456')
                self.assertEqual(list(data.glob('.results-*.tmp')), [])

            def test_failed_replacement_keeps_original_file(self):
                before = results_file.read_bytes()
                with self.assertLogs(api.logger, level='ERROR'), patch.object(api.os, 'replace', side_effect=OSError(errno.EBUSY, 'Device or resource busy')):
                    with self.assertRaises(OSError):
                        api.save_results([{'study_id': 'must-not-overwrite'}])
                self.assertEqual(results_file.read_bytes(), before)
                self.assertEqual(list(data.glob('.results-*.tmp')), [])

            def test_failed_flush_keeps_original_file(self):
                before = results_file.read_bytes()
                with self.assertLogs(api.logger, level='ERROR'), patch.object(api.os, 'fsync', side_effect=OSError(errno.ENOSPC, 'No space left')):
                    with self.assertRaises(OSError):
                        api.save_results([{'study_id': 'must-not-overwrite'}])
                self.assertEqual(results_file.read_bytes(), before)
                self.assertEqual(list(data.glob('.results-*.tmp')), [])

        try:
            result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(ResultsStorageTest))
        finally:
            gc.collect()  # Close sqlite connection cycles before Windows removes fixture storage.
        if not result.wasSuccessful():
            raise SystemExit(1)


if __name__ == '__main__':
    main()
