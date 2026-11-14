# NL2SQL evaluation [eval_holdout.yaml]  (2026-11-14 11:51)
- cases: 28 (24 SQL, 4 routing/unsupported) | wall time 251s | avg 20.1s per case
- routing accuracy:      27/28 = 96%
- execution accuracy:    23/24 = 96%
- result accuracy:       20/24 = 83%
- first-attempt correct: 17/24 = 71%  (schema-linking proxy)
- LLM-written answers that passed the number-grounding check: 21/21

## By category (result accuracy for SQL cases / routing for others)
- sales: 6/6
- product: 3/3
- inventory: 3/4
- expiry: 2/3
- supplier: 3/3
- dates: 2/2
- multi: 0/2
- edge: 1/1
- unsupported: 1/2
- rag_route: 2/2

## Failures
### H13 [inventory] Which products sell more than 1.5 units per day on average over the last 30 days?
- expected `sql`, route `sql`, status `success`, attempts 3
- sql: `SELECT product_name FROM v_sales_enriched WHERE date BETWEEN '2026-10-02' AND '2026-10-31' GROUP BY product_name HAVING ROUND(CAST(SUM(quantity_sold) AS DECIMAL) / COUNT(DISTINCT date), 2) > 1.5 ORDER BY SUM(quantity_sold) DESC LIMIT 500`
- got: [['Cola 750ml'], ['Biscuits'], ['Instant Noodles']]  | reference: [['Cola 750ml', 2.03], ['Bottled Water 1L', 1.87], ['Potato Chips', 1.8]]
- attempt errors: ["Table 'v_sales_enriched' is not available. Allowed tables: inventory.", 'for SELECT DISTINCT, ORDER BY expressions must appear in select list']
### H15 [expiry] Which Bakery products have batches expiring within 3 days?
- expected `sql`, route `sql`, status `success`, attempts 1
- sql: `SELECT DISTINCT b.product_name, b.batch_id, b.quantity_remaining, b.expiry_date, b.days_to_expiry FROM v_batch_expiry AS b WHERE b.category = 'Bakery' AND b.days_to_expiry <= 3 ORDER BY b.days_to_expiry, b.product_name LIMIT 500`
- got: [['Bread', 'B00501', 10, '2026-11-01', 1], ['Brown Bread', 'B00602', 1, '2026-11-01', 1], ['Brown Bread', 'B00601', 7, '2026-11-03', 3]]  | reference: [['Bread'], ['Brown Bread']]
### H22 [multi] Which Staples products sold more than 30 units in July 2026?
- expected `sql`, route `sql`, status `no_data`, attempts 2
- sql: `SELECT DISTINCT product_name FROM v_sales_enriched WHERE category = 'Staples' AND date BETWEEN '2026-07-01' AND '2026-07-31' AND quantity_sold > 30 ORDER BY product_name LIMIT 500`
- got: []  | reference: [['Basmati Rice 1kg', 38], ['Sugar 1kg', 38], ['Toor Dal 1kg', 31]]
- attempt errors: ["Invalid column or reference: Column 'product_category' could not be resolved. Line: 3, Col: 22"]
### H23 [multi] Which products have stock below their reorder point and are supplied by Metro Foods Supply?
- expected `sql`, route `sql`, status `error`, attempts 3
- attempt errors: ['for SELECT DISTINCT, ORDER BY expressions must appear in select list', 'for SELECT DISTINCT, ORDER BY expressions must appear in select list', 'for SELECT DISTINCT, ORDER BY expressions must appear in select list']
### H26 [unsupported] What is my customer satisfaction score?
- expected `unsupported`, route `rag`, status `None`, attempts None