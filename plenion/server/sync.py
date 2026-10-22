"""Export and send project JSON to Portal. Credentials stay on the Q-box."""
import argparse
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
    if config.get('connect_ip'):
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect(),
                                             RoutedHTTPSHandler(config['connect_ip']))
    else:
        opener = urllib.request.build_opener(NoRedirect(), urllib.request.HTTPSHandler(context=ssl.create_default_context()))
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
        raise RuntimeError(f'Cohvera weigert de import (HTTP {error.code}). ' +
                           (detail or 'Geen detail ontvangen; controleer de API-log.')) from None
    except (urllib.error.URLError, TimeoutError):
        raise RuntimeError('Cohvera niet bereikbaar of TLS-controle mislukt. Vorige import blijft behouden.') from None
    if result.get('accepted') is not True:
        raise RuntimeError('Geen geldige importbevestiging van Cohvera.')
    return result


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
        subprocess.run(['/bin/sh', str(ROOT / 'plenion.sh'), 'portal'], check=True, timeout=180)
        payload = (ROOT / 'data' / 'portal-projects.json').read_bytes()
        result = upload(config, payload)
        print(f'Cohvera bevestigt {result.get("count", 0)} projecten en '
              f'{result.get("documentCount", 0)} onderliggende records voor {result.get("companyCode", "onbekend")}.', flush=True)


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
TimeoutStartSec=300
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
