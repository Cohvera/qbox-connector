#!/usr/bin/python3
"""Single unprivileged worker; no commands supplied by the web interface."""
import argparse
import logging
import os
import signal
import threading
import time
from pathlib import Path
import qbox_sync as sync
from plugin_core import DEFAULTS, Store, locked, read, write


def run(store):
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    status = read(store.data / 'status.json', {})
    previous = read(store.data / 'state.json', {})
    if not status.get('last_success') and previous.get('sent_at'):
        status.update(last_success=previous['sent_at'], project_count=previous.get('count'), company=previous.get('company'))
    status.update(busy=False, heartbeat=time.time(), state='ready')
    guard = threading.Lock()

    def record(**values):
        with guard:
            status.update(values)
            status['heartbeat'] = values.get('heartbeat', time.time())
            write(store.data / 'status.json', status)

    def heartbeat():
        while not stop.wait(10):
            record()
    record()
    threading.Thread(target=heartbeat, daemon=True).start()
    next_run, revision, failures = 0, None, 0
    while not stop.is_set():
        settings = store.settings()
        if settings.get('revision') != revision:
            revision = settings.get('revision')
            next_run, failures = 0, 0
        with locked(store.config):
            job = read(store.data / 'request.json')
            if job:
                (store.data / 'request.json').unlink(missing_ok=True)
                if time.time() - job.get('time', 0) > 300:
                    job = None
                else:
                    record(busy=True)
        if not job and (not settings.get('enabled') or time.time() < next_run):
            record(state='paused' if not settings.get('enabled') else 'waiting', next_run=next_run if settings.get('enabled') else None)
            stop.wait(2)
            continue
        action = job['action'] if job else 'automatic'
        record(busy=True, state='testing' if action == 'test' else 'syncing', last_attempt=time.time(), message='', action=action)
        try:
            for k in DEFAULTS:
                if k.startswith('QBOX_'):
                    os.environ[k] = str(settings.get(k, DEFAULTS[k]))
            os.environ['QBOX_STATE_FILE'] = str(store.data / 'state.json')
            config = sync.settings()
            if action == 'test':
                sync.sync_once(config, dry_run=True)
                tv = sync.sync_tv(config, dry_run=True)
                health = sync.read_json(config['destination'] + '/api/health')
                if not isinstance(health, dict):
                    raise sync.SyncError('Cohvera-healthcontrole geeft geen geldig antwoord')
                record(last_test=time.time(), message='Bron gecontroleerd en Cohvera bereikbaar. De importsleutel wordt pas bij synchroniseren door Cohvera gecontroleerd.')
            else:
                result = sync.sync_once(config, force=action == 'sync')
                if result == 'accepted':
                    state = read(store.data / 'state.json', {})
                    record(last_success=time.time(), project_count=state.get('count'), company=state.get('company'))
                tv = sync.sync_tv(config)
                record(message='Synchronisatie afgerond.' if result == 'accepted' else 'Bron ongewijzigd; laatste bevestiging blijft geldig.')
            missing = []
            if tv is not None:
                if not tv['fleet']: missing.append('voertuigen')
                if config.get('nas_url') and not tv['nas']: missing.append('NAS')
            failures = 0
            record(last_error=None, tv_warning=('Geen beschikbare bron voor: ' + ', '.join(missing)) if missing else None)
        except (sync.SyncError, OSError, ValueError, TypeError) as error:
            failures += 1
            # Never expose URLs, remote response bodies, credentials or project data.
            record(last_error=str(error) if isinstance(error, sync.SyncError) else 'Lokale configuratie of opslag ongeldig. Controleer bestandsrechten en instellingen.', message='Geen volledige synchronisatie bevestigd.')
        finally:
            next_run = time.time() + min(900, int(settings.get('QBOX_POLL_SECONDS', '60')) * 2 ** min(failures, 4))
            record(busy=False, state='error' if failures else 'ready', next_run=next_run if settings.get('enabled') else None)
    record(state='stopped', busy=False, heartbeat=0)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    parser.add_argument('--data', required=True)
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)
    try:
        with locked(args.data, 'worker.lock', blocking=False):
            run(Store(args.config, args.data))
    except BlockingIOError:
        return 0

if __name__ == '__main__':
    main()
