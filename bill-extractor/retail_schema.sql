-- Copied from the team's backend/app/db/schema.sql (retail tables only, no pgvector app tables).
-- Used by dev_retail_db.py and the tests. The team's file is the source of truth.
-- StockSense AI retail schema. Derived from inspecting the CSVs in StockSense_AI_Retail_Dataset/.
-- Idempotent: safe to run repeatedly.

CREATE TABLE IF NOT EXISTS suppliers (
    supplier_id            TEXT PRIMARY KEY,
    supplier_name          TEXT NOT NULL,
    city                   TEXT,
    average_lead_time_days INTEGER NOT NULL,
    minimum_order_value    NUMERIC(10,2) NOT NULL
);

CREATE TABLE IF NOT EXISTS products (
    product_id              TEXT PRIMARY KEY,
    product_name            TEXT NOT NULL,
    category                TEXT NOT NULL,
    selling_price           NUMERIC(10,2) NOT NULL,
    unit_cost               NUMERIC(10,2) NOT NULL,
    shelf_life_days         INTEGER NOT NULL,
    supplier_lead_time_days INTEGER NOT NULL,
    minimum_order_quantity  INTEGER NOT NULL,
    base_daily_demand       NUMERIC(8,2) NOT NULL,
    supplier_id             TEXT NOT NULL REFERENCES suppliers(supplier_id),
    reorder_target_days     INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS calendar (
    date         DATE PRIMARY KEY,
    day_of_week  TEXT NOT NULL,
    is_weekend   BOOLEAN NOT NULL,
    month        SMALLINT NOT NULL CHECK (month BETWEEN 1 AND 12),
    festival     TEXT,
    is_festival  BOOLEAN NOT NULL
);

CREATE TABLE IF NOT EXISTS inventory (
    product_id               TEXT PRIMARY KEY REFERENCES products(product_id),
    current_stock            INTEGER NOT NULL,
    snapshot_date            DATE NOT NULL,
    avg_daily_sales_last_30d NUMERIC(8,2) NOT NULL,
    reorder_point            INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS batches (
    batch_id           TEXT PRIMARY KEY,
    product_id         TEXT NOT NULL REFERENCES products(product_id),
    quantity_remaining INTEGER NOT NULL,
    received_date      DATE NOT NULL,
    expiry_date        DATE NOT NULL
);

CREATE TABLE IF NOT EXISTS sales (
    date              DATE NOT NULL REFERENCES calendar(date),
    product_id        TEXT NOT NULL REFERENCES products(product_id),
    quantity_sold     INTEGER NOT NULL,
    promotion_applied BOOLEAN NOT NULL,
    selling_price     NUMERIC(10,2) NOT NULL,
    festival          TEXT,
    PRIMARY KEY (date, product_id)
);

CREATE INDEX IF NOT EXISTS idx_sales_product_date ON sales (product_id, date);
CREATE INDEX IF NOT EXISTS idx_sales_date         ON sales (date);
CREATE INDEX IF NOT EXISTS idx_products_category  ON products (category);
CREATE INDEX IF NOT EXISTS idx_products_supplier  ON products (supplier_id);
CREATE INDEX IF NOT EXISTS idx_batches_product    ON batches (product_id);
CREATE INDEX IF NOT EXISTS idx_batches_expiry     ON batches (expiry_date);

