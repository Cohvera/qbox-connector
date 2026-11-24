"""Export and send project JSON to Portal. Credentials stay on the Q-box."""
import argparse
from datetime import datetime, timezone
import uuid
import fcntl
import getpass
import hashlib
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
import time
import urllib.error
import urllib.parse
import urllib.request
from reader import save, normalize_column_name

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / 'data' / 'upload.json'
DEFAULT_PORTAL_URL = 'https://portal.cohvera.be'
ENDPOINT = '/api/integrations/qbox/plenion/reader-projects'
STATUS_ENDPOINT = '/api/integrations/qbox/reader-status'
READER_VERSION = '3.11'
RUN_HEADER = 'X-Qbox-Run-Id'
STATUS_PHASES = ('started', 'read_ok', 'configuration_failed', 'read_failed', 'upload_failed', 'success', 'command_poll_failed')
DIAGNOSTIC_STEPS = ('local_configuration', 'hfsql_schema', 'hfsql_statuses', 'portal_configuration',
                    'configuration_validation', 'hfsql_export', 'export_file', 'portal_upload',
                    'portal_accepted', 'command_poll', 'journal_sync')
JOURNAL_UNITS = ('cohvera-reader-sync.service', 'cohvera-reader-command.service')
READER_ERRORS = {
    'Cannot connect to the Docker daemon': 'docker_daemon_unreachable',
    'No such image': 'docker_image_missing',
    'Unable to find image': 'docker_image_missing',
    'Geen volledig PRS-statusoverzicht': 'incomplete_status_catalog',
    'Ontbrekende of dubbele status-ID': 'invalid_status_catalog',
    'Geen resultaat binnen 90 seconden': 'hfsql_reader_timeout',
    'Kies eerst een database': 'database_missing',
    'Portaalexport afgekapt': 'export_row_limit',
    'groter dan 20 MB': 'export_size_limit',
    'Onbekende vinkjeswaarde': 'invalid_project_flag',
    'Onbekende extra HFSQL-kolom': 'unknown_column',
    'Fout: timed out': 'TimeoutError',
    'Name or service not known': 'gaierror',
}


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


class PortalTransportError(RuntimeError):
    def __init__(self, operation, error):
        reason = error.reason if isinstance(error, urllib.error.URLError) else error
        self.reason = type(reason).__name__
        self.errno = reason.errno if isinstance(reason, OSError) else None
        super().__init__(f'{operation} niet bereikbaar ({self.reason}); controleer netwerk/ZeroTier en TLS.')


class SyncConfigurationError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def failure_fields(error):
    details = {'type': type(error).__name__}
    if isinstance(error, UploadRejected):
        details['httpStatus'] = error.status
    elif isinstance(error, PortalTransportError):
        details['networkError'] = error.reason
        if error.errno is not None:
            details['errno'] = error.errno
    elif isinstance(error, SyncConfigurationError):
        details['configurationError'] = error.code
    elif isinstance(error, subprocess.TimeoutExpired):
        details['timeoutSeconds'] = error.timeout
    elif isinstance(error, subprocess.CalledProcessError):
        details['exitCode'] = error.returncode
        stderr = error.stderr or ''
        if isinstance(stderr, bytes):
            stderr = stderr.decode('utf-8', errors='replace')
        odbc = re.search(r'(Aanmelden bij HFSQL|Projecten lezen|Kolommen opvragen|ODBC-omgeving|Verbinding aanmaken) '
                         r'mislukt \(SQLSTATE ([A-Z0-9]{5}|onbekend), code (-?\d{1,10})\)', stderr)
        if odbc:
            details.update(odbcAction=odbc[1], sqlState=odbc[2], nativeCode=int(odbc[3]))
        errno = re.search(r'\[Errno (\d+)\]', stderr)
        if errno:
            details['errno'] = int(errno[1])
        for marker, code in READER_ERRORS.items():
            if marker in stderr:
                details['readerError'] = code
                break
    elif isinstance(error, json.JSONDecodeError):
        details.update(line=error.lineno, column=error.colno)
    elif isinstance(error, OSError) and error.errno is not None:
        details['errno'] = error.errno
    return details


