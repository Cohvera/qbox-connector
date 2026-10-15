import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import configure


class ConfigurationTests(unittest.TestCase):
    def previous(self, path):
        value = {'host': '192.168.10.90', 'port': 4900, 'database': 'existing', 'user': 'reader', 'password': 'dummy-only'}
        path.write_text(json.dumps(value))
        return value

    def test_database_only_preserves_account(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'connection.json'
            expected = self.previous(path)
            with patch('builtins.input', return_value='chosen'), patch('getpass.getpass') as password:
                configure.configure(path, database_only=True)
            expected['database'] = 'chosen'
            self.assertEqual(json.loads(path.read_text()), expected)
            password.assert_not_called()

    def test_same_account_retains_password_without_printing_it(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'connection.json'
            expected = self.previous(path)
            with patch('builtins.input', return_value=''), patch('getpass.getpass', return_value=''), patch('builtins.print') as output:
                configure.configure(path)
            self.assertEqual(json.loads(path.read_text())['password'], expected['password'])
            self.assertNotIn(expected['password'], str(output.call_args_list))

    def test_changed_host_does_not_reuse_password_or_overwrite_config(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'connection.json'
            expected = self.previous(path)
            with patch('builtins.input', side_effect=['192.168.10.228', '', '', '']), patch('getpass.getpass', return_value=''):
                with self.assertRaisesRegex(ValueError, 'Geen wachtwoord'):
                    configure.configure(path)
            self.assertEqual(json.loads(path.read_text()), expected)
