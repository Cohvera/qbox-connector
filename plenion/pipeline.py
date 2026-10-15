#!/usr/bin/env python3
"""Local file capture -> offline reader -> atomic Cohvera project import."""
import argparse
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid


class PipelineError(Exception):
    pass


def stamp(value):
    result = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise PipelineError('Bronmoment moet een tijdzone bevatten.')
    return result.timestamp()


def iso(value):
    return dt.datetime.fromtimestamp(value, dt.timezone.utc).isoformat()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as out:
            json.dump(value, out, ensure_ascii=False)
            out.flush()
            os.fsync(out.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name): os.unlink(name)


def inventory(root, expected):
    result = {}
    if not expected or len(expected) > 5000 or len(set(expected)) != len(expected):
        raise PipelineError('Stel een unieke, volledige bestandslijst in.')
    for name in expected:
        relative = Path(name)
        if relative.is_absolute() or '..' in relative.parts or not relative.parts:
            raise PipelineError('Ongeldig bronbestandspad.')
        path = root / relative
        if any((root / Path(*relative.parts[:i])).is_symlink() for i in range(1, len(relative.parts)+1)):
            raise PipelineError('Symlinks in de bron zijn niet toegestaan.')
        if not path.is_file():
            raise PipelineError('Verplicht bronbestand ontbreekt.')
        stat = path.stat()
        result[name] = (stat.st_size, stat.st_mtime_ns, stat.st_ino)
    return result


def digest(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def capture(config, work):
    source = Path(config['source_dir']).resolve(strict=True)
    if source == work or source in work.parents or work in source.parents:
        raise PipelineError('Bron en werkmap mogen niet in elkaar liggen.')
    marker = source / config.get('completion_file', 'backup-complete.json')
    if marker.parent != source or marker.is_symlink():
        raise PipelineError('Ongeldig voltooiingsbestand.')
    raw = marker.read_bytes()
    receipt = json.loads(raw)
    observed = stamp(receipt['completed_at'])
    if receipt.get('complete') is not True or not isinstance(receipt.get('files'), dict):
        raise PipelineError('Geen bevestigde volledige bronbackup.')
    if not -300 <= time.time() - observed < 86400:
        raise PipelineError('Bronbackup is verouderd of ligt in de toekomst.')
    expected = config['required_files']
    if set(receipt['files']) != set(expected):
        raise PipelineError('Backupmanifest komt niet overeen met de vereiste bestanden.')
    before = inventory(source, expected)
    wait = int(config.get('stability_seconds', 120))
    if not 1 <= wait <= 600:
        raise PipelineError('Stabiliteitscontrole moet 1 tot 600 seconden duren.')
    time.sleep(wait)
    if inventory(source, expected) != before:
        raise PipelineError('Bron wordt nog bijgewerkt; probeer later opnieuw.')
    batch = 'qbox-' + uuid.uuid4().hex
    directory = work / 'captures' / batch
    directory.mkdir(parents=True, mode=0o700)
    try:
        for name in expected:
            target = directory / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / name, target)
            checksum = digest(target)
            if not re.fullmatch(r'[0-9a-f]{64}', str(receipt['files'][name])) or checksum != receipt['files'][name] or digest(source / name) != checksum:
                raise PipelineError('Bron of kopie wijkt af van de bevestigde backup.')
        if marker.read_bytes() != raw or inventory(source, expected) != before:
            raise PipelineError('Bron veranderde tijdens kopiëren.')
        write(directory / 'capture.json', {'id':batch, 'source_observed_at':iso(observed), 'files':receipt['files']})
        return directory, observed
    except Exception:
        shutil.rmtree(directory)
        raise


def flag(value):
    if value is True or value in ('1', 1): return True
    if value is False or value in ('0', 0): return False
    raise PipelineError('Een bronvinkje ontbreekt of is ongeldig.')