def public_diagnostic(value):
    if not isinstance(value, dict) or value.get('step') not in DIAGNOSTIC_STEPS:
        return None
    data = {'step': value['step']}
    for key, low, high in (('durationMs', 0, 900000), ('errno', 1, 4096), ('nativeCode', -2147483648, 2147483647)):
        number = value.get(key)
        if type(number) is int and low <= number <= high:
            data[key] = number
    sql_state = value.get('sqlState')
    if isinstance(sql_state, str) and re.fullmatch(r'[A-Z0-9]{5}', sql_state):
        data['sqlState'] = sql_state
    reason = value.get('reason')
    safe_reasons = (*READER_ERRORS.values(), 'status_not_in_hfsql', 'gaierror', 'TimeoutError',
                    'ConnectionRefusedError', 'ConnectionResetError', 'SSLError', 'SSLCertVerificationError',
                    'PermissionError', 'FileNotFoundError', 'JSONDecodeError', 'TimeoutExpired')
    if reason in safe_reasons:
        data['reason'] = reason
    return data


class SyncDiagnostic:
    def __init__(self, run_id):
        self.start = time.monotonic()
        self.step_start = self.start
        self.report = {'runId': run_id, 'readerVersion': READER_VERSION,
                       'startedAt': datetime.now(timezone.utc).isoformat(), 'steps': []}

    def write(self, name):
        try:
            save(ROOT / 'data' / name, self.report)
        except OSError:
            print(f'Sync {self.report["runId"]}: kon {name} niet opslaan.', file=sys.stderr, flush=True)

    def step(self, name, **metrics):
        now = time.monotonic()
        steps = self.report['steps']
        if steps:
            steps[-1]['durationMs'] = round((now - self.step_start) * 1000)
        self.step_start = now
        steps.append({'name': name, 'elapsedMs': round((now - self.start) * 1000), **metrics})
        print(f'Sync {self.report["runId"]}: {name} {json.dumps(metrics)}', flush=True)
        self.write('last-sync.json')

    def finish(self, error=None):
        now = time.monotonic()
        self.report['steps'][-1]['durationMs'] = round((now - self.step_start) * 1000)
        self.report['durationMs'] = round((now - self.start) * 1000)
        self.report['finishedAt'] = datetime.now(timezone.utc).isoformat()
        self.report['result'] = 'failed' if error is not None else 'success'
        if error is not None:
            self.report['error'] = failure_fields(error)
            self.write('last-sync-failure.json')
        self.write('last-sync.json')
        print(f'Sync {self.report["runId"]}: {self.report["result"]} na {self.report["durationMs"]} ms '
              f'bij {self.report["steps"][-1]["name"]} {json.dumps(self.report.get("error", {}))}', flush=True)

    def telemetry(self):
        error = self.report.get('error', {})
        return public_diagnostic({**error, 'step': self.report['steps'][-1]['name'],
            'durationMs': self.report.get('durationMs'),
            'reason': error.get('readerError') or error.get('configurationError') or error.get('networkError') or error.get('type')})


def run_reader(action, timeout):
    subprocess.run(['/bin/sh', str(ROOT / 'plenion.sh'), action], check=True, timeout=timeout,
                   stderr=subprocess.PIPE, text=True, errors='replace')


