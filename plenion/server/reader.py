"""Read Plenion PROJ through the official HFSQL C/S driver. No writes or uploads."""
import argparse
import ctypes as C
import json
import os
import re
from pathlib import Path
import subprocess
import socket
import sys
import tempfile
from datetime import datetime, timezone
from collections import Counter

DRIVER = '/driver/wd310hfo64.so'
FIELDS = ('source_id', 'number', 'customer_id', 'name', 'description', 'created_at')
QUERY = 'SELECT IDProj,projnr,KLCLEUNIK,Naam,oms,CDATUM FROM PROJ ORDER BY IDProj'


def status_query(column):
    # Accept one simple identifier, never an SQL expression or filter.
    if not isinstance(column, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,127}', column):
        raise ValueError('Geef één geldige statuskolom op; bekijk eerst ./plenion.sh columns.')
    return f'SELECT IDProj,projnr,PROJ.{column} FROM PROJ ORDER BY IDProj'


def describe_columns(lib, stmt, encoding):
    count = C.c_short()
    check(lib, lib.SQLNumResultCols(stmt, C.byref(count)), 'Kolommen tellen', 3, stmt)
    if not 1 <= count.value <= 4096:
        raise ValueError('Onverwacht aantal kolommen in PROJ.')
    describe = lib.SQLDescribeCol
    describe.argtypes = [C.c_void_p, C.c_ushort, C.c_void_p, C.c_short,
                         C.POINTER(C.c_short), C.POINTER(C.c_short), C.POINTER(C.c_size_t),
                         C.POINTER(C.c_short), C.POINTER(C.c_short)]
    describe.restype = C.c_short
    columns = []
    for index in range(1, count.value + 1):
        name = C.create_string_buffer(1024)
        length, kind, digits, nullable = C.c_short(), C.c_short(), C.c_short(), C.c_short()
        size = C.c_size_t()
        code = describe(stmt, index, name, len(name), C.byref(length), C.byref(kind),
                        C.byref(size), C.byref(digits), C.byref(nullable))
        check(lib, code, 'Kolomnaam lezen', 3, stmt)
        if code == 1 or not 0 < length.value < len(name):
            raise ValueError('Onvolledige kolomnaam; geen veldnamen geraden.')
        columns.append({'name': name.raw[:length.value].decode(encoding),
                        'sql_type': kind.value, 'size': size.value})
    return columns


def audit_status(rows, export, status_column, truncated=False):
    """Compare record identities; statuses come from the current database read."""
    if truncated:
        raise ValueError('Auditbron afgekapt; volledigheid kan niet worden vastgesteld.')
    if (not isinstance(export, dict) or export.get('source') != 'plenion_hfsql_server'
            or not isinstance(export.get('projects'), list)):
        raise ValueError('Geen geldige readerexport; voer eerst ./plenion.sh read uit.')
    projects = export['projects']
    if type(export.get('count')) is not int or export['count'] != len(projects):
        raise ValueError('Exportteller komt niet overeen met de opgeslagen records.')
    if not isinstance(export.get('truncated'), bool):
        raise ValueError('De export bevat geen geldige afkappingsindicator.')

    def index(records, label):
        result, duplicates = {}, 0
        for row in records:
            if not isinstance(row, dict) or not isinstance(row.get('source_id'), str) or not row['source_id'].strip():
                raise ValueError(f'{label} bevat een ontbrekende record-ID.')
            if row['source_id'] in result:
                duplicates += 1
            result[row['source_id']] = row
        return result, duplicates

    source, source_duplicates = index(rows, 'Database')
    saved, export_duplicates = index(projects, 'Export')
    if source_duplicates:
        raise ValueError('De auditbron bevat dubbele record-IDs; geen betrouwbare vergelijking mogelijk.')
    for row in rows:
        if 'status' not in row or (row['status'] is not None and not isinstance(row['status'], str)):
            raise ValueError('Een statuswaarde ontbreekt of is ongeldig.')
    source_ids, export_ids = set(source), set(saved)
    missing, extra = source_ids - export_ids, export_ids - source_ids
    mismatched = {key for key in source_ids & export_ids if source[key].get('number') != saved[key].get('number')}
    database_counts = Counter(row['status'] for row in rows)
    exported_counts = Counter(source[key]['status'] for key in source_ids & export_ids)
    statuses = [{'status': status, 'database_records': database_counts[status],
                 'export_records': exported_counts[status],
                 'missing_records': database_counts[status] - exported_counts[status]}
                for status in sorted(database_counts, key=lambda value: (value is not None, value or ''))]
    complete = not (missing or extra or mismatched or export_duplicates or export['truncated'])
    return {'checked_at': datetime.now(timezone.utc).isoformat(),
            'export_fetched_at': export.get('fetched_at'), 'status_column': status_column,
            'status_basis': 'current_database_by_record_id', 'all_versions': True,
            'complete': complete, 'database_records': len(rows), 'export_records': len(projects),
            'export_truncated': export['truncated'], 'duplicate_export_ids': export_duplicates,
            'missing_records': len(missing), 'extra_records': len(extra),
            'changed_project_numbers': len(mismatched),
            'missing_id_examples': sorted(missing)[:20], 'extra_id_examples': sorted(extra)[:20],
            'statuses': statuses}


