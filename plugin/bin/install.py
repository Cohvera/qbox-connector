#!/usr/bin/python3
"""Root-only installation hook. No shell parsing or runtime sudo grants."""
import argparse
import json
import os
import pwd
import re
import shlex
import subprocess
import sys
from pathlib import Path
from plugin_core import DEFAULTS, validate, write, read

UNIT = 'cohvera-qbox-plugin.service'
LEGACY = 'cohvera-qbox.service'
BACKUP = Path('/var/lib/cohvera-qbox-plugin-upgrade')


def systemctl(*args, check=True):
    return subprocess.run(['/bin/systemctl', *args], check=check, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def legacy_settings(path):
    values = DEFAULTS.copy()
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('export '):
            line = line[7:]
        key, sep, raw = line.partition('=')
        key = key.strip()
        if not sep or key not in DEFAULTS:
            continue
        words = shlex.split(raw, comments=True)
        if len(words) > 1:
            raise ValueError('Ongeldige oude configuratie')
        values[key] = words[0] if words else ''
    values['enabled'] = False
    return validate(values)


def unit_text(binary, config, data):
    return f'''[Unit]
Description=Cohvera Q-box LoxBerry plugin
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User=loxberry
Group=loxberry
ExecStart=/usr/bin/python3 {binary}/worker.py --config {config} --data {data}
Restart=on-failure
RestartSec=10
TimeoutStopSec=120
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths={config} {data}
UMask=0077

[Install]
WantedBy=multi-user.target
'''


def main():
    p = argparse.ArgumentParser()
    for name in ('binary', 'config', 'data', 'cgi', 'templates'):
        p.add_argument('--' + name, required=True)
    args = p.parse_args()
    if os.geteuid() != 0 or sys.version_info < (3, 10):
        raise RuntimeError('Root en Python 3.10+ vereist')
    paths = vars(args)
    for value in paths.values():
        if not re.fullmatch(r'/[A-Za-z0-9_./-]+', value) or '..' in Path(value).parts:
            raise RuntimeError('Ongeldig installatiepad')
    account = pwd.getpwnam('loxberry')
    config, data = Path(args.config), Path(args.data)
    for directory in (config, data):
        directory.mkdir(parents=True, exist_ok=True)
        directory.chmod(0o700)
        os.chown(directory, account.pw_uid, account.pw_gid)
    settings_path = config / 'settings.json'
    legacy = Path('/etc/cohvera-qbox/qbox.env')
    legacy_active = systemctl('is-active', '--quiet', LEGACY, check=False).returncode == 0
    legacy_enabled = systemctl('is-enabled', '--quiet', LEGACY, check=False).returncode == 0
    if settings_path.exists():
        settings = json.loads(settings_path.read_text())
        validate(settings, settings)
        if legacy_active:
            raise RuntimeError('Er bestaan al plugininstellingen terwijl de oude service actief is. Stop de oude service eerst bewust om dubbele aanvoer te voorkomen.')
    elif read(BACKUP / 'metadata.json', {}).get('folder') == config.name and (BACKUP / 'settings.json').exists():
        settings = read(BACKUP / 'settings.json')
        validate(settings, settings)
        state = read(BACKUP / 'state.json')
        if state:
            write(data / 'state.json', state)
            os.chown(data / 'state.json', account.pw_uid, account.pw_gid)
        print('<INFO> Instellingen en synchronisatiestatus hersteld uit de upgradebackup.')
    elif legacy.exists():
        settings = legacy_settings(legacy)
        settings['enabled'] = legacy_active
        print('<INFO> Bestaande connectorinstellingen worden overgenomen; het oude env-bestand blijft behouden.')
    else:
        settings = DEFAULTS.copy()
    # Preserve previous configuration before an upgrade; backups stay outside the web root.
    if settings_path.exists():
        write(config / 'settings.previous.json', settings)
        os.chown(config / 'settings.previous.json', account.pw_uid, account.pw_gid)
    systemctl('stop', UNIT, check=False)
    write(settings_path, settings)
    os.chown(settings_path, account.pw_uid, account.pw_gid)
    entry = Path(args.cgi) / 'index.cgi'
    source = entry.read_text()
    for key, value in {'BIN':args.binary,'CONFIG':args.config,'DATA':args.data,'TEMPLATES':args.templates}.items():
        source = source.replace('@@' + key + '@@', value)
    entry.write_text(source)
    entry.chmod(0o755)
    unit_path = Path('/etc/systemd/system') / UNIT
    unit_path.write_text(unit_text(args.binary, args.config, args.data))
    unit_path.chmod(0o644)
    try:
        if legacy_active or legacy_enabled:
            systemctl('disable', '--now', LEGACY)
        systemctl('daemon-reload')
        systemctl('enable', '--now', UNIT)
        systemctl('is-active', '--quiet', UNIT)
    except subprocess.CalledProcessError:
        systemctl('disable', '--now', UNIT, check=False)
        if legacy_enabled:
            systemctl('enable', LEGACY, check=False)
        if legacy_active:
            systemctl('start', LEGACY, check=False)
        raise RuntimeError('Nieuwe service kon niet starten; herstel van de oude service is geprobeerd.') from None
    print('<OK> Cohvera Q-box geïnstalleerd. Open de plugin om te configureren of te controleren.')

if __name__ == '__main__':
    try:
        main()
    except Exception:
        # Installer logs must never print config values or keys.
        print('<ERROR> Installatie niet afgerond. Controleer Python 3.10+, systemd, pluginpaden en bestaande configuratie. Zie de installatiehandleiding.')
        sys.exit(2)
