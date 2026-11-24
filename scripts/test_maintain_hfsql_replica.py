import json
import unittest
from unittest.mock import patch
from pathlib import Path
from tempfile import TemporaryDirectory
import subprocess
import sys

import maintain_hfsql_replica as maintenance
import probe_hfsql_docker as docker_probe


PRODUCTION = (28, 0, 123, 4)
REPLICA = (28, 0, 122, 9)
IMAGE = 'local/warco-hfsql@sha256:' + 'a' * 64
CONFIG = {
    'productionProbe': ['probe', 'production.json'],
    'replicaProbe': ['probe', 'replica.json'],
    'images': {'28.0.123.4': IMAGE},
    'replicaUpdate': ['deploy-replica', '{image}'],
}


class MaintenanceTests(unittest.TestCase):
    @patch.object(docker_probe, 'run')
    def test_docker_reports_installed_package_and_requires_verified_build_mapping(self, run):
        container_id = 'b' * 64
        run.side_effect = [container_id, 'install ok installed\t31.0.059\n'] * 2
        package = docker_probe.inspect_replica('warco-replica', {})
        self.assertEqual(package['packageVersion'], '31.0.059')
        self.assertNotIn('serverVersion', package)
        self.assertEqual(run.call_args.args[0][2], container_id)
        with patch.object(maintenance, 'run', return_value=json.dumps(package)):
            with self.assertRaisesRegex(RuntimeError, r'HFSQL-pakket 31\.0\.059'):
                maintenance.probe(['docker-probe'], 'Replica')
        builds = {'31.0.059': '31.0.123.4'}
        self.assertEqual(docker_probe.inspect_replica('warco-replica', builds)['serverVersion'], '31.0.123.4')

    @patch.object(docker_probe, 'run', return_value='\n')
    def test_docker_stopped_container_is_not_used(self, run):
        with self.assertRaisesRegex(RuntimeError, 'draait niet'):
            docker_probe.inspect_replica('warco-replica', {})
        run.assert_called_once()

    @patch.object(maintenance, 'run')
    @patch.object(maintenance, 'probe', side_effect=[PRODUCTION, REPLICA])
    def test_check_reports_mismatch_without_running_update(self, probe, run):
        self.assertEqual(maintenance.maintain(CONFIG), 2)
        run.assert_not_called()

    @patch.object(maintenance, 'run')
    @patch.object(maintenance, 'probe', side_effect=[PRODUCTION, REPLICA, PRODUCTION])
    def test_apply_runs_pinned_update_and_rechecks_replica(self, probe, run):
        self.assertEqual(maintenance.maintain(CONFIG, apply=True), 0)
        run.assert_called_once_with(['deploy-replica', IMAGE], 'Replica-update', timeout=900)
        self.assertEqual(probe.call_args.args[:2], (CONFIG['replicaProbe'], 'Replica'))

    @patch.object(maintenance, 'run')
    @patch.object(maintenance, 'probe', side_effect=[PRODUCTION, REPLICA])
    def test_apply_rejects_mutable_image_tag(self, probe, run):
        with self.assertRaisesRegex(ValueError, 'SHA-256'):
            maintenance.maintain({**CONFIG, 'images': {'28.0.123.4': 'windev/hfsql:latest'}}, apply=True)
        run.assert_not_called()

    @patch.object(maintenance, 'run')
    @patch.object(maintenance, 'probe', side_effect=[REPLICA, PRODUCTION])
    def test_apply_does_not_downgrade(self, probe, run):
        with self.assertRaisesRegex(RuntimeError, 'nieuwer'):
            maintenance.maintain(CONFIG, apply=True)
        run.assert_not_called()

    def test_probe_requires_unambiguous_full_engine_build(self):
        for info in ({'serverVersion': '20.00.0000', 'driverVersion': '31.00.37400'},
                     {'odbcDbmsVersion': '20.00.0000', 'driverVersion': '31.00.37400'},
                     {'serverVersion': '28.00.1234'},
                     {'serverVersion': '28.0.123.4', 'driverVersion': '28.00.123.4'},
                     {'driverVersion': '28.0.123.4'}):
            with self.subTest(info=info), patch.object(maintenance, 'run', return_value=json.dumps(info)):
                with self.assertRaises(RuntimeError):
                    maintenance.probe(['probe'], 'Replica')

    def test_probe_reads_version_after_reader_progress(self):
        with patch.object(maintenance, 'run', return_value='Stap 1\n{"serverVersion":"28.00.123.4"}\n'):
            self.assertEqual(maintenance.probe(['probe'], 'Productie'), PRODUCTION)

    def test_checker_shows_both_builds_received_from_native_probe(self):
        scripts = Path(maintenance.__file__).parent
        with TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / 'engine-probe'
            executable.write_text('''#!/bin/sh
case "$1" in
  */production.json) version=31.0.123.4 ;;
  */replica.json) version=31.0.122.9 ;;
  *) exit 1 ;;
esac
printf '{"serverVersion":"%s"}\\n' "$version" > "$2"
''')
            executable.chmod(0o700)
            production, replica = root / 'production.json', root / 'replica.json'
            for connection in (production, replica):
                connection.write_text('{}')
            command = [sys.executable, str(scripts / 'probe_hfsql_engine.py'), str(executable)]
            config = root / 'checks.json'
            config.write_text(json.dumps({'productionProbe': [*command, str(production)],
                                          'replicaProbe': [*command, str(replica)]}))
            result = subprocess.run([sys.executable, str(scripts / 'maintain_hfsql_replica.py'),
                                     '--config', str(config)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn('Productie: HFSQL 31.0.123.4.', result.stdout)
            self.assertIn('Replica: HFSQL 31.0.122.9.', result.stdout)
