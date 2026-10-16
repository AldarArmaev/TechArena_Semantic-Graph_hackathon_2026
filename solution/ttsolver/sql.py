"""Binary-safe reader for CREATE/INSERT statements in the supplied dump.

SQL is split using bytes, before decoding individual nonbinary UTF-8 literals.
No DB client, MySQL process, Java application or InitData is executed.
"""
import gzip
import re
from decimal import Decimal
from copy import deepcopy


def statements(data):
    start = 0
    quoted = False
    i = 0
    while i < len(data):
        c = data[i]
        if quoted and c == 92:
            i += 2
            continue
        if c == 39:
            if quoted and i + 1 < len(data) and data[i+1] == 39:
                i += 2
                continue
            quoted = not quoted
        elif c == 59 and not quoted:
            yield data[start:i+1]
            start = i + 1
        i += 1
    if quoted:
        raise ValueError('Unterminated SQL literal')


def literal(raw):
    raw = raw.strip()
    binary = raw.startswith(b'_binary')
    if binary:
        raw = raw[7:].strip()
    if raw == b'NULL':
        return None
    if raw.startswith(b"'") and raw.endswith(b"'"):
        value = bytearray()
        i = 1
        while i < len(raw) - 1:
            if raw[i] == 92:
                i += 1
                value.append({48: 0, 110: 10, 114: 13, 116: 9, 98: 8, 90: 26}.get(raw[i], raw[i]))
            elif raw[i:i+2] == b"''":
                value.append(39)
                i += 1
            else:
                value.append(raw[i])
            i += 1
        return bytes(value) if binary else value.decode('utf-8')
    if re.fullmatch(rb'-?\d+', raw):
        return int(raw)
    if re.fullmatch(rb'-?\d+\.\d+', raw):
        return Decimal(raw.decode('ascii'))
    if raw.startswith(b'0x'):
        return bytes.fromhex(raw[2:].decode('ascii'))
    raise ValueError('Unsupported SQL value: ' + repr(raw[:70]))


def tuples(data):
    i = 0
    while i < len(data):
        if data[i] in b' \r\n,;':
            i += 1
            continue
        if data[i] != 40:
            raise ValueError('Expected SQL tuple')
        i += 1
        start = i
        quoted = False
        values = []
        while i < len(data):
            c = data[i]
            if quoted and c == 92:
                i += 2
                continue
            if c == 39:
                if quoted and data[i:i+2] == b"''":
                    i += 2
                    continue
                quoted = not quoted
            elif not quoted and c in (44, 41):
                values.append(literal(data[start:i]))
                start = i + 1
                if c == 41:
                    i += 1
                    break
            i += 1
        else:
            raise ValueError('Unterminated SQL tuple')
        yield values


class Database:
    def __init__(self, path):
        self.tables = {}
        self.columns = {}
        self.counter = 0
        db = None
        for stmt in statements(gzip.open(path, 'rb').read()):
            use = re.search(rb'\bUSE `([^`]+)`', stmt)
            if use:
                db = use[1].decode()
            create = re.search(rb'CREATE TABLE `([^`]+)` \((.*?)\) ENGINE=', stmt, re.S)
            if create:
                key = (db, create[1].decode())
                self.columns[key] = [x.decode() for x in re.findall(rb'^\s*`([^`]+)`', create[2], re.M)]
                self.tables[key] = []
            insert = re.search(rb'INSERT INTO `([^`]+)` VALUES\s*(.*);\s*$', stmt, re.S)
            if insert:
                key = (db, insert[1].decode())
                if key not in self.columns:
                    raise ValueError('INSERT without CREATE: ' + repr(key))
                for row in tuples(insert[2]):
                    if len(row) != len(self.columns[key]):
                        raise ValueError('SQL column count mismatch: ' + repr(key))
                    self.tables[key].append(dict(zip(self.columns[key], row)))

    def clone(self):
        return deepcopy(self)

    def key(self, table, module=None):
        matches = [k for k in self.tables if k[1] == table]
        if len(matches) == 1:
            return matches[0]
        preferred = (module or '').replace('-service', '').replace('-', '_')
        return next((k for k in matches if k[0] == preferred), None)

    def rows(self, table, module=None):
        key = self.key(table, module)
        if key is None:
            raise KeyError('Unknown/ambiguous table ' + table)
        return self.tables[key]

    def new_id(self):
        self.counter += 1
        return '00000000-0000-4000-8000-' + f'{self.counter:012d}'
