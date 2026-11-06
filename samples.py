#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Built-in sample schemas used by the UI and by the test suite."""

SAMPLES = [
    {
        "id": "saas_core",
        "name": "Clean multi-tenant SaaS core",
        "note": "Well-built shared-database schema: surrogate keys, tenant "
                "scope, constrained status columns, indexed foreign keys.",
        "profile": "saas_multi_tenant",
        "ddl": """CREATE TABLE orgs (
    id BIGSERIAL PRIMARY KEY,
    slug VARCHAR(64) NOT NULL UNIQUE,
    name VARCHAR(255) NOT NULL,
    plan VARCHAR(32) NOT NULL DEFAULT 'free'
        CHECK (plan IN ('free', 'team', 'business')),
    currency CHAR(3) NOT NULL DEFAULT 'USD'
        CHECK (currency IN ('USD', 'EUR', 'GBP')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE users (
    id BIGSERIAL PRIMARY KEY,
    org_id BIGINT NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    email VARCHAR(254) NOT NULL UNIQUE,
    display_name VARCHAR(120) NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_users_org ON users (org_id);

CREATE TABLE projects (
    id BIGSERIAL PRIMARY KEY,
    org_id BIGINT NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'archived', 'deleted')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_projects_org ON projects (org_id);

CREATE TABLE project_members (
    project_id BIGINT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role VARCHAR(32) NOT NULL DEFAULT 'member'
        CHECK (role IN ('owner', 'member', 'viewer')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (project_id, user_id)
);
CREATE INDEX idx_project_members_user ON project_members (user_id);
""",
    },
    {
        "id": "legacy_ecommerce",
        "name": "Legacy e-commerce schema",
        "note": "The classic mess: repeating phone columns, a copied country "
                "name, floating point money, orphan-prone columns, a junction "
                "table with no key and a foreign key to a table that is not "
                "there.",
        "profile": "legacy_migration",
        "ddl": """CREATE TABLE country (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(60) NOT NULL
);

CREATE TABLE customer (
    id INT AUTO_INCREMENT PRIMARY KEY,
    first_name VARCHAR(50),
    last_name VARCHAR(50),
    full_name VARCHAR(100),
    email VARCHAR(50),
    phone_1 VARCHAR(20),
    phone_2 VARCHAR(20),
    address_line_1 VARCHAR(200),
    address_line_2 VARCHAR(200),
    country_id INT,
    country_name VARCHAR(60),
    status VARCHAR(20),
    created DATETIME,
    credit_limit DOUBLE,
    balance DOUBLE
);

CREATE TABLE orders (
    id INT AUTO_INCREMENT PRIMARY KEY,
    customer_id INT,
    shipment_id INT,
    order_date DATETIME,
    status VARCHAR(20),
    total_amount DOUBLE,
    shipping_amount DOUBLE,
    currency_code VARCHAR(3),
    notes TEXT
);

CREATE TABLE order_items (
    id INT AUTO_INCREMENT PRIMARY KEY,
    order_id INT,
    product_id INT,
    quantity INT,
    unit_price DOUBLE,
    line_total DOUBLE
);

CREATE TABLE products (
    id INT AUTO_INCREMENT PRIMARY KEY,
    sku VARCHAR(40),
    name VARCHAR(200),
    category_id INT,
    price DOUBLE,
    is_active TINYINT,
    created_at DATETIME
);

CREATE TABLE categories (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(100)
);

CREATE TABLE product_categories (
    product_id INT,
    category_id INT
);

ALTER TABLE orders ADD CONSTRAINT fk_orders_shipment
    FOREIGN KEY (shipment_id) REFERENCES shipments(id);
""",
    },
    {
        "id": "analytics_star",
        "name": "Analytics star schema",
        "note": "Dimension and fact tables that are deliberately denormalised "
                "for query speed. Reviewed as an analytics warehouse, not as "
                "an OLTP schema.",
        "profile": "analytics_star",
        "ddl": """CREATE TABLE dim_date (
    date_key INT PRIMARY KEY,
    full_date DATE NOT NULL,
    year INT NOT NULL,
    quarter INT NOT NULL,
    month INT NOT NULL,
    day_of_week INT NOT NULL,
    is_weekend BOOLEAN NOT NULL
);

CREATE TABLE dim_product (
    product_key INT PRIMARY KEY,
    sku VARCHAR(64) NOT NULL UNIQUE,
    product_name VARCHAR(255) NOT NULL,
    category VARCHAR(120) NOT NULL,
    brand VARCHAR(120),
    unit_cost NUMERIC(12,2) NOT NULL,
    list_price NUMERIC(12,2) NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT true,
    valid_from DATE NOT NULL,
    valid_to DATE NOT NULL
);

CREATE TABLE fact_sales (
    sales_key BIGSERIAL PRIMARY KEY,
    date_key INT NOT NULL REFERENCES dim_date(date_key),
    product_key INT NOT NULL REFERENCES dim_product(product_key),
    store_id INT NOT NULL,
    customer_key INT,
    quantity INT NOT NULL,
    unit_price NUMERIC(12,2) NOT NULL,
    gross_amount NUMERIC(12,2) NOT NULL,
    discount_amount NUMERIC(12,2) NOT NULL DEFAULT 0,
    net_amount NUMERIC(12,2) NOT NULL,
    currency CHAR(3) NOT NULL DEFAULT 'USD'
        CHECK (currency IN ('USD', 'EUR', 'GBP')),
    loaded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_net_amount
        CHECK (net_amount = gross_amount - discount_amount)
);
CREATE INDEX idx_fact_sales_date ON fact_sales (date_key);
CREATE INDEX idx_fact_sales_product ON fact_sales (product_key);
CREATE INDEX idx_fact_sales_store ON fact_sales (store_id);
CREATE INDEX idx_fact_sales_date_product ON fact_sales (date_key, product_key);
""",
    },
    {
        "id": "iot_readings",
        "name": "IoT time-series readings",
        "note": "Append-only sensor readings. Reviewed as a time-series "
                "store: no audit columns, no natural key, append-only "
                "writes.",
        "profile": "iot_timeseries",
        "ddl": """CREATE TABLE devices (
    device_id BIGSERIAL PRIMARY KEY,
    device_uid VARCHAR(64) NOT NULL UNIQUE,
    site VARCHAR(80) NOT NULL,
    firmware_version VARCHAR(32),
    installed_at TIMESTAMP NOT NULL DEFAULT now()
);
CREATE INDEX idx_devices_site ON devices (site);

CREATE TABLE readings (
    reading_id BIGSERIAL PRIMARY KEY,
    device_id BIGINT NOT NULL REFERENCES devices(device_id),
    recorded_at TIMESTAMP NOT NULL,
    temperature DOUBLE PRECISION,
    humidity DOUBLE PRECISION,
    battery_voltage DOUBLE PRECISION,
    payload JSONB
);
CREATE INDEX idx_readings_device_time ON readings (device_id, recorded_at);
CREATE INDEX idx_readings_recorded ON readings (recorded_at);
""",
    },
    {
        "id": "content_cms",
        "name": "Content CMS",
        "note": "Articles, authors and taxonomies with a comma-separated tag "
                "column and a junction table that never got a key.",
        "profile": "content_cms",
        "ddl": """CREATE TABLE authors (
    id BIGSERIAL PRIMARY KEY,
    email VARCHAR(254) NOT NULL UNIQUE,
    display_name VARCHAR(120) NOT NULL,
    bio TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE articles (
    id BIGSERIAL PRIMARY KEY,
    author_id BIGINT NOT NULL REFERENCES authors(id),
    slug VARCHAR(160) NOT NULL,
    title VARCHAR(255) NOT NULL,
    body TEXT NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'draft',
    tags TEXT,
    published_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_articles_author ON articles (author_id);
CREATE INDEX idx_articles_status ON articles (status);

CREATE TABLE article_tags (
    article_id BIGINT NOT NULL REFERENCES articles(id),
    tag VARCHAR(40) NOT NULL
);

CREATE TABLE comments (
    id BIGSERIAL PRIMARY KEY,
    article_id BIGINT NOT NULL REFERENCES articles(id),
    author_id BIGINT REFERENCES authors(id),
    body TEXT NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'visible',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_comments_article ON comments (article_id);
""",
    },
    {
        "id": "fintech_ledger",
        "name": "Fintech double-entry ledger",
        "note": "Accounts, postings and a balance table where the money is "
                "held in floating point.",
        "profile": "fintech_ledger",
        "ddl": """CREATE TABLE accounts (
    id BIGSERIAL PRIMARY KEY,
    account_number VARCHAR(34) NOT NULL UNIQUE,
    holder_name VARCHAR(160) NOT NULL,
    currency CHAR(3) NOT NULL,
    account_type VARCHAR(24) NOT NULL DEFAULT 'checking',
    opened_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    closed_at TIMESTAMPTZ
);

CREATE TABLE ledger_entries (
    id BIGSERIAL PRIMARY KEY,
    entry_id UUID NOT NULL DEFAULT gen_random_uuid(),
    account_id BIGINT NOT NULL REFERENCES accounts(id),
    amount DOUBLE PRECISION NOT NULL,
    fee DOUBLE PRECISION NOT NULL DEFAULT 0,
    currency CHAR(3) NOT NULL,
    posted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    memo TEXT
);
CREATE INDEX idx_ledger_entries_account ON ledger_entries (account_id);
CREATE INDEX idx_ledger_entries_posted ON ledger_entries (posted_at);

CREATE TABLE ledger_postings (
    entry_id BIGINT NOT NULL REFERENCES ledger_entries(id),
    account_id BIGINT NOT NULL REFERENCES accounts(id),
    amount DOUBLE PRECISION NOT NULL,
    side VARCHAR(8) NOT NULL
);

CREATE TABLE balances (
    account_id BIGINT NOT NULL REFERENCES accounts(id),
    balance DOUBLE PRECISION NOT NULL,
    as_of TIMESTAMP NOT NULL,
    PRIMARY KEY (account_id, as_of)
);
""",
    },
]


def get_sample(sid: str):
    for s in SAMPLES:
        if s["id"] == sid:
            return s
    return None


def list_samples():
    return [{"id": s["id"], "name": s["name"], "note": s["note"],
             "profile": s["profile"]} for s in SAMPLES]
