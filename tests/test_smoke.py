# -*- coding: utf-8 -*-
"""db-schema-forge smoke tests. Run: python3 tests/test_smoke.py"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import engine        # noqa: E402
import parser        # noqa: E402
import rules         # noqa: E402
import samples       # noqa: E402

CLEAN = """
CREATE TABLE orgs (
    id BIGSERIAL PRIMARY KEY,
    slug VARCHAR(64) NOT NULL UNIQUE,
    name VARCHAR(255) NOT NULL,
    plan VARCHAR(32) NOT NULL DEFAULT 'free'
        CHECK (plan IN ('free', 'team')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE users (
    id BIGSERIAL PRIMARY KEY,
    org_id BIGINT NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    email VARCHAR(254) NOT NULL UNIQUE,
    is_active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_users_org ON users (org_id);
"""

MESSY = """
CREATE TABLE customer (
    id INT AUTO_INCREMENT PRIMARY KEY,
    first_name VARCHAR(50),
    email VARCHAR(50),
    phone_1 VARCHAR(20),
    phone_2 VARCHAR(20),
    country_id INT,
    country_name VARCHAR(60),
    status VARCHAR(20),
    balance DOUBLE
);
CREATE TABLE country (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(60) NOT NULL
);
CREATE TABLE orders (
    id INT AUTO_INCREMENT PRIMARY KEY,
    customer_id INT,
    total DOUBLE,
    status VARCHAR(20),
    placed_at DATETIME
);
CREATE TABLE order_items (
    order_id INT,
    product_id INT,
    quantity INT,
    unit_price DOUBLE
);
CREATE TABLE products (
    id INT AUTO_INCREMENT PRIMARY KEY,
    sku VARCHAR(40),
    name VARCHAR(200),
    price DOUBLE
);
CREATE TABLE order_products (
    order_id INT,
    product_id INT
);
"""


def ids(result):
    return {f["id"] for f in result["findings"]}


class TestParser(unittest.TestCase):

    def test_tables_and_columns(self):
        s = parser.parse(CLEAN)
        self.assertEqual(len(s.tables), 2)
        users = s.table("users")
        self.assertEqual(users.column_names,
                         ["id", "org_id", "email", "is_active", "created_at",
                          "updated_at"])

    def test_primary_key(self):
        s = parser.parse(CLEAN)
        self.assertEqual(s.table("users").pk_columns, ["id"])

    def test_nullability_and_unique(self):
        s = parser.parse(CLEAN)
        u = s.table("users")
        self.assertFalse(u.col("email").nullable)
        self.assertTrue(u.col("email").unique)
        self.assertFalse(u.col("id").nullable)

    def test_foreign_key(self):
        s = parser.parse(CLEAN)
        fks = s.table("users").foreign_keys
        self.assertEqual(len(fks), 1)
        self.assertEqual(fks[0].ref_table, "orgs")
        self.assertEqual(fks[0].on_delete, "CASCADE")

    def test_index(self):
        s = parser.parse(CLEAN)
        idx = s.table("users").indexes
        self.assertEqual(len(idx), 1)
        self.assertEqual(idx[0].columns, ["org_id"])

    def test_alter_table_add_constraint(self):
        ddl = ("CREATE TABLE a (id INT PRIMARY KEY);"
               "CREATE TABLE b (id INT PRIMARY KEY, a_id INT);"
               "ALTER TABLE b ADD CONSTRAINT fk_b_a FOREIGN KEY (a_id) "
               "REFERENCES a(id) ON DELETE CASCADE;")
        s = parser.parse(ddl)
        fks = s.table("b").foreign_keys
        self.assertEqual(len(fks), 1)
        self.assertEqual(fks[0].ref_table, "a")
        self.assertEqual(fks[0].on_delete, "CASCADE")

    def test_simple_sketch_mode(self):
        s = parser.parse("users: id int pk, email text unique not null, "
                          "org_id int fk orgs.id")
        self.assertEqual(s.mode, "simple")
        u = s.table("users")
        self.assertEqual(u.pk_columns, ["id"])
        self.assertTrue(u.col("email").unique)
        self.assertEqual(u.foreign_keys[0].ref_table, "orgs")

    def test_enum_type(self):
        ddl = ("CREATE TYPE order_status AS ENUM ('new', 'done');"
               "CREATE TABLE o (id INT PRIMARY KEY, "
               "status order_status NOT NULL);")
        s = parser.parse(ddl)
        col = s.table("o").col("status")
        self.assertEqual(col.category, "enum")
        self.assertEqual(sorted(col.enum_values), ["done", "new"])

    def test_type_classification(self):
        self.assertEqual(parser.classify_type("BIGSERIAL")["category"], "int")
        self.assertEqual(parser.classify_type("double precision")["category"],
                         "float")
        self.assertEqual(parser.classify_type("numeric(12,2)")["precision"], 12)
        self.assertEqual(parser.classify_type("numeric(12,2)")["scale"], 2)
        self.assertEqual(
            parser.classify_type("timestamp with time zone")["category"],
            "timestamp")
        self.assertEqual(parser.classify_type("jsonb")["category"], "json")
        self.assertEqual(parser.classify_type("text[]")["is_array"], True)

    def test_comment_is_ignored(self):
        s = parser.parse("-- a comment\nCREATE TABLE t (id INT PRIMARY KEY);")
        self.assertEqual(len(s.tables), 1)


class TestRules(unittest.TestCase):

    def test_missing_primary_key(self):
        r = engine.analyze("CREATE TABLE t (a INT, b TEXT);")
        self.assertIn("missing_primary_key", ids(r))

    def test_float_money(self):
        r = engine.analyze("CREATE TABLE t (id INT PRIMARY KEY, "
                           "total_amount DOUBLE PRECISION);")
        self.assertIn("float_money", ids(r))

    def test_missing_foreign_key_inferred(self):
        r = engine.analyze("CREATE TABLE a (id INT PRIMARY KEY);"
                           "CREATE TABLE b (id INT PRIMARY KEY, "
                           "a_id INT);")
        self.assertIn("missing_foreign_key", ids(r))

    def test_repeating_group(self):
        r = engine.analyze("CREATE TABLE t (id INT PRIMARY KEY, "
                           "phone_1 VARCHAR(20), phone_2 VARCHAR(20));")
        self.assertIn("repeating_group", ids(r))

    def test_transitive_dependency(self):
        r = engine.analyze("CREATE TABLE country (id INT PRIMARY KEY, "
                           "name VARCHAR(60));"
                           "CREATE TABLE t (id INT PRIMARY KEY, "
                           "country_id INT, country_name VARCHAR(60));")
        self.assertIn("transitive_dependency", ids(r))

    def test_junction_without_pk(self):
        r = engine.analyze("CREATE TABLE a (id INT PRIMARY KEY);"
                           "CREATE TABLE b (id INT PRIMARY KEY);"
                           "CREATE TABLE ab (a_id INT, b_id INT);")
        self.assertIn("junction_table_no_pk", ids(r))

    def test_status_without_constraint(self):
        r = engine.analyze("CREATE TABLE t (id INT PRIMARY KEY, "
                           "status VARCHAR(20));")
        self.assertIn("status_without_constraint", ids(r))

    def test_status_with_check_is_quiet(self):
        r = engine.analyze("CREATE TABLE t (id INT PRIMARY KEY, "
                           "status VARCHAR(20) NOT NULL "
                           "CHECK (status IN ('a', 'b')));")
        self.assertNotIn("status_without_constraint", ids(r))

    def test_timestamp_without_tz(self):
        r = engine.analyze("CREATE TABLE t (id INT PRIMARY KEY, "
                           "created_at TIMESTAMP);")
        self.assertIn("timestamp_without_tz", ids(r))

    def test_broken_foreign_key(self):
        r = engine.analyze("CREATE TABLE t (id INT PRIMARY KEY, "
                           "x_id INT REFERENCES missing_table(id));")
        self.assertIn("broken_foreign_key", ids(r))

    def test_tenant_isolation_only_for_saas_profile(self):
        ddl = "CREATE TABLE invoices (id INT PRIMARY KEY, amount NUMERIC);"
        r_saas = engine.analyze(ddl, "saas_multi_tenant")
        r_proto = engine.analyze(ddl, "prototype")
        self.assertIn("missing_tenant_column", ids(r_saas))
        self.assertNotIn("missing_tenant_column", ids(r_proto))

    def test_nullable_required_field(self):
        r = engine.analyze("CREATE TABLE t (id INT PRIMARY KEY, "
                           "email VARCHAR(254));")
        self.assertIn("nullable_required_field", ids(r))

    def test_every_finding_has_evidence_and_fix(self):
        r = engine.analyze(MESSY, "legacy_migration")
        for f in r["findings"]:
            self.assertTrue(f["evidence"], f)
            self.assertTrue(f["fix"], f)
            self.assertTrue(f["why"], f)
            self.assertIn(f["severity"], ("high", "medium", "low"))
            self.assertIn(f["axis"], rules.AXES)


class TestScoring(unittest.TestCase):

    def test_clean_schema_scores_well(self):
        r = engine.analyze(CLEAN, "saas_multi_tenant")
        self.assertGreaterEqual(r["score"], 85)
        self.assertEqual(r["verdict"]["band"], "production_ready")

    def test_messy_schema_scores_badly(self):
        r = engine.analyze(MESSY, "legacy_migration")
        self.assertLess(r["score"], 50)
        self.assertEqual(r["verdict"]["band"], "needs_rework")

    def test_missing_pk_caps_keying(self):
        r = engine.analyze("CREATE TABLE t (a INT);")
        self.assertLessEqual(r["axes"]["keying"], 30)
        self.assertTrue(any(c["scope"] == "keying" for c in r["caps"]))

    def test_broken_fk_caps_integrity(self):
        r = engine.analyze("CREATE TABLE t (id INT PRIMARY KEY, "
                           "x_id INT REFERENCES gone(id));")
        self.assertLessEqual(r["axes"]["integrity"], 40)

    def test_float_money_caps_typing(self):
        r = engine.analyze("CREATE TABLE t (id INT PRIMARY KEY, "
                           "amount DOUBLE PRECISION);")
        self.assertLessEqual(r["axes"]["typing"], 45)

    def test_verdict_bands(self):
        self.assertEqual(engine.verdict_for(95)["band"], "production_ready")
        self.assertEqual(engine.verdict_for(75)["band"], "mostly_ready")
        self.assertEqual(engine.verdict_for(60)["band"], "needs_polish")
        self.assertEqual(engine.verdict_for(45)["band"], "tight_but_usable")
        self.assertEqual(engine.verdict_for(10)["band"], "needs_rework")

    def test_profile_changes_the_score(self):
        ddl = ("CREATE TABLE t (id INT PRIMARY KEY, amount DOUBLE, "
               "status VARCHAR(20), created_at TIMESTAMP);")
        a = engine.analyze(ddl, "fintech_ledger")
        b = engine.analyze(ddl, "iot_timeseries")
        self.assertNotEqual(a["score"], b["score"])

    def test_axis_scores_in_range(self):
        r = engine.analyze(MESSY, "legacy_migration")
        for axis in rules.AXES:
            self.assertGreaterEqual(r["axes"][axis], 0)
            self.assertLessEqual(r["axes"][axis], 100)

    def test_empty_input(self):
        r = engine.analyze("", "saas_multi_tenant")
        self.assertEqual(r["score"], 0)
        self.assertEqual(r["stats"]["tables"], 0)


class TestScaffold(unittest.TestCase):

    def test_money_becomes_numeric(self):
        r = engine.analyze("CREATE TABLE t (id INT PRIMARY KEY, "
                           "amount DOUBLE PRECISION);")
        self.assertIn("NUMERIC(12,2)", r["scaffold"]["ddl"])

    def test_missing_pk_gets_surrogate_key(self):
        r = engine.analyze("CREATE TABLE t (a INT);")
        self.assertIn("id BIGSERIAL PRIMARY KEY", r["scaffold"]["ddl"])

    def test_foreign_key_index_added(self):
        r = engine.analyze("CREATE TABLE a (id INT PRIMARY KEY);"
                           "CREATE TABLE b (id INT PRIMARY KEY, "
                           "a_id INT);")
        self.assertIn("CREATE INDEX idx_b_a_id", r["scaffold"]["ddl"])

    def test_status_constraint_is_a_todo(self):
        r = engine.analyze("CREATE TABLE t (id INT PRIMARY KEY, "
                           "status VARCHAR(20));")
        self.assertIn("TODO", r["scaffold"]["ddl"])

    def test_scaffold_keeps_tables(self):
        r = engine.analyze(CLEAN, "saas_multi_tenant")
        self.assertIn("CREATE TABLE users", r["scaffold"]["ddl"])


class TestSamples(unittest.TestCase):

    def test_all_samples_analyse(self):
        for s in samples.SAMPLES:
            r = engine.analyze(s["ddl"], s["profile"])
            self.assertGreater(r["stats"]["tables"], 0, s["id"])
            self.assertIsInstance(r["findings"], list)

    def test_sample_profiles_are_known(self):
        for s in samples.SAMPLES:
            self.assertIn(s["profile"], engine.PROFILES)

    def test_clean_sample_scores_high(self):
        s = samples.get_sample("saas_core")
        r = engine.analyze(s["ddl"], s["profile"])
        self.assertGreaterEqual(r["score"], 85)

    def test_legacy_sample_scores_low(self):
        s = samples.get_sample("legacy_ecommerce")
        r = engine.analyze(s["ddl"], s["profile"])
        self.assertLess(r["score"], 45)

    def test_ledger_sample_flags_float_money(self):
        s = samples.get_sample("fintech_ledger")
        r = engine.analyze(s["ddl"], s["profile"])
        self.assertIn("float_money", ids(r))


class TestExports(unittest.TestCase):

    def test_markdown_export(self):
        r = engine.analyze(CLEAN, "saas_multi_tenant")
        md = engine.to_markdown(r)
        self.assertIn("# db-schema-forge report", md)
        self.assertIn(str(r["score"]), md)

    def test_json_serialisable(self):
        r = engine.analyze(MESSY, "legacy_migration")
        json.dumps(r)

    def test_schema_map_edges(self):
        r = engine.analyze(CLEAN, "saas_multi_tenant")
        edges = r["schema_map"]["edges"]
        self.assertTrue(any(e["from"] == "users" and e["to"] == "orgs"
                            for e in edges))


if __name__ == "__main__":
    unittest.main(verbosity=2)
