#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
db-schema-forge - scoring, fix plan, normalised-shape plan, DDL scaffold.

The engine takes DDL (or a quick sketch), runs the rule engine, scores the
schema on five axes, applies structural caps so a schema cannot score well
by skipping the hard part, and produces a prioritised fix plan plus an
improved DDL scaffold with the safe fixes already applied.
"""

import json
import re
from typing import Dict, List, Optional

import parser
import rules
from parser import Schema, Table, Column, ForeignKey

# --------------------------------------------------------------------------
# profiles
# --------------------------------------------------------------------------

PROFILES = {
    "saas_multi_tenant": {
        "label": "SaaS / multi-tenant",
        "blurb": "Shared database, many customers, tenant isolation is the "
                 "whole game. Keying and integrity weigh heaviest.",
        "weights": {"normalization": 0.16, "keying": 0.26, "typing": 0.18,
                    "integrity": 0.24, "operability": 0.16},
        "soften": [],
    },
    "analytics_star": {
        "label": "Analytics / star schema",
        "blurb": "Dimension and fact tables that are deliberately "
                 "denormalised for query speed. Operability and typing "
                 "weigh heaviest.",
        "weights": {"normalization": 0.12, "keying": 0.20, "typing": 0.20,
                    "integrity": 0.18, "operability": 0.30},
        "soften": ["transitive_dependency", "derived_column",
                   "repeating_group", "json_unqueryable"],
    },
    "content_cms": {
        "label": "Content / CMS",
        "blurb": "Articles, authors, taxonomies and media. Normalisation "
                 "and content integrity carry the score.",
        "weights": {"normalization": 0.22, "keying": 0.20, "typing": 0.18,
                    "integrity": 0.24, "operability": 0.16},
        "soften": [],
    },
    "fintech_ledger": {
        "label": "Fintech / ledger",
        "blurb": "Double-entry ledgers and money movement. Typing and "
                 "integrity are non-negotiable.",
        "weights": {"normalization": 0.16, "keying": 0.22, "typing": 0.28,
                    "integrity": 0.24, "operability": 0.10},
        "soften": ["naming_inconsistent_tables", "naming_inconsistent_columns",
                   "fk_naming_inconsistent", "no_pagination_key"],
    },
    "iot_timeseries": {
        "label": "IoT / time series",
        "blurb": "High-volume append-only readings. Typing and write-path "
                 "operability matter most; audit columns are optional.",
        "weights": {"normalization": 0.14, "keying": 0.22, "typing": 0.26,
                    "integrity": 0.18, "operability": 0.20},
        "soften": ["missing_audit_timestamps", "missing_updated_at",
                   "missing_natural_key_unique", "nullable_required_field"],
    },
    "legacy_migration": {
        "label": "Legacy / pre-migration",
        "blurb": "An existing schema that has to survive a migration. "
                 "Operability and keying carry the score.",
        "weights": {"normalization": 0.18, "keying": 0.22, "typing": 0.18,
                    "integrity": 0.18, "operability": 0.24},
        "soften": ["naming_inconsistent_tables", "naming_inconsistent_columns",
                   "fk_naming_inconsistent", "reserved_word_column"],
    },
    "prototype": {
        "label": "Prototype / early schema",
        "blurb": "A schema that still has to change. Weighting is relaxed "
                 "so the tool flags real risks without blocking progress.",
        "weights": {"normalization": 0.12, "keying": 0.18, "typing": 0.16,
                    "integrity": 0.16, "operability": 0.38},
        "soften": ["missing_audit_timestamps", "missing_updated_at",
                   "naming_inconsistent_tables", "naming_inconsistent_columns",
                   "fk_naming_inconsistent", "no_pagination_key",
                   "nullable_with_default", "fk_no_delete_rule"],
    },
}

DEFAULT_PROFILE = "saas_multi_tenant"

VERDICT_BANDS = [
    (85, "production_ready", "Production ready",
     "The schema can carry real traffic and real data."),
    (70, "mostly_ready", "Mostly ready",
     "Sound overall - a handful of fixes stand between it and production."),
    (55, "needs_polish", "Needs polish",
     "The shape is right but the constraints are not tight enough."),
    (40, "tight_but_usable", "Tight but usable",
     "It will work, and it will work badly under load or under revision."),
    (0, "needs_rework", "Needs rework",
     "The foundations are wrong - fix these before you build on top."),
]


def verdict_for(score: float) -> Dict:
    for threshold, key, label, blurb in VERDICT_BANDS:
        if score >= threshold:
            return {"band": key, "label": label, "blurb": blurb,
                    "threshold": threshold}
    return {"band": "needs_rework", "label": "Needs rework",
            "blurb": VERDICT_BANDS[-1][3], "threshold": 0}


# --------------------------------------------------------------------------
# scoring
# --------------------------------------------------------------------------

SEV_WEIGHTS = {"high": 10.0, "medium": 5.0, "low": 2.0}

# The same rule firing again on another table is less news than the first
# time, so repeats are discounted rather than counted in full.
REPEAT_DECAY = [1.0, 0.6, 0.4, 0.25]


def score_axes(findings: List[rules.Finding], schema: Schema,
               profile: str) -> Dict:
    cfg = PROFILES[profile]
    scores = {a: 100.0 for a in rules.AXES}
    reasons = {a: [] for a in rules.AXES}
    counts = {}
    for f in findings:
        weight = SEV_WEIGHTS.get(f.severity, 2.0)
        if f.id in cfg["soften"]:
            weight = weight * 0.25
            f.severity = "low"
        key = (f.axis, f.id)
        seen = counts.get(key, 0)
        counts[key] = seen + 1
        factor = REPEAT_DECAY[min(seen, len(REPEAT_DECAY) - 1)]
        scores[f.axis] -= weight * factor
        reasons[f.axis].append(
            "%s (%s): %s" % (f.title, f.severity, f.evidence))
    for axis in rules.AXES:
        scores[axis] = max(0.0, min(100.0, scores[axis]))
    return {"scores": scores, "reasons": reasons}


def apply_structural_caps(scores: Dict[str, float], findings: List[rules.Finding],
                          schema: Schema, profile: str) -> List[Dict]:
    """Structural caps: a schema cannot score well by skipping the hard part."""
    caps = []
    ids = {f.id for f in findings}
    tables = schema.tables

    no_pk = [f for f in findings if f.id == "missing_primary_key"]
    if no_pk:
        caps.append({"scope": "keying", "cap": 30,
                     "reason": "At least one table has no primary key, so "
                               "the keying score is capped at 30."})
        scores["keying"] = min(scores["keying"], 30)
        if len(no_pk) >= max(1, len(tables)):
            caps.append({"scope": "overall", "cap": 35,
                         "reason": "Every table is missing a primary key."})
            scores["_overall_cap"] = min(scores.get("_overall_cap", 100), 35)

    if "broken_foreign_key" in ids:
        caps.append({"scope": "integrity", "cap": 40,
                     "reason": "A foreign key points at a table that is not "
                               "in the schema."})
        scores["integrity"] = min(scores["integrity"], 40)
        scores["_overall_cap"] = min(scores.get("_overall_cap", 100), 55)

    if "float_money" in ids or "money_as_text" in ids:
        caps.append({"scope": "typing", "cap": 45,
                     "reason": "Money is not stored in an exact numeric "
                               "type."})
        scores["typing"] = min(scores["typing"], 45)

    if "missing_tenant_column" in ids:
        caps.append({"scope": "integrity", "cap": 45,
                     "reason": "A table in a multi-tenant schema has no "
                               "tenant scope."})
        scores["integrity"] = min(scores["integrity"], 45)
        scores["_overall_cap"] = min(scores.get("_overall_cap", 100), 60)

    high = sum(1 for f in findings if f.severity == "high")
    if high >= 14:
        caps.append({"scope": "overall", "cap": 32,
                     "reason": "%d high-severity findings - the schema has "
                               "too many structural problems to score well."
                               % high})
        scores["_overall_cap"] = min(scores.get("_overall_cap", 100), 32)
    elif high >= 8:
        caps.append({"scope": "overall", "cap": 48,
                     "reason": "%d high-severity findings - the schema has "
                               "too many structural problems to score well."
                               % high})
        scores["_overall_cap"] = min(scores.get("_overall_cap", 100), 48)
    elif high >= 5:
        caps.append({"scope": "overall", "cap": 60,
                     "reason": "%d high-severity findings - the schema is not "
                               "production ready yet." % high})
        scores["_overall_cap"] = min(scores.get("_overall_cap", 100), 60)

    if not tables:
        caps.append({"scope": "overall", "cap": 0,
                     "reason": "No tables were found in the input."})
        scores["_overall_cap"] = 0
    return caps


def compute_overall(scores: Dict[str, float], profile: str) -> float:
    weights = PROFILES[profile]["weights"]
    total = sum(scores[a] * weights[a] for a in rules.AXES)
    cap = scores.get("_overall_cap", 100)
    return round(max(0.0, min(total, cap)), 1)


# --------------------------------------------------------------------------
# fix plan
# --------------------------------------------------------------------------

def build_fix_plan(findings: List[rules.Finding], profile: str) -> List[Dict]:
    weights = PROFILES[profile]["weights"]
    plan = []
    for f in findings:
        weight = weights.get(f.axis, 0.2)
        base = rules.SEV_PENALTY.get(f.severity, 2.0)
        gain = round(base * (0.5 + weight), 1)
        plan.append({
            "rank": 0,
            "id": f.id,
            "axis": f.axis,
            "severity": f.severity,
            "title": f.title,
            "evidence": f.evidence,
            "fix": f.fix,
            "gain": gain,
            "auto": f.auto_fixable,
            "table": f.table,
            "column": f.column,
        })
    plan.sort(key=lambda p: (-p["gain"], rules.SEV_ORDER.get(p["severity"], 3)))
    for i, p in enumerate(plan):
        p["rank"] = i + 1
    return plan


def build_normalisation_plan(findings: List[rules.Finding],
                             schema: Schema) -> List[Dict]:
    steps = []
    for f in findings:
        if f.axis != "normalization":
            continue
        target = ""
        detail = ""
        if f.id == "repeating_group":
            base = f.evidence.split(": ")[-1].split(",")[0].strip()
            base = re.sub(r"_?\d+$", "", base)
            target = "%s_%s" % (rules.singular(f.table), base)
            detail = ("New table %s (id, %s_id, value) - one row per value "
                      "instead of one column per value."
                      % (target, rules.singular(f.table)))
        elif f.id == "multivalued_column":
            col = f.column
            target = "%s_%s" % (rules.singular(f.table), col)
            detail = ("New junction table %s (%s_id, %s_id) with a composite "
                      "primary key." % (target, rules.singular(f.table), col))
        elif f.id == "transitive_dependency":
            target = f.table
            detail = ("Drop %s from %s and read it from the parent table via "
                      "the foreign key." % (f.column, f.table))
        elif f.id == "partial_dependency":
            target = f.table
            detail = ("Split %s so the columns that depend on one part of "
                      "the key live in their own table." % f.table)
        elif f.id == "derived_column":
            target = f.table
            detail = ("Make the derived value a view or a GENERATED column "
                      "in %s." % f.table)
        else:
            target = f.table
            detail = f.fix
        steps.append({
            "id": f.id,
            "severity": f.severity,
            "title": f.title,
            "evidence": f.evidence,
            "target": target,
            "detail": detail,
        })
    return steps


def build_strengths(schema: Schema, findings: List[rules.Finding]) -> List[str]:
    strengths = []
    tables = schema.tables
    if tables and all(t.pk_columns for t in tables):
        strengths.append("Every table has a primary key.")
    fks = [f for t in tables for f in t.foreign_keys]
    if fks:
        strengths.append("%d foreign key constraint(s) declared in the "
                         "schema." % len(fks))
    idx = schema.all_indexes()
    if idx:
        strengths.append("%d index(es) declared." % len(idx))
    if any(t.checks for t in tables):
        strengths.append("CHECK constraints are used to constrain values.")
    if any(c.category == "enum" for t in tables for c in t.columns):
        strengths.append("Closed value sets are modelled as enums.")
    if any(c.nullable is False for t in tables for c in t.columns):
        strengths.append("NOT NULL is used where a value is required.")
    if any("with time zone" in c.raw_type.lower()
           for t in tables for c in t.columns):
        strengths.append("Timestamps are stored with a time zone.")
    if any(c.category in ("decimal", "money")
           for t in tables for c in t.columns):
        strengths.append("Money is stored in an exact numeric type.")
    if not strengths:
        strengths.append("The schema parses cleanly - that is the start.")
    return strengths


# --------------------------------------------------------------------------
# improved DDL scaffold
# --------------------------------------------------------------------------

_TYPE_FIXES = {
    "float_money": "NUMERIC(12,2)",
    "money_as_text": "NUMERIC(12,2)",
    "decimal_without_precision": "NUMERIC(12,2)",
    "bool_as_int": "BOOLEAN",
    "timestamp_without_tz": "TIMESTAMPTZ",
    "unbounded_varchar": "VARCHAR(255)",
    "email_too_short": "VARCHAR(254)",
    "uuid_without_default": "UUID DEFAULT gen_random_uuid()",
}


def build_scaffold(schema: Schema, findings: List[rules.Finding],
                   profile: str) -> Dict:
    """Rebuild the DDL with the safe fixes already applied."""
    by_col = {}
    by_table = {}
    for f in findings:
        if f.table and f.column:
            by_col.setdefault((f.table.lower(), f.column.lower()),
                              []).append(f)
        elif f.table:
            by_table.setdefault(f.table.lower(), []).append(f)
    inferred = {}
    for t_, c_, target in rules.infer_fk_columns(schema):
        inferred.setdefault(t_.name.lower(), []).append(c_.name)

    applied = []
    out = []
    out.append("-- db-schema-forge improved scaffold")
    out.append("-- Generated from the reviewed schema; safe fixes are already "
               "applied,")
    out.append("-- changes that need a human decision are listed as TODO "
               "comments.")
    out.append("")

    for t in schema.tables:
        lines = []
        pk = [c.lower() for c in t.pk_columns]
        fk_cols = {}
        for fk in t.foreign_keys:
            for cn in fk.columns:
                fk_cols[cn.lower()] = fk
        unique_cols = set()
        for u in t.unique_columns:
            if len(u) == 1:
                unique_cols.add(u[0].lower())
        body_lines = []
        for c in t.columns:
            col_findings = by_col.get((t.name.lower(), c.name.lower()), [])
            ids_here = {x.id for x in col_findings}
            parts = [c.name]
            type_text = c.raw_type or "text"
            for fid in sorted(ids_here):
                if fid in _TYPE_FIXES:
                    type_text = _TYPE_FIXES[fid]
                    applied.append("%s.%s: %s -> %s"
                                   % (t.name, c.name, c.raw_type, type_text))
                    break
            parts.append(type_text)
            if c.category == "enum" and c.enum_values:
                if not c.nullable:
                    parts.append("NOT NULL")
            else:
                if not c.nullable:
                    parts.append("NOT NULL")
                if c.name.lower() in pk:
                    parts.append("PRIMARY KEY")
                if c.name.lower() in unique_cols:
                    parts.append("UNIQUE")
            if c.default and c.default.lower() != "now()":
                parts.append("DEFAULT " + c.default)
            if "nullable_required_field" in ids_here:
                parts.append("NOT NULL")
                applied.append("%s.%s: added NOT NULL" % (t.name, c.name))
            if "missing_natural_key_unique" in ids_here:
                parts.append("UNIQUE")
                applied.append("%s.%s: added UNIQUE" % (t.name, c.name))
            if "one_to_one_not_unique" in ids_here:
                parts.append("UNIQUE")
                applied.append("%s.%s: added UNIQUE (one-to-one)"
                               % (t.name, c.name))
            fk = fk_cols.get(c.name.lower())
            if fk and fk.source == "column":
                ref_col = fk.ref_columns[0] if fk.ref_columns else "id"
                od = fk.on_delete or "RESTRICT"
                parts.append("REFERENCES %s(%s) ON DELETE %s"
                             % (fk.ref_table, ref_col, od))
            if "status_without_constraint" in ids_here:
                body_lines.append("    -- TODO: constrain %s to its allowed "
                                  "values (ENUM or CHECK)" % c.name)
            body_lines.append("    " + " ".join(parts) + ",")

        for fk in t.foreign_keys:
            if fk.source != "table_constraint":
                continue
            ref_cols = ", ".join(fk.ref_columns or ["id"])
            od = fk.on_delete or "RESTRICT"
            body_lines.append("    CONSTRAINT fk_%s_%s FOREIGN KEY (%s) "
                              "REFERENCES %s(%s) ON DELETE %s"
                              % (t.name, "_".join(fk.columns).lower(),
                                 ", ".join(fk.columns), fk.ref_table, ref_cols,
                                 od))
        for u in t.uniques:
            if len(u) > 1:
                body_lines.append("    UNIQUE (%s)" % ", ".join(u))

        if not t.pk_columns:
            body_lines.insert(0, "    id BIGSERIAL PRIMARY KEY,")
            applied.append("%s: added surrogate primary key id" % t.name)
        elif len(t.foreign_keys) == 2 and not t.pk_columns:
            cols = []
            for fk in t.foreign_keys:
                cols.extend(fk.columns)
            body_lines.insert(0, "    PRIMARY KEY (%s)," % ", ".join(cols))
            applied.append("%s: added composite primary key (%s)"
                           % (t.name, ", ".join(cols)))

        names = {c.name.lower() for c in t.columns}
        if profile not in ("analytics_star", "iot_timeseries") \
                and "created_at" not in names:
            body_lines.append("    created_at TIMESTAMPTZ NOT NULL DEFAULT "
                              "now(),")
            body_lines.append("    updated_at TIMESTAMPTZ NOT NULL DEFAULT "
                              "now(),")
            applied.append("%s: added created_at / updated_at" % t.name)

        body_lines = [b.rstrip(",") if i == len(body_lines) - 1 else b
                      for i, b in enumerate(body_lines)]
        out.append("CREATE TABLE %s (\n%s\n);" % (t.name, "\n".join(body_lines)))

        indexed = set()
        for idx in t.indexes:
            for cn in idx.columns:
                indexed.add(cn.lower())
        index_cols = []
        for fk in t.foreign_keys:
            index_cols.extend(fk.columns)
        for cn in inferred.get(t.name.lower(), []):
            if cn not in index_cols:
                index_cols.append(cn)
        for cn in index_cols:
            if cn.lower() in indexed:
                continue
            out.append("CREATE INDEX idx_%s_%s ON %s (%s);"
                       % (t.name, cn, t.name, cn))
            applied.append("%s: added index on %s" % (t.name, cn))
            indexed.add(cn.lower())
        for f in by_table.get(t.name.lower(), []):
            if f.id == "status_not_indexed" and f.column.lower() not in indexed:
                out.append("CREATE INDEX idx_%s_%s ON %s (%s);"
                           % (t.name, f.column, t.name, f.column))
                applied.append("%s: added index on %s" % (t.name, f.column))
                indexed.add(f.column.lower())
        out.append("")

    singulars = {}
    for t in schema.tables:
        singulars.setdefault(rules.singular(t.name), t)
    for t in schema.tables:
        declared = {c.lower() for f in t.foreign_keys for c in f.columns}
        for c in t.columns:
            if c.name.lower() in declared or not c.name.lower().endswith("_id"):
                continue
            base = c.name[:-3]
            target = singulars.get(base.lower())
            if not target or target.name.lower() == t.name.lower():
                continue
            out.append("ALTER TABLE %s ADD CONSTRAINT fk_%s_%s FOREIGN KEY "
                       "(%s) REFERENCES %s(id) ON DELETE RESTRICT;"
                       % (t.name, t.name, c.name, c.name, target.name))
            applied.append("%s: added foreign key %s -> %s.id"
                           % (t.name, c.name, target.name))

    return {"ddl": "\n".join(out).strip() + "\n", "applied": applied}


# --------------------------------------------------------------------------
# schema map (for the UI)
# --------------------------------------------------------------------------

def build_schema_map(schema: Schema) -> Dict:
    tables = []
    for t in schema.tables:
        cols = []
        pk_lower = [p.lower() for p in t.pk_columns]
        for c in t.columns:
            cols.append({
                "name": c.name,
                "type": c.raw_type,
                "category": c.category,
                "nullable": c.nullable,
                "pk": c.name.lower() in pk_lower,
                "unique": c.unique,
                "fk": (c.foreign_key.ref_table if c.foreign_key else None),
                "default": c.default,
            })
        tables.append({
            "name": t.name,
            "columns": cols,
            "pk": t.pk_columns,
            "indexes": [{"name": i.name, "columns": i.columns,
                         "unique": i.unique} for i in t.indexes],
            "fks": [{"column": f.column_name, "ref_table": f.ref_table,
                     "on_delete": f.on_delete} for f in t.foreign_keys],
            "checks": t.checks,
        })
    edges = []
    for t in schema.tables:
        for f in t.foreign_keys:
            edges.append({"from": t.name, "to": f.ref_table,
                          "column": f.column_name,
                          "on_delete": f.on_delete,
                          "resolves": schema.table(f.ref_table) is not None})
    return {"tables": tables, "edges": edges}


# --------------------------------------------------------------------------
# exports
# --------------------------------------------------------------------------

def to_markdown(result: Dict) -> str:
    lines = []
    lines.append("# db-schema-forge report")
    lines.append("")
    lines.append("- **Profile:** %s" % result["profile"]["label"])
    lines.append("- **Score:** %s / 100 - **%s**"
                 % (result["score"], result["verdict"]["label"]))
    lines.append("- **Tables:** %d | **Columns:** %d | **Findings:** %d"
                 % (result["stats"]["tables"], result["stats"]["columns"],
                    result["stats"]["findings"]))
    lines.append("")
    lines.append("## Scorecard")
    lines.append("")
    for axis in rules.AXES:
        lines.append("- **%s: %s/100** - %s"
                     % (rules.AXIS_LABELS[axis], result["axes"][axis],
                        rules.AXIS_BLURB[axis]))
    lines.append("")
    if result["caps"]:
        lines.append("## Structural caps")
        lines.append("")
        for c in result["caps"]:
            lines.append("- %s: %s" % (c["scope"], c["reason"]))
        lines.append("")
    lines.append("## Strengths")
    lines.append("")
    for s in result["strengths"]:
        lines.append("- " + s)
    lines.append("")
    lines.append("## Fix plan")
    lines.append("")
    for p in result["fix_plan"][:40]:
        lines.append("%d. **%s** (%s) - %s"
                     % (p["rank"], p["title"], p["evidence"], p["fix"]))
    lines.append("")
    if result["normalisation_plan"]:
        lines.append("## Normalisation plan")
        lines.append("")
        for s in result["normalisation_plan"]:
            lines.append("- **%s** (%s): %s"
                         % (s["title"], s["evidence"], s["detail"]))
        lines.append("")
    lines.append("## All findings")
    lines.append("")
    for f in result["findings"]:
        lines.append("### %s [%s / %s]" % (f["title"], f["axis"], f["severity"]))
        lines.append("- **Evidence:** %s" % f["evidence"])
        lines.append("- **Why it matters:** %s" % f["why"])
        lines.append("- **Fix:** %s" % f["fix"])
        lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------

def analyze(text: str, profile: str = DEFAULT_PROFILE) -> Dict:
    if profile not in PROFILES:
        profile = DEFAULT_PROFILE
    schema = parser.parse(text or "")
    findings = rules.collect_findings(schema, profile)

    axis_result = score_axes(findings, schema, profile)
    scores = axis_result["scores"]
    caps = apply_structural_caps(scores, findings, schema, profile)
    overall = compute_overall(scores, profile)
    verdict = verdict_for(overall)

    for f in findings:
        f.gain = round(rules.SEV_PENALTY.get(f.severity, 2.0) *
                       PROFILES[profile]["weights"].get(f.axis, 0.2), 1)

    fix_plan = build_fix_plan(findings, profile)
    norm_plan = build_normalisation_plan(findings, schema)
    scaffold = build_scaffold(schema, findings, profile)

    clean_scores = {a: round(scores[a], 1) for a in rules.AXES}

    return {
        "profile": {"id": profile, "label": PROFILES[profile]["label"],
                    "blurb": PROFILES[profile]["blurb"],
                    "weights": PROFILES[profile]["weights"]},
        "score": overall,
        "verdict": verdict,
        "axes": clean_scores,
        "axis_reasons": axis_result["reasons"],
        "caps": caps,
        "findings": [f.to_dict() for f in findings],
        "fix_plan": fix_plan,
        "normalisation_plan": norm_plan,
        "strengths": build_strengths(schema, findings),
        "schema_map": build_schema_map(schema),
        "scaffold": scaffold,
        "stats": {
            "tables": len(schema.tables),
            "columns": sum(len(t.columns) for t in schema.tables),
            "findings": len(findings),
            "high": sum(1 for f in findings if f.severity == "high"),
            "medium": sum(1 for f in findings if f.severity == "medium"),
            "low": sum(1 for f in findings if f.severity == "low"),
            "foreign_keys": sum(len(t.foreign_keys) for t in schema.tables),
            "indexes": len(schema.all_indexes()),
            "mode": schema.mode,
        },
        "parse_errors": schema.parse_errors,
        "input": text or "",
    }