def report_status(config, run_id, phase, record_count=None, http_status=None, diagnostic=None):
    try:
        with (ROOT / 'data' / 'reader-status.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            send_status(config, run_id, phase, record_count, http_status, diagnostic)
    except OSError:
        print('Statusmelding kon niet lokaal worden opgeslagen; controleer de data-map.', flush=True)


def post_status_events(config, events):
    endpoint = validate(config).removesuffix(ENDPOINT) + STATUS_ENDPOINT
    request = urllib.request.Request(endpoint, data=json.dumps({'events': events}).encode(), method='POST',
                headers={'Content-Type': 'application/json', 'X-Qbox-Key': config['api_key']})
    with portal_opener(config).open(request, timeout=5) as response:
        result = json.loads(response.read(8192))
    if not isinstance(result, dict) or result.get('accepted') is not True:
        raise ValueError('Status niet bevestigd')


def send_status(config, run_id, phase, record_count, http_status, diagnostic):
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
                        e['phase'] in STATUS_PHASES and
                        str(uuid.UUID(e['runId'], version=4)) == e['runId'] and
                        re.fullmatch(r'[0-9]{1,3}\.[0-9]{1,3}(?:\.[0-9]{1,3})?', e['readerVersion'])):
                    queued_event = {k: e[k] for k in ('runId', 'phase', 'observedAt', 'readerVersion', 'recordCount', 'httpStatus') if k in e}
                    if details := public_diagnostic(e.get('diagnostic')):
                        queued_event['diagnostic'] = details
                    events.append(queued_event)
            except (KeyError, TypeError, ValueError, AttributeError):
                continue

        event = {'runId': run_id, 'phase': phase, 'observedAt': now.isoformat(), 'readerVersion': READER_VERSION}
        if record_count is not None:
            event['recordCount'] = record_count
        if http_status is not None:
            event['httpStatus'] = http_status
        if details := public_diagnostic(diagnostic):
            event['diagnostic'] = details
        events = (events + [event])[-100:]
        save(outbox, events)
        queued = True
        post_status_events(config, events)
        save(outbox, [])
    except Exception:
        # Monitoring must never prevent reading/uploading projects; retry next phase/run.
        print('Statusmelding niet afgeleverd; lokale wachtrij wordt bij een volgende poging opnieuw verstuurd.' if queued
              else 'Statusmelding kon niet lokaal worden opgeslagen; controleer de data-map.', flush=True)


def journal_event(entry):
    unit = entry.get('_SYSTEMD_UNIT')
    if unit not in JOURNAL_UNITS:
        unit = entry.get('UNIT')
    message, cursor = entry.get('MESSAGE'), entry.get('__CURSOR')
    if unit not in JOURNAL_UNITS or not isinstance(message, str) or not isinstance(cursor, str) or not cursor:
        return None
    try:
        observed = datetime.fromtimestamp(int(entry['__REALTIME_TIMESTAMP']) / 1000000, timezone.utc)
    except (ValueError, TypeError, OverflowError, OSError, KeyError):
        return None
    if not 0 <= (datetime.now(timezone.utc) - observed).total_seconds() < 3 * 86400:
        return None

    # The journal may contain arbitrary input. Only recognize known messages and extract typed fields.
    logged_failure = re.fullmatch(r'Sync ([0-9a-f-]{36}): failed na (\d{1,7}) ms bij ([a-z_]+) (\{[^\n]{0,2048}\})', message)
    http_status = None
    if logged_failure:
        try:
            details = json.loads(logged_failure[4])
            if not isinstance(details, dict):
                return None
            run_id = str(uuid.UUID(logged_failure[1], version=4))
        except (ValueError, TypeError):
            return None
        diagnostic = public_diagnostic({**details, 'step': logged_failure[3], 'durationMs': int(logged_failure[2]),
            'reason': details.get('readerError') or details.get('configurationError') or details.get('networkError') or details.get('type')})
        http_status = details.get('httpStatus')
    else:
        # Old reader failures had no run ID. A cursor produces a stable ID across retries.
        run_id = str(uuid.UUID(bytes=hashlib.sha256(cursor.encode()).digest()[:16], version=4))
        step, details = 'journal_sync', {}
        if message.startswith('Fout: '):
            details = failure_fields(subprocess.CalledProcessError(1, 'reader', stderr=message))
            if details.get('sqlState') or details.get('errno') in (101, 110, 111, 113):
                step = 'hfsql_export'
            if not any(key in details for key in ('sqlState', 'errno', 'readerError')):
                return None
        elif message.startswith(('Cohvera weigert de import (HTTP ', 'Portal-configuratie geweigerd (HTTP ',
                                  'Portal weigert het ophalen van sync-aanvragen (HTTP ')):
            match = re.search(r'\(HTTP (\d{3})\)', message)
            http_status = int(match[1]) if match else None
            step = ('portal_upload' if message.startswith('Cohvera weigert') else
                    'portal_configuration' if message.startswith('Portal-configuratie') else 'command_poll')
        elif 'Failed with result \'timeout\'' in message and entry.get('UNIT') in JOURNAL_UNITS:
            details['type'] = 'TimeoutExpired'
        elif message.startswith('Sync-aanvragen niet bereikbaar'):
            step = 'command_poll'
        else:
            return None
        diagnostic = public_diagnostic({**details, 'step': step, 'reason': details.get('readerError') or details.get('type')})

    if not diagnostic:
        return None
    diagnostic['journalUnit'] = unit
    event = {'runId': run_id, 'phase': 'journal_error', 'observedAt': observed.isoformat(),
             'readerVersion': READER_VERSION, 'diagnostic': diagnostic}
    if type(http_status) is int and 100 <= http_status <= 599:
        event['httpStatus'] = http_status
    return event


