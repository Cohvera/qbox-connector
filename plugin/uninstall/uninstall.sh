#!/bin/bash
set -eu
# Configuration/data are handled by LoxBerry. Do not restart the old connector silently.
systemctl disable --now cohvera-qbox-plugin.service || true
rm -f /etc/systemd/system/cohvera-qbox-plugin.service
rm -f /var/lib/cohvera-qbox-plugin-upgrade/settings.json /var/lib/cohvera-qbox-plugin-upgrade/state.json /var/lib/cohvera-qbox-plugin-upgrade/metadata.json
rmdir /var/lib/cohvera-qbox-plugin-upgrade 2>/dev/null || true
systemctl daemon-reload
echo '<INFO> Pluginservice verwijderd. De oude losse connector is niet automatisch herstart.'
exit 0
