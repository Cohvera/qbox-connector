"""Export and send project JSON to Portal. Credentials stay on the Q-box."""
import argparse
from datetime import datetime, timezone
import uuid
import fcntl
import getpass
import http.client
import ipaddress
import socket
import json
import os
from pathlib import Path
import re
import ssl
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from reader import save

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / 'data' / 'upload.json'
DEFAULT_PORTAL_URL = 'https://portal.cohvera.be'
ENDPOINT = '/api/integrations/qbox/plenion/reader-projects'
STATUS_ENDPOINT = '/api/integrations/qbox/reader-status'
READER_VERSION = '3.3'


def validate(config):
    url = config.get('portal_url', DEFAULT_PORTAL_URL)
    parts = urllib.parse.urlsplit(url)
    if (parts.scheme != 'https' or not parts.hostname or parts.username or parts.password
            or parts.query or parts.fragment or parts.path not in ('', '/')):
        raise ValueError('Geef alleen het HTTPS-adres van Cohvera, zonder pad of inloggegevens.')
    if not re.fullmatch(r'[A-Za-z0-9_-]{43,128}', config.get('api_key', '')):
        raise ValueError('De importsleutel moet 43–128 letters, cijfers, underscores of streepjes bevatten.')
    if config.get('connect_ip'):
        try:
            ipaddress.ip_address(config['connect_ip'])
        except ValueError:
            raise ValueError('Vul een geldig ZeroTier-IP van de Portal-server in, of laat leeg.') from None
    return url.rstrip('/') + ENDPOINT


class RoutedHTTPSConnection(http.client.HTTPSConnection):
    """Connect to the chosen IP while verifying TLS against the URL hostname."""
    def __init__(self, host, *, connect_ip, **kwargs):
        super().__init__(host, **kwargs)
        self.connect_ip = connect_ip

    def connect(self):
        # No proxy/tunnel: this route is explicitly selected by the operator.
        sock = socket.create_connection((self.connect_ip, self.port), self.timeout,
                                        self.source_address)
        try:
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
        except BaseException:
            sock.close()
            raise


class RoutedHTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self, connect_ip):
        super().__init__(context=ssl.create_default_context())
        self.connect_ip = connect_ip

    def https_open(self, request):
        def connection(host, **kwargs):
            return RoutedHTTPSConnection(host, connect_ip=self.connect_ip, **kwargs)
        return self.do_open(connection, request, context=self._context)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never send the import key to a redirect target.


def portal_opener(config):
    if config.get('connect_ip'):
        return urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect(),
                                             RoutedHTTPSHandler(config['connect_ip']))
    return urllib.request.build_opener(NoRedirect(), urllib.request.HTTPSHandler(context=ssl.create_default_context()))


class UploadRejected(RuntimeError):
    def __init__(self, message, status):
        super().__init__(message)
        self.status = status


def report_status(config, run_id, phase, record_count=None, http_status=None):
    # Only explicit operational fields, never driver output, config or credentials.
    queued = False
    try:
        outbox = ROOT / 'data' / 'reader-status-outbox.json'
        try:
            previous = json.loads(outbox.read_text()) if outbox.exists() else []
        except (ValueError, OSError):
            previous = []
        now = datetime.now(timezone.utc)
        events = []
        for e in previous if isinstance(previous, list) else []:
            try:
                if (isinstance(e, dict) and
                        0 <= (now - datetime.fromisoformat(e['observedAt'])).total_seconds() < 29 * 86400 and
                        e['phase'] in ('started', 'read_ok', 'configuration_failed', 'read_failed', 'upload_failed', 'success') and
                        str(uuid.UUID(e['runId'], version=4)) == e['runId'] and
                        re.fullmatch(r'[0-9]{1,3}\.[0-9]{1,3}(?:\.[0-9]{1,3})?', e['readerVersion'])):
                    events.append({k: e[k] for k in ('runId', 'phase', 'observedAt', 'readerVersion', 'recordCount', 'httpStatus') if k in e})
            except (KeyError, TypeError, ValueError, AttributeError):
                continue

        event = {'runId': run_id, 'phase': phase, 'observedAt': now.isoformat(), 'readerVersion': READER_VERSION}
        if record_count is not None:
            event['recordCount'] = record_count
        if http_status is not None:
            event['httpStatus'] = http_status
        events = (events + [event])[-100:]
        save(outbox, events)
        queued = True
        endpoint = validate(config).removesuffix(ENDPOINT) + STATUS_ENDPOINT
        request = urllib.request.Request(endpoint, data=json.dumps({'events': events}).encode(), method='POST',
                    headers={'Content-Type': 'application/json', 'X-Qbox-Key': config['api_key']})
        with portal_opener(config).open(request, timeout=5) as response:
            result = json.loads(response.read(8192))
        if result.get('accepted') is not True:
            raise ValueError('Status niet bevestigd')
        save(outbox, [])
    except Exception:
        # Monitoring must never prevent reading/uploading projects; retry next phase/run.
        print('Statusmelding niet afgeleverd; lokale wachtrij wordt bij een volgende poging opnieuw verstuurd.' if queued
              else 'Statusmelding kon niet lokaal worden opgeslagen; controleer de data-map.', flush=True)


