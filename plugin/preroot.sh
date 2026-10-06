#!/bin/bash
set -eu
/usr/bin/python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 2)'
plugin_home=${LBHOMEDIR:-${5:?}}
plugin_folder=${3:?}
plugin_temp=${6:?}
exec /usr/bin/python3 "$plugin_temp/bin/backup.py" "$plugin_home" "$plugin_folder"
