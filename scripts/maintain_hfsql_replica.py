#!/usr/bin/env python3
"""Compare the production and replica HFSQL builds and optionally upgrade the replica."""

import argparse
import fcntl
import json
import re
import subprocess
import time
from pathlib import Path


FULL_VERSION = re.compile(r'[0-9]{1,5}(?:\.[0-9]{1,5}){3}')
PACKAGE_VERSION = re.compile(r'[0-9][a-zA-Z0-9.+:~\-]{0,79}')
IMAGE_DIGEST = re.compile(r'[a-z0-9][a-z0-9._:/-]*@sha256:[0-9a-f]{64}')


def command(config, key):
    args = config.get(key)
    if not isinstance(args, list) or not args or not all(isinstance(arg, str) and arg for arg in args):
        raise ValueError(f'{key} moet een lijst met een commando en argumenten zijn.')
    return args


def run(args, label, timeout):
    try:
        result = subprocess.run(args, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                check=False, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f'{label}: geen resultaat binnen de ingestelde tijd.') from None
    except (OSError, UnicodeError):
        raise RuntimeError(f'{label}: commando kon niet worden uitgevoerd of gelezen.') from None
    if result.returncode:
        raise RuntimeError(f'{label}: commando mislukt (exitcode {result.returncode}).')
    return result.stdout


def full_version(value):
    if not isinstance(value, str) or not FULL_VERSION.fullmatch(value):
        raise ValueError('Een volledige HFSQL-serverversie is vereist, bijvoorbeeld 28.0.123.4.')
    return tuple(int(part) for part in value.split('.'))


def probe(args, label, timeout=100):
    output = run(args, label, timeout).strip().splitlines()
    try:
        info = json.loads(output[-1])
        if not isinstance(info, dict):
            raise TypeError
        if 'odbcDbmsVersion' in info or 'driverVersion' in info:
            raise RuntimeError(f'{label}: ODBC-versiemetadata is geen bevestigde HFSQL-engineversie.')
        package = info.get('packageVersion')
        if 'serverVersion' not in info and isinstance(package, str) and PACKAGE_VERSION.fullmatch(package):
            raise RuntimeError(f'{label}: HFSQL-pakket {package}. Koppel dit pakket aan de engineversie uit Control Center.')
        raw_version = info['serverVersion']
    except (IndexError, KeyError, TypeError, json.JSONDecodeError):
        raise RuntimeError(f'{label}: geen geldig versierapport ontvangen.') from None
    try:
        version = full_version(raw_version)
    except ValueError as error:
        if isinstance(raw_version, str) and re.fullmatch(r'[0-9][0-9.]{0,47}', raw_version):
            raise RuntimeError(f'{label}: HFSQL {raw_version}. {error}') from None
        raise RuntimeError(f'{label}: {error}') from None
    return version


def maintain(config, apply=False):
    production_probe = command(config, 'productionProbe')
    replica_probe = command(config, 'replicaProbe')
    if production_probe == replica_probe:
        raise ValueError('Configureer afzonderlijke probes voor productie en de replica.')
    versions = {}
    for label, args in (('Productie', production_probe), ('Replica', replica_probe)):
        try:
            versions[label] = probe(args, label)
        except RuntimeError as error:
            print(error, flush=True)
        else:
            print(f'{label}: HFSQL {".".join(map(str, versions[label]))}.', flush=True)
    if len(versions) != 2:
        return 1
    production, replica = versions['Productie'], versions['Replica']
    target = '.'.join(map(str, production))
    if production == replica:
        print('De HFSQL-serverversies komen overeen.')
        return 0
    if replica > production:
        raise RuntimeError('De replica is nieuwer dan productie. Controleer de compatibiliteit voordat je ingrijpt.')
    if not apply:
        print('De replica loopt achter. Gebruik --apply met de ingestelde updateprocedure om haar bij te werken.')
        return 2

    images = config.get('images')
    image = images.get(target) if isinstance(images, dict) else None
    if not isinstance(image, str) or not IMAGE_DIGEST.fullmatch(image):
        raise ValueError(f'Voor HFSQL {target} ontbreekt een replica-image met een vaste SHA-256-digest.')
    update = command(config, 'replicaUpdate')
    if update.count('{image}') != 1:
        raise ValueError('replicaUpdate moet {image} één keer als apart argument bevatten.')
    run([image if arg == '{image}' else arg for arg in update], 'Replica-update', timeout=900)

    deadline = time.monotonic() + 180
    while (remaining := deadline - time.monotonic()) > 0:
        try:
            if probe(replica_probe, 'Replica', timeout=min(100, remaining)) == production:
                print(f'Replica meldt HFSQL {target}.')
                return 0
        except RuntimeError:
            pass  # HFSQL can reject connections while starting the replacement.
        time.sleep(min(5, max(0, deadline - time.monotonic())))
    raise RuntimeError('De replica bevestigde de doelversie niet. Controleer de update- en replicatielogs.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--apply', action='store_true', help='Voer de geconfigureerde replica-update uit.')
    args = parser.parse_args()
    try:
        with args.config.open() as file:
            if args.apply:
                try:
                    fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise RuntimeError('Er loopt al een update voor deze configuratie.') from None
            config = json.load(file)
            if not isinstance(config, dict):
                raise ValueError('Ongeldige versiebeheerconfiguratie.')
            return maintain(config, args.apply)
    except (OSError, ValueError, RuntimeError) as error:
        parser.exit(1, f'HFSQL-versiebeheer: {error}\n')


if __name__ == '__main__':
    raise SystemExit(main())
