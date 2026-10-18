"""Interactive local setup. Never passes the database password on the command line."""
import getpass
import argparse
import codecs
import json
import os
from pathlib import Path
import sys
from reader import save, attr


def configure(path=Path('data/connection.json'), database_only=False, encoding_only=False):
    previous = json.loads(path.read_text()) if path.exists() else {}

    def ask(label, key, default='', required=True):
        default = previous.get(key, default)
        value = input(f'{label}' + (f' [{default}]' if default else '') + ': ').strip() or default
        if required and not value:
            raise ValueError(f'{label} is verplicht.')
        return value

    if database_only or encoding_only:
        if not previous:
            raise ValueError('Voer eerst ./plenion.sh configure uit.')
        config = dict(previous)
        if encoding_only:
            chosen = ask('Tekstcodering (bijvoorbeeld utf-8 of cp1252)', 'encoding', 'utf-8')
            try:
                if not isinstance(codecs.decode(b'', chosen), str):
                    raise ValueError('Geen tekstcodering')
                config['encoding'] = codecs.lookup(chosen).name
            except Exception:
                raise ValueError('Onbekende of ongeschikte tekstcodering; configuratie niet gewijzigd.') from None
        else:
            config['database'] = ask('Exacte HFSQL-databasenaam', 'database')
    else:
        print('Gebruik je bestaande HFSQL-databaseaccount, niet je Windows- of Q-box-login.')
        print('Vul de Plenion/HFSQL-server van dit bedrijf in; dit is niet het adres van de Q-box.')
        config = {
            'host': ask('IP-adres of naam HFSQL-server van dit bedrijf', 'host'),
            'port': int(ask('Poort', 'port', '4900')),
            'database': ask('Databasenaam (nog onbekend? laat leeg)', 'database', required=False),
            'user': ask('HFSQL-gebruikersnaam', 'user'),
            'encoding': previous.get('encoding', 'utf-8'),
        }
        # Do not reuse a saved password implicitly for a different destination/account.
        same_account = all(config.get(k) == previous.get(k) for k in ('host', 'port', 'user'))
        can_keep = same_account and 'password' in previous
        prompt = 'HFSQL-wachtwoord (onzichtbaar'
        prompt += '; Enter = behouden, /leeg = leeg wachtwoord' if can_keep else '; /leeg = leeg wachtwoord'
        entered = getpass.getpass(prompt + '): ')
        if entered == '/leeg':
            config['password'] = ''
        elif entered:
            config['password'] = entered
        elif can_keep:
            config['password'] = previous['password']
        else:
            raise ValueError('Geen wachtwoord ingevuld. Gebruik /leeg alleen als je account echt geen wachtwoord heeft.')
    if not 1 <= config['port'] <= 65535:
        raise ValueError('Ongeldige poort.')
    for value in config.values():
        attr(value)
    os.umask(0o077)
    path.parent.mkdir(exist_ok=True, mode=0o700)
    save(path, config)
    print('Configuratie bewaard; het wachtwoord wordt niet getoond.')
    print('Volgende stap: sudo ./plenion.sh ' + ('test' if config['database'] else 'databases'))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--database-only', action='store_true')
    mode.add_argument('--encoding-only', action='store_true')
    args = parser.parse_args()
    try:
        configure(database_only=args.database_only, encoding_only=args.encoding_only)
    except (OSError, ValueError) as exc:
        print(f'Fout: {exc}', file=sys.stderr)
        sys.exit(1)
