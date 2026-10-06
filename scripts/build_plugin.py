#!/usr/bin/env python3
"""Build a clean LoxBerry ZIP from an explicit allowlist, never from the working tree."""
import configparser
import hashlib
import stat
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = ['plugin.cfg', 'preinstall.sh', 'preroot.sh', 'postroot.sh', 'uninstall/uninstall.sh',
         'webfrontend/htmlauth/index.cgi', 'templates/index.html', 'templates/app.js',
         'templates/style.css', 'bin/plugin_core.py', 'bin/worker.py', 'bin/install.py', 'bin/backup.py', 'icons/icon.svg']


def build():
    cfg = configparser.ConfigParser()
    cfg.read(ROOT / 'plugin/plugin.cfg')
    version = cfg['PLUGIN']['VERSION']
    if not all(c.isdigit() or c == '.' for c in version):
        raise ValueError('Ongeldige versie')
    if cfg['AUTHOR']['EMAIL'].endswith('.invalid'):
        raise ValueError('Vul vóór de eerste release een vast auteuradres in')
    out = ROOT / 'dist'
    out.mkdir(exist_ok=True)
    target = out / f'cohvera-qbox-{version}.zip'
    with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name in FILES + ['bin/qbox_sync.py']:
            source = ROOT / 'qbox_sync.py' if name == 'bin/qbox_sync.py' else ROOT / 'plugin' / name
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.create_system = 3
            mode = 0o755 if name.endswith(('.cgi', '.sh')) else 0o644
            info.external_attr = (stat.S_IFREG | mode) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, source.read_bytes())
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    target.with_suffix('.zip.sha256').write_text(f'{digest}  {target.name}\n')
    print(target)
    return target

if __name__ == '__main__':
    build()
