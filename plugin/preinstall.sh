#!/bin/bash
set -eu
if ! /usr/bin/python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)'; then
  echo '<ERROR> Python 3.10 of nieuwer vereist. De huidige connector is niet gewijzigd.'
  exit 2
fi
if ! command -v systemctl >/dev/null; then
  echo '<ERROR> Deze plugin vereist LoxBerry met systemd.'
  exit 2
fi
exit 0
