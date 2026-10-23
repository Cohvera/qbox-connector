#!/usr/bin/env python3
"""Package the standalone HFSQL reader, excluding device data and credentials."""
from pathlib import Path
import tarfile

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'plenion' / 'server'
FILES = (
    'Dockerfile', '.dockerignore', '.gitignore', 'plenion.sh', 'configure.py',
    'reader.py', 'sync.py', 'hfsql-columns.json', 'test_sync.py', 'test_reader.py', 'test_configuration.py', 'test_audit.py', 'README.md', 'HFSQL_COLUMNS.md',
)


def build():
    for name in FILES:
        path = SOURCE / name
        if path.is_symlink() or not path.is_file():
            raise ValueError(f'Missing or unsafe package input: {name}')
    target = ROOT / 'dist' / 'cohvera-plenion-reader.tar.gz'
    target.parent.mkdir(exist_ok=True)
    temp = target.with_suffix('.tmp')
    try:
        with tarfile.open(temp, 'w:gz', format=tarfile.PAX_FORMAT) as archive:
            for name in FILES:
                path = SOURCE / name
                info = archive.gettarinfo(str(path), arcname=name)
                info.uid = info.gid = 0
                info.uname = info.gname = ''
                info.mode = 0o755 if name == 'plenion.sh' else 0o644
                with path.open('rb') as stream:
                    archive.addfile(info, stream)
        temp.replace(target)
    finally:
        temp.unlink(missing_ok=True)
    print(target)
    return target


if __name__ == '__main__':
    build()

