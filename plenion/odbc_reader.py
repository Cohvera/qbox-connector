"""Bounded iODBC reader. Only a local capture is exposed to the driver."""
import ctypes as C
import re


def read_projects(config, capture):
    template = config['connection_template']
    if not re.search(r'(?:^|;)\s*RepFic=\{capture\}(?:;|$)', template, re.I) or re.search(r'server|\bDSN\s*=', template, re.I):
        raise ValueError('Gebruik een offline verbinding met {capture}, zonder server of DSN.')
    if any(c in str(capture) for c in ';{}\n\r'):
        raise ValueError('Ongeldig capturepad')
    sql = config['projects_sql'].strip()
    if not re.match(r'^SELECT\s', sql, re.I) or re.search(r';|--|/\*|\b(INTO|UPDATE|DELETE|INSERT|DROP|EXEC|CALL)\b', sql, re.I):
        raise ValueError('Alleen één SELECT-query is toegestaan.')
    if 'REPLACE_' in sql or 'REPLACE_' in template:
        raise ValueError('De HFSQL-veldkoppeling is nog niet ingesteld.')
    lib = C.CDLL(config.get('iodbc_library', 'libiodbc.so.2'))
    handle = C.c_void_p
    specs = {
        'SQLAllocHandle': [C.c_short, handle, C.POINTER(handle)],
        'SQLSetEnvAttr': [handle, C.c_int, handle, C.c_int],
        'SQLSetConnectAttr': [handle, C.c_int, handle, C.c_int],
        'SQLDriverConnect': [handle, handle, C.c_char_p, C.c_short, C.c_void_p, C.c_short, C.POINTER(C.c_short), C.c_ushort],
        'SQLExecDirect': [handle, C.c_char_p, C.c_int],
        'SQLNumResultCols': [handle, C.POINTER(C.c_short)],
        'SQLFetch': [handle],
        'SQLGetData': [handle, C.c_ushort, C.c_short, C.c_void_p, C.c_ssize_t, C.POINTER(C.c_ssize_t)],
        'SQLFreeHandle': [C.c_short, handle], 'SQLDisconnect': [handle],
    }
    for name, args in specs.items():
        getattr(lib, name).argtypes = args
        getattr(lib, name).restype = C.c_short
    def check(code):
        # Do not expose driver diagnostics: they may contain paths or credentials.
        if code not in (0, 1):
            raise ValueError('ODBC-opdracht mislukt; controleer driver, analysebestand en veldkoppeling.')
    env, dbc, stmt = handle(), handle(), handle()
    connected = False
    try:
        check(lib.SQLAllocHandle(1, None, C.byref(env)))
        check(lib.SQLSetEnvAttr(env, 200, handle(3), 0))
        check(lib.SQLAllocHandle(2, env, C.byref(dbc)))
        check(lib.SQLSetConnectAttr(dbc, 101, handle(1), 0))  # SQL_MODE_READ_ONLY
        check(lib.SQLSetConnectAttr(dbc, 103, handle(15), 0))  # login timeout
        output, length = C.create_string_buffer(2048), C.c_short()
        connection = template.replace('{capture}', str(capture)).encode('utf-8')
        check(lib.SQLDriverConnect(dbc, None, connection, -3, output, len(output), C.byref(length), 0))
        connected = True
        check(lib.SQLAllocHandle(3, dbc, C.byref(stmt)))
        check(lib.SQLExecDirect(stmt, sql.encode('utf-8'), -3))
        count = C.c_short()
        check(lib.SQLNumResultCols(stmt, C.byref(count)))
        if count.value != 10:
            raise ValueError('De projectquery moet exact tien kolommen leveren; zie README.')
        names = ('source_id', 'number', 'customer', 'description', 'planned', 'status_label', 'active', 'closed', 'is_current', 'revision')
        rows = []
        total_bytes = 0
        while True:
            code = lib.SQLFetch(stmt)
            if code == 100:
                break
            check(code)
            if len(rows) >= 20000:
                raise ValueError('Meer dan 20000 bronrecords; import gestopt.')
            row = {}
            for i, name in enumerate(names, 1):
                buf, size = C.create_string_buffer(16384), C.c_ssize_t()
                code = lib.SQLGetData(stmt, i, 1, buf, len(buf), C.byref(size))
                check(code)
                if size.value == -1:
                    row[name] = None
                elif size.value < 0 or size.value >= len(buf) or code == 1:
                    raise ValueError('ODBC-veld onvolledig; import gestopt.')
                else:
                    total_bytes += size.value
                    if total_bytes > 20 * 1024 * 1024:
                        raise ValueError('ODBC-resultaat te groot; import gestopt.')
                    row[name] = buf.value.decode(config.get('encoding', 'utf-8')).strip()
            rows.append(row)
        return rows
    finally:
        if stmt: lib.SQLFreeHandle(3, stmt)
        if connected: lib.SQLDisconnect(dbc)
        if dbc: lib.SQLFreeHandle(2, dbc)
        if env: lib.SQLFreeHandle(1, env)
