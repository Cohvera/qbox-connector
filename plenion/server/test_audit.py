import ctypes as C
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import reader


def source(identity, status, number=None):
    return {'source_id': identity, 'number': number or identity, 'status': status}


def exported(*rows, truncated=False):
    return {'source': 'plenion_hfsql_server', 'count': len(rows), 'truncated': truncated,
            'fetched_at': '2026-10-18T16:34:39Z', 'projects': list(rows)}


class AuditTests(unittest.TestCase):
    def test_per_status_includes_null_empty_and_all_versions_without_guessing_labels(self):
        rows = [source('1', '02', '123'), source('2', '07', '123'),
                source('3', None), source('4', ''), source('5', ' 07 ')]
        report = reader.audit_status(rows, exported(*rows), 'ActualStatus')
        self.assertTrue(report['complete'])
        self.assertTrue(report['all_versions'])
        self.assertEqual(sum(row['database_records'] for row in report['statuses']), 5)
        self.assertEqual({row['status'] for row in report['statuses']}, {'02', '07', None, '', ' 07 '})
        self.assertEqual(report['status_basis'], 'current_database_by_record_id')

    def test_equal_totals_do_not_hide_missing_and_extra_id(self):
        report = reader.audit_status([source('1', '02'), source('2', '07')],
                                     exported(source('1', '02'), source('3', '07')), 'ActualStatus')
        self.assertFalse(report['complete'])
        self.assertEqual((report['missing_records'], report['extra_records']), (1, 1))
        self.assertEqual(next(row for row in report['statuses'] if row['status'] == '07')['missing_records'], 1)

    def test_duplicate_export_and_changed_project_number_are_not_complete(self):
        report = reader.audit_status([source('1', '07')],
                                     exported(source('1', '07', 'wrong'), source('1', '07', 'wrong')), 'ActualStatus')
        self.assertFalse(report['complete'])
        self.assertEqual(report['duplicate_export_ids'], 1)
        self.assertEqual(report['changed_project_numbers'], 1)

    def test_truncation_and_invalid_export_are_never_accepted(self):
        rows = [source('1', '07')]
        with self.assertRaisesRegex(ValueError, 'afgekapt'):
            reader.audit_status(rows, exported(*rows), 'Status', truncated=True)
        self.assertFalse(reader.audit_status(rows, exported(*rows, truncated=True), 'Status')['complete'])
        for value in ({}, [], exported({'number': '1'}), dict(exported(*rows), count=2)):
            with self.subTest(value=value), self.assertRaises(ValueError):
                reader.audit_status(rows, value, 'Status')
        with self.assertRaisesRegex(ValueError, 'dubbele'):
            reader.audit_status(rows * 2, exported(*rows), 'Status')

    def test_column_is_one_identifier_not_sql(self):
        self.assertEqual(reader.status_query('ActualStatus'),
                         'SELECT IDProj,projnr,PROJ.ActualStatus FROM PROJ ORDER BY IDProj')
        for value in (None, '', 'Status;DELETE FROM PROJ', 'Status WHERE 1=1', 'x.y', 'x--', 'a' * 129):
            with self.subTest(value=value), self.assertRaises(ValueError):
                reader.status_query(value)

    def test_columns_describe_empty_result_without_fetching_business_data(self):
        lib = MagicMock()
        for name in ('SQLAllocHandle', 'SQLSetEnvAttr', 'SQLDriverConnect', 'SQLExecDirect'):
            getattr(lib, name).return_value = 0
        def columns(stmt, count):
            count._obj.value = 2
            return 0
        def describe(stmt, index, name, buflen, length, kind, size, digits, nullable):
            name.value = (b'IDProj', b'ActualStatus')[index - 1]
            length._obj.value = len(name.value)
            kind._obj.value = 12
            size._obj.value = 100
            return 0
        lib.SQLNumResultCols.side_effect = columns
        lib.SQLDescribeCol.side_effect = describe
        with patch('reader.socket.create_connection'), patch('reader.odbc', return_value=lib):
            rows, truncated = reader.read({'host': 'localhost', 'database': 'fixture', 'user': 'reader', 'password': 'test'}, 2000, 'columns')
        self.assertEqual([row['name'] for row in rows], ['IDProj', 'ActualStatus'])
        self.assertFalse(truncated)
        lib.SQLFetch.assert_not_called()
        self.assertEqual(lib.SQLExecDirect.call_args.args[1], b'SELECT * FROM PROJ WHERE 1=0')

    def test_audit_read_keeps_raw_status_and_uses_only_selected_column(self):
        lib = MagicMock()
        for name in ('SQLAllocHandle', 'SQLSetEnvAttr', 'SQLDriverConnect', 'SQLExecDirect'):
            getattr(lib, name).return_value = 0
        def columns(stmt, count):
            count._obj.value = 3
            return 0
        values = [b'1', b'123', b' 07 ']
        def field(stmt, index, kind, buf, length, size):
            buf.value = values[index - 1]
            size._obj.value = len(buf.value)
            return 0
        lib.SQLNumResultCols.side_effect = columns
        lib.SQLGetData.side_effect = field
        lib.SQLFetch.side_effect = [0, 100]
        with patch('reader.socket.create_connection'), patch('reader.odbc', return_value=lib):
            rows, truncated = reader.read({'host': 'localhost', 'database': 'fixture', 'user': 'reader', 'password': 'test'}, 20000, 'audit', 'ActualStatus')
        self.assertEqual(rows, [{'source_id': '1', 'number': '123', 'status': ' 07 '}])
        self.assertFalse(truncated)
        self.assertEqual(lib.SQLExecDirect.call_args.args[1], reader.status_query('ActualStatus').encode())

    def test_truncated_column_name_is_not_used(self):
        lib = MagicMock()
        def columns(stmt, count):
            count._obj.value = 1
            return 0
        lib.SQLNumResultCols.side_effect = columns
        lib.SQLDescribeCol.return_value = 1
        with self.assertRaisesRegex(ValueError, 'Onvolledige kolomnaam'):
            reader.describe_columns(lib, C.c_void_p(), 'utf-8')

    def test_cli_report_does_not_overwrite_export_and_mismatch_exits_two(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / 'connection.json'
            config.write_text(json.dumps({'database': 'fixture'}))
            export = root / 'projects.json'
            export.write_text(json.dumps(exported(source('1', '07'))))
            original = export.read_bytes()
            output = root / 'audit-status.json'
            args = ['reader.py', '--worker', '--mode', 'audit', '--status-column', 'ActualStatus',
                    '--config', str(config), '--export', str(export), '--output', str(output)]
            with patch('sys.argv', args), patch('reader.read', return_value=([source('2', '07')], False)), patch('builtins.print'):
                with self.assertRaises(SystemExit) as error:
                    reader.main()
            self.assertEqual(error.exception.code, 2)
            self.assertEqual(export.read_bytes(), original)
            self.assertFalse(json.loads(output.read_text())['complete'])
            for target in (export, config):
                with patch('sys.argv', args[:-1] + [str(target)]), patch('reader.read') as read:
                    with self.assertRaisesRegex(ValueError, 'niet overschrijven'):
                        reader.main()
                    read.assert_not_called()


if __name__ == '__main__':
    unittest.main()
