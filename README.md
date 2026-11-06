# db-schema-forge

**Paste a database schema. Get the scorecard, the gaps, the fix plan, and the fixed DDL.**

A local-first database schema design reviewer for backend engineers, data
engineers and anyone about to write a migration. It parses real DDL (or a
quick sketch), scores the schema across five axes, explains every problem in
plain English, and hands back an improved DDL scaffold with the safe fixes
already applied.

No API keys. No network calls. No LLM. Everything runs on your machine.

---

## Why it exists

Most schema problems are not subtle - they are *unchecked*. The money column
is a `DOUBLE PRECISION`, the foreign key was never declared, the `status`
column accepts any string you send it, the junction table has no primary key.
Nothing tells you, because the database accepts all of it.

A linter tells you the schema is not formatted. A query coach tells you your
SQL is slow. **db-schema-forge tells you the shape of your schema is wrong** -
before the migration, before the incident, before the invoice has a cent of
rounding error in it.

It is built around three ideas:

1. **Every finding carries its evidence.** "Float money" is useless on its
   own. "`ledger_entries.amount DOUBLE PRECISION` cannot represent 0.10
   exactly" is a bug report.
2. **Structural caps.** A schema cannot score well by skipping the hard part.
   No primary key anywhere means the keying axis is capped at 30 and the
   overall score at 45, however tidy the rest looks.
3. **Fix the safe things for me.** The tool rebuilds your DDL with the
   mechanical fixes applied - money becomes `NUMERIC(12,2)`, missing foreign
   keys get declared, foreign key columns get indexed, a table with no key
   gets a surrogate one - and leaves a `TODO` comment where a human has to
   decide.

## What you get

- **A 0-100 score** across five axes - normalisation, keying, typing,
  integrity, operability - and a verdict band from *production ready* to
  *needs rework*.
- **50+ rules**, each with the evidence that triggered it, why it matters and
  a concrete fix.
- **A normal-form analysis** that says which tables should be split, into
  what, and which column belongs where.
- **A prioritised fix plan** with an estimated score gain per fix and a badge
  for the ones the tool can apply mechanically.
- **An improved DDL scaffold** with the safe fixes applied and the risky
  ones left as TODO comments.
- **A schema map** - every table, column, key and relationship, including the
  ones that point at a table you did not paste.
- **Seven schema profiles** that re-weight the axes: SaaS / multi-tenant,
  analytics star schema, content CMS, fintech ledger, IoT time series, legacy
  pre-migration, and prototype.
- **Markdown, JSON and `.sql` exports**, plus a local SQLite run history with
  a 1-5 star self-rating and notes.

## Install and run

``bash
cd db-schema-forge
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
./run.sh
`@

Then open <http://127.0.0.1:8765>.

The tool needs nothing but Python 3.9+ and Flask. No database server, no
API key, no network access.

``bash
python3 tests/test_smoke.py     # 45 tests
`@

## How to use it

1. Paste DDL into the left panel - `CREATE TABLE` statements, `CREATE INDEX`,
   `ALTER TABLE ... ADD CONSTRAINT`, `CREATE TYPE ... AS ENUM`. Comments are
   fine, so is MySQL-style backticks and Postgres-style quoted identifiers.
2. Pick the profile that matches what you are building.
3. Press **Review schema**.
4. Read the findings, work down the fix plan, copy the improved DDL.

There is also a quick sketch format for when the schema is still a napkin:

