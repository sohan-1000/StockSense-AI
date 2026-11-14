StockSense AI – Extended Transformer Training Dataset

SOURCE: Expanded from the user's uploaded StockSense_AI_Retail_Dataset(1).zip.
All six CSV tables retain EXACTLY the original column names and order.
All original sales rows (20,070) and original calendar rows are preserved.
Newly generated historical rows were added for 2006-01-01 through 2024-12-31.
Final sales coverage: 2006-01-01 to 2026-10-31.
Products: 30 (same product IDs and products as source).
Sales rows: 228,270; observations per product: 7,609.
New data is SYNTHETIC, not genuine historical sales.
No additional CSV tables or CSV columns were introduced.

QUANTITY UNITS:
quantity_sold, current_stock, quantity_remaining and minimum_order_quantity all refer
to SELLABLE UNITS, e.g., one milk pack, one 1-kg rice bag, or one 6-egg pack.
See quantity_units.json for each original product's sellable unit.
Where original product names omit a package size, the unit is labelled unspecified
rather than inventing a size. The JSON is reference metadata, not a new table.

DATA GENERATION:
Historical records before 2025 include product-specific trends, annual seasonality,
weekday/weekend patterns, promotion effects, annual holiday-like windows and
stochastic count demand. Holiday windows are illustrative, NOT verified historical dates.
Original rows from 2025 onward are retained unchanged, so there may be a distribution
shift around 2025. This is intentional preservation of source data.

IMPORTANT MODELING NOTES:
- Each product is one daily time series. 30 products x 7,609 days.
- Larger synthetic data does not guarantee better Transformer performance.
- Use time-based splits, compare against seasonal naive and rolling-mean baselines.
- Fit scalers on training data only.
- Future promotion status must be supplied as a scenario or excluded.
- Inventory.csv and batches.csv are unchanged point-in-time snapshots, NOT
  historical inventory trajectories. They cannot establish real stockout labels.
