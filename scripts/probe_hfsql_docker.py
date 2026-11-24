#!/usr/bin/env python3
"""Report the installed HFSQL package from a running Docker replica."""

import argparse
import json
import re
from pathlib import Path

from maintain_hfsql_replica import PACKAGE_VERSION, full_version, run


def inspect_replica(container, builds):
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]*', container):
        raise ValueError('Geef een geldige naam of ID van de replica-container op.')
    container_id = run(['docker', 'container', 'inspect', '--format',
                        '{{if .State.Running}}{{.Id}}{{end}}', container], 'Docker-replica', 10).strip()
    if not re.fullmatch(r'[0-9a-f]{64}', container_id):
        raise RuntimeError('De HFSQL-replica draait niet.')
    result = run(['docker', 'exec', container_id, 'dpkg-query', '-W',
                  '-f=${Status}\t${Version}\n', 'hfsql'], 'HFSQL-pakketversie', 10).strip()
    status, _, package = result.partition('\t')
    if status != 'install ok installed' or not PACKAGE_VERSION.fullmatch(package):
        raise RuntimeError('Docker gaf geen geldige geïnstalleerde HFSQL-pakketversie terug.')
    report = {'packageVersion': package, 'container': container}
    if package in builds:
        full_version(builds[package])
        report['serverVersion'] = builds[package]
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--container', required=True)
    parser.add_argument('--build-map', type=Path)
    args = parser.parse_args()
    try:
        builds = json.loads(args.build_map.read_text()) if args.build_map else {}
        if not isinstance(builds, dict):
            raise ValueError('De buildmap moet een JSON-object met pakketversies als sleutels zijn.')
        print(json.dumps(inspect_replica(args.container, builds)))
    except (OSError, RuntimeError, ValueError) as error:
        parser.exit(1, f'HFSQL Docker: {error}\n')


if __name__ == '__main__':
    main()
