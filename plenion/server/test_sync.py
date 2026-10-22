import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch, MagicMock
import urllib.error
import sync

CONFIG = {'portal_url':'https://portal.example', 'api_key':'a'*43}
PAYLOAD = json.dumps({'source':'plenion_reader_projects','schema_version':2,'truncated':False}).encode()


class SyncTests(unittest.TestCase):
    def test_https_only_and_no_embedded_credentials_or_paths(self):
        self.assertEqual(sync.validate(CONFIG), 'https://portal.example'+sync.ENDPOINT)
        for url in ['http://portal.example', 'https://user:pass@portal.example', 'https://portal.example/other', 'https://portal.example?key=x']:
            with self.assertRaises(ValueError):
                sync.validate({**CONFIG,'portal_url':url})

    def test_upload_uses_key_header_and_requires_confirmation(self):
        opener = MagicMock()
        opener.open.return_value.__enter__.return_value.read.return_value = b'{"accepted":true,"count":2}'
        with patch('sync.urllib.request.build_opener', return_value=opener):
            self.assertEqual(sync.upload(CONFIG,PAYLOAD)['count'],2)
        request = opener.open.call_args.args[0]
        self.assertEqual(request.get_header('X-qbox-key'), CONFIG['api_key'])
        self.assertEqual(request.data, PAYLOAD)
        self.assertEqual(request.full_url, 'https://portal.example'+sync.ENDPOINT)
        self.assertIsNone(sync.NoRedirect().redirect_request(request,None,302,'',{},'https://other.example'))
        opener.open.return_value.__enter__.return_value.read.return_value = b'{"accepted":false}'
        with patch('sync.urllib.request.build_opener', return_value=opener):
            with self.assertRaises(RuntimeError):
                sync.upload(CONFIG,PAYLOAD)

    def test_v3_export_is_uploaded(self):
        payload = json.dumps({'source':'plenion_reader_projects','schema_version':3,'truncated':False}).encode()
        opener = MagicMock()
        opener.open.return_value.__enter__.return_value.read.return_value = b'{"accepted":true}'
        with patch('sync.urllib.request.build_opener', return_value=opener):
            self.assertTrue(sync.upload(CONFIG,payload)['accepted'])
        self.assertEqual(opener.open.call_args.args[0].data,payload)

    def test_failed_read_never_uploads_previous_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'data').mkdir()
            config = root/'data/upload.json'
            config.write_text(json.dumps(CONFIG))
            (root/'data/portal-projects.json').write_bytes(PAYLOAD)
            with patch('sync.ROOT',root), patch('sync.CONFIG',config), patch('sync.subprocess.run',side_effect=subprocess.CalledProcessError(1,'reader')), patch('sync.upload') as upload:
                with self.assertRaises(subprocess.CalledProcessError):
                    sync.sync()
                upload.assert_not_called()

    def test_bad_export_not_sent_and_http_errors_do_not_leak_key(self):
        with patch('sync.urllib.request.build_opener') as build:
            with self.assertRaises(ValueError):
                sync.upload(CONFIG,b'{"source":"plenion_reader_projects","schema_version":2,"truncated":true}')
            build.assert_not_called()
        opener = MagicMock()
        opener.open.side_effect = urllib.error.HTTPError('https://portal.example',401,CONFIG['api_key'],{},None)
        with patch('sync.urllib.request.build_opener',return_value=opener):
            with self.assertRaisesRegex(RuntimeError,'401') as error:
                sync.upload(CONFIG,PAYLOAD)
            self.assertNotIn(CONFIG['api_key'],str(error.exception))

    def test_zerotier_route_keeps_certificate_hostname(self):
        context = MagicMock()
        raw_socket = MagicMock()
        connection = sync.RoutedHTTPSConnection('portal.cohvera.be', connect_ip='10.121.15.99', context=context)
        with patch('sync.socket.create_connection', return_value=raw_socket) as connect:
            connection.connect()
        self.assertEqual(connect.call_args.args[0], ('10.121.15.99',443))
        context.wrap_socket.assert_called_once_with(raw_socket,server_hostname='portal.cohvera.be')
        # Certificate failures must propagate, never fall back to plaintext or skip verification.
        context.wrap_socket.side_effect = sync.ssl.SSLCertVerificationError('test')
        with patch('sync.socket.create_connection', return_value=raw_socket):
            with self.assertRaises(sync.ssl.SSLCertVerificationError):
                connection.connect()
        raw_socket.close.assert_called_once()

    def test_route_validation_and_default_url(self):
        self.assertEqual(sync.validate({'api_key':'a'*43}),sync.DEFAULT_PORTAL_URL+sync.ENDPOINT)
        for value in ['http://10.0.0.1','server.example','10.0.0.1:443','invalid']:
            with self.assertRaises(ValueError):
                sync.validate({**CONFIG,'connect_ip':value})
        self.assertEqual(sync.validate({**CONFIG,'connect_ip':'10.0.0.1'}),CONFIG['portal_url']+sync.ENDPOINT)

    def test_configure_no_url_prompt_and_preserves_existing_key_and_route(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory)/'upload.json'
            config.write_text(json.dumps({**CONFIG,'connect_ip':'10.0.0.1'}))
            with patch('sync.CONFIG',config), patch('sys.argv',['sync.py','configure']), patch('builtins.input',return_value='') as prompt, patch('sync.getpass.getpass',return_value=''):
                sync.main()
            self.assertEqual(prompt.call_count,1)
            self.assertEqual(json.loads(config.read_text()),{**CONFIG,'connect_ip':'10.0.0.1'})
            self.assertEqual(config.stat().st_mode & 0o777,0o600)
            with patch('sync.CONFIG',config), patch('sys.argv',['sync.py','configure']), patch('builtins.input',return_value='/direct'), patch('sync.getpass.getpass',return_value=''):
                sync.main()
            self.assertNotIn('connect_ip',json.loads(config.read_text()))

    def test_new_config_defaults_to_public_portal_and_uses_entered_route(self):
        with tempfile.TemporaryDirectory() as directory:
            config=Path(directory)/'data/upload.json'
            with patch('sync.CONFIG',config), patch('sys.argv',['sync.py','configure']), patch('builtins.input',return_value='10.0.0.1'), patch('sync.getpass.getpass',return_value='a'*43):
                sync.main()
            self.assertEqual(json.loads(config.read_text()),{'portal_url':sync.DEFAULT_PORTAL_URL,'api_key':'a'*43,'connect_ip':'10.0.0.1'})