def sync_journal(config):
    if not isinstance(config, dict):
        raise ValueError('Ongeldige Portal-uploadconfiguratie.')
    validate(config)
    cursor_file = ROOT / 'data' / 'journal-cursor.json'
    with (ROOT / 'data' / 'journal-sync.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        try:
            cursor = json.loads(cursor_file.read_text()).get('cursor')
        except (OSError, ValueError, AttributeError):
            cursor = None
        args = ['journalctl', '--since=48 hours ago', '--lines=5000', '--output=json', '--no-pager']
        for unit in JOURNAL_UNITS:
            args.extend(['--unit', unit])
        if isinstance(cursor, str) and cursor:
            args.append('--after-cursor=' + cursor)
        result = subprocess.run(args, check=False, capture_output=True, text=True, errors='replace', timeout=10)
        if result.returncode and isinstance(cursor, str) and cursor:
            # Rotation can remove the saved cursor. Re-read the bounded window and deduplicate in Portal.
            result = subprocess.run(args[:-1], check=False, capture_output=True, text=True, errors='replace', timeout=10)
        if result.returncode:
            raise RuntimeError('Het Qbox-journaal kon niet worden gelezen.')
        events = []
        latest_cursor = None
        for line in result.stdout.splitlines():
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if not isinstance(entry, dict) or not isinstance(entry.get('__CURSOR'), str):
                continue
            latest_cursor = entry['__CURSOR']
            if event := journal_event(entry):
                events.append(event)
                if len(events) == 100:
                    break
        if events:
            post_status_events(config, events)
        if latest_cursor:
            save(cursor_file, {'cursor': latest_cursor})
        return len(events)


def portal_error_detail(error, config):
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
    return detail


def upload(config, payload, run_id=None):
    endpoint = validate(config)
    if len(payload) > 20 * 1024 * 1024:
        raise ValueError('Export groter dan 20 MB; niet verstuurd.')
    data = json.loads(payload)
    if (data.get('source') != 'plenion_reader_projects' or data.get('schema_version') not in (2, 3)
            or data.get('truncated') is not False):
        raise ValueError('Geen volledige readerexport v2/v3; niet verstuurd.')
    request = urllib.request.Request(endpoint, data=payload, method='POST', headers={
        'Content-Type':'application/json', 'X-Qbox-Key':config['api_key']})
    if run_id:
        request.add_header(RUN_HEADER, run_id)
    opener = portal_opener(config)
    try:
        with opener.open(request, timeout=90) as response:
            result = json.loads(response.read(65537))
    except urllib.error.HTTPError as error:
        detail = portal_error_detail(error, config)
        raise UploadRejected(f'Cohvera weigert de import (HTTP {error.code}). ' +
                           (detail or 'Geen detail ontvangen; controleer de API-log.'), error.code) from None
    except (urllib.error.URLError, TimeoutError, ssl.SSLError) as error:
        raise PortalTransportError('Portal-upload', error) from None
    if not isinstance(result, dict) or result.get('accepted') is not True:
        raise RuntimeError('Geen geldige importbevestiging van Cohvera.')
    return result


def fetch_reader_options(config, applied_revision=None, diagnostic=None):
    if diagnostic:
        diagnostic.step('hfsql_schema')
    schema_path = ROOT / 'data' / 'hfsql-schema.json'
    # Query column metadata only; no extra project contents are read here.
    if not schema_path.exists() or datetime.now().timestamp() - schema_path.stat().st_mtime > 86400:
        run_reader('schema', 60)
    schema = json.loads(schema_path.read_text())
    schema['columns'] = [normalize_column_name(c) for c in schema['columns']]
    if len(set(schema['columns'])) != len(schema['columns']):
        raise ValueError('Dubbele PROJ-kolommetadata.')
    statuses_path = ROOT / 'data' / 'project-statuses.json'
    # Status labels can change in Plenion without changing the status ID or project schema.
    if diagnostic:
        diagnostic.step('hfsql_statuses', columnCount=len(schema['columns']))
    run_reader('statuses', 60)
    statuses = [{**({'id': str(s['status_id']).strip()} if s.get('status_id') else {}), 'code': str(s.get('code') or '').strip(), 'label': str(s.get('label') or '').replace('\x00', '').strip()}
                for s in json.loads(statuses_path.read_text())['statuses']
                if re.fullmatch(r'[A-Za-z0-9_-]{1,30}', str(s.get('code') or '').strip())]
    body = {'columns': schema['columns'], 'readerVersion': READER_VERSION, 'statuses': statuses}
    if applied_revision is not None:
        body['appliedRevision'] = applied_revision
    endpoint = validate(config).removesuffix(ENDPOINT) + '/api/integrations/qbox/reader-config'
    request = urllib.request.Request(endpoint, data=json.dumps(body).encode(), method='POST',
        headers={'Content-Type': 'application/json', 'X-Qbox-Key': config['api_key']})
    if diagnostic:
        diagnostic.step('portal_configuration', statusCount=len(statuses))
        request.add_header(RUN_HEADER, diagnostic.report['runId'])
    print('Portal-configuratie ophalen...', flush=True)
    try:
        with portal_opener(config).open(request, timeout=10) as response:
            options = json.loads(response.read(65536))
    except urllib.error.HTTPError as error:
        detail = portal_error_detail(error, config)
        hints = {404: 'De reader-config-route ontbreekt. Werk de Portal-API bij.',
                 401: 'Controleer de bedrijfs-API-key.',
                 403: 'Controleer of het bedrijf actief is en toegang heeft.',
                 500: 'Controleer de Portal-API-log en of de database-migraties zijn uitgevoerd.'}
        raise UploadRejected(f'Portal-configuratie geweigerd (HTTP {error.code}). ' +
                             (detail + ' ' if detail else '') + hints.get(error.code, 'Controleer de configuratie in Middleware.'), error.code) from None
    except (urllib.error.URLError, TimeoutError, ssl.SSLError) as error:
        raise PortalTransportError('Portal-configuratie', error) from None
    except (ValueError, UnicodeDecodeError):
        raise RuntimeError('Portal-configuratie bevat geen geldige JSON. Controleer de Portal-API en reverse proxy.') from None
    if not isinstance(options, dict):
        raise RuntimeError('Portal-configuratie heeft een ongeldig formaat; verwacht een JSON-object.')
    if diagnostic:
        diagnostic.step('configuration_validation')
    from reader import portal_selection, project_filter
    _, _, chosen = portal_selection(options)
    selection = project_filter(options.get('projectFilter'))
    if selection:
        missing_codes = sorted(set(selection['statusCodes']) - {s['code'] for s in statuses})
        missing_ids = sorted(set(selection.get('statusIds', [])) - {s.get('id') for s in statuses})
        if missing_codes or missing_ids:
            if diagnostic:
                diagnostic.report['steps'][-1].update(missingStatusCodes=missing_codes, missingStatusIds=missing_ids)
            raise SyncConfigurationError('status_not_in_hfsql', 'Geselecteerde status ontbreekt in Plenion; controleer de statusselectie in Portal.')
    if type(options.get('revision')) is not int or options['revision'] < 0:
        raise ValueError('Ongeldige configuratieversie van Portal.')
    available = {c.lower() for c in schema['columns']}
    if any(c.lower() not in available for c in chosen):
        raise ValueError('Geselecteerde HFSQL-kolom ontbreekt; export gestopt.')
    if selection and selection.get('relationshipType'):
        column = 'idprospect' if selection['relationshipType'] == 'prospect' else 'klcleunik'
        if column not in available:
            raise ValueError('Prospect- of klantnummerveld ontbreekt in PROJ; export gestopt.')
    # Always carry available relationship numbers for the Projects overview filter.
    chosen = sorted(set(chosen) | {c for c in ('IDProsPect', 'KLCLEUNIK') if c.lower() in available})
    # Docker uses the connection-file owner, even when this process runs as root.
    path = ROOT / 'data' / 'reader-options.json'
    save(path, {'revision': options['revision'], 'selectedColumns': chosen, 'projectFilter': selection})
    owner = (ROOT / 'data' / 'connection.json').stat()
    if os.geteuid() == 0:
        os.chown(path, owner.st_uid, owner.st_gid)
    print(f'Portal-configuratie ontvangen: versie {options["revision"]}.', flush=True)
    return options


def sync(run_id=None):
    # A failed new read must never upload the previous file.
    with (ROOT / 'data' / 'upload.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('Een synchronisatie loopt al; deze poging overgeslagen.')
            return False
        run_id = run_id or str(uuid.uuid4())
        diagnostic = SyncDiagnostic(run_id)
        diagnostic.step('local_configuration')
        config = None
        stage = 'configuration'
        try:
            current = json.loads(CONFIG.read_text())
            validate(current)
            config = current
            report_status(config, run_id, 'started')
            applied_path = ROOT / 'data' / 'applied-reader-options.json'
            applied = json.loads(applied_path.read_text()).get('revision') if applied_path.exists() else None
            options = fetch_reader_options(config, applied, diagnostic)
            stage = 'read'
            diagnostic.step('hfsql_export', revision=options['revision'])
            run_reader('portal', 180)
            diagnostic.step('export_file')
            payload = (ROOT / 'data' / 'portal-projects.json').read_bytes()
            record_count = json.loads(payload).get('count')
            report_status(config, run_id, 'read_ok', record_count=record_count, diagnostic=diagnostic.telemetry())
            stage = 'upload'
            diagnostic.step('portal_upload', bytes=len(payload), recordCount=record_count if type(record_count) is int else None)
            result = upload(config, payload, run_id)
            diagnostic.step('portal_accepted')
            try:
                save(applied_path, {'revision': options['revision']})
            except OSError:
                print('Import gelukt, maar de toegepaste configuratieversie kon niet lokaal worden bewaard.', flush=True)
            diagnostic.finish()
            report_status(config, run_id, 'success', record_count=record_count, diagnostic=diagnostic.telemetry())
            print(f'Cohvera bevestigt {result.get("count", 0)} projecten en '
                  f'{result.get("documentCount", 0)} onderliggende records voor {result.get("companyCode", "onbekend")}.', flush=True)
        except Exception as error:
            diagnostic.finish(error)
            if config is not None:
                report_status(config, run_id, {'configuration':'configuration_failed','read':'read_failed','upload':'upload_failed'}[stage],
                              http_status=error.status if isinstance(error, UploadRejected) else None, diagnostic=diagnostic.telemetry())
            raise

        return True


def command_request(config, acknowledged_id=None):
    endpoint = validate(config).removesuffix(ENDPOINT) + '/api/integrations/qbox/reader-command'
    body = {'acknowledgedId': acknowledged_id} if acknowledged_id else {}
    request = urllib.request.Request(endpoint, data=json.dumps(body).encode(), method='POST',
        headers={'Content-Type': 'application/json', 'X-Qbox-Key': config['api_key']})
    try:
        with portal_opener(config).open(request, timeout=10) as response:
            result = json.loads(response.read(4096))
    except urllib.error.HTTPError as error:
        raise UploadRejected(f'Portal weigert het ophalen van sync-aanvragen (HTTP {error.code}).', error.code) from None
    except (urllib.error.URLError, TimeoutError, ssl.SSLError) as error:
        raise PortalTransportError('Sync-aanvragen', error) from None
    request_id = result.get('requestId') if isinstance(result, dict) else False
    if request_id is not None and (not isinstance(request_id, str) or not re.fullmatch(
            r'[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}', request_id, re.I)):
        raise ValueError('Ongeldige sync-aanvraag van Portal.')
    return request_id


def report_command_failure(config, error):
    # Keep a failed 30-second poll from flooding the log when Portal is offline.
    path = ROOT / 'data' / 'last-command-failure.json'
    now = time.time()
    try:
        if now - path.stat().st_mtime < 300:
            return
    except OSError:
        pass
    details = failure_fields(error)
    diagnostic = public_diagnostic({**details, 'step': 'command_poll',
                                    'reason': details.get('networkError') or details['type']})
    report_status(config, str(uuid.uuid4()), 'command_poll_failed',
                  http_status=details.get('httpStatus'), diagnostic=diagnostic)
    try:
        save(path, {'diagnostic': diagnostic, 'httpStatus': details.get('httpStatus')})
    except OSError:
        pass


def poll_command():
    config = json.loads(CONFIG.read_text())
    with (ROOT / 'data' / 'command.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        completed = ROOT / 'data' / 'completed-sync-request.json'
        acknowledged = json.loads(completed.read_text()).get('id') if completed.exists() else None
        try:
            request_id = command_request(config, acknowledged)
        except (OSError, RuntimeError, ValueError) as error:
            report_command_failure(config, error)
            raise
        try:
            (ROOT / 'data' / 'last-command-failure.json').unlink(missing_ok=True)
        except OSError:
            pass
        if not request_id:
            return
        # A failed manual attempt is acknowledged too; its failure is in the sync log.
        # If another sync holds the lock, leave the request pending for the next poll.
        try:
            attempted = sync(run_id=request_id)
        except Exception:
            save(completed, {'id': request_id})
            raise
        if attempted:
            save(completed, {'id': request_id})
            command_request(config, request_id)


def install_command_timer():
    if os.geteuid() != 0 or not re.fullmatch(r'/[A-Za-z0-9_./-]+', str(ROOT)):
        raise ValueError('Installatie vereist sudo en een geldige installatiemap.')
    validate(json.loads(CONFIG.read_text()))
    unit_dir = Path('/etc/systemd/system')
    (unit_dir / 'cohvera-reader-command.service').write_text(f"""[Unit]
Description=Cohvera sync-aanvragen controleren
Wants=network-online.target
After=network-online.target docker.service

[Service]
Type=oneshot
User=root
WorkingDirectory={ROOT}
ExecStart=/usr/bin/python3 {ROOT}/sync.py poll-command
UMask=0077
TimeoutStartSec=480
""")
    (unit_dir / 'cohvera-reader-command.timer').write_text("""[Unit]
Description=Cohvera sync-aanvragen elke 30 seconden controleren

[Timer]
OnBootSec=30
OnUnitInactiveSec=30
AccuracySec=1
Unit=cohvera-reader-command.service

[Install]
WantedBy=timers.target
""")
    subprocess.run(['systemctl', 'daemon-reload'], check=True)
    subprocess.run(['systemctl', 'enable', '--now', 'cohvera-reader-command.timer'], check=True)



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
    install_command_timer()
    print('Timer actief: elke 15 minuten. Sync-aanvragen vanuit Portal worden elke 30 seconden gecontroleerd.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['configure', 'run', 'debug', 'journal-sync', 'install-timer', 'install-command-timer', 'poll-command'])
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
    elif args.action == 'debug':
        for name in ('last-sync.json', 'last-sync-failure.json'):
            path = ROOT / 'data' / name
            print(f'{name}:')
            if path.is_file():
                print(path.read_text().strip())
            else:
                print('Nog geen diagnose opgeslagen.')
    elif args.action == 'journal-sync':
        try:
            config = json.loads(CONFIG.read_text())
            count = sync_journal(config)
        except urllib.error.HTTPError as error:
            raise RuntimeError(f'Portal weigert het Qbox-journaal (HTTP {error.code}).') from None
        except (OSError, ValueError, subprocess.SubprocessError):
            raise RuntimeError('Qbox-journaal niet doorgestuurd; controleer de verbinding met Portal en probeer opnieuw.') from None
        print(f'{count} Qbox-journaalmeldingen doorgestuurd.')
    elif args.action == 'poll-command':
        poll_command()
    elif args.action == 'install-command-timer':
        install_command_timer()
    elif args.action == 'install-timer':
        install_timer()
    else:
        try:
            sync()
        finally:
            try:
                config = json.loads(CONFIG.read_text())
                if count := sync_journal(config):
                    print(f'{count} Qbox-journaalmeldingen doorgestuurd.')
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
                print('Qbox-journaal niet doorgestuurd; de volgende poging probeert opnieuw.', flush=True)


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, SyncConfigurationError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
    except (OSError, ValueError, subprocess.SubprocessError):
        # Do not print raw native/network exceptions: they may contain credentials or response data.
        print('Synchronisatie/configuratie mislukt. Controleer de instellingen en de voorgaande readeruitvoer; vorige import blijft behouden.', file=sys.stderr)
        sys.exit(1)
