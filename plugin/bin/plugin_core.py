"""Configuration and queue shared by authenticated CGI and unprivileged worker."""
import contextlib
import fcntl
import json
import os
import re
import secrets
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit

FIELDS = ('QBOX_PORTAL_URL', 'QBOX_DATAHUB_URL', 'QBOX_SOURCE_URL', 'QBOX_SOURCE_MODE',
          'QBOX_POLL_SECONDS', 'QBOX_TV_ENABLED', 'QBOX_NAS_URL', 'QBOX_SOURCE_CA_FILE')
DEFAULTS = dict(QBOX_PORTAL_URL='https://portal.cohvera.be', QBOX_DATAHUB_URL='http://192.168.10.228:3210',
                QBOX_SOURCE_URL='http://www.tomme-energie.lan/projecten-tv', QBOX_SOURCE_MODE='datahub',
                QBOX_POLL_SECONDS='60', QBOX_TV_ENABLED='0', QBOX_NAS_URL='', QBOX_SOURCE_CA_FILE='',
                QBOX_IMPORT_API_KEY='', enabled=False)

class InputError(Exception):
    pass


def read(path, fallback=None):
    try:
        return json.loads(Path(path).read_text())
    except (FileNotFoundError, ValueError):
        return fallback


def write(path, value):
    path = Path(path)
    fd, tmp = tempfile.mkstemp(prefix='.qbox-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as out:
            json.dump(value, out)
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


@contextlib.contextmanager
def locked(directory, name='control.lock', blocking=True):
    with open(Path(directory) / name, 'a') as handle:
        os.chmod(handle.name, 0o600)
        fcntl.flock(handle, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        yield


def validate(body, previous=None):
    if not isinstance(body, dict):
        raise InputError('Ongeldige instellingen.')
    previous = previous or DEFAULTS
    result = {}
    for key in FIELDS:
        value = body.get(key, DEFAULTS[key])
        if not isinstance(value, str) or len(value) > 2048 or any(ord(c) < 32 for c in value):
            raise InputError('Ongeldig configuratieveld: ' + key)
        result[key] = value.strip()
    for key in ('QBOX_PORTAL_URL', 'QBOX_DATAHUB_URL', 'QBOX_SOURCE_URL', 'QBOX_NAS_URL'):
        value = result[key]
        if key == 'QBOX_NAS_URL' and not value:
            continue
        try:
            u = urlsplit(value)
            schemes = ('https',) if key == 'QBOX_PORTAL_URL' else ('http', 'https')
            if u.scheme not in schemes or not u.hostname or u.username or u.password or u.query or u.fragment:
                raise ValueError()
            u.port
            if key == 'QBOX_PORTAL_URL' and u.path not in ('', '/'):
                raise ValueError()
        except ValueError:
            raise InputError('Controleer het adres: ' + key + '. Cohvera vereist HTTPS, zonder /api.') from None
        result[key] = value.rstrip('/')
    if result['QBOX_SOURCE_MODE'] not in ('datahub', 'tv'):
        raise InputError('Kies een geldige bronmodus.')
    try:
        interval = int(result['QBOX_POLL_SECONDS'])
        if not 30 <= interval <= 3600:
            raise ValueError()
    except ValueError:
        raise InputError('Het controle-interval moet 30 tot 3600 seconden zijn.') from None
    if result['QBOX_TV_ENABLED'] not in ('0', '1'):
        raise InputError('Ongeldige keuze voor tv-gegevens.')
    ca = result['QBOX_SOURCE_CA_FILE']
    if ca and (not ca.startswith('/') or '..' in Path(ca).parts):
        raise InputError('Gebruik een absoluut pad naar het CA-certificaat.')
    key = body.get('QBOX_IMPORT_API_KEY', '')
    if not isinstance(key, str):
        raise InputError('Ongeldige importsleutel.')
    key = key.strip() or previous.get('QBOX_IMPORT_API_KEY', '')
    if key and not re.fullmatch(r'[A-Za-z0-9_-]{43,128}', key):
        raise InputError('De importsleutel heeft een ongeldig formaat.')
    if type(body.get('enabled', False)) is not bool:
        raise InputError('Ongeldige synchronisatiekeuze.')
    result.update(QBOX_IMPORT_API_KEY=key, enabled=body.get('enabled', False))
    if result['enabled'] and not key:
        raise InputError('Vul eerst de importsleutel in.')
    result['revision'] = secrets.token_hex(12)
    return result


class Store:
    def __init__(self, config, data):
        self.config = Path(config)
        self.data = Path(data)

    def settings(self):
        return read(self.config / 'settings.json', DEFAULTS.copy())

    def token(self):
        with locked(self.config):
            token = read(self.config / 'csrf.json')
            if not token:
                token = secrets.token_urlsafe(32)
                write(self.config / 'csrf.json', token)
            return token

    def public(self):
        settings = self.settings()
        safe = {k: settings.get(k, DEFAULTS[k]) for k in FIELDS}
        safe['enabled'] = settings.get('enabled', False)
        safe['has_key'] = bool(settings.get('QBOX_IMPORT_API_KEY'))
        status = read(self.data / 'status.json', {})
        status['worker_online'] = time.time() - status.get('heartbeat', 0) < 45
        status['queued'] = (self.data / 'request.json').exists()
        return dict(settings=safe, status=status, csrf=self.token())

    def action(self, body):
        if not isinstance(body, dict) or not isinstance(body.get('csrf'), str) or not secrets.compare_digest(body['csrf'], self.token()):
            raise InputError('De pagina is verlopen. Vernieuw en probeer opnieuw.')
        action = body.get('action')
        with locked(self.config):
            if action == 'save':
                settings = validate(body.get('settings'), self.settings())
                write(self.config / 'settings.json', settings)
                return {'message': 'Instellingen opgeslagen. De connector past ze automatisch toe.'}
            if action not in ('test', 'sync'):
                raise InputError('Onbekende actie.')
            if not self.settings().get('QBOX_IMPORT_API_KEY'):
                raise InputError('Sla eerst een importsleutel op.')
            status = read(self.data / 'status.json', {})
            if time.time() - status.get('heartbeat', 0) >= 45:
                raise InputError('De connectorservice is niet bereikbaar. Controleer de installatie.')
            if (self.data / 'request.json').exists() or status.get('busy'):
                raise InputError('Er loopt al een controle of synchronisatie.')
            write(self.data / 'request.json', {'action': action, 'id': secrets.token_hex(12), 'time': time.time()})
            return {'message': 'Broncontrole ingepland.' if action == 'test' else 'Synchronisatie ingepland.'}