def attr(value):
    value = str(value)
    if any(c in value for c in ';\r\n\0'):
        raise ValueError('De HFSQL-verbinding ondersteunt hier geen puntkomma, NUL of regelovergang in een verbindingswaarde.')
    return value  # HFSQL expects bare server/port values; reject attribute separators above.


def odbc(library):
    lib = C.CDLL(library)
    h = C.c_void_p
    specs = {
        'SQLAllocHandle': [C.c_short, h, C.POINTER(h)],
        'SQLSetEnvAttr': [h, C.c_int, h, C.c_int],
        'SQLSetConnectAttr': [h, C.c_int, h, C.c_int],
        'SQLDriverConnect': [h, h, C.c_char_p, C.c_short, h, C.c_short, C.POINTER(C.c_short), C.c_ushort],
        'SQLTables': [h, C.c_char_p, C.c_short, C.c_char_p, C.c_short, C.c_char_p, C.c_short, C.c_char_p, C.c_short],
        'SQLExecDirect': [h, C.c_char_p, C.c_int],
        'SQLFetch': [h],
        'SQLNumResultCols': [h, C.POINTER(C.c_short)],
        'SQLGetData': [h, C.c_ushort, C.c_short, h, C.c_ssize_t, C.POINTER(C.c_ssize_t)],
        'SQLGetDiagRec': [C.c_short, h, C.c_short, h, C.POINTER(C.c_int), h, C.c_short, C.POINTER(C.c_short)],
        'SQLFreeHandle': [C.c_short, h], 'SQLDisconnect': [h],
    }
    for name, args in specs.items():
        getattr(lib, name).argtypes = args
        getattr(lib, name).restype = C.c_short
    return lib


def check(lib, code, stage, kind=0, handle=None):
    if code in (0, 1):
        return
    state, native = C.create_string_buffer(6), C.c_int()
    message, size = C.create_string_buffer(2048), C.c_short()
    if handle:
        lib.SQLGetDiagRec(kind, handle, 1, state, C.byref(native), message, len(message), C.byref(size))
    # Driver messages can contain credentials. Expose only diagnostic codes.
    raise RuntimeError(f'{stage} mislukt (SQLSTATE {state.value.decode("ascii", "replace") or "onbekend"}, code {native.value}). '
                       'Controleer server, databasenaam, HFSQL-account en leesrechten op PROJ.')


def driver_check():
    lib = odbc(DRIVER)
    env = C.c_void_p()
    check(lib, lib.SQLAllocHandle(1, None, C.byref(env)), 'Driverinitialisatie')
    if env:
        lib.SQLFreeHandle(1, env)
    print('OK: officiële HFSQL-driver geladen en ODBC-omgeving aangemaakt.')