``
users: id int pk, email text unique not null, org_id int fk orgs.id
orders: id int pk, user_id int fk users.id, total numeric, status text
`@

## The five axes

| Axis | What it asks |
| --- | --- |
| **Normalisation** | Is the data in a normal form, or does it carry repeating groups, multi-valued columns, partial dependencies and copied parent attributes? |
| **Keying** | Can every row be addressed, and does the database enforce every relationship instead of leaving it to application code? |
| **Typing** | Do the column types match what the columns hold - money, booleans, timestamps and status codes included? |
| **Integrity** | Does the database itself stop bad rows from being written, and is tenant and audit data complete? |
| **Operability** | Will the joins, filters and pagination this schema needs run fast once it is in production? |

Each axis starts at 100 and loses points per finding (high 10, medium 5, low
2). A repeat of the same rule on another table is discounted, so ten missing
indexes do not read as ten separate catastrophes.

### Structural caps

| Condition | Effect |
| --- | --- |
| A table has no primary key | keying capped at 30 |
| Every table has no primary key | overall capped at 35 |
| A foreign key points at a table that is not in the schema | integrity capped at 40, overall at 55 |
| Money is stored as a float or as text | typing capped at 45 |
| A multi-tenant schema has a table with no tenant scope | integrity capped at 45, overall at 60 |
| 5 / 8 / 14 high-severity findings | overall capped at 60 / 48 / 32 |

## The rules

**Normalisation** - repeating group columns (`phone_1`, `phone_2`),
multi-valued columns (`tags`, `skills`), transitive dependencies
(`country_id` + `country_name`), partial dependencies against a composite
key, stored derived totals and names.

**Keying** - missing primary key, many-to-many table with no key, text
primary key, candidate key without `UNIQUE`, `<table>_id` column with no
foreign key behind it, foreign key to a table that is not there, key type
mismatch, nullable foreign key with `ON DELETE CASCADE`, missing `ON DELETE`
rule, one-to-one relationship that is not enforced.

**Typing** - money as float or text, `NUMERIC` without precision, boolean
stored as an integer, `VARCHAR` with no length, email column too short for
real addresses, status column with no allowed values, timestamp with no time
zone, structured data hidden in JSON, unknown type, UUID key with no default
generator.

**Integrity** - no `NOT NULL` anywhere in a table, required-looking column
that allows `NULL`, nullable column with a default, missing tenant scope,
missing audit timestamps, soft-delete column with no index.

**Operability** - foreign key column with no index, table wider than 25
columns, no stable pagination key, too many indexes, frequently filtered
column with no index, low-cardinality column leading a composite index,
inconsistent table and column naming, reserved-word column names.

## REST API

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/health` | liveness check |
| `GET` | `/api/profiles` | the seven schema profiles and their weights |
| `GET` | `/api/samples` | list the built-in samples |
| `GET` | `/api/sample?id=<id>` | one built-in sample |
| `POST` | `/api/analyze` | `{schema, profile, save?}` -> the full review |
| `GET` | `/api/sessions` | the local run history |
| `GET` | `/api/sessions/<id>` | one saved review |
| `PATCH` | `/api/sessions/<id>` | `{self_rating, notes}` |
| `DELETE` | `/api/sessions/<id>` | delete a saved review |
| `GET` | `/api/export/<id>?fmt=md|json|ddl` | download an export |

``bash
curl -s localhost:8765/api/analyze \
  -H 'Content-Type: application/json' \
  -d '{"schema":"CREATE TABLE t (id INT PRIMARY KEY, amount DOUBLE);","profile":"fintech_ledger"}' \
  | python3 -m json.tool
`@

## Built-in samples

| Sample | Profile | Score | What it demonstrates |
| --- | --- | --- | --- |
| `saas_core` | SaaS / multi-tenant | 98 | a clean shared-database schema |
| `legacy_ecommerce` | Legacy / pre-migration | 32 | repeating groups, copied parent data, float money, orphan columns, a keyless junction table, a dangling foreign key |
| `analytics_star` | Analytics / star schema | 98 | deliberate denormalisation, correctly weighted |
| `iot_readings` | IoT / time series | 96 | append-only readings, no audit columns required |
| `content_cms` | Content / CMS | 60 | comma-separated tags and a keyless tag junction |
| `fintech_ledger` | Fintech / ledger | 48 | a double-entry ledger holding money in floating point |

## Architecture

``
parser.py   DDL and sketch parsing - statements, types, keys, constraints
rules.py    the rule engine - 50+ findings across the five axes
engine.py   profiles, scoring, structural caps, fix plan, DDL scaffold, exports
samples.py  six built-in sample schemas
db.py       SQLite run history (self-rating, notes)
app.py      Flask API
static/     dark vanilla-JS UI, no framework, no build step
tests/      45 smoke tests
`@

Standard library only in the analysis engine - no pandas, no SQLAlchemy, no
model calls. Flask is the single dependency.

## Known limits

- The analysis is static. It reads the schema you paste, not a live
  database, so it cannot see indexes, constraints or data that live outside
  the pasted DDL.
- Transitive dependency and partial dependency detection are heuristic. They
  read column and key names, not a formal dependency analysis, so a hand-
  crafted schema can slip past them.
- The generated scaffold is Postgres-flavoured. It is a starting point to
  edit, not a migration to run unattended - read it before you apply it.
- Soft deletes, sharding, partitioning and read replicas are outside the
  scope of the rules.

## License

MIT.
