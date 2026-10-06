#!/usr/bin/python3
"""Runs only behind LoxBerry's authenticated webfrontend; no listening socket."""
import json
import os
import sys
from pathlib import Path

# Replaced by postroot after LoxBerry has selected the actual plugin directory.
sys.path.insert(0, '@@BIN@@')
from plugin_core import Store, InputError
STORE = Store('@@CONFIG@@', '@@DATA@@')
TEMPLATES = Path('@@TEMPLATES@@')


def respond(body, status='200 OK', content_type='application/json; charset=utf-8'):
    print('Status: ' + status)
    print('Content-Type: ' + content_type)
    print('Cache-Control: no-store')
    print('X-Content-Type-Options: nosniff')
    print("Content-Security-Policy: default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; frame-ancestors 'self'; base-uri 'none'; form-action 'self'")
    print('')
    print(json.dumps(body) if content_type.startswith('application/json') else body)


def main():
    if not os.environ.get('REMOTE_USER'):
        respond({'message': 'Meld je aan in LoxBerry.'}, '403 Forbidden')
        return
    method = os.environ.get('REQUEST_METHOD', 'GET')
    query = os.environ.get('QUERY_STRING', '')
    if method == 'GET':
        if query == 'status':
            respond(STORE.public())
        elif query in ('style', 'script'):
            name, kind = ('style.css', 'text/css') if query == 'style' else ('app.js', 'application/javascript')
            respond((TEMPLATES / name).read_text(), content_type=kind + '; charset=utf-8')
        elif not query:
            respond((TEMPLATES / 'index.html').read_text(), content_type='text/html; charset=utf-8')
        else:
            respond({'message': 'Niet gevonden.'}, '404 Not Found')
        return
    if method != 'POST':
        respond({'message': 'Niet toegestaan.'}, '405 Method Not Allowed')
        return
    # Authenticated user plus per-installation CSRF token. JSON disallows simple cross-site forms.
    if os.environ.get('CONTENT_TYPE', '').split(';')[0] != 'application/json' or os.environ.get('HTTP_SEC_FETCH_SITE') == 'cross-site':
        respond({'message': 'Ongeldige aanvraag.'}, '403 Forbidden')
        return
    try:
        size = int(os.environ.get('CONTENT_LENGTH', '0'))
        if not 0 < size <= 16384:
            raise InputError('Aanvraag te groot of leeg.')
        body = json.loads(sys.stdin.read(size))
        respond(STORE.action(body))
    except (InputError, ValueError) as error:
        respond({'message': str(error) if isinstance(error, InputError) else 'Ongeldige aanvraag.'}, '400 Bad Request')


try:
    main()
except Exception:
    respond({'message': 'Pluginconfiguratie niet beschikbaar. Controleer de installatie en bestandsrechten.'}, '500 Internal Server Error')
