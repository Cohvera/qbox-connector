#!/usr/bin/env python3
"""Run only after the upstream backup/copy job has successfully completed."""
import argparse
import json
from pathlib import Path
from pipeline import digest, inventory, stamp, write


def seal(config, completed_at):
    stamp(completed_at)
    root = Path(config['source_dir']).resolve(strict=True)
    names = config['required_files']
    before = inventory(root, names)
    files = {name: digest(root/name) for name in names}
    if inventory(root,names) != before:
        raise ValueError('Backup veranderde tijdens verzegelen.')
    marker = config.get('completion_file','backup-complete.json')
    if Path(marker).name != marker or marker in names:
        raise ValueError('Ongeldig voltooiingsbestand.')
    write(root/marker, {'complete':True,'completed_at':completed_at,'files':files})


if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config',required=True)
    p.add_argument('--completed-at',required=True,help='Oorspronkelijk geslaagd backupmoment met tijdzone; nooit de datum van een herimport.')
    args=p.parse_args()
    seal(json.loads(Path(args.config).read_text()),args.completed_at)
