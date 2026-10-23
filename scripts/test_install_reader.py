import fcntl
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch, MagicMock
import install_reader as installer


class InstallTests(unittest.TestCase):
    def test_sync_lock_refuses_overlap(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'data').mkdir()
            with (root/'data/upload.lock').open('a') as lock:
                fcntl.flock(lock,fcntl.LOCK_EX | fcntl.LOCK_NB)
                with self.assertRaisesRegex(RuntimeError,'sync'):
                    with installer.sync_lock(root):
                        self.fail('must not acquire lock')

    def exercise(self,fail_build=False,fail_activation=False):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'source';target=root/'installed'
            source.mkdir();target.mkdir();(target/'data').mkdir()
            (source/'plenion.sh').write_text('new script')
            (source/'hfsql-columns.json').write_text('[]')
            (target/'plenion.sh').write_text('old script')
            config=target/'data/connection.json';config.write_text('private-config')
            unit=root/'sync.service';unit.write_text('[Service]\nTimeoutStartSec=300\n')
            calls=[]
            def run(*args,**kwargs):
                calls.append(args)
                if fail_build and args[:2]==('docker','build'):
                    raise subprocess.CalledProcessError(1,args)
                if fail_activation and args[:2]==('docker','tag') and ':candidate-' in args[2]:
                    raise subprocess.CalledProcessError(1,args)
                return MagicMock(stdout='sha256:old\n')
            with patch.object(installer,'SOURCE',source),patch.object(installer,'FILES',('plenion.sh','hfsql-columns.json')),patch.object(installer,'UNIT',unit),patch.object(installer.os,'geteuid',return_value=0),patch.object(installer.os,'chown'),patch.object(installer,'run',side_effect=run),patch.object(installer.subprocess,'run'):
                if fail_build or fail_activation:
                    with self.assertRaises(subprocess.CalledProcessError):
                        installer.install(target)
                    self.assertEqual((target/'plenion.sh').read_text(),'old script')
                    self.assertFalse((target/'hfsql-columns.json').exists())
                    self.assertIn('TimeoutStartSec=300',unit.read_text())
                    if fail_activation:self.assertIn(('docker','tag','sha256:old',installer.IMAGE),calls)
                else:
                    installer.install(target)
                    self.assertEqual((target/'plenion.sh').read_text(),'new script')
                    self.assertIn('TimeoutStartSec=480',unit.read_text())
                    self.assertEqual((target/'plenion.sh').stat().st_mode & 0o777,0o755)
                    self.assertEqual((target/'hfsql-columns.json').stat().st_mode & 0o777,0o644)
                    self.assertTrue(list(root.glob('.plenion-reader-backup-*')))
                self.assertEqual(config.read_text(),'private-config')
    def test_build_failure_leaves_installation_untouched(self):self.exercise(fail_build=True)
    def test_activation_failure_restores_scripts_and_image(self):self.exercise(fail_activation=True)
    def test_success_preserves_config_and_updates_existing_service(self):self.exercise()
