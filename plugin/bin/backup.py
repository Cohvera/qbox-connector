#!/usr/bin/python3
"""Preserve settings/state outside directories that LoxBerry purges during upgrades."""
import os
import subprocess
import sys
from pathlib import Path
from plugin_core import read, validate, write

BACKUP = Path('/var/lib/cohvera-qbox-plugin-upgrade')


def backup(home, folder, target=BACKUP):
    config = Path(home) / 'config/plugins' / folder / 'settings.json'
    if not config.exists():
        return False
    settings = read(config)
    validate(settings, settings)
    target.mkdir(parents=True, exist_ok=True)
    target.chmod(0o700)
    write(target / 'settings.json', settings)
    write(target / 'metadata.json', {'folder': folder})
    subprocess.run(['/bin/systemctl', 'stop', 'cohvera-qbox-plugin.service'], check=True)
    state = read(Path(home) / 'data/plugins' / folder / 'state.json')
    if state:
        write(target / 'state.json', state)
    elif (target / 'state.json').exists():
        (target / 'state.json').unlink()
    return True

if __name__ == '__main__':
    try:
        if os.geteuid() != 0: raise RuntimeError()
        backup(sys.argv[1], sys.argv[2])
    except Exception:
        print('<ERROR> Upgradebackup mislukt; installatie gestopt. Controleer de bestaande service voordat je opnieuw probeert.')
        sys.exit(2)
