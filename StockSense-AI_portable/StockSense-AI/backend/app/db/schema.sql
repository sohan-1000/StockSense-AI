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

-- ---- application tables (RAG + chat log). NOT readable by the read-only chatbot role. ----
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS documents (
    document_id  SERIAL PRIMARY KEY,
    filename     TEXT NOT NULL,
    file_hash    TEXT NOT NULL UNIQUE,
    status       TEXT NOT NULL DEFAULT 'queued',   -- queued | processing | ready | failed
    error        TEXT,
    page_count   INTEGER,
    chunk_count  INTEGER,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS doc_chunks (
    chunk_id     SERIAL PRIMARY KEY,
    document_id  INTEGER NOT NULL REFERENCES documents(document_id) ON DELETE CASCADE,
    chunk_index  INTEGER NOT NULL,
    page         INTEGER,
    content      TEXT NOT NULL,
    embedding    vector(384) NOT NULL,
    UNIQUE (document_id, chunk_index)
);
CREATE INDEX IF NOT EXISTS idx_doc_chunks_embedding ON doc_chunks USING hnsw (embedding vector_cosine_ops);

CREATE TABLE IF NOT EXISTS chat_log (
    message_id      SERIAL PRIMARY KEY,
    conversation_id TEXT,
    question        TEXT NOT NULL,
    route           TEXT,
    status          TEXT,
    answer          TEXT,
    sql_text        TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS chat_feedback (
    feedback_id SERIAL PRIMARY KEY,
    message_id  INTEGER NOT NULL REFERENCES chat_log(message_id) ON DELETE CASCADE,
    rating      SMALLINT NOT NULL CHECK (rating IN (-1, 1)),
    comment     TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
