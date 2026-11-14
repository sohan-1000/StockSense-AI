-- Copied from the team's backend/app/db/views.sql. The team's file is the source of truth.
-- Business views: hide tricky joins/formulas so LLM-written SQL stays simple and correct.

-- Revenue uses sales.selling_price (actual price paid that day; differs from the list price on promo days).
CREATE OR REPLACE VIEW v_sales_enriched AS
SELECT s.date,
       EXTRACT(YEAR FROM s.date)::int AS year,
       c.month,
       c.day_of_week,
       c.is_weekend,
       s.product_id,
       p.product_name,
       p.category,
       p.supplier_id,
       su.supplier_name,
       s.quantity_sold,
       s.selling_price,
       p.unit_cost,
       s.promotion_applied,
       s.festival,
       (s.quantity_sold * s.selling_price)                    AS revenue,
       (s.quantity_sold * (s.selling_price - p.unit_cost))    AS profit
FROM sales s
JOIN products  p  ON p.product_id  = s.product_id
JOIN suppliers su ON su.supplier_id = p.supplier_id
JOIN calendar  c  ON c.date = s.date;

CREATE OR REPLACE VIEW v_inventory_status AS
SELECT i.product_id,
       p.product_name,
       p.category,
       p.supplier_id,
       su.supplier_name,
       i.current_stock,
       i.snapshot_date,
       i.avg_daily_sales_last_30d,
       i.reorder_point,
       (i.current_stock <= i.reorder_point)                                   AS below_reorder_point,
       ROUND(i.current_stock / NULLIF(i.avg_daily_sales_last_30d, 0), 1)      AS days_of_cover,
       p.supplier_lead_time_days,
       (i.current_stock / NULLIF(i.avg_daily_sales_last_30d, 0)
            < p.supplier_lead_time_days)                                      AS stockout_risk,
       p.minimum_order_quantity,
       p.unit_cost,
       p.selling_price,
       (i.current_stock * p.unit_cost)                                        AS stock_value
FROM inventory i
JOIN products  p  ON p.product_id  = i.product_id
JOIN suppliers su ON su.supplier_id = p.supplier_id;

CREATE OR REPLACE VIEW v_batch_expiry AS
SELECT b.batch_id,
       b.product_id,
       p.product_name,
       p.category,
       b.quantity_remaining,
       b.received_date,
       b.expiry_date,
       (b.expiry_date - i.snapshot_date)           AS days_to_expiry,
       p.unit_cost,
       (b.quantity_remaining * p.unit_cost)        AS value_at_risk
FROM batches b
JOIN products  p ON p.product_id = b.product_id
JOIN inventory i ON i.product_id = b.product_id;
