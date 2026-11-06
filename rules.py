#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
db-schema-forge - rule engine.

Every rule returns Finding objects carrying the evidence that triggered it,
why it matters and a concrete fix. Findings are grouped onto five axes:

    normalization  1NF / 2NF / 3NF shape of the tables
    keying         primary keys, foreign keys, uniqueness, referential actions
    typing         column types and the values they can hold
    integrity      NOT NULL, CHECK, tenant isolation, audit columns
    operability    indexes, table width, pagination keys, naming consistency
"""

import re
from dataclasses import dataclass, field
from typing import List, Dict, Optional

from parser import (Schema, Table, Column, ForeignKey, MONEY_HINT, STATUS_HINT,
                    BOOL_HINT, EMAIL_HINT, URL_HINT, CODE_HINT, JSON_HINT,
                    AUDIT_HINT, RESERVED_WORDS, RESERVED_SOFT, REF_CODE_HINT)

AXES = ["normalization", "keying", "typing", "integrity", "operability"]

AXIS_LABELS = {
    "normalization": "Normalisation",
    "keying": "Keying",
    "typing": "Typing",
    "integrity": "Integrity",
    "operability": "Operability",
}

AXIS_BLURB = {
    "normalization": "Are the tables in a normal form, or do they carry "
                      "repeating groups, multi-valued columns and duplicated "
                      "parent data?",
    "keying": "Can every row be addressed, and can every relationship be "
              "enforced by the database instead of by application code?",
    "typing": "Do the column types match what the column actually holds - "
              "money, booleans, timestamps and status codes included?",
    "integrity": "Does the database itself stop bad rows from being written, "
                 "and is tenant / audit data complete?",
    "operability": "Will the queries, joins and pagination this schema needs "
                   "run fast once it is in production?",
}


@dataclass
class Finding:
    id: str
    axis: str
    severity: str            # high | medium | low
    title: str
    evidence: str
    why: str
    fix: str
    table: str = ""
    column: str = ""
    auto_fixable: bool = False
    gain: float = 0.0

    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "axis": self.axis,
            "severity": self.severity,
            "title": self.title,
            "evidence": self.evidence,
            "why": self.why,
            "fix": self.fix,
            "table": self.table,
            "column": self.column,
            "auto_fixable": self.auto_fixable,
            "gain": round(self.gain, 1),
        }


SEV_PENALTY = {"high": 10.0, "medium": 5.0, "low": 2.0}
SEV_ORDER = {"high": 0, "medium": 1, "low": 2}


def singular(name: str) -> str:
    n = (name or "").lower()
    if n.endswith("ies") and len(n) > 4:
        return n[:-3] + "y"
    if n.endswith("ses") and len(n) > 4:
        return n[:-2]
    if n.endswith("s") and not n.endswith("ss") and len(n) > 2:
        return n[:-1]
    return n


def is_pluralish(name: str) -> bool:
    n = (name or "").lower()
    return n.endswith("s") and not n.endswith("ss") and not n.endswith("us")


def _fk_columns(table: Table) -> List[ForeignKey]:
    return list(table.foreign_keys)


def _indexed_leading(table: Table) -> List[str]:
    out = []
    for idx in table.indexes:
        if idx.columns:
            out.append(idx.columns[0].lower())
    return out


def _indexed_any(table: Table) -> set:
    out = set()
    for idx in table.indexes:
        for c in idx.columns:
            out.add(c.lower())
    for c in table.pk_columns:
        out.add(c.lower())
    for u in table.uniques:
        for c in u:
            out.add(c.lower())
    return out


def _has_unique_on(table: Table, colname: str) -> bool:
    low = colname.lower()
    for u in table.unique_columns:
        if len(u) == 1 and u[0].lower() == low:
            return True
    return False


_INT_WIDTH = {
    "tinyint": 1, "year": 1,
    "smallint": 2, "smallserial": 2, "int2": 2,
    "mediumint": 3,
    "int": 4, "integer": 4, "int4": 4, "serial": 4, "serial4": 4,
    "bigint": 8, "int8": 8, "bigserial": 8, "serial8": 8,
}


def _int_width(base_type) -> int:
    return _INT_WIDTH.get((base_type or "").lower(), 0)


def _pk_columns(table: Table) -> List[str]:
    return [c.lower() for c in table.pk_columns]


def infer_fk_columns(schema: Schema):
    """Columns that look like foreign keys to another table in this schema.

    Returns a list of (table, column, target_table) triples for columns that
    follow the <table>_id convention but have no constraint behind them.
    """
    table_names = {t.name.lower(): t for t in schema.tables}
    singulars = {}
    for t in schema.tables:
        singulars.setdefault(singular(t.name), t)
    out = []
    for t in schema.tables:
        declared = {c.lower() for f in t.foreign_keys for c in f.columns}
        for c in t.columns:
            if c.name.lower() in declared:
                continue
            if not c.name.lower().endswith("_id"):
                continue
            base = c.name[:-3]
            target = table_names.get(base.lower()) or singulars.get(base.lower())
            if not target or target.name.lower() == t.name.lower():
                continue
            out.append((t, c, target))
    return out


def _is_monotonic_id(col: Column) -> bool:
    if col.name.lower() in ("id",) and col.category == "int":
        return True
    return False


# --------------------------------------------------------------------------
# normalisation rules
# --------------------------------------------------------------------------

_REPEAT_SUFFIX = re.compile(r"^(.*?)_?(\d+)$")


def rule_repeating_groups(schema: Schema) -> List[Finding]:
    out = []
    for t in schema.tables:
        buckets = {}
        for c in t.columns:
            m = _REPEAT_SUFFIX.match(c.name.lower())
            if not m:
                continue
            base = m.group(1)
            if base in ("id", "fk", "pk", "no", "num", "qty"):
                continue
            buckets.setdefault(base, []).append(c.name)
        for base, names in buckets.items():
            if len(names) >= 2 and len(base) >= 3:
                out.append(Finding(
                    id="repeating_group",
                    axis="normalization",
                    severity="high",
                    title="Repeating group of columns",
                    evidence=t.name + ": " + ", ".join(names),
                    why="A second and third '%s' column means the number of "
                        "values per row is fixed by the schema. The day "
                        "someone needs a third, you migrate the table anyway "
                        "- and every query that touches it has to change."
                        % base,
                    fix="Move the group into a child table (%s) with "
                        "(%s_id, value) and drop the numbered columns."
                        % (t.name.rstrip("s") + "_" + base, singular(t.name)),
                    table=t.name,
                ))
    return out


def rule_multivalued_columns(schema: Schema) -> List[Finding]:
    out = []
    hint = re.compile(r"^(tags|labels|categories|category_list|skills|roles|"
                      r"items|phones|emails|photos|images|attachments|"
                      r"keywords|topics|allergens|permissions|groups|"
                      r"recipients|participants|options|features|tags_list)$",
                      re.I)
    for t in schema.tables:
        for c in t.columns:
            if hint.match(c.name.strip()) and c.category in ("string", "text"):
                out.append(Finding(
                    id="multivalued_column",
                    axis="normalization",
                    severity="high",
                    title="Multi-valued column",
                    evidence=c.qualified,
                    why="A list of values stuffed into one text column cannot "
                        "be indexed, joined, counted or constrained. 'Find all "
                        "rows tagged urgent' becomes a LIKE scan and a "
                        "correctness problem the moment a value contains a "
                        "comma.",
                    fix="Split it into a junction table (%s_%s) with a "
                        "foreign key back to %s."
                        % (singular(t.name), c.name, singular(t.name)),
                    table=t.name,
                    column=c.name,
                ))
    return out


def rule_transitive_dependencies(schema: Schema) -> List[Finding]:
    out = []
    inferred = {}
    for t, c, target in infer_fk_columns(schema):
        inferred.setdefault(t.name.lower(), []).append((c, target))
    for t in schema.tables:
        refs = []
        for fk in _fk_columns(t):
            refs.append((fk.column_name, fk.ref_table))
        for c, target in inferred.get(t.name.lower(), []):
            refs.append((c.name, target.name))
        for ref_col, ref_table in refs:
            parent = schema.table(ref_table)
            if not parent:
                continue
            base = singular(parent.name)
            for c in t.columns:
                low = c.name.lower()
                if low == (ref_col or "").lower():
                    continue
                if not low.startswith(base + "_"):
                    continue
                suffix = low[len(base) + 1:]
                if suffix in ("id", "uuid", "key", "code"):
                    continue
                out.append(Finding(
                    id="transitive_dependency",
                    axis="normalization",
                    severity="high",
                    title="Transitive dependency on %s" % parent.name,
                    evidence=c.qualified,
                    why="%s already depends on %s through %s. Copying a "
                        "parent attribute into the child means the two can "
                        "disagree - change the country name in one place and "
                        "every copied row is now wrong."
                        % (t.name, parent.name, ref_col or "the FK"),
                    fix="Delete %s and read it by joining %s. If it must stay "
                        "for speed, say so in a comment and refresh it from a "
                        "trigger." % (c.name, parent.name),
                    table=t.name,
                    column=c.name,
                ))
    return out


def rule_partial_dependencies(schema: Schema) -> List[Finding]:
    out = []
    for t in schema.tables:
        pk = _pk_columns(t)
        if len(pk) < 2:
            continue
        for c in t.columns:
            low = c.name.lower()
            if low in pk:
                continue
            for part in pk:
                if low == part or low == part + "_name" or low == part + "_code":
                    out.append(Finding(
                        id="partial_dependency",
                        axis="normalization",
                        severity="high",
                        title="Partial dependency on part of the composite key",
                        evidence=c.qualified + " (key: " + ", ".join(pk) + ")",
                        why="%s depends on only one part of the composite "
                            "primary key (%s), so it is in violation of second "
                            "normal form. The value is stored many times over "
                            "and can drift."
                            % (c.name, part),
                        fix="Move %s and everything that depends on it into "
                            "their own table keyed on %s." % (c.name, part),
                        table=t.name,
                        column=c.name,
                    ))
                    break
    return out


def rule_derived_columns(schema: Schema) -> List[Finding]:
    out = []
    qty = re.compile(r"^(quantity|qty|count|units)$", re.I)
    price = re.compile(r"^(unit_price|price|unit_cost|rate)$", re.I)
    total = re.compile(r"^(total|total_amount|amount|line_total|net_amount|"
                       r"gross_amount)$", re.I)
    for t in schema.tables:
        names = {c.name.lower(): c for c in t.columns}
        q = [n for n in names if qty.match(n)]
        p = [n for n in names if price.match(n)]
        to = [n for n in names if total.match(n)]
        if q and p and to:
            out.append(Finding(
                id="derived_column",
                axis="normalization",
                severity="medium",
                title="Stored derived total",
                evidence=t.name + ": " + ", ".join(sorted(to)),
                why="The total is computable from quantity x unit price. "
                    "Storing it means every write path has to remember to "
                    "keep the three in step, and they eventually will not.",
                fix="Drop the stored column and compute it in a view, or make "
                    "it a GENERATED column so the database owns the rule.",
                table=t.name,
            ))
    full = re.compile(r"^full_?name$", re.I)
    for t in schema.tables:
        names = {c.name.lower() for c in t.columns}
        if full.match("full_name") and ("first_name" in names or
                                        "last_name" in names):
            out.append(Finding(
                id="derived_column",
                axis="normalization",
                severity="low",
                title="Stored derived full name",
                evidence=t.name + ".full_name",
                why="The full name can be built from the parts, so the two "
                    "can disagree.",
                fix="Compute it in a view, or keep only the parts.",
                table=t.name,
                column="full_name",
            ))
    return out


# --------------------------------------------------------------------------
# keying rules
# --------------------------------------------------------------------------

def rule_missing_primary_key(schema: Schema) -> List[Finding]:
    out = []
    for t in schema.tables:
        if not t.pk_columns:
            out.append(Finding(
                id="missing_primary_key",
                axis="keying",
                severity="high",
                title="No primary key",
                evidence=t.name,
                why="Without a primary key the rows of %s cannot be "
                    "addressed. Replica identity breaks, ON DELETE CASCADE "
                    "has nothing to hang off, and 'update that row' has to "
                    "match on some other column that may not be unique."
                    % t.name,
                fix="Add a surrogate key: 'id BIGSERIAL PRIMARY KEY' (or "
                    "UUID when rows are created on many nodes).",
                table=t.name,
                auto_fixable=True,
            ))
    return out


_SAFE_EXTRA = (r"^(created_at|updated_at|created_by|added_at|"
               r"amount|quantity|qty|side|note|notes|position|sort_order|"
               r"value|tag|tags|label|name|code|data|type|key)$")


def rule_junction_tables(schema: Schema) -> List[Finding]:
    out = []
    inferred = {}
    for t, c, target in infer_fk_columns(schema):
        inferred.setdefault(t.name.lower(), []).append(c.name)
    for t in schema.tables:
        if t.pk_columns:
            continue
        names = []
        for fk in _fk_columns(t):
            for cn in fk.columns:
                if cn.lower().endswith("_id"):
                    names.append(cn)
        for cn in inferred.get(t.name.lower(), []):
            if cn not in names:
                names.append(cn)
        if len(names) != 2:
            continue
        others = [c.name.lower() for c in t.columns if c.name not in names]
        if any(not re.match(_SAFE_EXTRA, o) for o in others):
            continue
        out.append(Finding(
            id="junction_table_no_pk",
            axis="keying",
            severity="high",
            title="Many-to-many table with no primary key",
            evidence=t.name + " (" + ", ".join(sorted(set(names))) + ")",
            why="%s links two parents, so the same pair can be inserted "
                "twice and there is no key to update or delete a single "
                "link by." % t.name,
            fix="Add a composite primary key over the two foreign keys: "
                "PRIMARY KEY (%s, %s)."
                % (sorted(set(names))[0], sorted(set(names))[1]),
            table=t.name,
            auto_fixable=True,
        ))
    return out


def rule_text_primary_key(schema: Schema) -> List[Finding]:
    out = []
    for t in schema.tables:
        for pk in t.pk_columns:
            c = t.col(pk)
            if c and c.category in ("string", "json"):
                out.append(Finding(
                    id="text_primary_key",
                    axis="keying",
                    severity="medium",
                    title="Text primary key",
                    evidence=c.qualified,
                    why="A wide string as the key inflates every index that "
                        "references it and makes joins on the key expensive.",
                    fix="Use a surrogate integer or UUID key and keep the "
                        "natural key as a UNIQUE column.",
                    table=t.name,
                    column=c.name,
                ))
    return out


def rule_natural_key_uniqueness(schema: Schema) -> List[Finding]:
    out = []
    for t in schema.tables:
        pk = _pk_columns(t)
        for c in t.columns:
            if c.name.lower() in pk:
                continue
            if REF_CODE_HINT.search(c.name):
                continue
            if not (EMAIL_HINT.search(c.name) or CODE_HINT.search(c.name)):
                continue
            if _has_unique_on(t, c.name):
                continue
            if c.name.lower() in ("external_id", "reference", "number"):
                sev = "medium"
            else:
                sev = "high"
            out.append(Finding(
                id="missing_natural_key_unique",
                axis="keying",
                severity=sev,
                title="Candidate key is not unique",
                evidence=c.qualified,
                why="'%s' looks like a business key - two rows with the same "
                    "value mean two different records pretending to be the "
                    "same one, and nothing in the schema stops it."
                    % c.name,
                fix="Add UNIQUE to %s.%s." % (t.name, c.name),
                table=t.name,
                column=c.name,
                auto_fixable=True,
            ))
    return out


def rule_missing_foreign_keys(schema: Schema) -> List[Finding]:
    out = []
    for t, c, target in infer_fk_columns(schema):
        if True:
            out.append(Finding(
                id="missing_foreign_key",
                axis="keying",
                severity="high",
                title="Orphan-prone column with no foreign key",
                evidence=c.qualified,
                why="%s holds what looks like a reference to %s.%s but the "
                    "database does not enforce it. A typo or a deleted parent "
                    "leaves a row pointing at nothing, and only an "
                    "application-level join will ever notice."
                    % (c.name, target.name, "id"),
                fix="Add the constraint: FOREIGN KEY (%s) REFERENCES %s(id) "
                    "ON DELETE RESTRICT." % (c.name, target.name),
                table=t.name,
                column=c.name,
                auto_fixable=True,
            ))
    return out


def rule_broken_foreign_keys(schema: Schema) -> List[Finding]:
    out = []
    for t in schema.tables:
        for fk in _fk_columns(t):
            if schema.table(fk.ref_table):
                continue
            out.append(Finding(
                id="broken_foreign_key",
                axis="keying",
                severity="high",
                title="Foreign key points at a table that is not here",
                evidence=t.name + "." + fk.column_name + " -> " + fk.ref_table,
                why="The referenced table %s does not exist in this schema, so "
                    "the constraint cannot be created and the relationship is "
                    "held together by convention." % fk.ref_table,
                fix="Reference the right table, or include %s in the schema "
                    "you are reviewing." % fk.ref_table,
                table=t.name,
                column=fk.column_name,
            ))
    return out


def rule_fk_type_mismatch(schema: Schema) -> List[Finding]:
    out = []
    for t in schema.tables:
        for fk in _fk_columns(t):
            parent = schema.table(fk.ref_table)
            if not parent:
                continue
            for col_name, ref_name in zip(fk.columns, fk.ref_columns):
                c = t.col(col_name)
                pc = parent.col(ref_name)
                if not c or not pc:
                    continue
                if c.category != pc.category:
                    out.append(Finding(
                        id="fk_type_mismatch",
                        axis="keying",
                        severity="medium",
                        title="Foreign key type does not match its target",
                        evidence=t.name + "." + col_name + " (" + c.raw_type +
                        ") -> " + parent.name + "." + ref_name + " (" +
                        pc.raw_type + ")",
                        why="The two sides of the relationship have different "
                            "types, so the constraint either cannot be "
                            "created or silently casts, and values stop "
                            "matching.",
                        fix="Make both sides the same type - usually "
                            "BIGINT on both sides.",
                        table=t.name,
                        column=col_name,
                    ))
                elif (c.category == "int" and pc.category == "int"
                      and _int_width(c.base_type) != _int_width(pc.base_type)):
                    out.append(Finding(
                        id="fk_type_mismatch",
                        axis="keying",
                        severity="low",
                        title="Foreign key integer width differs",
                        evidence=t.name + "." + col_name + " (" + c.raw_type +
                        ") -> " + parent.name + "." + ref_name + " (" +
                        pc.raw_type + ")",
                        why="INT and BIGINT are different types; Postgres "
                            "will reject the constraint and MySQL will cast.",
                        fix="Use the same integer width on both sides.",
                        table=t.name,
                        column=col_name,
                    ))
    return out


def rule_nullable_cascade(schema: Schema) -> List[Finding]:
    out = []
    for t in schema.tables:
        for fk in _fk_columns(t):
            if not fk.on_delete or "CASCADE" not in fk.on_delete.upper():
                continue
            for col_name in fk.columns:
                c = t.col(col_name)
                if not c:
                    continue
                if c.nullable:
                    out.append(Finding(
                        id="nullable_fk_cascade",
                        axis="keying",
                        severity="high",
                        title="Nullable foreign key with ON DELETE CASCADE",
                        evidence=c.qualified + " ON DELETE CASCADE",
                        why="The column allows NULL but the delete rule is "
                            "CASCADE. Deleting the parent either removes the "
                            "child rows (when it should have set the column "
                            "to NULL) or fails, depending on how the "
                            "constraint was written.",
                        fix="Make the column NOT NULL and keep CASCADE, or "
                            "switch the delete rule to ON DELETE SET NULL.",
                        table=t.name,
                        column=col_name,
                    ))
    return out


def rule_fk_delete_rule(schema: Schema) -> List[Finding]:
    out = []
    for t in schema.tables:
        for fk in _fk_columns(t):
            if fk.on_delete:
                continue
            out.append(Finding(
                id="fk_no_delete_rule",
                axis="keying",
                severity="low",
                title="Foreign key with no ON DELETE rule",
                evidence=t.name + "." + fk.column_name + " -> " + fk.ref_table,
                why="Without an explicit rule the behaviour is whatever the "
                    "database defaults to, which differs between engines.",
                fix="State it: ON DELETE RESTRICT (safe default), "
                    "ON DELETE CASCADE (child belongs to parent) or "
                    "ON DELETE SET NULL (parent is a label, not an owner).",
                table=t.name,
                column=fk.column_name,
            ))
    return out


ONE_TO_ONE_HINT = re.compile(r"(profile|settings|preferences|details|"
                              r"account|meta|attributes|config)$", re.I)


def rule_one_to_one(schema: Schema) -> List[Finding]:
    out = []
    for t in schema.tables:
        fks = _fk_columns(t)
        if len(fks) != 1:
            continue
        fk = fks[0]
        if not ONE_TO_ONE_HINT.search(t.name):
            continue
        if _has_unique_on(t, fk.columns[0]):
            continue
        out.append(Finding(
            id="one_to_one_not_unique",
            axis="keying",
            severity="medium",
            title="One-to-one relationship is not enforced",
            evidence=t.name + "." + fk.column_name,
            why="%s looks like it holds one row per %s, but nothing stops two "
                "rows claiming the same parent - so 'the' settings row is "
                "ambiguous."
                % (t.name, fk.ref_table),
            fix="Add UNIQUE (%s) to %s." % (fk.columns[0], t.name),
            table=t.name,
            column=fk.column_name,
            auto_fixable=True,
        ))
    return out


# --------------------------------------------------------------------------
# typing rules
# --------------------------------------------------------------------------

def rule_money_as_float(schema: Schema) -> List[Finding]:
    out = []
    for t in schema.tables:
        for c in t.columns:
            if c.category != "float":
                continue
            if not MONEY_HINT.search(c.name):
                continue
            out.append(Finding(
                id="float_money",
                axis="typing",
                severity="high",
                title="Money stored as a floating point number",
                evidence=c.qualified + " " + c.raw_type,
                why="Binary floating point cannot represent 0.10 exactly. "
                    "Add a hundred such amounts and the total no longer "
                    "matches the sum of the lines - the classic billing "
                    "dispute.",
                fix="Use NUMERIC(12,2) (or MONEY) and store the currency in "
                    "a separate ISO-4217 code column.",
                table=t.name,
                column=c.name,
                auto_fixable=True,
            ))
    return out


def rule_money_as_text(schema: Schema) -> List[Finding]:
    out = []
    for t in schema.tables:
        for c in t.columns:
            if c.category not in ("string", "text"):
                continue
            if not MONEY_HINT.search(c.name):
                continue
            out.append(Finding(
                id="money_as_text",
                axis="typing",
                severity="high",
                title="Money stored as text",
                evidence=c.qualified + " " + c.raw_type,
                why="Amounts stored as strings sort alphabetically ('100' "
                    "before '9'), refuse to sum, and need parsing in every "
                    "query that touches them.",
                fix="Use NUMERIC(12,2).",
                table=t.name,
                column=c.name,
                auto_fixable=True,
            ))
    return out


def rule_decimal_without_precision(schema: Schema) -> List[Finding]:
    out = []
    for t in schema.tables:
        for c in t.columns:
            if c.category != "decimal":
                continue
            if c.precision is None:
                out.append(Finding(
                    id="decimal_without_precision",
                    axis="typing",
                    severity="medium",
                    title="NUMERIC without precision or scale",
                    evidence=c.qualified + " " + c.raw_type,
                    why="Unbounded NUMERIC picks arbitrary precision at "
                        "runtime, which makes the storage and the index "
                        "size unpredictable.",
                    fix="State the precision and scale: NUMERIC(12,2) for "
                        "money, NUMERIC(18,6) for rates.",
                    table=t.name,
                    column=c.name,
                    auto_fixable=True,
                ))
    return out


def rule_bool_as_int(schema: Schema) -> List[Finding]:
    out = []
    for t in schema.tables:
        for c in t.columns:
            if c.category != "int":
                continue
            if not BOOL_HINT.match(c.name.lower()):
                continue
            out.append(Finding(
                id="bool_as_int",
                axis="typing",
                severity="medium",
                    title="Boolean flag stored as an integer",
                    evidence=c.qualified + " " + c.raw_type,
                    why="An integer flag has three states in practice - 0, 1 "
                        "and 'someone put 2 in there'. A boolean type removes "
                        "the third state and reads clearly in the schema.",
                    fix="Use BOOLEAN (or SMALLINT with a CHECK if the flag is "
                        "genuinely tri-state).",
                    table=t.name,
                    column=c.name,
                    auto_fixable=True,
                ))
    return out


def rule_unbounded_varchar(schema: Schema) -> List[Finding]:
    out = []
    for t in schema.tables:
        for c in t.columns:
            if c.category != "string":
                continue
            if c.length is not None:
                continue
            if c.base_type in ("text", "citext"):
                continue
            out.append(Finding(
                id="unbounded_varchar",
                axis="typing",
                severity="medium",
                title="VARCHAR with no length limit",
                evidence=c.qualified + " " + c.raw_type,
                why="Without a length there is nothing stopping a 10 MB string "
                    "going into a column that was meant for a short code.",
                fix="Give it a realistic limit (VARCHAR(64), VARCHAR(255)) or "
                    "use TEXT deliberately when the length really is open.",
                table=t.name,
                column=c.name,
                auto_fixable=True,
            ))
    return out


def rule_email_type(schema: Schema) -> List[Finding]:
    out = []
    for t in schema.tables:
        for c in t.columns:
            if not EMAIL_HINT.search(c.name):
                continue
            if c.category not in ("string", "text"):
                continue
            if c.length is not None and c.length < 254:
                out.append(Finding(
                    id="email_too_short",
                    axis="typing",
                    severity="medium",
                    title="Email column too short for real addresses",
                    evidence=c.qualified + " " + c.raw_type,
                    why="RFC 5321 allows a full address up to 254 characters. "
                        "A shorter limit silently truncates or rejects valid "
                        "addresses.",
                    fix="Use VARCHAR(254) and add a CHECK constraint for the "
                        "shape of the address.",
                    table=t.name,
                    column=c.name,
                    auto_fixable=True,
                ))
            if not c.check and not _has_unique_on(t, c.name):
                out.append(Finding(
                    id="email_unconstrained",
                    axis="typing",
                    severity="low",
                    title="Email address has no format check",
                    evidence=c.qualified,
                    why="Nothing in the schema stops 'not-an-email' being "
                        "stored, so validation happens in application code and "
                        "drifts between code paths.",
                    fix="Add a CHECK (email ~* '^[^@]+@[^@]+\\.[^@]+$').",
                    table=t.name,
                    column=c.name,
                ))
    return out


def rule_status_unconstrained(schema: Schema) -> List[Finding]:
    out = []
    for t in schema.tables:
        for c in t.columns:
            if c.category not in ("string", "text", "enum"):
                continue
            if not STATUS_HINT.search(c.name):
                continue
            if c.check or c.enum_values:
                continue
            ref_code = bool(REF_CODE_HINT.search(c.name))
            strong = bool(re.search(r"(^|_)(status|state|stage)$", c.name,
                                    re.I))
            if ref_code:
                title = "Reference code column with no allowed values"
                severity = "medium"
                why = ("'%s' should hold a fixed set of codes (ISO 4217 "
                       "for currency, ISO 3166 for country). Left open, the "
                       "database accepts 'usd', 'USD ' and 'US Dollars' as "
                       "three different currencies." % c.name)
            elif strong:
                title = "Status column with no allowed values"
                severity = "high"
                why = ("'%s' is a closed set in every real system, but the "
                       "schema accepts any string. Typos ('shippedd'), "
                       "untranslated values and case differences ('Shipped' "
                       "vs 'shipped') all reach production." % c.name)
            else:
                title = "Controlled-vocabulary column with no allowed values"
                severity = "medium"
                why = ("'%s' holds a controlled vocabulary in practice, but "
                       "the schema accepts anything, so the value set lives "
                       "only in application code and drifts." % c.name)
            out.append(Finding(
                id="status_without_constraint",
                axis="typing",
                severity=severity,
                title=title,
                evidence=c.qualified + " " + (c.raw_type or "text"),
                why=why,
                fix="Constrain it: an ENUM type, or CHECK (%s IN ('a','b')) - "
                    "or a lookup table if the values carry their own "
                    "attributes." % c.name,
                table=t.name,
                column=c.name,
            ))
    return out


def rule_timestamp_without_tz(schema: Schema) -> List[Finding]:
    out = []
    for t in schema.tables:
        for c in t.columns:
            if c.category != "timestamp":
                continue
            if "with time zone" in c.raw_type.lower():
                continue
            if c.base_type in ("timestamptz", "datetime2"):
                continue
            out.append(Finding(
                id="timestamp_without_tz",
                axis="typing",
                severity="medium",
                title="Timestamp without time zone",
                evidence=c.qualified + " " + c.raw_type,
                why="A naive timestamp has no single meaning: the same value "
                    "is a different instant depending on who wrote it and "
                    "where the server is configured. Daylight saving changes "
                    "make the arithmetic wrong twice a year.",
                fix="Use TIMESTAMPTZ (or TIMESTAMP WITH TIME ZONE).",
                table=t.name,
                column=c.name,
                auto_fixable=True,
            ))
    return out


def rule_json_blob(schema: Schema) -> List[Finding]:
    out = []
    for t in schema.tables:
        for c in t.columns:
            if c.category != "json":
                continue
            if not JSON_HINT.search(c.name):
                continue
            out.append(Finding(
                id="json_unqueryable",
                axis="typing",
                severity="medium",
                title="Structured data hidden in a JSON column",
                evidence=c.qualified + " " + c.raw_type,
                why="JSON is fine for data you never query. The moment "
                    "something inside '%s' needs a filter, a join or a "
                    "constraint, the database cannot help and every query "
                    "becomes a full scan with a JSON path expression."
                    % c.name,
                fix="If the fields are queried, promote them to columns. If "
                    "they are not, say so in a column comment.",
                table=t.name,
                column=c.name,
            ))
    return out


def rule_unknown_type(schema: Schema) -> List[Finding]:
    out = []
    for t in schema.tables:
        for c in t.columns:
            if c.category != "unknown":
                continue
            out.append(Finding(
                id="unknown_type",
                axis="typing",
                severity="medium",
                title="Unrecognised column type",
                evidence=c.qualified + " " + (c.raw_type or "?"),
                why="'%s' is not a type this tool can reason about, so its "
                    "nullability, precision and storage are unknown."
                    % (c.raw_type or "?"),
                fix="Use a standard type, or declare the custom type in the "
                    "schema so its rules can be reviewed.",
                table=t.name,
                column=c.name,
            ))
    return out


def rule_uuid_without_default(schema: Schema) -> List[Finding]:
    out = []
    for t in schema.tables:
        pk = _pk_columns(t)
        for c in t.columns:
            if c.category != "uuid":
                continue
            if c.name.lower() not in pk:
                continue
            d = (c.default or "").lower()
            if "gen_random_uuid" in d or "uuid_generate" in d:
                continue
            out.append(Finding(
                id="uuid_without_default",
                axis="typing",
                severity="low",
                title="UUID key with no default generator",
                evidence=c.qualified,
                why="Every insert has to remember to generate the key, so "
                    "one code path that forgets produces a null primary key.",
                fix="DEFAULT gen_random_uuid() (Postgres 13+) or "
                    "DEFAULT uuid_generate_v4().",
                table=t.name,
                column=c.name,
                auto_fixable=True,
            ))
    return out


# --------------------------------------------------------------------------
# integrity rules
# --------------------------------------------------------------------------

REQUIRED_NAMES = re.compile(
    r"^(email|name|title|first_name|last_name|status|amount|price|quantity|"
    r"currency|slug|sku|code|role|type|created_at)$", re.I)


def rule_missing_not_null(schema: Schema) -> List[Finding]:
    out = []
    for t in schema.tables:
        pk = _pk_columns(t)
        nullable = [c for c in t.columns
                    if c.nullable and c.name.lower() not in pk]
        optional = [c for c in t.columns if c.name.lower() not in pk]
        if len(t.columns) >= 6 and optional and len(nullable) == len(optional):
            out.append(Finding(
                id="no_not_null_constraints",
                axis="integrity",
                severity="high",
                title="No NOT NULL constraints in the table",
                evidence=t.name + " (" + str(len(t.columns)) + " columns)",
                why="Every column accepts NULL, so 'required' is decided by "
                    "whichever application code happens to run. The "
                    "database cannot tell a blank value from a missing one.",
                fix="Mark the required columns NOT NULL and leave the "
                    "genuinely optional ones nullable.",
                table=t.name,
            ))
    return out


def rule_nullable_required(schema: Schema) -> List[Finding]:
    out = []
    for t in schema.tables:
        pk = _pk_columns(t)
        for c in t.columns:
            if not c.nullable:
                continue
            if c.name.lower() in pk:
                continue
            if not REQUIRED_NAMES.match(c.name):
                continue
            out.append(Finding(
                id="nullable_required_field",
                axis="integrity",
                severity="medium",
                title="Required-looking column allows NULL",
                evidence=c.qualified,
                why="'%s' is populated on every real row, but the schema "
                    "accepts NULL, so downstream code has to handle a case "
                    "that never happens - and does it differently in each "
                    "place." % c.name,
                fix="Make it NOT NULL (or explain in a comment why it is "
                    "optional).",
                table=t.name,
                column=c.name,
                auto_fixable=True,
            ))
    return out


def rule_nullable_with_default(schema: Schema) -> List[Finding]:
    out = []
    for t in schema.tables:
        for c in t.columns:
            if c.nullable and c.default:
                out.append(Finding(
                    id="nullable_with_default",
                    axis="integrity",
                    severity="low",
                    title="Nullable column with a default value",
                    evidence=c.qualified + " DEFAULT " + c.default,
                    why="An explicit NULL bypasses the default, so the "
                        "column ends up with three states: the default, a "
                        "value, and NULL.",
                    fix="Either drop the default or make the column NOT "
                        "NULL.",
                    table=t.name,
                    column=c.name,
                ))
    return out


TENANT_TABLE_HINT = re.compile(
    r"^(tenants?|orgs?|organizations?|accounts?|workspaces?|teams?|"
    r"companies|realms?)$", re.I)

TENANT_COLUMN_HINT = re.compile(
    r"^(tenant|org|organization|account|workspace|team|company|realm)"
    r"_?id$", re.I)


def rule_tenant_isolation(schema: Schema, profile: str) -> List[Finding]:
    out = []
    if profile != "saas_multi_tenant":
        return out
    scoped = set()
    changed = True
    while changed:
        changed = False
        for t in schema.tables:
            low = t.name.lower()
            if low in scoped:
                continue
            has = bool(TENANT_TABLE_HINT.match(t.name))
            if not has:
                for c in t.columns:
                    if TENANT_COLUMN_HINT.match(c.name.lower()):
                        has = True
            if not has:
                for fk in t.foreign_keys:
                    if fk.ref_table.lower() in scoped:
                        has = True
            if has:
                scoped.add(low)
                changed = True
    for t in schema.tables:
        if t.name.lower() in scoped:
            continue
        out.append(Finding(
                id="missing_tenant_column",
                axis="integrity",
                severity="high",
                title="Table has no tenant scope",
                evidence=t.name,
                why="In a multi-tenant schema every row has to belong to a "
                    "tenant. A table without a tenant key can return another "
                    "customer's rows the moment a query forgets a join.",
                fix="Add a tenant id column with a foreign key, and include "
                    "it in the primary key or in a composite unique "
                    "constraint.",
                table=t.name,
            ))
    return out


def rule_audit_columns(schema: Schema, profile: str) -> List[Finding]:
    out = []
    if profile in ("analytics_star", "iot_timeseries"):
        return out
    for t in schema.tables:
        names = {c.name.lower() for c in t.columns}
        if "created_at" in names and "updated_at" not in names:
            out.append(Finding(
                id="missing_updated_at",
                axis="integrity",
                severity="low",
                title="created_at without updated_at",
                evidence=t.name,
                why="You can say when a row appeared but not when it last "
                    "changed, which is usually the question asked during an "
                    "incident.",
                fix="Add updated_at TIMESTAMPTZ NOT NULL DEFAULT now() and "
                    "keep it current with a trigger.",
                table=t.name,
                column="updated_at",
            ))
        if "created_at" not in names and "updated_at" not in names:
            out.append(Finding(
                id="missing_audit_timestamps",
                axis="integrity",
                severity="medium",
                title="No audit timestamps",
                evidence=t.name,
                why="Nothing in the row records when it was written or last "
                    "changed. Debugging 'when did this happen' becomes a "
                    "log hunt, and stale rows cannot be identified.",
                fix="Add created_at and updated_at TIMESTAMPTZ columns with "
                    "now() defaults.",
                table=t.name,
                column="created_at",
                auto_fixable=True,
            ))
    return out


def rule_soft_delete_index(schema: Schema) -> List[Finding]:
    out = []
    for t in schema.tables:
        names = {c.name.lower() for c in t.columns}
        if "deleted_at" not in names:
            continue
        if "deleted_at" in _indexed_leading(t):
            continue
        out.append(Finding(
            id="soft_delete_without_index",
            axis="integrity",
            severity="low",
            title="Soft-delete column is not indexed",
            evidence=t.name + ".deleted_at",
            why="Every live query filters on deleted_at, so without an index "
                "the table is scanned in full.",
            fix="Add a partial index: CREATE INDEX ... ON %s (id) WHERE "
                "deleted_at IS NULL." % t.name,
            table=t.name,
            column="deleted_at",
        ))
    return out


# --------------------------------------------------------------------------
# operability rules
# --------------------------------------------------------------------------

def rule_fk_missing_index(schema: Schema) -> List[Finding]:
    out = []
    inferred = {}
    for t, c, target in infer_fk_columns(schema):
        inferred.setdefault(t.name.lower(), []).append(c.name)
    for t in schema.tables:
        indexed = _indexed_any(t)
        cols = []
        for fk in _fk_columns(t):
            cols.extend(fk.columns)
        for cn in inferred.get(t.name.lower(), []):
            if cn not in cols:
                cols.append(cn)
        for col_name in cols:
            if col_name.lower() in indexed:
                continue
            if True:
                out.append(Finding(
                    id="fk_missing_index",
                    axis="operability",
                    severity="high",
                    title="Foreign key column is not indexed",
                    evidence=t.name + "." + col_name,
                    why="Postgres does not index the referencing side of a "
                        "foreign key. Every join and every parent delete has "
                        "to scan %s to find the children." % t.name,
                    fix="CREATE INDEX idx_%s_%s ON %s (%s);"
                        % (t.name, col_name, t.name, col_name),
                    table=t.name,
                    column=col_name,
                    auto_fixable=True,
                ))
    return out


def rule_wide_table(schema: Schema) -> List[Finding]:
    out = []
    for t in schema.tables:
        if len(t.columns) > 25:
            out.append(Finding(
                id="wide_table",
                axis="operability",
                severity="medium",
                title="Very wide table",
                evidence=t.name + " (" + str(len(t.columns)) + " columns)",
                why="A wide table slows every read (more pages per row), "
                    "every index build and every schema change, and usually "
                    "hides two or three different entities mixed together.",
                fix="Split it into entities that share the same lifecycle.",
                table=t.name,
            ))
    return out


def rule_no_pagination_key(schema: Schema, profile: str) -> List[Finding]:
    out = []
    if profile == "iot_timeseries":
        return out
    for t in schema.tables:
        names = {c.name.lower(): c for c in t.columns}
        pk = _pk_columns(t)
        if re.match(r"^(dim|ref|vw|lookup)_", t.name.lower()):
            continue
        if pk and all(n.endswith("_key") for n in pk):
            continue
        if any(_is_monotonic_id(names[n]) for n in pk if n in names):
            continue
        if "created_at" in names:
            continue
        if len(t.columns) < 5:
            continue
        out.append(Finding(
            id="no_pagination_key",
            axis="operability",
            severity="medium",
            title="No stable key to paginate on",
            evidence=t.name,
            why="Keyset pagination needs a unique, ordered column. Without "
                "one, every page of results shifts under the reader and "
                "rows get skipped or repeated.",
            fix="Use a monotonic id (BIGSERIAL) or a unique created_at with a "
                "tiebreaker column.",
            table=t.name,
        ))
    return out


def rule_too_many_indexes(schema: Schema) -> List[Finding]:
    out = []
    for t in schema.tables:
        if len(t.indexes) > 6:
            out.append(Finding(
                id="too_many_indexes",
                axis="operability",
                severity="medium",
                title="Too many indexes on one table",
                evidence=t.name + " (" + str(len(t.indexes)) + " indexes)",
                why="Every index is written on every insert and update. Past "
                    "a point the write cost of the table is dominated by "
                    "indexes nobody reads.",
                fix="Audit the index list and drop the ones no query plan "
                    "uses.",
                table=t.name,
            ))
    return out


def rule_status_not_indexed(schema: Schema) -> List[Finding]:
    out = []
    for t in schema.tables:
        indexed = _indexed_leading(t)
        for c in t.columns:
            if c.name.lower() in indexed:
                continue
            if not re.search(r"(^|_)(status|state|stage)$", c.name, re.I):
                continue
            if c.category not in ("string", "text", "enum", "int"):
                continue
            out.append(Finding(
                id="status_not_indexed",
                axis="operability",
                severity="low",
                title="Frequently filtered column is not indexed",
                evidence=c.qualified,
                why="Status-style columns are filtered on in almost every "
                    "screen. Without an index the filter runs against the "
                    "whole table.",
                fix="CREATE INDEX idx_%s_%s ON %s (%s);"
                    % (t.name, c.name, t.name, c.name),
                table=t.name,
                column=c.name,
                auto_fixable=True,
            ))
    return out


def rule_composite_index_order(schema: Schema) -> List[Finding]:
    out = []
    for t in schema.tables:
        for idx in t.indexes:
            if len(idx.columns) < 2:
                continue
            first = t.col(idx.columns[0])
            if not first:
                continue
            low_card = first.category == "bool" or \
                (STATUS_HINT.search(first.name) and first.category == "int")
            if not low_card:
                continue
            out.append(Finding(
                id="composite_index_low_cardinality_first",
                axis="operability",
                severity="medium",
                title="Low-cardinality column leads a composite index",
                evidence=t.name + ": " + idx.name + " (" +
                ", ".join(idx.columns) + ")",
                why="'%s' has a handful of possible values, so leading with "
                    "it makes the index barely selective - most of the index "
                    "is not usable for the range scan you actually want."
                    % first.name,
                fix="Put the equality columns first and the range column "
                    "last, or split this into two indexes.",
                table=t.name,
                column=idx.columns[0],
            ))
    return out


def rule_naming_consistency(schema: Schema) -> List[Finding]:
    out = []
    tables = schema.tables
    if len(tables) < 2:
        return out
    conventional = re.compile(r"^(fact|dim|agg|bridge|stg|vw)_", re.I)
    plurals = [t.name for t in tables
               if is_pluralish(t.name) and not conventional.match(t.name)]
    singulars = [t.name for t in tables
                 if not is_pluralish(t.name) and not conventional.match(t.name)]
    if plurals and singulars:
        out.append(Finding(
            id="naming_inconsistent_tables",
            axis="operability",
            severity="low",
            title="Table naming is inconsistent",
            evidence=", ".join(sorted(plurals)[:4]) + " vs " +
            ", ".join(sorted(singulars)[:4]),
            why="Some tables are plural and some are singular, so nobody can "
                "guess a table name without looking it up.",
            fix="Pick one convention (usually plural) and rename the "
                "outliers.",
        ))
    styles = {"snake": 0, "camel": 0}
    for t in tables:
        for c in t.columns:
            n = c.name
            if re.search(r"[a-z][A-Z]", n):
                styles["camel"] += 1
            elif "_" in n:
                styles["snake"] += 1
    if styles["snake"] >= 3 and styles["camel"] >= 3:
        out.append(Finding(
            id="naming_inconsistent_columns",
            axis="operability",
            severity="low",
            title="Column naming mixes styles",
            evidence="snake_case %d / camelCase %d"
            % (styles["snake"], styles["camel"]),
            why="Mixed naming makes generated code, grep and migrations "
                "harder than they need to be.",
            fix="Standardise on snake_case across the schema.",
        ))
    fk_bases = set()
    for t in tables:
        for fk in t.foreign_keys:
            for cn in fk.columns:
                if cn.lower().endswith("_id"):
                    fk_bases.add(cn[:-3].lower())
    if fk_bases and len(fk_bases) > 2:
        inconsistent = [b for b in fk_bases if not b.endswith("s")]
        if inconsistent and len(inconsistent) != len(fk_bases):
            out.append(Finding(
                id="fk_naming_inconsistent",
                axis="operability",
                severity="low",
                title="Foreign key column names do not follow the table name",
                evidence=", ".join(sorted(fk_bases)[:5]),
                why="Some keys are named after the table and some after the "
                    "entity, so the relationship is not guessable from the "
                    "column name.",
                fix="Name every foreign key exactly as the singular table "
                    "name plus '_id'.",
            ))
    return out


def rule_reserved_names(schema: Schema) -> List[Finding]:
    out = []
    for t in schema.tables:
        for c in t.columns:
            low = c.name.lower()
            if low in RESERVED_WORDS:
                out.append(Finding(
                    id="reserved_word_column",
                    axis="operability",
                    severity="medium",
                    title="Column name is a reserved word",
                    evidence=c.qualified,
                    why="'%s' is a reserved word in most SQL dialects, so "
                        "every query that touches it needs quoting and some "
                        "tools and ORMs get it wrong." % c.name,
                    fix="Rename the column (for example '%s_') or always "
                        "quote it consistently." % c.name,
                    table=t.name,
                    column=c.name,
                ))
            elif low in RESERVED_SOFT:
                out.append(Finding(
                    id="reserved_word_column",
                    axis="operability",
                    severity="low",
                    title="Column name is a reserved word in some dialects",
                    evidence=c.qualified,
                    why="'%s' is a keyword in at least one common SQL "
                        "dialect, so portability depends on quoting it "
                        "everywhere." % c.name,
                    fix="Rename the column or quote it consistently.",
                    table=t.name,
                    column=c.name,
                ))
    return out


# --------------------------------------------------------------------------
# registry
# --------------------------------------------------------------------------

def collect_findings(schema: Schema, profile: str) -> List[Finding]:
    findings = []
    findings += rule_repeating_groups(schema)
    findings += rule_multivalued_columns(schema)
    findings += rule_transitive_dependencies(schema)
    findings += rule_partial_dependencies(schema)
    findings += rule_derived_columns(schema)

    findings += rule_missing_primary_key(schema)
    findings += rule_junction_tables(schema)
    findings += rule_text_primary_key(schema)
    findings += rule_natural_key_uniqueness(schema)
    findings += rule_missing_foreign_keys(schema)
    findings += rule_broken_foreign_keys(schema)
    findings += rule_fk_type_mismatch(schema)
    findings += rule_nullable_cascade(schema)
    findings += rule_fk_delete_rule(schema)
    findings += rule_one_to_one(schema)

    findings += rule_money_as_float(schema)
    findings += rule_money_as_text(schema)
    findings += rule_decimal_without_precision(schema)
    findings += rule_bool_as_int(schema)
    findings += rule_unbounded_varchar(schema)
    findings += rule_email_type(schema)
    findings += rule_status_unconstrained(schema)
    findings += rule_timestamp_without_tz(schema)
    findings += rule_json_blob(schema)
    findings += rule_unknown_type(schema)
    findings += rule_uuid_without_default(schema)

    findings += rule_missing_not_null(schema)
    findings += rule_nullable_required(schema)
    findings += rule_nullable_with_default(schema)
    findings += rule_tenant_isolation(schema, profile)
    findings += rule_audit_columns(schema, profile)
    findings += rule_soft_delete_index(schema)

    findings += rule_fk_missing_index(schema)
    findings += rule_wide_table(schema)
    findings += rule_no_pagination_key(schema, profile)
    findings += rule_too_many_indexes(schema)
    findings += rule_status_not_indexed(schema)
    findings += rule_composite_index_order(schema)
    findings += rule_naming_consistency(schema)
    findings += rule_reserved_names(schema)

    # de-duplicate on (id, evidence)
    seen = set()
    unique = []
    for f in findings:
        key = (f.id, f.evidence)
        if key in seen:
            continue
        seen.add(key)
        unique.append(f)
    unique.sort(key=lambda f: (SEV_ORDER.get(f.severity, 3), f.axis, f.table,
                               f.column, f.id))
    return unique
