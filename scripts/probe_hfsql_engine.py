#!/usr/bin/env python3
"""Run the compiled WINDEV engine probe and relay its JSON report."""

import argparse
import json
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('probe', type=Path)
    parser.add_argument('connection', type=Path)
    args = parser.parse_args()
    try:
        with TemporaryDirectory(prefix='hfsql-engine-') as directory:
            output = Path(directory) / 'version.json'
            subprocess.run([str(args.probe.resolve(strict=True)),
                            str(args.connection.resolve(strict=True)), str(output)],
                           check=True, timeout=80, stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            report = json.loads(output.read_bytes())
            version = report['serverVersion']
            if not isinstance(version, str) or not version.strip():
                raise ValueError('missing version')
            print(json.dumps({'serverVersion': version.strip()}))
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        parser.exit(1, 'Native HFSQL-engineversie kon niet worden opgehaald.\n')


if __name__ == '__main__':
    main()
