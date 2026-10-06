#!/bin/bash
set -eu
# LoxBerry supplies its home and selected plugin folder, including any numeric suffix.
plugin_home=${LBHOMEDIR:-${5:?}}
plugin_folder=${3:?}
exec /usr/bin/python3 "$plugin_home/bin/plugins/$plugin_folder/install.py" \
  --binary "$plugin_home/bin/plugins/$plugin_folder" \
  --config "$plugin_home/config/plugins/$plugin_folder" \
  --data "$plugin_home/data/plugins/$plugin_folder" \
  --cgi "$plugin_home/webfrontend/htmlauth/plugins/$plugin_folder" \
  --templates "$plugin_home/templates/plugins/$plugin_folder"
