# StockSense AI – Dataset documentation

Source: `StockSense_AI_Retail_Dataset/` (6 CSV files, structured). Per its README the data is **synthetic**
(30 grocery/kirana products, 6 suppliers, daily sales 2025-01-01 → 2026-10-31). All facts below were verified
against the actual files and the loaded PostgreSQL tables (`backend/scripts/verify_ingest.py`).

## Tables

| Table | Rows | Primary key | Foreign keys | Meaning |
|---|---|---|---|---|
| `suppliers` | 6 | `supplier_id` | – | Supplier directory with lead time and minimum order value |
| `products` | 30 | `product_id` | `supplier_id → suppliers` | Product master: category, list price, cost, shelf life, ordering settings |
| `calendar` | 669 | `date` | – | Date dimension (2025-01-01…2026-10-31): weekday, weekend, month, illustrative festivals |
| `inventory` | 30 | `product_id` | `product_id → products` | **Current** stock snapshot (all rows dated 2026-10-31), 30-day average sales, reorder point |
| `batches` | 51 | `batch_id` | `product_id → products` | Current stock split by batch with received/expiry dates |
| `sales` | 20,070 | (`date`,`product_id`) | `date → calendar`, `product_id → products` | Daily units sold per product (30 products × 669 days), promotion flag, actual selling price, festival |

### Columns and types

**suppliers** – `supplier_id` TEXT · `supplier_name` TEXT · `city` TEXT (3 cities) · `average_lead_time_days` INT · `minimum_order_value` NUMERIC

**products** – `product_id` TEXT · `product_name` TEXT · `category` TEXT (9 values) · `selling_price` NUMERIC (list price) · `unit_cost` NUMERIC · `shelf_life_days` INT · `supplier_lead_time_days` INT · `minimum_order_quantity` INT · `base_daily_demand` NUMERIC (modelling parameter) · `supplier_id` TEXT · `reorder_target_days` INT (always 10)

**calendar** – `date` DATE · `day_of_week` TEXT · `is_weekend` BOOL · `month` SMALLINT (1-12, no year) · `festival` TEXT (NULL on 659 of 669 days) · `is_festival` BOOL

**inventory** – `product_id` TEXT · `current_stock` INT · `snapshot_date` DATE · `avg_daily_sales_last_30d` NUMERIC · `reorder_point` INT

**batches** – `batch_id` TEXT · `product_id` TEXT · `quantity_remaining` INT · `received_date` DATE · `expiry_date` DATE (2026-11-01…2028-10-28)

**sales** – `date` DATE · `product_id` TEXT · `quantity_sold` INT (0–10) · `promotion_applied` BOOL · `selling_price` NUMERIC (price actually charged that day) · `festival` TEXT (NULL on 19,770 rows)

## Relationships

```
suppliers 1──* products 1──* sales *──1 calendar
                   │ 1──1 inventory
                   └ 1──* batches
```
Every foreign key was checked: **zero orphan rows**.

## Derived business metrics (implemented as views / rules in `semantic_layer.yaml`)

| Metric | Definition |
|---|---|
| Revenue | `quantity_sold × sales.selling_price` (`v_sales_enriched.revenue`) |
| Profit | `quantity_sold × (sales.selling_price − products.unit_cost)` |
| Days of cover | `current_stock / avg_daily_sales_last_30d` |
| Below reorder point | `current_stock <= reorder_point` (15 of 30 products) |
| Stockout risk | `days_of_cover < supplier_lead_time_days` |
| Days to expiry | `expiry_date − inventory.snapshot_date` (10 batches within 7 days) |
| Stock value / value at risk | `quantity × unit_cost` |

Views: `v_sales_enriched`, `v_inventory_status`, `v_batch_expiry` (`backend/app/db/views.sql`).

## Data quality findings

- No duplicate rows, no duplicate (date, product) keys, no missing values except the legitimately blank `festival` fields.
- `inventory.current_stock` equals the sum of `batches.quantity_remaining` for **all 30** products.
- `sales.selling_price` differs from `products.selling_price` on **1,639 rows – all of them promotion days**.
  Revenue must use `sales.selling_price`.
- Two different lead-time fields: `products.supplier_lead_time_days` differs from `suppliers.average_lead_time_days`
  for **27 of 30** products. They are separate attributes; the semantic layer says which to use.
- 35 % of sales rows have `quantity_sold = 0` (genuine "no sale" days, kept).
- Data ends **2026-10-31**; relative dates ("last month") are anchored to that date, not to today.
- `calendar.month` has no year; combine with `date`/`year` when comparing across years.
- The festival calendar is illustrative (6 festivals), not an official holiday list.

## Not present in the data (questions about these are answered "unsupported")

Customers / bills, stores or branches (so store-level authorisation has nothing to filter on), returns, wastage,
purchase orders / receipts, stock history (inventory is a single snapshot), staff.

## Structured vs unstructured

- **Structured (6 CSVs)** → PostgreSQL + NL2SQL. Never embedded.
- **Unstructured** → none shipped with the repo (the pitch deck PDF describes the product, not store operations).
  Shopkeeper documents are uploaded through `/api/documents` and answered through RAG.