def read(config, limit, mode="projects", status_column=None):
    if mode not in ("projects", "databases", "tables", "columns", "audit"):
        raise ValueError("Onbekende leesmodus.")
    audit_query = status_query(status_column) if mode == 'audit' else None
    if mode != "databases" and not config.get("database", "").strip():
        raise ValueError("Kies eerst een database: ./plenion.sh database")
    host, port = config['host'], int(config.get('port', 4900))
    if not 1 <= port <= 65535:
        raise ValueError('Ongeldige serverpoort.')
    print('Stap 1: netwerkpoort controleren...', flush=True)
    # Give a useful network error before entering the proprietary driver.
    with socket.create_connection((host, port), timeout=5):
        pass
    print('OK: netwerkpoort bereikbaar.', flush=True)
    settings = [
        ('DRIVER', DRIVER), ('Server Name', host), ('Server Port', port),
        ('UID', config['user']), ('PWD', config['password'])]
    if config.get('database'):
        settings.append(('Database', config['database']))
    connection = ';'.join(f'{k}={attr(v)}' for k, v in settings) + ';'
    if len(connection.encode()) > 30000:
        raise ValueError('Verbindingsconfiguratie te lang.')
    print('Stap 2: ODBC-manager laden...', flush=True)
    lib = odbc('libiodbc.so.2')
    print('OK: ODBC-manager geladen.', flush=True)
    env, dbc, stmt = C.c_void_p(), C.c_void_p(), C.c_void_p()
    connected = False
    try:
        print('Stap 3: ODBC-verbinding voorbereiden...', flush=True)
        check(lib, lib.SQLAllocHandle(1, None, C.byref(env)), 'ODBC-omgeving')
        check(lib, lib.SQLSetEnvAttr(env, 200, C.c_void_p(3), 0), 'ODBC-versie', 1, env)
        check(lib, lib.SQLAllocHandle(2, env, C.byref(dbc)), 'Verbinding aanmaken', 1, env)
        # Pre-setting SQL_ATTR_ACCESS_MODE blocks SQLDriverConnect with the
        # observed HFSQL 28 / Linux driver 31 combination. This reader only
        # issues the fixed SELECT or catalog calls below. Enforce read-only
        # permissions on the database account, not through this optional hint.
        print('Stap 4: verbindingsinstellingen voorbereid.', flush=True)
        # Driver 31 stalls under emulation with SQL_ATTR_LOGIN_TIMEOUT.
        # The supervising Python process enforces the total timeout instead.
        out, size = C.create_string_buffer(2048), C.c_short()
        print('Stap 5: HFSQL-driver laden en aanmelden bij de server...', flush=True)
        check(lib, lib.SQLDriverConnect(dbc, None, connection.encode(), -3, out, len(out), C.byref(size), 0), 'Aanmelden bij HFSQL', 2, dbc)
        connected = True
        print('OK: aangemeld bij HFSQL.', flush=True)
        print('Stap 6: leesopdracht voorbereiden...', flush=True)
        check(lib, lib.SQLAllocHandle(3, dbc, C.byref(stmt)), 'Query aanmaken', 2, dbc)
        names = FIELDS
        if mode in ('projects', 'audit', 'columns'):
            print('Stap 7: projectquery uitvoeren...', flush=True)
            query = 'SELECT * FROM PROJ WHERE 1=0' if mode == 'columns' else (audit_query or QUERY)
            check(lib, lib.SQLExecDirect(stmt, query.encode(), -3), 'Projecten lezen', 3, stmt)
            if mode == 'columns':
                return describe_columns(lib, stmt, config.get('encoding', 'utf-8')), False
            if mode == 'audit':
                names = ('source_id', 'number', 'status')
        else:
            print('Stap 7: metadata opvragen...', flush=True)
            names = ('database', 'schema', 'table', 'type', 'remarks')
            if mode == 'databases':
                # Standard ODBC SQL_ALL_CATALOGS. Support depends on the driver/server.
                code = lib.SQLTables(stmt, b'%', -3, b'', 0, b'', 0, b'', 0)
            else:
                code = lib.SQLTables(stmt, None, 0, None, 0, b'%', -3, b'TABLE,VIEW', -3)
            check(lib, code, 'Databaseoverzicht (mogelijk niet ondersteund)' if mode == 'databases' else 'Tabellen opvragen', 3, stmt)
        count = C.c_short()
        check(lib, lib.SQLNumResultCols(stmt, C.byref(count)), 'Kolommen lezen', 3, stmt)
        if count.value < len(names) or (mode in ('projects', 'audit') and count.value != len(names)):
            raise ValueError('De projectquery levert onverwachte kolommen op.')
        print('Stap 8: resultaat ophalen...', flush=True)
        rows, total = [], 0
        while True:
            code = lib.SQLFetch(stmt)
            if code == 100:
                return rows, False
            check(lib, code, 'Project lezen', 3, stmt)
            if len(rows) == limit:
                return rows, True
            row = {}
            for i, field in enumerate(names, 1):
                buf, size = C.create_string_buffer(65536), C.c_ssize_t()
                code = lib.SQLGetData(stmt, i, 1, buf, len(buf), C.byref(size))
                check(lib, code, 'Projectveld lezen', 3, stmt)
                if size.value == -1:
                    row[field] = None
                elif code == 1 or size.value < 0 or size.value >= len(buf):
                    raise ValueError('Onvolledig projectveld; vorig bestand blijft bewaard.')
                else:
                    total += size.value
                    if total > 20 * 1024 * 1024:
                        raise ValueError('Resultaat groter dan 20 MB; export gestopt.')
                    try:
                        value = buf.raw[:size.value].decode(config.get('encoding', 'utf-8'))
                        row[field] = value if mode == 'audit' and field == 'status' else value.strip()
                    except (UnicodeDecodeError, LookupError):
                        raise ValueError(
                            f'Tekstcodering ongeldig voor veld {field}; controleer de broncodering en gebruik '
                            './plenion.sh encoding (bijvoorbeeld cp1252 voor een Windows-1252-bron). '
                            'Vorig bestand blijft bewaard.') from None
            rows.append(row)
    except RuntimeError as exc:
        print(f'Leesfout vóór afsluiten: {exc}', file=sys.stderr, flush=True)
        raise
    finally:
        print('Stap 9: verbinding afsluiten...', flush=True)
        if stmt: lib.SQLFreeHandle(3, stmt)
        if connected: lib.SQLDisconnect(dbc)
        if dbc: lib.SQLFreeHandle(2, dbc)
        if env: lib.SQLFreeHandle(1, env)
        print('OK: verbinding afgesloten.', flush=True)