def normalize(rows, observed, batch, capture_hash):
    projects, numbers, ids = [], set(), set()
    for row in rows:
        if not flag(row.get('is_current')): continue
        p = {key: row.get(key) for key in ('source_id','number','customer','description','planned','status_label')}
        for key, maximum in (('source_id',100),('number',60),('customer',300),('description',2000),('planned',10),('status_label',100)):
            if not isinstance(p[key], str) or len(p[key]) > maximum:
                raise PipelineError('Projectveld ontbreekt of is te lang: ' + key)
        if not re.fullmatch(r'[0-9]{1,60}', p['number']) or not p['source_id'] or not p['status_label'].strip():
            raise PipelineError('Projectidentiteit of status ontbreekt.')
        if p['number'] in numbers or p['source_id'] in ids:
            raise PipelineError('Meerdere huidige revisies voor hetzelfde project.')
        if p['planned'] and dt.date.fromisoformat(p['planned']).isoformat() != p['planned']:
            raise PipelineError('Ongeldige plandatum.')
        numbers.add(p['number']); ids.add(p['source_id'])
        p.update(active=flag(row.get('active')), closed=flag(row.get('closed')), is_current=True, evidence='qbox_capture')
        projects.append(p)
    if len(projects) > 10000:
        raise PipelineError('Te veel huidige projecten.')
    return dict(schema_version=4,source_kind='qbox_capture',source_system='PLENION',live=True,scope='ALL_CURRENT_PROJECTS',complete=True,
                snapshot_id=batch,batch_id=batch,source_observed_at=iso(observed),valid_until=iso(observed+86400),
                capture_sha256=capture_hash,projects=sorted(projects,key=lambda p:p['number']))


def publish(config, payload):
    url = config['portal_url'].rstrip('/')
    from urllib.parse import urlsplit
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path:
        raise PipelineError('Portal moet een HTTPS-basisadres zijn.')
    key = os.environ.get('QBOX_IMPORT_API_KEY', '')
    if not re.fullmatch(r'[A-Za-z0-9_-]{43,128}', key):
        raise PipelineError('QBOX_IMPORT_API_KEY ontbreekt of is ongeldig.')
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            raise PipelineError('Doorverwijzingen bij import worden niet gevolgd.')
    request = urllib.request.Request(url+'/api/integrations/qbox/plenion/projects', data=json.dumps(payload).encode(),
                                    headers={'Content-Type':'application/json','X-Qbox-Key':key}, method='POST')
    with urllib.request.build_opener(NoRedirect).open(request, timeout=90) as response:
        result = json.loads(response.read(65536))
    if result.get('accepted') is not True or result.get('count') != len(payload['projects']) or result.get('snapshotId') != payload['snapshot_id']:
        raise PipelineError('Cohvera heeft de import niet bevestigd.')
    return result


def run(config, dry_run=False):
    work = Path(config['work_dir']).resolve()
    work.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (work/'worker.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        def status(stage, **values):
            write(work/'status.json', dict(stage=stage, updated_at=iso(time.time()), **values))
        directory = None
        try:
            status('capture')
            directory, observed = capture(config, work)
            identity = hashlib.sha256(json.dumps(json.loads((directory/'capture.json').read_text())['files'],sort_keys=True).encode()).hexdigest()
            # A stable ID makes retries idempotent even when the transport fails after commit.
            batch = 'qbox-' + hashlib.sha256((iso(observed)+identity).encode()).hexdigest()[:40]
            status('reading', snapshot_id=batch)
            output = directory/'reader.json'
            subprocess.run([sys.executable, str(Path(__file__).resolve()), '--read-capture', str(directory), '--config', config['_path']],
                           check=True, timeout=300, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            rows = json.loads(output.read_text())
            payload = normalize(rows, observed, batch, identity)
            if not payload['projects'] and not config.get('allow_empty', False):
                raise PipelineError('Lege projectexport niet gepubliceerd; controleer de bronselectie.')
            status('validated', snapshot_id=batch, project_count=len(payload['projects']))
            if dry_run:
                return {'validated':True, 'count':len(payload['projects'])}
            status('publishing', snapshot_id=batch)
            result = publish(config, payload)
            write(work/'last-success.json',dict(snapshot_id=batch,source_observed_at=iso(observed),received_at=iso(time.time()),count=result['count'],company=result.get('companyCode')))
            status('complete', snapshot_id=batch, project_count=result['count'])
            return result
        except Exception as error:
            status('failed', message=str(error) if isinstance(error, PipelineError) else 'Verwerking niet voltooid. Controleer driver, veldkoppeling en verbinding; de laatste bevestigde import blijft behouden.')
            raise
        finally:
            # Raw business data is temporary; retries recapture the verified source.
            if directory: shutil.rmtree(directory)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--read-capture', help=argparse.SUPPRESS)
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text())
    config['_path'] = str(Path(args.config).resolve())
    os.umask(0o077)
    if args.read_capture:
        from odbc_reader import read_projects
        root = Path(args.read_capture)
        write(root/'reader.json', read_projects(config['reader'], root))
    else:
        print(json.dumps(run(config,args.dry_run)))

if __name__ == '__main__':
    try: main()
    except Exception:
        print('Q-box verwerking mislukt. Controleer bronmanifest, configuratie, driver en verbinding; geen nieuwe import bevestigd.', file=sys.stderr)
        sys.exit(1)