def upload(config, payload):
    endpoint = validate(config)
    if len(payload) > 20 * 1024 * 1024:
        raise ValueError('Export groter dan 20 MB; niet verstuurd.')
    data = json.loads(payload)
    if (data.get('source') != 'plenion_reader_projects' or data.get('schema_version') not in (2, 3)
            or data.get('truncated') is not False):
        raise ValueError('Geen volledige readerexport v2/v3; niet verstuurd.')
    request = urllib.request.Request(endpoint, data=payload, method='POST', headers={
        'Content-Type':'application/json', 'X-Qbox-Key':config['api_key']})
    opener = portal_opener(config)
    try:
        with opener.open(request, timeout=90) as response:
            result = json.loads(response.read(65537))
    except urllib.error.HTTPError as error:
        detail = ''
        try:
            body = json.loads(error.read(8192)) if error.fp is not None else {}
            message = body.get('message') if isinstance(body, dict) else None
            if isinstance(message, list):
                message = '; '.join(item for item in message if isinstance(item, str))
            if isinstance(message, str):
                detail = ' '.join(message.replace(config['api_key'], '[afgeschermd]').split())[:500]
        except (ValueError, OSError, AttributeError):
            pass
        raise UploadRejected(f'Cohvera weigert de import (HTTP {error.code}). ' +
                           (detail or 'Geen detail ontvangen; controleer de API-log.'), error.code) from None
    except (urllib.error.URLError, TimeoutError):
        raise RuntimeError('Cohvera niet bereikbaar of TLS-controle mislukt. Vorige import blijft behouden.') from None
    if result.get('accepted') is not True:
        raise RuntimeError('Geen geldige importbevestiging van Cohvera.')
    return result


def fetch_reader_options(config, applied_revision=None):
    schema_path = ROOT / 'data' / 'hfsql-schema.json'
    # Query column metadata only; no extra project contents are read here.
    if not schema_path.exists() or datetime.now().timestamp() - schema_path.stat().st_mtime > 86400:
        subprocess.run(['/bin/sh', str(ROOT / 'plenion.sh'), 'schema'], check=True, timeout=60)
    schema = json.loads(schema_path.read_text())
    statuses_path = ROOT / 'data' / 'project-statuses.json'
    if not statuses_path.exists() or datetime.now().timestamp() - statuses_path.stat().st_mtime > 86400:
        subprocess.run(['/bin/sh', str(ROOT / 'plenion.sh'), 'statuses'], check=True, timeout=60)
    statuses = [{'code': str(s.get('code') or '').strip(), 'label': str(s.get('label') or '').replace('\x00', '').strip()}
                for s in json.loads(statuses_path.read_text())['statuses']
                if re.fullmatch(r'[A-Za-z0-9_-]{1,30}', str(s.get('code') or '').strip())]
    body = {'columns': schema['columns'], 'readerVersion': READER_VERSION, 'statuses': statuses}
    if applied_revision is not None:
        body['appliedRevision'] = applied_revision
    endpoint = validate(config).removesuffix(ENDPOINT) + '/api/integrations/qbox/reader-config'
    request = urllib.request.Request(endpoint, data=json.dumps(body).encode(), method='POST',
        headers={'Content-Type': 'application/json', 'X-Qbox-Key': config['api_key']})
    with portal_opener(config).open(request, timeout=10) as response:
        options = json.loads(response.read(65536))
    from reader import portal_selection, project_filter
    _, _, chosen = portal_selection(options)
    selection = project_filter(options.get('projectFilter'))
    if selection and any(c not in {s['code'] for s in statuses} for c in selection['statusCodes']):
        raise ValueError('Gekozen status ontbreekt in Plenion; export gestopt.')
    if type(options.get('revision')) is not int or options['revision'] < 0:
        raise ValueError('Ongeldige configuratieversie van Portal.')
    available = {c.lower() for c in schema['columns']}
    if any(c.lower() not in available for c in chosen):
        raise ValueError('Geselecteerde HFSQL-kolom ontbreekt; export gestopt.')
    # Docker uses the connection-file owner, even when this process runs as root.
    path = ROOT / 'data' / 'reader-options.json'
    save(path, {'revision': options['revision'], 'selectedColumns': chosen, 'projectFilter': selection})
    owner = (ROOT / 'data' / 'connection.json').stat()
    if os.geteuid() == 0:
        os.chown(path, owner.st_uid, owner.st_gid)
    return options


