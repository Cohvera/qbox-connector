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

    def test_validation_error_shows_message_but_never_key(self):
        import io
        opener = MagicMock()
        opener.open.side_effect = urllib.error.HTTPError('https://portal.example', 400, '', {},
            io.BytesIO(json.dumps({'message':'Ongeldige brondatum: created_date '+CONFIG['api_key']}).encode()))
        with patch('sync.urllib.request.build_opener', return_value=opener):
            with self.assertRaisesRegex(RuntimeError, 'Ongeldige brondatum') as error:
                sync.upload(CONFIG, PAYLOAD)
        self.assertNotIn(CONFIG['api_key'], str(error.exception))

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
            with patch('sync.ROOT',root), patch('sync.CONFIG',config), patch('sync.subprocess.run',side_effect=subprocess.CalledProcessError(1,'reader')), patch('sync.report_status'), patch('sync.fetch_reader_options',return_value={'revision':2}), patch('sync.upload') as upload:
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

    def test_status_queue_retries_without_sending_credentials_in_body(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'data').mkdir()
            opener = MagicMock()
            opener.open.side_effect = OSError('offline')
            run_id = 'b279aa43-f995-4dce-a539-3be28993a976'
            with patch('sync.ROOT',root), patch('sync.portal_opener',return_value=opener):
                sync.report_status(CONFIG,run_id,'started')
                queue = root/'data/reader-status-outbox.json'
                self.assertEqual(len(json.loads(queue.read_text())),1)
                self.assertEqual(queue.stat().st_mode & 0o777,0o600)
                opener.open.side_effect = None
                opener.open.return_value.__enter__.return_value.read.return_value = b'{"accepted":true}'
                sync.report_status(CONFIG,run_id,'read_ok',record_count=57)
                request = opener.open.call_args.args[0]
                self.assertEqual(request.full_url,CONFIG['portal_url']+sync.STATUS_ENDPOINT)
                self.assertEqual([e['phase'] for e in json.loads(request.data)['events']],['started','read_ok'])
                self.assertNotIn(CONFIG['api_key'],request.data.decode())
                self.assertEqual(json.loads(queue.read_text()),[])

    def test_corrupt_status_queue_does_not_block_new_reports(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'data').mkdir()
            queue = root/'data/reader-status-outbox.json'
            opener = MagicMock()
            opener.open.side_effect = OSError('offline')
            with patch('sync.ROOT',root), patch('sync.portal_opener',return_value=opener):
                for broken in ['invalid json','[{}, {"observedAt":"bad"}]']:
                    queue.write_text(broken)
                    sync.report_status(CONFIG,'b279aa43-f995-4dce-a539-3be28993a976','success')
                    self.assertEqual(len(json.loads(queue.read_text())),1)

    def test_sync_reports_success_and_upload_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'data').mkdir()
            config = root/'data/upload.json'
            config.write_text(json.dumps(CONFIG))
            (root/'data/portal-projects.json').write_bytes(PAYLOAD)
            with patch('sync.ROOT',root), patch('sync.CONFIG',config), patch('sync.subprocess.run'), patch('sync.report_status') as report, patch('sync.fetch_reader_options',return_value={'revision':2}), patch('sync.upload',return_value={'accepted':True}):
                sync.sync()
                self.assertEqual([c.args[2] for c in report.call_args_list],['started','read_ok','success'])
            with patch('sync.ROOT',root), patch('sync.CONFIG',config), patch('sync.subprocess.run'), patch('sync.report_status') as report, patch('sync.fetch_reader_options',return_value={'revision':2}), patch('sync.upload',side_effect=sync.UploadRejected('rejected',400)):
                with self.assertRaises(sync.UploadRejected):
                    sync.sync()
                self.assertEqual(report.call_args.args[2],'upload_failed')
                self.assertEqual(report.call_args.kwargs['http_status'],400)

    def test_options_are_validated_against_catalog_and_actual_schema_before_saving(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'data').mkdir()
            (root/'data/connection.json').write_text('{}')
            (root/'data/hfsql-schema.json').write_text(json.dumps({'columns':['IDProj\x00\x00','memo\x00\x00']}))
            (root/'data/project-statuses.json').write_text(json.dumps({'statuses':[{'code':'07','label':'In uitvoering'}]}))
            opener=MagicMock()
            response=opener.open.return_value.__enter__.return_value
            with patch('sync.ROOT',root), patch('sync.portal_opener',return_value=opener), patch('sync.subprocess.run') as process:
                response.read.return_value=json.dumps({'revision':4,'selectedColumns':['MEMO']}).encode()
                sync.fetch_reader_options(CONFIG,3)
                process.assert_not_called()
                request=opener.open.call_args.args[0]
                self.assertEqual(json.loads(request.data)['appliedRevision'],3)
                self.assertEqual(json.loads(request.data)['columns'], ['IDProj', 'memo'])
                self.assertNotIn(CONFIG['api_key'],request.data.decode())
                saved=(root/'data/reader-options.json').read_bytes()
                self.assertEqual(json.loads(saved)['selectedColumns'],['MEMO'])
                response.read.return_value=json.dumps({'revision':5,'selectedColumns':['MEMO'],'projectFilter':{'statusCodes':['07'],'outOfWip':'off'}}).encode()
                sync.fetch_reader_options(CONFIG)
                saved=(root/'data/reader-options.json').read_bytes()
                self.assertEqual(json.loads(saved)['projectFilter'],{'statusCodes':['07'],'outOfWip':'off'})
                response.read.return_value=json.dumps({'revision':6,'selectedColumns':['MEMO'],'projectFilter':{'statusCodes':['09'],'outOfWip':'off'}}).encode()
                with self.assertRaises(ValueError):sync.fetch_reader_options(CONFIG)
                for selected in [['A_PC'],['MEMO;DELETE'],['APP_Paswoord']]:
                    response.read.return_value=json.dumps({'revision':5,'selectedColumns':selected}).encode()
                    with self.assertRaises(ValueError):
                        sync.fetch_reader_options(CONFIG)
                    self.assertEqual((root/'data/reader-options.json').read_bytes(),saved)

    def test_unreachable_configuration_never_uploads_stale_export(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'data').mkdir()
            config=root/'data/upload.json'
            config.write_text(json.dumps(CONFIG))
            (root/'data/portal-projects.json').write_bytes(PAYLOAD)
            with patch('sync.ROOT',root),patch('sync.CONFIG',config),patch('sync.report_status'),patch('sync.fetch_reader_options',side_effect=OSError('offline')),patch('sync.upload') as upload:
                with self.assertRaises(OSError):
                    sync.sync()
                upload.assert_not_called()

    def test_configuration_http_failure_shows_status_without_credentials(self):
        import io
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'data').mkdir()
            (root/'data/hfsql-schema.json').write_text('{"columns":["IDProj"]}')
            (root/'data/project-statuses.json').write_text('{"statuses":[]}')
            opener=MagicMock()
            with patch('sync.ROOT',root),patch('sync.portal_opener',return_value=opener):
                for code in [400,401,404,500]:
                    opener.open.side_effect=urllib.error.HTTPError('https://portal.example',code,'',{},io.BytesIO(json.dumps({'message':'test '+CONFIG['api_key']}).encode()))
                    with self.assertRaisesRegex(sync.UploadRejected,f'HTTP {code}') as result:
                        sync.fetch_reader_options(CONFIG)
                    self.assertNotIn(CONFIG['api_key'],str(result.exception))
                    self.assertEqual(result.exception.status,code)
                opener.open.side_effect=urllib.error.URLError(CONFIG['api_key'])
                with self.assertRaisesRegex(RuntimeError,'niet bereikbaar') as result:
                    sync.fetch_reader_options(CONFIG)
                self.assertNotIn(CONFIG['api_key'],str(result.exception))
                self.assertFalse((root/'data/reader-options.json').exists())

    def test_manual_request_waits_for_lock_and_is_not_repeated_after_completion(self):
        request_id='12345678-1234-4234-8234-123456789abc'
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'data').mkdir()
            config=root/'data/upload.json';config.write_text(json.dumps(CONFIG))
            with patch('sync.ROOT',root),patch('sync.CONFIG',config),patch('sync.command_request',return_value=request_id) as poll,patch('sync.sync',return_value=False) as run:
                sync.poll_command()
                run.assert_called_once_with(run_id=request_id)
                self.assertFalse((root/'data/completed-sync-request.json').exists())
                run.return_value=True
                sync.poll_command()
                self.assertEqual(json.loads((root/'data/completed-sync-request.json').read_text())['id'],request_id)
                poll.assert_called_with(CONFIG,request_id)
                poll.return_value=None
                run.reset_mock()
                sync.poll_command()
                run.assert_not_called()
                poll.assert_called_with(CONFIG,request_id)

    def test_failed_manual_sync_is_acknowledged_on_next_poll(self):
        request_id='12345678-1234-4234-8234-123456789abc'
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'data').mkdir()
            config=root/'data/upload.json';config.write_text(json.dumps(CONFIG))
            with patch('sync.ROOT',root),patch('sync.CONFIG',config),patch('sync.command_request',return_value=request_id) as poll,patch('sync.sync',side_effect=RuntimeError('read failed')):
                with self.assertRaisesRegex(RuntimeError,'read failed'):sync.poll_command()
                self.assertEqual(json.loads((root/'data/completed-sync-request.json').read_text())['id'],request_id)
                poll.return_value=None
                sync.poll_command()
                poll.assert_called_with(CONFIG,request_id)

    def test_command_poll_accepts_only_request_ids_and_keeps_key_out_of_body(self):
        opener=MagicMock();response=opener.open.return_value.__enter__.return_value
        with patch('sync.portal_opener',return_value=opener):
            response.read.return_value=b'{"requestId":null}'
            self.assertIsNone(sync.command_request(CONFIG))
            req=opener.open.call_args.args[0]
            self.assertTrue(req.full_url.endswith('/reader-command'))
            self.assertNotIn(CONFIG['api_key'],req.data.decode())
            for value in ['run rm',False,{},[]]:
                response.read.return_value=json.dumps({'requestId':value}).encode()
                with self.assertRaises(ValueError):sync.command_request(CONFIG)
