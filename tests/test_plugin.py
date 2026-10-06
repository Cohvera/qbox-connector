import json
import os
import stat
import subprocess
import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'plugin/bin'))
sys.path.insert(0, str(ROOT / 'scripts'))
from plugin_core import DEFAULTS, Store, InputError, validate, write
from install import legacy_settings, unit_text
from backup import backup
from build_plugin import build


class PluginTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.config, self.data = self.root / 'config', self.root / 'data'
        self.config.mkdir(); self.data.mkdir()
        self.store = Store(self.config, self.data)
        self.settings = {**DEFAULTS, 'QBOX_IMPORT_API_KEY': 'S' * 43, 'enabled': True}
        write(self.config / 'settings.json', validate(self.settings))

    def tearDown(self):
        self.tmp.cleanup()

    def test_validation_preserves_blank_secret_and_rejects_unsafe_inputs(self):
        self.assertEqual(validate({**self.settings,'QBOX_IMPORT_API_KEY':''}, self.settings)['QBOX_IMPORT_API_KEY'], 'S'*43)
        for extra in ({'QBOX_PORTAL_URL':'http://portal.example'}, {'QBOX_PORTAL_URL':'https://user:pass@portal.example'}, {'QBOX_PORTAL_URL':'https://portal.example/api'}, {'QBOX_POLL_SECONDS':'2'}, {'QBOX_SOURCE_CA_FILE':'/tmp/../secret'}, {'enabled':'true'}, {'QBOX_SOURCE_URL':'http://lan\nSECRET=x'}):
            with self.assertRaises(InputError): validate({**self.settings, **extra})

    def test_status_never_contains_key(self):
        public = self.store.public()
        self.assertTrue(public['settings']['has_key'])
        self.assertNotIn('S'*43, json.dumps(public))
        self.assertNotIn('QBOX_IMPORT_API_KEY', public['settings'])
        self.assertEqual(stat.S_IMODE((self.config/'settings.json').stat().st_mode), 0o600)

    def test_csrf_queue_and_busy_guards(self):
        with self.assertRaises(InputError): self.store.action({'action':'sync','csrf':'wrong'})
        token=self.store.token()
        with self.assertRaises(InputError): self.store.action({'action':'sync','csrf':token})
        write(self.data/'status.json', {'heartbeat':time.time(),'busy':False})
        self.store.action({'action':'sync','csrf':token})
        self.assertEqual(json.loads((self.data/'request.json').read_text())['action'],'sync')
        with self.assertRaises(InputError): self.store.action({'action':'test','csrf':token})
        self.assertFalse((self.data/'request.json').read_text().find('S'*43)>=0)

    def test_legacy_env_is_parsed_not_executed(self):
        env=self.root/'old.env'
        env.write_text('QBOX_IMPORT_API_KEY='+ 'S'*43 + '\nQBOX_SOURCE_URL="http://lan/projecten-tv"\nQBOX_POLL_SECONDS=60\nUNRELATED=$(touch /tmp/should-not-run)\n')
        settings=legacy_settings(env)
        self.assertFalse(settings['enabled'])
        self.assertEqual(settings['QBOX_IMPORT_API_KEY'],'S'*43)
        self.assertNotIn('UNRELATED',settings)

    def test_cgi_requires_authentication_and_csrf(self):
        entry=self.root/'index.cgi'
        text=(ROOT/'plugin/webfrontend/htmlauth/index.cgi').read_text()
        for key,value in {'BIN':ROOT/'plugin/bin','CONFIG':self.config,'DATA':self.data,'TEMPLATES':ROOT/'plugin/templates'}.items():
            text=text.replace('@@'+key+'@@',str(value))
        entry.write_text(text)
        def call(method='GET', query='status', body='', auth=True, content='application/json'):
            env={**os.environ,'REQUEST_METHOD':method,'QUERY_STRING':query,'CONTENT_LENGTH':str(len(body)),'CONTENT_TYPE':content}
            env.pop('REMOTE_USER',None)
            if auth: env['REMOTE_USER']='admin'
            return subprocess.run([sys.executable,str(entry)],input=body,text=True,capture_output=True,env=env,check=True).stdout
        self.assertIn('403 Forbidden',call(auth=False))
        self.assertNotIn('S'*43,call())
        self.assertIn('400 Bad Request',call('POST','',json.dumps({'action':'save','csrf':'bad'})))
        self.assertIn('403 Forbidden',call('POST','', '{}',content='text/plain'))
        self.assertIn('text/html',call(query=''))
        self.assertIn('application/javascript',call(query='script'))

    def test_package_contains_only_release_files_and_correct_permissions(self):
        target=build()
        with zipfile.ZipFile(target) as archive:
            names=archive.namelist()
            self.assertIn('plugin.cfg',names)
            self.assertIn('webfrontend/htmlauth/index.cgi',names)
            self.assertNotIn('qbox.env',names)
            self.assertFalse(any('.git/' in n or '__pycache__' in n for n in names))
            self.assertEqual(archive.read('bin/qbox_sync.py'),(ROOT/'qbox_sync.py').read_bytes())
            self.assertTrue(archive.getinfo('postroot.sh').external_attr>>16 & 0o100)

    def test_worker_runs_paused_and_stops_cleanly(self):
        write(self.config/'settings.json',{**validate(self.settings),'enabled':False})
        write(self.data/'state.json',{'sent_at':123,'count':41,'company':'TOMME'})
        env={**os.environ,'PYTHONPATH':str(ROOT)}
        worker=subprocess.Popen([sys.executable,str(ROOT/'plugin/bin/worker.py'),'--config',str(self.config),'--data',str(self.data)],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        try:
            deadline=time.time()+5
            while time.time()<deadline and not (self.data/'status.json').exists(): time.sleep(.05)
            self.assertTrue(self.store.public()['status']['worker_online'])
            self.assertFalse(self.store.public()['settings']['enabled'])
            self.assertEqual(self.store.public()['status']['project_count'],41)
            worker.terminate()
            worker.communicate(timeout=5)
            self.assertEqual(worker.returncode,0)
            self.assertEqual(self.store.public()['status']['state'],'stopped')
            self.assertFalse(self.store.public()['status']['worker_online'])
        finally:
            if worker.poll() is None: worker.kill();worker.communicate()

    def test_upgrade_backup_survives_purged_plugin_directory(self):
        home=self.root/'loxberry'
        config=home/'config/plugins/test01'
        data=home/'data/plugins/test01'
        config.mkdir(parents=True);data.mkdir(parents=True)
        write(config/'settings.json',validate(self.settings))
        write(data/'state.json',{'digest':'hash','sent_at':123})
        target=self.root/'upgrade-backup'
        with patch('backup.subprocess.run') as stop:
            self.assertTrue(backup(home,'test01',target))
            stop.assert_called_once()
        import shutil
        shutil.rmtree(config);shutil.rmtree(data)
        self.assertEqual(json.loads((target/'settings.json').read_text())['QBOX_IMPORT_API_KEY'],'S'*43)
        self.assertEqual(json.loads((target/'metadata.json').read_text())['folder'],'test01')
        self.assertEqual(json.loads((target/'state.json').read_text())['digest'],'hash')
        self.assertEqual(stat.S_IMODE(target.stat().st_mode),0o700)

    def test_systemd_uses_unprivileged_account_and_no_shell(self):
        text=unit_text('/opt/loxberry/bin/plugins/example01','/opt/loxberry/config/plugins/example01','/opt/loxberry/data/plugins/example01')
        self.assertIn('User=loxberry',text)
        self.assertIn('NoNewPrivileges=yes',text)
        self.assertNotIn('/bin/sh',text)
        self.assertIn('example01/worker.py',text)

if __name__=='__main__': unittest.main()
