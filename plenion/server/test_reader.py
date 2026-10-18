import json
from pathlib import Path
import tempfile
import subprocess
import unittest
from unittest.mock import patch
import reader

class ReaderTests(unittest.TestCase):
    def test_connection_values_cannot_inject_attributes(self):
        self.assertEqual(reader.attr('normal-value'), 'normal-value')
        with self.assertRaises(ValueError): reader.attr('a};PWD=other')
        with self.assertRaises(ValueError): reader.attr('bad\0value')

    def test_atomic_private_output_and_previous_file_survives_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'projects.json'
            reader.save(path, {'projects': [{'name': 'Énergie'}]})
            original = path.read_bytes()
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            with patch('reader.os.replace', side_effect=OSError('test')):
                with self.assertRaises(OSError): reader.save(path, {'projects': []})
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(len(list(Path(directory).iterdir())), 1)

    def test_reject_invalid_port_before_connecting(self):
        with self.assertRaises(ValueError): reader.read({'host': 'localhost', 'port': 99999, 'database': 'test'}, 5)

    def test_projects_need_database_before_any_network_call(self):
        with patch('reader.socket.create_connection') as connect:
            with self.assertRaisesRegex(ValueError, 'Kies eerst'):
                reader.read({'host': 'localhost', 'database': ''}, 5)
            connect.assert_not_called()

    def test_login_without_preconnection_access_mode_still_uses_fixed_select(self):
        from unittest.mock import MagicMock
        lib = MagicMock()
        pending_attributes = []
        for name in ('SQLAllocHandle', 'SQLSetEnvAttr', 'SQLExecDirect'):
            getattr(lib, name).return_value = 0
        def set_attribute(handle, attribute, value, length):
            pending_attributes.append(attribute)
            return 0
        def connect(*args):
            # Model the observed driver/manager combination: the manager
            # accepts the option, but applying it during login blocks.
            if 101 in pending_attributes:
                raise RuntimeError('Access mode set before login blocks this driver')
            return 0
        def columns(stmt, count):
            count._obj.value = len(reader.FIELDS)
            return 0
        lib.SQLSetConnectAttr.side_effect = set_attribute
        lib.SQLDriverConnect.side_effect = connect
        lib.SQLNumResultCols.side_effect = columns
        lib.SQLFetch.return_value = 100
        with patch('reader.socket.create_connection'), patch('reader.odbc', return_value=lib):
            rows, truncated = reader.read(
                {'host': 'localhost', 'database': 'fixture', 'user': 'reader', 'password': 'test'}, 5)
        self.assertEqual(rows, [])
        self.assertFalse(truncated)
        lib.SQLExecDirect.assert_called_once()
        self.assertEqual(lib.SQLExecDirect.call_args.args[1], reader.QUERY.encode())
        self.assertTrue(reader.QUERY.startswith('SELECT '))

    def test_catalog_uses_metadata_and_never_project_query(self):
        import ctypes as C
        from unittest.mock import MagicMock
        lib = MagicMock()
        for name in ('SQLAllocHandle', 'SQLSetEnvAttr', 'SQLSetConnectAttr', 'SQLDriverConnect', 'SQLTables'):
            getattr(lib, name).return_value = 0
        def cols(stmt, value):
            value._obj.value = 5
            return 0
        lib.SQLNumResultCols.side_effect = cols
        lib.SQLFetch.side_effect = [0, 100]
        def field(stmt, index, kind, buf, length, size):
            if index == 1:
                buf.value = b'actual_catalog'
                size._obj.value = len(buf.value)
            else:
                size._obj.value = -1
            return 0
        lib.SQLGetData.side_effect = field
        with patch('reader.socket.create_connection'), patch('reader.odbc', return_value=lib):
            rows, truncated = reader.read({'host': 'localhost', 'user': 'reader', 'password': 'test'}, 10, 'databases')
        self.assertEqual(rows[0]['database'], 'actual_catalog')
        self.assertFalse(truncated)
        lib.SQLExecDirect.assert_not_called()
        self.assertEqual(lib.SQLTables.call_args.args[1:], (b'%', -3, b'', 0, b'', 0, b'', 0))
        self.assertNotIn(b'Database=', lib.SQLDriverConnect.call_args.args[2])

    def encoded_project_driver(self, name):
        from unittest.mock import MagicMock
        lib = MagicMock()
        for function in ('SQLAllocHandle', 'SQLSetEnvAttr', 'SQLDriverConnect', 'SQLExecDirect'):
            getattr(lib, function).return_value = 0
        def columns(stmt, count):
            count._obj.value = len(reader.FIELDS)
            return 0
        values = [b'1', b'123', b'42', name, b'Project', b'2026-10-18']
        def field(stmt, index, kind, buf, length, size):
            buf.value = values[index - 1]
            size._obj.value = len(buf.value)
            return 0
        lib.SQLNumResultCols.side_effect = columns
        lib.SQLFetch.side_effect = [0, 100]
        lib.SQLGetData.side_effect = field
        return lib

    def test_cp1252_source_keeps_accents_euro_and_quotes_in_utf8_json(self):
        text = 'Caf\u00e9 \u20ac \u201cProject\u201d'
        lib = self.encoded_project_driver(text.encode('cp1252'))
        config = {'host': 'localhost', 'database': 'fixture', 'user': 'reader',
                  'password': 'test', 'encoding': 'cp1252'}
        with patch('reader.socket.create_connection'), patch('reader.odbc', return_value=lib):
            rows, truncated = reader.read(config, 5)
        self.assertEqual(rows[0]['name'], text)
        self.assertFalse(truncated)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'projects.json'
            reader.save(path, {'projects': rows})
            self.assertEqual(json.loads(path.read_bytes().decode('utf-8'))['projects'][0]['name'], text)

    def test_utf8_default_still_rejects_cp1252_without_exposing_field_data(self):
        lib = self.encoded_project_driver(b'private-caf\xe9')
        config = {'host': 'localhost', 'database': 'fixture', 'user': 'reader', 'password': 'test'}
        with patch('reader.socket.create_connection'), patch('reader.odbc', return_value=lib):
            with self.assertRaisesRegex(ValueError, './plenion.sh encoding') as error:
                reader.read(config, 5)
        self.assertNotIn('private', str(error.exception))

    def test_blocked_driver_is_reported_by_supervisor(self):
        with patch('sys.argv', ['reader.py']), patch('reader.subprocess.run', side_effect=subprocess.TimeoutExpired('reader', 90)):
            with self.assertRaisesRegex(RuntimeError, '90 seconden'):
                reader.main()

if __name__ == '__main__': unittest.main()
