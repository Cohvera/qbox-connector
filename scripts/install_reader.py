#!/usr/bin/env python3
"""Upgrade an existing standalone reader from this checkout, preserving device data."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import uuid
from build_reader import SOURCE, FILES

IMAGE = 'cohvera-plenion-reader:local'
UNIT = Path('/etc/systemd/system/cohvera-reader-sync.service')


def run(*args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def atomic_copy(source, destination, mode, uid, gid):
    fd, temporary = tempfile.mkstemp(dir=destination.parent, prefix='.reader-update-')
    try:
        with os.fdopen(fd, 'wb') as output, source.open('rb') as incoming:
            shutil.copyfileobj(incoming, output)
        os.chmod(temporary, mode)
        os.chown(temporary, uid, gid)
        os.replace(temporary, destination)
    finally:
        Path(temporary).unlink(missing_ok=True)


@contextmanager
def sync_lock(target):
    with (target/'data/upload.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('Er loopt een sync. Probeer de update na die sync opnieuw.')
        yield


def install(target):
    target = target.expanduser().resolve()
    if os.geteuid() != 0:
        raise RuntimeError('Voer de installatie met sudo uit.')
    if target == SOURCE.resolve() or not (target/'plenion.sh').is_file() or not (target/'data/connection.json').is_file():
        raise RuntimeError('Kies de bestaande installatie met plenion.sh en data/connection.json.')
    for name in FILES:
        if (SOURCE/name).is_symlink() or not (SOURCE/name).is_file() or (target/name).is_symlink():
            raise RuntimeError('Onveilig of ontbrekend readerbestand: '+name)
    owner=(target/'data/connection.json').stat()
    stamp=datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8]
    candidate='cohvera-plenion-reader:candidate-'+stamp
    backup_image='cohvera-plenion-reader:backup-'+stamp
    backup=target.parent/('.plenion-reader-backup-'+stamp)
    try:
        run('docker','build','--platform','linux/amd64','-t',candidate,str(SOURCE))
        run('docker','run','--rm','--platform','linux/amd64','--network','none','--read-only',
            '--user',f'{owner.st_uid}:{owner.st_gid}','--tmpfs','/tmp:rw,size=64m',
            '--cap-drop','ALL','--security-opt','no-new-privileges',candidate,'--check')
        with sync_lock(target):
            old_image=run('docker','image','inspect','--format','{{.Id}}',IMAGE,capture_output=True,text=True).stdout.strip()
            backup.mkdir(mode=0o700)
            existing={}
            for name in FILES:
                path=target/name
                if path.exists():
                    info=path.stat()
                    existing[name]=(info.st_mode & 0o777,info.st_uid,info.st_gid)
                    shutil.copy2(path,backup/name)
            unit_before=UNIT.read_bytes() if UNIT.exists() else None
            if unit_before is not None:
                (backup/'sync-service.before').write_bytes(unit_before)
            run('docker','tag',old_image,backup_image)
            try:
                for name in FILES:
                    atomic_copy(SOURCE/name,target/name,0o755 if name=='plenion.sh' else 0o644,owner.st_uid,owner.st_gid)
                run('docker','tag',candidate,IMAGE)
                if unit_before is not None:
                    unit=unit_before.decode()
                    if not re.search(r'^TimeoutStartSec=',unit,re.M):
                        raise RuntimeError('Service heeft geen bekende timeout; controleer de unit handmatig.')
                    UNIT.write_text(re.sub(r'^TimeoutStartSec=.*$', 'TimeoutStartSec=480',unit,flags=re.M))
                    run('systemctl','daemon-reload')
            except BaseException:
                for name in FILES:
                    if name in existing:
                        atomic_copy(backup/name,target/name,*existing[name])
                    else:
                        (target/name).unlink(missing_ok=True)
                run('docker','tag',old_image,IMAGE)
                if unit_before is not None:
                    UNIT.write_bytes(unit_before)
                    run('systemctl','daemon-reload')
                raise
        print(f'Reader bijgewerkt. Configuratie en timer behouden. Reservekopie: {backup}')
        print(f'Vorige Docker-image: {backup_image}')
        if unit_before is not None and (target/'data/upload.json').exists():
            run('/usr/bin/python3', str(target/'sync.py'), 'install-command-timer')
            print('Starten vanuit Portal ingeschakeld.')
        print(f'Test nu: cd {target} && sudo ./plenion.sh sync')
    finally:
        subprocess.run(['docker','image','rm',candidate],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=False)


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target',required=True,type=Path)
    args=parser.parse_args()
    try:
        install(args.target)
    except (RuntimeError,OSError,subprocess.SubprocessError) as error:
        parser.exit(1,f'Update mislukt: {error}\n')