def save(path, payload):
    path = Path(path)
    fd, temp = tempfile.mkstemp(prefix='.projects-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp): os.unlink(temp)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--mode', choices=('projects', 'databases', 'tables', 'columns', 'audit'), default='projects')
    parser.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--config', default='/data/connection.json')
    parser.add_argument('--output')
    parser.add_argument('--status-column')
    parser.add_argument('--export', default='/data/projects.json', help='Bestaande export voor de audit')
    parser.add_argument('--limit', type=int, default=20000)
    args = parser.parse_args()
    if not 1 <= args.limit <= 20000:
        parser.error('--limit moet tussen 1 en 20000 liggen')
    if args.mode == 'audit':
        try:
            status_query(args.status_column)
        except ValueError as error:
            parser.error(str(error))
    elif args.status_column:
        parser.error('--status-column hoort bij --mode audit')
    # Supervise a separate process: a native driver can block Python signal handlers.
    if not args.worker:
        try:
            result = subprocess.run([sys.executable, __file__, '--worker', *sys.argv[1:]], timeout=90)
        except subprocess.TimeoutExpired:
            raise RuntimeError('Geen resultaat binnen 90 seconden; reader gestopt. Vorig bestand blijft bewaard.')
        raise SystemExit(result.returncode)
    if args.check:
        driver_check()
        return
    config = json.loads(Path(args.config).read_text())
    if args.mode == 'columns':
        columns, _ = read(config, 2000, 'columns')
        print('PROJ-kolommen (geen projectgegevens gelezen):')
        for column in columns:
            print(column['name'])
        return
    if args.mode == 'audit':
        output = Path(args.output or '/data/audit-status.json')
        export_path = Path(args.export)
        if output.resolve() in (export_path.resolve(), Path(args.config).resolve()):
            raise ValueError('Auditrapport mag de export of configuratie niet overschrijven.')
        export = json.loads(export_path.read_text(encoding='utf-8'))
        rows, truncated = read(config, args.limit, 'audit', args.status_column)
        report = audit_status(rows, export, args.status_column, truncated)
        report['database'] = config.get('database')
        save(output, report)
        print('Statuswaarde | Database | Export-IDs aanwezig | Ontbrekend')
        for row in report['statuses']:
            print(f'{json.dumps(row["status"], ensure_ascii=True)} | {row["database_records"]} | '
                  f'{row["export_records"]} | {row["missing_records"]}')
        print(f'Totaal: database {report["database_records"]}; export {report["export_records"]}; '
              f'ontbrekend {report["missing_records"]}; extra {report["extra_records"]}; '
              f'dubbele export-IDs {report["duplicate_export_ids"]}; '
              f'gewijzigde projectnummers {report["changed_project_numbers"]}.')
        print('Controleert alle records/versies. Statuswaarden komen uit de database op het controlemoment.')
        print(('OK: alle record-IDs en projectnummers komen overeen.' if report['complete'] else
               'AFWIJKING: export is onvolledig, afgekapt of verschilt van de huidige database.') + f' Rapport: {output}')
        if not report['complete']:
            raise SystemExit(2)
        return
    if args.mode != 'projects':
        try:
            rows, truncated = read(config, 2000, args.mode)
        except RuntimeError as exc:
            if args.mode == 'databases':
                raise RuntimeError(str(exc) + ' Catalogusopvraging kan een ingestelde database vereisen of niet ondersteund zijn. Gebruik ./plenion.sh database als je de naam kent.') from exc
            raise
        key = 'database' if args.mode == 'databases' else 'table'
        values = sorted({row[key] for row in rows if row.get(key)})
        if not values:
            raise RuntimeError('Geen namen ontvangen. Dat betekent niet dat er geen databases zijn. De driver of accountrechten kunnen opsommen beperken; vul de exacte databasenaam in via ./plenion.sh database.')
        print('Beschikbare namen volgens de driver:')
        for value in values:
            print(json.dumps(value, ensure_ascii=False))
        if truncated:
            print('Let op: overzicht beperkt tot 2000 records.')
        return
    rows, truncated = read(config, args.limit)
    output = args.output or '/data/projects.json'
    save(output, {'source': 'plenion_hfsql_server', 'fetched_at': datetime.now(timezone.utc).isoformat(),
                      'count': len(rows), 'truncated': truncated, 'projects': rows})
    print(f'OK: {len(rows)} projectrecords opgeslagen in {output}.' + (' Dit is een beperkte selectie.' if truncated else ''))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, RuntimeError) as exc:
        print(f'Fout: {exc}', file=sys.stderr)
        sys.exit(1)

