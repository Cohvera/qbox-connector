#!/bin/sh
set -eu
cd "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
umask 077
IMAGE=cohvera-plenion-reader:local
ACTION=${1:-help}
case "$ACTION" in
  configure|database)
    mkdir -p data
    chmod 700 data
    if [ "$ACTION" = database ]; then
      python3 configure.py --database-only
    else
      python3 configure.py
    fi
    exit
    ;;
  install)
    if ! command -v docker >/dev/null 2>&1; then
      echo 'Het docker-commando ontbreekt in PATH. docker.io kan wel geïnstalleerd zijn.'
      echo 'Op Debian 13 is docker-cli een apart pakket. Voer uit:'
      echo 'sudo apt-get update'
      echo 'sudo apt-get install -y docker.io qemu-user-static binfmt-support'
      echo 'Op Debian 13 ook: sudo apt-get install -y docker-cli'
      echo 'sudo systemctl enable --now docker'
      echo 'sudo update-binfmts --enable qemu-x86_64'
      echo 'Daarna: sudo ./plenion.sh install'
      exit 1
    fi
    docker info >/dev/null
    if ! docker run --rm --platform linux/amd64 debian:bookworm-slim /bin/true; then
      echo 'x86-64-emulatie werkt nog niet. Op de Q-box:' >&2
      echo 'sudo apt-get install -y qemu-user-static binfmt-support' >&2
      echo 'sudo update-binfmts --enable qemu-x86_64' >&2
      exit 1
    fi
    docker build --platform linux/amd64 -t "$IMAGE" .
    docker run --rm --platform linux/amd64 --network none --read-only --tmpfs /tmp:rw,size=64m --cap-drop ALL --security-opt no-new-privileges "$IMAGE" --check
    echo 'Installatie klaar. Volgende stap als loxberry: ./plenion.sh configure'
    exit
    ;;
  check|test|read|databases|tables) ;;
  *) echo 'Gebruik: ./plenion.sh install | configure | database | databases | tables | check | test | read'; exit 0 ;;
esac
command -v docker >/dev/null 2>&1 || { echo 'Voer eerst ./plenion.sh install uit.' >&2; exit 1; }
if [ "$ACTION" = check ]; then
  exec docker run --rm --platform linux/amd64 --network none --read-only --tmpfs /tmp:rw,size=64m --cap-drop ALL --security-opt no-new-privileges "$IMAGE" --check
fi
[ -f data/connection.json ] || { echo 'Voer eerst ./plenion.sh configure uit als loxberry.' >&2; exit 1; }
# Run as the owner of the configuration, even when Docker needs sudo.
OWNER=$(stat -c '%u:%g' data/connection.json 2>/dev/null || stat -f '%u:%g' data/connection.json)
if [ "$ACTION" = test ]; then
  set -- --limit 5 --output /data/projects-test.json
elif [ "$ACTION" = databases ] || [ "$ACTION" = tables ]; then
  set -- --mode "$ACTION"
else
  set --
fi
# Host networking lets Linux reach the same LAN/VPN routes as the Q-box itself.
exec docker run --rm --platform linux/amd64 --network host \
  --user "$OWNER" --read-only --tmpfs /tmp:rw,size=64m \
  --cap-drop ALL --security-opt no-new-privileges --memory 512m --pids-limit 64 \
  -v "$PWD/data:/data" -v "$PWD/data/connection.json:/data/connection.json:ro" \
  "$IMAGE" "$@"
