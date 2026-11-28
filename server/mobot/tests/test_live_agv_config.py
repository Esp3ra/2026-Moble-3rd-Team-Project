"""Private temporary settings only; no real device or production database."""
import json
from contextlib import closing
import os
import sqlite3
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from configure_live_agv import configure, local_address, settings_path
from run_live_agv import load_configuration, read_device_key, check_journal_before_control, StartupBlocked


class AgvConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'factory-v1.json'
        self.key = self.path.with_name('private.h')
        self.key.write_text('#define AGV_DEVICE_KEY "' + 'a' * 64 + '"\n')
        self.original = {'mode': 'HARDWARE', 'camera_indices': {'cam1': 2},
                         'devices': {'LINE': {'base_url': 'http://127.0.0.1:18101'},
                                     'AGV': {'base_url': 'http://127.0.0.1:8765',
                                             'controller_token': 'c' * 40,
                                             'request_timeout_seconds': 2,
                                             'custom_option': 'retain'}},
                         'device_tokens': {'LINE': 'line', 'AGV': 'e' * 40}}
        self.path.write_text(json.dumps(self.original))

    def setup_profile(self):
        configure(self.path, 'http://192.0.2.61', self.key, command_timeout=45,
                  device_timeout=1)

    def test_preserves_settings_and_keeps_three_timeouts_independent(self):
        self.setup_profile()
        configure(self.path)  # Omitted values must survive a repeat setup.
        settings = json.loads(self.path.read_text())
        self.assertEqual(settings, self.original)
        agv, event, profile = load_configuration(self.path)
        self.assertEqual((agv['request_timeout_seconds'], profile['command_timeout_seconds'],
                          profile['device_request_timeout_seconds']), (2, 45, 1))
        self.assertEqual(profile['events_url'], 'http://127.0.0.1:8000/api/v1/device-events')
        self.assertEqual(read_device_key(self.key), 'a' * 64)
        self.assertNotIn('a' * 64, self.path.with_name('agv-adapter.json').read_text())
        self.assertTrue(list(self.path.parent.glob('factory-v1.json.before-agv-*.bak')))

    def test_default_settings_follow_business_database_location(self):
        with patch.dict(os.environ, {'MOBOT_DB': str(self.path.with_name('other.sqlite3'))}):
            self.assertEqual(settings_path(), self.path)

    def test_invalid_listener_and_timings_do_not_write_settings(self):
        before = self.path.read_bytes()
        for value in ['http://127.0.0.1', 'http://127.0.0.1:0', 'https://adapter.internal:8443',
                      'http://192.0.2.1:8765', 'http://127.0.0.1:8765/path']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                configure(self.path, adapter_url=value)
        for value in [0, -1, float('nan'), float('inf')]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                configure(self.path, 'http://192.0.2.61', self.key, command_timeout=value)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse(self.path.with_name('agv-adapter.json').exists())

    def test_listener_mismatch_and_wrong_callback_are_rejected(self):
        self.setup_profile()
        path = self.path.with_name('agv-adapter.json')
        original = json.loads(path.read_text())
        for update in [{'listen_port': 8000}, {'events_url': 'http://127.0.0.1:8000/device/v1/events'}]:
            path.write_text(json.dumps(original | update))
            with self.assertRaises(ValueError):
                load_configuration(self.path)

    def test_simulation_configuration_is_not_converted_to_hardware(self):
        self.path.write_text(json.dumps(self.original | {'mode': 'SIMULATION'}))
        with self.assertRaises(ValueError):
            self.setup_profile()
        self.assertEqual(json.loads(self.path.read_text())['mode'], 'SIMULATION')

    def test_recovery_journal_is_rejected_without_mutating_old_evidence(self):
        journal = self.path.with_name('journal.sqlite3').resolve()
        with closing(sqlite3.connect(journal)) as db, db:
            db.execute('CREATE TABLE agv_meta (key TEXT PRIMARY KEY, value TEXT)')
            db.execute('CREATE TABLE agv_commands (result TEXT)')
            db.execute("INSERT INTO agv_meta VALUES ('lane',?)", (json.dumps({'phase': 'READY', 'recovery': True}),))
        with self.assertRaises(StartupBlocked):
            check_journal_before_control(journal)
        with closing(sqlite3.connect(journal)) as db, db:
            self.assertTrue(json.loads(db.execute('SELECT value FROM agv_meta').fetchone()[0])['recovery'])
            db.execute('UPDATE agv_meta SET value=?', (json.dumps({'phase': 'CLOSED', 'recovery': False}),))
        check_journal_before_control(journal)
        with closing(sqlite3.connect(journal)) as db, db:
            db.execute('INSERT INTO agv_commands VALUES (?)', (json.dumps({'state': 'UNKNOWN'}),))
        check_journal_before_control(journal)  # Explicitly reconciled history is retained.
        with closing(sqlite3.connect(journal)) as db, db:
            db.execute('INSERT INTO agv_commands VALUES (?)', (json.dumps({'state': 'RUNNING'}),))
        with self.assertRaises(StartupBlocked):
            check_journal_before_control(journal)


if __name__ == '__main__':
    unittest.main()
