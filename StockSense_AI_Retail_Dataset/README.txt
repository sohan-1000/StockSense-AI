# StockSense AI sample retail dataset

This is a synthetic, realistic-style dataset created for the StockSense AI MVP.
It is not real merchant data and should not be represented as actual business records.

## Files
- products.csv: 30 grocery / kirana products, categories, prices, shelf life, supplier lead time and reorder settings.
- sales.csv: daily product-level sales from 2025-01-01 through 2026-10-31 (30 products x 669 days).
- inventory.csv: current stock snapshot as of 2026-10-31, recent average daily sales and reorder point.
- batches.csv: current stock split into batches with received and expiry dates.
- suppliers.csv: supplier directory and lead-time information.
- calendar.csv: date features including weekday, weekend and a small illustrative festival calendar.

## Recommended MVP use
1. Train demand forecasting models using sales.csv.
2. Join sales.csv with calendar.csv on date to add weekend / festival features.
3. Join products.csv on product_id to add category, price and shelf-life features.
4. Use inventory.csv and suppliers.csv for stockout risk and reorder recommendations.
5. Use batches.csv for expiry alerts and FEFO (First Expired, First Out) logic.

## Important notes
- Sales are daily product totals, not individual bills / customer transactions.
- The festival calendar is illustrative and is not a complete official holiday calendar.
- Inventory is a point-in-time snapshot, not a full historical stock ledger.
- For rigorous expiry / inventory simulation, add purchase receipts, returns, wastage and stock adjustments.
- For forecasting, use chronological train/validation/test splits; do not randomly split time-series rows.
