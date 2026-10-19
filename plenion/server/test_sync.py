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
