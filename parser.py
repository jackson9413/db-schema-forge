#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
db-schema-forge - DDL / schema parsing.

Two input modes are supported:

1. DDL mode - real SQL DDL (Postgres / MySQL / SQLite flavoured):
       CREATE TABLE users (
           id BIGSERIAL PRIMARY KEY,
           email VARCHAR(255) NOT NULL UNIQUE,
           org_id BIGINT REFERENCES orgs(id)
       );
       CREATE INDEX idx_users_org ON users(org_id);
       ALTER TABLE users ADD CONSTRAINT fk_x FOREIGN KEY (...) REFERENCES ...;

2. Simple mode - a quick sketch, one table per line:
       users: id int pk, email varchar unique not null, org_id int fk orgs.id

Everything the rest of the tool sees is normalised into Table / Column
objects with a stable base_type and a category (int, string, temporal...).
"""

import re
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Tuple

BT = chr(96)
_IDENT = r'(?:"[^"]+"|' + BT + r'[^' + BT + r']+' + BT + r'|\[[^\]]+\]|[A-Za-z_][A-Za-z0-9_$]*)'


def strip_quotes(name: str) -> str:
    name = name.strip()
    if len(name) >= 2:
        if name[0] == '"' and name[-1] == '"':
            return name[1:-1]
        if name[0] == BT and name[-1] == BT:
            return name[1:-1]
        if name[0] == "[" and name[-1] == "]":
            return name[1:-1]
    return name


def split_statements(sql: str) -> List[str]:
    """Split a SQL script into statements, respecting quotes, comments, parens."""
    stmts = []
    buf = []
    depth = 0
    i = 0
    n = len(sql)
    while i < n:
        ch = sql[i]
        two = sql[i:i + 2]
        if two == "--":
            j = sql.find("\n", i)
            if j == -1:
                break
            buf.append(" ")
            i = j + 1
            continue
        if two == "/*":
            j = sql.find("*/", i + 2)
            if j == -1:
                break
            i = j + 2
            continue
        if ch == "'":
            buf.append(ch)
            i += 1
            while i < n:
                buf.append(sql[i])
                if sql[i] == "'":
                    if i + 1 < n and sql[i + 1] == "'":
                        buf.append(sql[i + 1])
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            continue
        if ch == '"':
            buf.append(ch)
            i += 1
            while i < n:
                buf.append(sql[i])
                if sql[i] == '"':
                    i += 1
                    break
                i += 1
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        if ch == ";" and depth == 0:
            stmts.append("".join(buf))
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        stmts.append(tail)
    return [s.strip() for s in stmts if s.strip()]


def split_top_level(body: str, sep: str = ",") -> List[str]:
    """Split on sep at paren depth 0, ignoring quoted text."""
    out = []
    buf = []
    depth = 0
    quote = None
    for ch in body:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch == "'" or ch == '"' or ch == BT:
            quote = ch
            buf.append(ch)
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == sep and depth == 0:
            out.append("".join(buf))
            buf = []
            continue
        buf.append(ch)
    out.append("".join(buf))
    return [x.strip() for x in out if x.strip()]


def _match_paren(text: str, start: int) -> Tuple[str, int]:
    """Return (inside, index_after_closing_paren) for the paren at start."""
    depth = 0
    quote = None
    for i in range(start, len(text)):
        ch = text[i]
        if quote:
            if ch == quote:
                quote = None
            continue
        if ch == "'" or ch == '"' or ch == BT:
            quote = ch
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return text[start + 1:i], i + 1
    return text[start + 1:], len(text)


# --------------------------------------------------------------------------
# type handling
# --------------------------------------------------------------------------

INT_TYPES = {"int", "integer", "int4", "int2", "int8", "smallint", "bigint",
             "tinyint", "mediumint", "serial", "bigserial", "smallserial",
             "serial2", "serial4", "serial8", "year"}
FLOAT_TYPES = {"float", "real", "double", "double precision", "float4", "float8"}
DECIMAL_TYPES = {"numeric", "decimal"}
STRING_TYPES = {"varchar", "char", "character", "text", "character varying",
                "citext", "nvarchar", "nchar", "bpchar"}
BOOL_TYPES = {"bool", "boolean"}
DATE_TYPES = {"date"}
TIME_TYPES = {"time", "timetz", "time with time zone", "time without time zone"}
TS_TYPES = {"timestamp", "timestamptz", "datetime", "timestamp with time zone",
            "timestamp without time zone", "datetime2", "smalldatetime"}
JSON_TYPES = {"json", "jsonb", "jsonpath"}
BINARY_TYPES = {"bytea", "blob", "binary", "varbinary", "longblob", "image"}
UUID_TYPES = {"uuid", "uniqueidentifier"}
NET_TYPES = {"inet", "cidr", "macaddr", "macaddr8"}
OTHER_TYPES = {"interval", "tsvector", "tsquery", "xml", "money", "bit", "varbit",
               "hstore", "oid", "geometry", "geography"}

MONEY_HINT = re.compile(
    r"(amount|price|total|cost|balance|fee|salary|revenue|subtotal|tax|"
    r"discount|refund|payment|billing|charge|rate|net_|gross|balance_)", re.I)
STATUS_HINT = re.compile(
    r"(^|_)(status|state|type|kind|category|priority|level|role|source|"
    r"channel|mode|reason|method|visibility|severity|tier|plan|country|"
    r"currency|language|timezone)(_|$)", re.I)
BOOL_HINT = re.compile(r"^(is|has|can|should|was|does|did|allow|enable)[_]?",
                       re.I)
EMAIL_HINT = re.compile(r"(^|_)(email|mail)(_|$)", re.I)
URL_HINT = re.compile(r"(^|_)(url|uri|website|homepage|link)(_|$)", re.I)
CODE_HINT = re.compile(r"(^|_)(code|sku|slug|token|zip|postal|ref|"
                       r"reference|external_id|number)(_|$)", re.I)
JSON_HINT = re.compile(r"(^|_)(settings|metadata|meta|payload|extra|props|"
                       r"properties|config|context|attributes|data)(_|$)", re.I)
AUDIT_HINT = re.compile(r"^(created_at|updated_at|deleted_at|created_by|"
                        r"updated_by|created|updated)$", re.I)

RESERVED_WORDS = {
    "order", "user", "users", "group", "groups", "select", "from", "where",
    "table", "key", "value", "values", "default", "check", "constraint",
    "column", "index", "primary", "foreign", "references",
    "authorization", "session", "condition", "desc", "asc", "offset",
    "limit", "position", "option",
}

RESERVED_SOFT = {
    "timestamp", "date", "time", "level", "type", "status", "size",
    "comment", "role", "language", "source", "target",
}

REF_CODE_HINT = re.compile(
    r"(^|_)(currency|country|language|timezone|locale)(_|$)", re.I)


@dataclass
class Column:
    name: str
    raw_type: str = ""
    base_type: str = ""
    category: str = "unknown"
    length: Optional[int] = None
    precision: Optional[int] = None
    scale: Optional[int] = None
    nullable: bool = True
    primary_key: bool = False
    unique: bool = False
    default: Optional[str] = None
    check: Optional[str] = None
    comment: Optional[str] = None
    auto_increment: bool = False
    is_array: bool = False
    enum_values: List[str] = field(default_factory=list)
    foreign_key: Optional["ForeignKey"] = None
    ordinal: int = 0
    table: str = ""

    @property
    def qualified(self) -> str:
        return (self.table + "." + self.name) if self.table else self.name


@dataclass
class ForeignKey:
    table: str
    columns: List[str]
    ref_table: str
    ref_columns: List[str]
    on_delete: Optional[str] = None
    on_update: Optional[str] = None
    name: Optional[str] = None
    source: str = "column"
    column: Optional[str] = None

    @property
    def column_name(self) -> str:
        return self.columns[0] if self.columns else ""


@dataclass
class Index:
    name: str
    table: str
    columns: List[str]
    unique: bool = False
    method: str = "btree"


@dataclass
class Table:
    name: str
    columns: List[Column] = field(default_factory=list)
    primary_key: List[str] = field(default_factory=list)
    uniques: List[List[str]] = field(default_factory=list)
    checks: List[str] = field(default_factory=list)
    indexes: List[Index] = field(default_factory=list)
    foreign_keys: List[ForeignKey] = field(default_factory=list)
    comment: Optional[str] = None

    def col(self, name: str) -> Optional[Column]:
        low = name.lower()
        for c in self.columns:
            if c.name.lower() == low:
                return c
        return None

    @property
    def column_names(self) -> List[str]:
        return [c.name for c in self.columns]

    @property
    def pk_columns(self) -> List[str]:
        if self.primary_key:
            return list(self.primary_key)
        return [c.name for c in self.columns if c.primary_key]

    @property
    def unique_columns(self) -> List[List[str]]:
        out = list(self.uniques)
        for c in self.columns:
            if c.unique:
                out.append([c.name])
        return out


@dataclass
class Schema:
    tables: List[Table] = field(default_factory=list)
    enum_types: Dict[str, List[str]] = field(default_factory=dict)
    mode: str = "ddl"
    parse_errors: List[str] = field(default_factory=list)

    def table(self, name: str) -> Optional[Table]:
        low = (name or "").lower()
        for t in self.tables:
            if t.name.lower() == low:
                return t
            if t.name.lower().split(".")[-1] == low:
                return t
        return None

    @property
    def table_names(self) -> List[str]:
        return [t.name for t in self.tables]

    def all_indexes(self) -> List[Index]:
        out = []
        for t in self.tables:
            out.extend(t.indexes)
        return out


# --------------------------------------------------------------------------
# type classification
# --------------------------------------------------------------------------

def classify_type(raw: str) -> Dict:
    t = (raw or "").strip()
    is_array = False
    if t.endswith("[]"):
        is_array = True
        t = t[:-2].strip()
    t = re.sub(r"\s+", " ", t).strip()
    qualified = None
    if "." in t and not t.startswith("("):
        head, _, tail = t.partition(".")
        if head.lower() not in ("double", "character", "time", "timestamp", "bit"):
            qualified = head.strip()
            t = tail.strip()
    inner = None
    if "(" in t:
        base = t.partition("(")[0].strip()
        inner, _ = _match_paren(t, t.index("("))
        t = base
    low = t.lower()
    enum_values = []
    if low.startswith("enum"):
        category = "enum"
        base_type = "enum"
        if inner:
            enum_values = [v.strip().strip("'") for v in inner.split(",") if v.strip()]
    else:
        base_type = low
        if low in INT_TYPES:
            category = "int"
        elif low in FLOAT_TYPES:
            category = "float"
        elif low in DECIMAL_TYPES:
            category = "decimal"
        elif low == "money":
            category = "money"
        elif low in BOOL_TYPES:
            category = "bool"
        elif low in STRING_TYPES:
            category = "string"
        elif low in UUID_TYPES:
            category = "uuid"
        elif low in DATE_TYPES:
            category = "date"
        elif low in TIME_TYPES:
            category = "time"
        elif low in TS_TYPES:
            category = "timestamp"
        elif low in JSON_TYPES:
            category = "json"
        elif low in BINARY_TYPES:
            category = "binary"
        elif low in NET_TYPES:
            category = "network"
        elif low in OTHER_TYPES:
            category = "other"
        else:
            category = "unknown"
    length = precision = scale = None
    if inner:
        parts = [p.strip() for p in inner.split(",") if p.strip()]
        nums = []
        for p in parts:
            try:
                nums.append(int(p))
            except ValueError:
                nums = []
                break
        if nums:
            if category == "decimal":
                precision = nums[0]
                scale = nums[1] if len(nums) > 1 else 0
            elif category == "string" or category == "binary" or category == "other":
                length = nums[0]
            elif category == "int" and not is_array:
                length = nums[0]
    return {
        "raw": raw.strip(),
        "base_type": base_type,
        "category": category,
        "qualified_type": qualified,
        "length": length,
        "precision": precision,
        "scale": scale,
        "is_array": is_array,
        "enum_values": enum_values,
    }


# --------------------------------------------------------------------------
# DDL parsing
# --------------------------------------------------------------------------

_CONSTRAINT_START = re.compile(
    r"^(CONSTRAINT|PRIMARY\s+KEY|FOREIGN\s+KEY|UNIQUE|CHECK|KEY|INDEX|EXCLUDE|"
    r"LIKE)\b", re.I)

_COL_CONSTRAINT_KW = re.compile(
    r"\b(NOT\s+NULL|NULL|DEFAULT|PRIMARY\s+KEY|UNIQUE|REFERENCES|CHECK|"
    r"GENERATED|COLLATE|COMMENT|AUTO_INCREMENT|IDENTITY|CONSTRAINT|"
    r"ON\s+DELETE|ON\s+UPDATE|DEFERRABLE|INITIALLY|AS\s+IDENTITY|"
    r"STORED|VIRTUAL|UNSIGNED|ZEROFILL|COMPRESSION|STORAGE)\b", re.I)


def _parse_references(text: str) -> Dict:
    out = {"ref_table": None, "ref_columns": [], "on_delete": None,
           "on_update": None}
    m = re.search(r"REFERENCES\s+(" + _IDENT + r")", text, re.I)
    if not m:
        return out
    ref = strip_quotes(m.group(1))
    out["ref_table"] = ref.split(".")[-1]
    rest = text[m.end():]
    idx = rest.find("(")
    if idx != -1:
        inner, _ = _match_paren(rest, idx)
        out["ref_columns"] = [strip_quotes(c) for c in
                              split_top_level(inner) if c.strip()]
    m2 = re.search(r"ON\s+DELETE\s+(CASCADE|RESTRICT|SET\s+NULL|SET\s+DEFAULT|"
                   r"NO\s+ACTION)", text, re.I)
    if m2:
        out["on_delete"] = re.sub(r"\s+", " ", m2.group(1)).upper()
    m3 = re.search(r"ON\s+UPDATE\s+(CASCADE|RESTRICT|SET\s+NULL|SET\s+DEFAULT|"
                   r"NO\s+ACTION)", text, re.I)
    if m3:
        out["on_update"] = re.sub(r"\s+", " ", m3.group(1)).upper()
    return out


def _parse_column(name: str, rest: str, table_name: str, ordinal: int,
                  schema: Schema, table_checks: List[str]) -> Column:
    col = Column(name=name, table=table_name, ordinal=ordinal)
    m = _COL_CONSTRAINT_KW.search(rest)
    if m:
        type_text = rest[:m.start()].strip()
        constraints = rest[m.start():]
    else:
        type_text = rest.strip()
        constraints = ""
    info = classify_type(type_text)
    col.raw_type = info["raw"]
    col.base_type = info["base_type"]
    col.category = info["category"]
    col.length = info["length"]
    col.precision = info["precision"]
    col.scale = info["scale"]
    col.is_array = info["is_array"]
    col.enum_values = info["enum_values"]
    q = info.get("qualified_type")
    enum_key = (q or col.base_type or "").lower()
    if enum_key in schema.enum_types:
        col.enum_values = schema.enum_types[enum_key]
        col.category = "enum"

    up = constraints.upper()
    if re.search(r"\bNOT\s+NULL\b", up):
        col.nullable = False
    elif re.search(r"\bNULL\b", up):
        col.nullable = True
    if "PRIMARY KEY" in up:
        col.primary_key = True
        col.nullable = False
    if re.search(r"\bUNIQUE\b", up):
        col.unique = True
    m = re.search(r"DEFAULT\s+(.+?)(?=\b(NOT\s+NULL|PRIMARY\s+KEY|UNIQUE|"
                  r"REFERENCES|CHECK|COLLATE|COMMENT|GENERATED)\b|$)", constraints,
                  re.I | re.S)
    if m:
        col.default = m.group(1).strip().rstrip(",")
    if re.search(r"\bAUTO_INCREMENT\b|\bAUTOINCREMENT\b|\bIDENTITY\b|"
                 r"\bGENERATED\s+(ALWAYS|BY DEFAULT)\s+AS\s+IDENTITY\b", up):
        col.auto_increment = True
    m = re.search(r"COMMENT\s+'([^']*)'", constraints, re.I)
    if m:
        col.comment = m.group(1)
    m = re.search(r"\bCHECK\s*\(", constraints, re.I)
    if m:
        inner, _ = _match_paren(constraints, m.end() - 1)
        col.check = inner.strip()
        table_checks.append(inner.strip())
    if re.search(r"\bREFERENCES\b", up):
        ref = _parse_references(constraints)
        if ref["ref_table"]:
            fk = ForeignKey(
                table=table_name,
                columns=[col.name],
                ref_table=ref["ref_table"],
                ref_columns=ref["ref_columns"] or ["id"],
                on_delete=ref["on_delete"],
                on_update=ref["on_update"],
                source="column",
                column=col.name,
            )
            col.foreign_key = fk
    return col


def _parse_table_body(body: str, table_name: str, schema: Schema) -> Table:
    table = Table(name=table_name)
    ordinal = 0
    for item in split_top_level(body):
        if _CONSTRAINT_START.match(item):
            _parse_table_constraint(table, item)
            continue
        m = re.match(r"^(" + _IDENT + r")\s+(.*)$", item, re.S)
        if not m:
            continue
        name = strip_quotes(m.group(1))
        rest = m.group(2)
        ordinal += 1
        table.columns.append(
            _parse_column(name, rest, table_name, ordinal, schema, table.checks))
    return table


def _parse_table_constraint(table: Table, item: str) -> None:
    text = item.strip()
    up = text.upper()
    name = None
    m = re.match(r"^CONSTRAINT\s+(" + _IDENT + r")\s+(.*)$", text, re.I | re.S)
    if m:
        name = strip_quotes(m.group(1))
        text = m.group(2).strip()
        up = text.upper()
    if up.startswith("PRIMARY KEY"):
        idx = text.find("(")
        if idx != -1:
            inner, _ = _match_paren(text, idx)
            table.primary_key = [strip_quotes(c) for c in split_top_level(inner)]
    elif up.startswith("UNIQUE"):
        idx = text.find("(")
        if idx != -1:
            inner, _ = _match_paren(text, idx)
            table.uniques.append([strip_quotes(c) for c in split_top_level(inner)])
    elif up.startswith("FOREIGN KEY"):
        idx = text.find("(")
        inner, after = ("", 0)
        if idx != -1:
            inner, after = _match_paren(text, idx)
        cols = [strip_quotes(c) for c in split_top_level(inner)]
        ref = _parse_references(text[after:])
        if ref["ref_table"]:
            table.foreign_keys.append(ForeignKey(
                table=table.name, columns=cols, ref_table=ref["ref_table"],
                ref_columns=ref["ref_columns"] or ["id"],
                on_delete=ref["on_delete"], on_update=ref["on_update"],
                name=name, source="table_constraint"))
    elif up.startswith("CHECK"):
        idx = text.find("(")
        if idx != -1:
            inner, _ = _match_paren(text, idx)
            table.checks.append(inner.strip())
    elif up.startswith("KEY") or up.startswith("INDEX") or up.startswith("EXCLUDE"):
        idx = text.find("(")
        if idx != -1:
            inner, _ = _match_paren(text, idx)
            cols = [strip_quotes(c) for c in split_top_level(inner)]
            inm = re.match(r"^(?:KEY|INDEX|EXCLUDE)\s+(" + _IDENT + r")", text,
                           re.I)
            iname = strip_quotes(inm.group(1)) if inm else "idx_" + table.name + \
                "_" + "_".join(cols)
            table.indexes.append(Index(name=iname, table=table.name,
                                      columns=cols, unique=False))


def parse_ddl(sql: str) -> Schema:
    schema = Schema(mode="ddl")
    statements = split_statements(sql)
    for stmt in statements:
        s = stmt.strip().rstrip(";")
        if not s:
            continue
        try:
            _parse_statement(s, schema)
        except Exception as exc:
            schema.parse_errors.append(s[:60].replace("\n", " ") + ": " + str(exc))
    for t in schema.tables:
        for c in t.columns:
            if c.foreign_key:
                t.foreign_keys.append(c.foreign_key)
    return schema


def _parse_statement(stmt: str, schema: Schema) -> None:
    up = stmt.upper()
    if up.startswith("CREATE TYPE"):
        m = re.match(r"^CREATE\s+TYPE\s+(" + _IDENT + r")\s+AS\s+ENUM\s*\(",
                     stmt, re.I | re.S)
        if m:
            idx = stmt.index("(", m.end() - 1)
            inner, _ = _match_paren(stmt, idx)
            vals = [v.strip().strip("'") for v in inner.split(",") if v.strip()]
            schema.enum_types[strip_quotes(m.group(1)).lower()] = vals
        return
    if up.startswith("CREATE TABLE"):
        m = re.match(r"^CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(" +
                     _IDENT + r")", stmt, re.I)
        if not m:
            return
        name = strip_quotes(m.group(1)).split(".")[-1]
        idx = stmt.find("(", m.end())
        if idx == -1:
            return
        body, _ = _match_paren(stmt, idx)
        table = _parse_table_body(body, name, schema)
        cm = re.search(r"COMMENT\s+ON\s+TABLE\s+" + _IDENT +
                       r"\s+IS\s+'([^']*)'", stmt, re.I)
        if cm:
            table.comment = cm.group(1)
        schema.tables.append(table)
        return
    if up.startswith("CREATE INDEX") or up.startswith("CREATE UNIQUE INDEX"):
        m = re.match(r"^CREATE\s+(UNIQUE\s+)?INDEX\s+(?:IF\s+NOT\s+EXISTS\s+)?(" +
                     _IDENT + r")\s+ON\s+(" + _IDENT + r")\s*\(", stmt, re.I | re.S)
        if not m:
            return
        unique = bool(m.group(1))
        iname = strip_quotes(m.group(2))
        tname = strip_quotes(m.group(3)).split(".")[-1]
        idx = stmt.index("(", m.end() - 1)
        inner, _ = _match_paren(stmt, idx)
        cols = []
        for c in split_top_level(inner):
            c = re.sub(r"\s+(ASC|DESC)\b", "", c, flags=re.I).strip()
            bits = c.split()
            cols.append(strip_quotes(bits[0]) if bits else strip_quotes(c))
        method = "btree"
        mm = re.search(r"USING\s+(\w+)", stmt, re.I)
        if mm:
            method = mm.group(1).lower()
        table = schema.table(tname)
        if table:
            table.indexes.append(Index(name=iname, table=tname, columns=cols,
                                      unique=unique, method=method))
        return
    if up.startswith("ALTER TABLE"):
        m = re.match(r"^ALTER\s+TABLE\s+(?:ONLY\s+)?(" + _IDENT + r")\s+(.*)$",
                     stmt, re.I | re.S)
        if not m:
            return
        tname = strip_quotes(m.group(1)).split(".")[-1]
        actions = m.group(2)
        table = schema.table(tname)
        if table is None:
            table = Table(name=tname)
            schema.tables.append(table)
        for act in split_top_level(actions):
            a = act.strip()
            au = a.upper()
            if (au.startswith("ADD CONSTRAINT") or au.startswith("ADD FOREIGN KEY")
                    or au.startswith("ADD PRIMARY KEY") or au.startswith("ADD UNIQUE")
                    or au.startswith("ADD CHECK")):
                item = re.sub(r"^ADD\s+", "", a, flags=re.I)
                _parse_table_constraint(table, item)
            elif au.startswith("ADD COLUMN") or au.startswith("ADD "):
                item = re.sub(r"^ADD\s+(COLUMN\s+)?", "", a, flags=re.I)
                cm = re.match(r"^(" + _IDENT + r")\s+(.*)$", item, re.S)
                if cm:
                    table.columns.append(_parse_column(
                        strip_quotes(cm.group(1)), cm.group(2), tname,
                        len(table.columns) + 1, schema, table.checks))
            elif au.startswith("ADD INDEX") or au.startswith("ADD KEY"):
                item = re.sub(r"^ADD\s+(INDEX|KEY)\s+", "", a, flags=re.I)
                im = re.match(r"^(" + _IDENT + r")\s*\((.*)\)\s*$", item, re.S)
                if im:
                    cols = [strip_quotes(c) for c in split_top_level(im.group(2))]
                    table.indexes.append(Index(name=strip_quotes(im.group(1)),
                                              table=tname, columns=cols))
        return


# --------------------------------------------------------------------------
# simple sketch mode
# --------------------------------------------------------------------------

_SIMPLE_TYPES = {
    "int": "int", "integer": "int", "bigint": "int", "smallint": "int",
    "serial": "int", "bigserial": "int", "uuid": "uuid", "text": "text",
    "str": "text", "string": "text", "varchar": "text", "char": "text",
    "bool": "boolean", "boolean": "boolean", "float": "double precision",
    "real": "double precision", "double": "double precision",
    "numeric": "numeric", "decimal": "numeric", "money": "money",
    "date": "date", "time": "time", "timestamp": "timestamp",
    "datetime": "timestamp", "timestamptz": "timestamp with time zone",
    "json": "jsonb", "jsonb": "jsonb", "blob": "bytea",
}


def parse_simple(text: str) -> Schema:
    """Parse 'table: col type flags, col2 type' sketch lines."""
    schema = Schema(mode="simple")
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("--"):
            continue
        if ":" not in line:
            continue
        tname, _, body = line.partition(":")
        tname = strip_quotes(tname.strip())
        if not tname or not body.strip():
            continue
        table = Table(name=tname)
        ordinal = 0
        for item in split_top_level(body):
            parts = item.split()
            if not parts:
                continue
            cname = strip_quotes(parts[0])
            ordinal += 1
            rest = " ".join(parts[1:])
            flags = rest.lower()
            flat = flags.replace(" ", "")
            type_word = "text"
            if rest:
                first = re.split(r"[\s(]", rest)[0].lower()
                type_word = _SIMPLE_TYPES.get(first, first)
            col = Column(name=cname, table=tname, ordinal=ordinal)
            info = classify_type(type_word)
            col.raw_type = info["raw"]
            col.base_type = info["base_type"]
            col.category = info["category"]
            tokens = flags.replace(",", " ").split()
            if "pk" in tokens:
                col.primary_key = True
                col.nullable = False
                if cname not in table.primary_key:
                    table.primary_key.append(cname)
            if "notnull" in flat or "not_null" in flat or "required" in tokens:
                col.nullable = False
            if "unique" in tokens or "uq" in tokens:
                col.unique = True
                table.uniques.append([cname])
            if "null" in tokens:
                col.nullable = True
            m = re.search(r"fk\s+([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)",
                          flags)
            if m:
                fk = ForeignKey(table=tname, columns=[cname],
                                ref_table=m.group(1), ref_columns=[m.group(2)],
                                source="column", column=cname)
                col.foreign_key = fk
                table.foreign_keys.append(fk)
            m = re.search(r"default\s+([A-Za-z0-9_]+)", flags)
            if m:
                col.default = m.group(1)
            if "index" in tokens or "idx" in tokens:
                table.indexes.append(Index(name="idx_" + tname + "_" + cname,
                                          table=tname, columns=[cname]))
            table.columns.append(col)
        schema.tables.append(table)
    return schema


def parse(text: str) -> Schema:
    """Auto-detect the input mode and parse."""
    if not text or not text.strip():
        return Schema(mode="empty")
    if re.search(r"\bCREATE\s+TABLE\b", text, re.I):
        return parse_ddl(text)
    if re.search(r"^\s*[A-Za-z_][A-Za-z0-9_]*\s*:", text, re.M):
        return parse_simple(text)
    return parse_ddl(text)
