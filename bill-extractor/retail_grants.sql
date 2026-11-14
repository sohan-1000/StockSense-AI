-- Least-privilege login for the bill extractor on the shared retail database.
-- Run once as the database owner, then use this role in RETAIL_DATABASE_URL.
-- (The chatbot keeps its own read-only role, chatbot_ro. Nothing here touches it.)

CREATE ROLE billapp_rw LOGIN PASSWORD 'change-me';
GRANT CONNECT ON DATABASE "<your_retail_database>" TO billapp_rw;
GRANT USAGE ON SCHEMA public TO billapp_rw;

-- read the catalogue to match bill items to products and suppliers
GRANT SELECT ON products, suppliers TO billapp_rw;

-- add stock: new batch rows, and +units on the existing inventory row
GRANT SELECT, INSERT ON batches TO billapp_rw;
GRANT SELECT, INSERT, UPDATE ON inventory TO billapp_rw;

-- everything else (sales, calendar, documents, chat_log, ...) is deliberately not granted