def sync():
    # A failed new read must never upload the previous file.
    config = json.loads(CONFIG.read_text())
    validate(config)
    with (ROOT / 'data' / 'upload.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('Een synchronisatie loopt al; deze poging overgeslagen.')
            return
        run_id = str(uuid.uuid4())
        report_status(config, run_id, 'started')
        stage = 'configuration'
        try:
            applied_path = ROOT / 'data' / 'applied-reader-options.json'
            applied = json.loads(applied_path.read_text()).get('revision') if applied_path.exists() else None
            options = fetch_reader_options(config, applied)
            stage = 'read'
            subprocess.run(['/bin/sh', str(ROOT / 'plenion.sh'), 'portal'], check=True, timeout=180)
            payload = (ROOT / 'data' / 'portal-projects.json').read_bytes()
            record_count = json.loads(payload).get('count')
            report_status(config, run_id, 'read_ok', record_count=record_count)
            stage = 'upload'
            result = upload(config, payload)
            try:
                save(applied_path, {'revision': options['revision']})
            except OSError:
                print('Import gelukt, maar de toegepaste configuratieversie kon niet lokaal worden bewaard.', flush=True)
            report_status(config, run_id, 'success', record_count=record_count)
            print(f'Cohvera bevestigt {result.get("count", 0)} projecten en '
                  f'{result.get("documentCount", 0)} onderliggende records voor {result.get("companyCode", "onbekend")}.', flush=True)
        except Exception as error:
            report_status(config, run_id, {'configuration':'configuration_failed','read':'read_failed','upload':'upload_failed'}[stage],
                          http_status=error.status if isinstance(error, UploadRejected) else error.code if isinstance(error, urllib.error.HTTPError) else None)
            raise


def install_timer():
    if os.geteuid() != 0:
        raise ValueError('Gebruik sudo ./plenion.sh auto-install.')
    if not re.fullmatch(r'/[A-Za-z0-9_./-]+', str(ROOT)):
        raise ValueError('De installatiemap bevat niet-ondersteunde tekens.')
    validate(json.loads(CONFIG.read_text()))
    unit_dir = Path('/etc/systemd/system')
    (unit_dir / 'cohvera-reader-sync.service').write_text(f"""[Unit]
Description=Cohvera Plenion-reader export en upload
Wants=network-online.target
After=network-online.target docker.service
Requires=docker.service

[Service]
Type=oneshot
User=root
WorkingDirectory={ROOT}
ExecStart=/usr/bin/python3 {ROOT}/sync.py run
UMask=0077
TimeoutStartSec=480
""")
    (unit_dir / 'cohvera-reader-sync.timer').write_text("""[Unit]
Description=Plenion-projecten elke 15 minuten synchroniseren

[Timer]
OnCalendar=*:0/15
Persistent=true
RandomizedDelaySec=15
Unit=cohvera-reader-sync.service

[Install]
WantedBy=timers.target
""")
    subprocess.run(['systemctl', 'daemon-reload'], check=True)
    subprocess.run(['systemctl', 'enable', '--now', 'cohvera-reader-sync.timer'], check=True)
    print('Timer actief: elke 15 minuten. Test nu met sudo ./plenion.sh sync.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['configure', 'run', 'install-timer'])
    parser.add_argument('--portal-url', help='Optioneel afwijkend HTTPS-adres bij configure')
    args = parser.parse_args()
    if args.portal_url and args.action != 'configure':
        parser.error('--portal-url hoort bij configure')
    if args.action == 'configure':
        existing = json.loads(CONFIG.read_text()) if CONFIG.exists() else {}
        portal_url = args.portal_url or existing.get('portal_url') or DEFAULT_PORTAL_URL
        print(f'Cohvera: {portal_url}')
        print('ZeroTier-IP = het adres van de Portal-server, niet van deze Q-box. /direct gebruikt internet.')
        current_ip = existing.get('connect_ip', '')
        route = input(f'ZeroTier-IP Portal-server [{current_ip or "direct via internet"}]: ').strip()
        connect_ip = '' if route == '/direct' else (route or current_ip)
        key = getpass.getpass('Bedrijfs-API-key (onzichtbaar; leeg = bestaande behouden): ').strip()
        config = {'portal_url': portal_url, 'api_key': key or existing.get('api_key', '')}
        if connect_ip:
            config['connect_ip'] = connect_ip
        validate(config)
        CONFIG.parent.mkdir(mode=0o700, exist_ok=True)
        save(CONFIG, config)
        print('Uploadconfiguratie opgeslagen. De server kiest het bedrijf; geen bedrijfsfilter op de Q-box.')
    elif args.action == 'install-timer':
        install_timer()
    else:
        sync()


if __name__ == '__main__':
    try:
        main()
    except RuntimeError as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
    except (OSError, ValueError, subprocess.SubprocessError):
        # Do not print raw native/network exceptions: they may contain credentials or response data.
        print('Synchronisatie/configuratie mislukt. Controleer de instellingen en de voorgaande readeruitvoer; vorige import blijft behouden.', file=sys.stderr)
        sys.exit(1)
