# NL2SQL evaluation [eval_cases.yaml]  (2026-11-14 11:47)
- cases: 79 (66 SQL, 13 routing/unsupported) | wall time 251s | avg 9.5s per case
- routing accuracy:      78/79 = 99%
- execution accuracy:    65/66 = 98%
- result accuracy:       61/66 = 92%
- first-attempt correct: 53/66 = 80%  (schema-linking proxy)
- LLM-written answers that passed the number-grounding check: 64/64

## By category (result accuracy for SQL cases / routing for others)
- sales: 17/17
- product: 10/10
- inventory: 10/10
- expiry: 8/9
- supplier: 6/6
- dates: 3/5
- multi: 4/5
- edge: 3/4
- unsupported: 5/5
- rag_route: 8/8

## Failures
### B08 [expiry] Which products have more than one batch in stock?
- expected `sql`, route `sql`, status `success`, attempts 2
- sql: `SELECT DISTINCT b.product_id, p.product_name FROM batches AS b JOIN products AS p ON b.product_id = p.product_id GROUP BY b.product_id, p.product_name HAVING COUNT(DISTINCT b.batch_id) > 1 ORDER BY p.product_name LIMIT 500`
- got: [['P029', 'Apples 1kg'], ['P025', 'Bath Soap'], ['P020', 'Biscuits']]  | reference: [['Bottled Water 1L', 3], ['Apples 1kg', 2], ['Sugar 1kg', 3]]
- attempt errors: ["Table 'products' is not available. Allowed tables: batches."]
### D01 [dates] Compare October 2026 revenue with October 2025
- expected `sql`, route `sql`, status `success`, attempts 1
- sql: `SELECT 'October 2026' AS period, SUM(revenue) AS total_revenue FROM v_sales_enriched WHERE date BETWEEN '2026-10-01' AND '2026-10-31' UNION ALL SELECT 'October 2025', SUM(revenue) FROM v_sales_enriched WHERE date BETWEEN '2025-10-01' AND '2025-10-31' ORDER BY total_revenue DESC LIMIT 500`
- got: [['October 2026', 68542], ['October 2025', 57431.8]]  | reference: [[2025, 57431.8], [2026, 68542]]
### D05 [dates] Compare revenue of September and October 2026
- expected `sql`, route `sql`, status `success`, attempts 1
- sql: `SELECT 'September 2026' AS period, SUM(revenue) AS total_revenue FROM v_sales_enriched WHERE date BETWEEN '2026-09-01' AND '2026-09-30' UNION ALL SELECT 'October 2026' AS period, SUM(revenue) AS total_revenue FROM v_sales_enriched WHERE date BETWEEN '2026-10-01' AND '2026-10-31' ORDER BY period LIMIT 500`
- got: [['October 2026', 68542], ['September 2026', 63333.6]]  | reference: [['2026-09-01', 63333.6], ['2026-10-01', 68542]]
### M02 [multi] Which products are below their reorder point and also have a batch expiring within 7 days?
- expected `sql`, route `sql`, status `success`, attempts 1
- sql: `SELECT DISTINCT i.product_name, i.current_stock, i.reorder_point, b.batch_id, b.quantity_remaining, b.days_to_expiry FROM v_inventory_status AS i JOIN v_batch_expiry AS b ON i.product_id = b.product_id WHERE i.below_reorder_point AND b.days_to_expiry <= 7 ORDER BY b.days_to_expiry LIMIT 500`
- got: [['Paneer 200g', 2, 5, 'B00301', 2, 2], ['Apples 1kg', 4, 5, 'B02902', 2, 3], ['Apples 1kg', 4, 5, 'B02901', 2, 4]]  | reference: [['Apples 1kg'], ['Paneer 200g']]
### E02 [edge] What was the total revenue in 2030?
- expected `sql`, route `unsupported`, status `None`, attempts None