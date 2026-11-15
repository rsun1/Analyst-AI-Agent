---
name: monthly_gmv
domain: sales
title: Monthly GMV
route: analyze
description: "Gross merchandise value per calendar month: total customer payment value of orders by purchase month."
triggers:
  - "What was our GMV per month?"
  - "Show monthly gross merchandise value"
  - "How much did customers pay each month in 2017?"
disambiguation: "GMV here is payment_value (includes freight and vouchers). For product/seller revenue without freight use revenue_by_product_category."
tables: [orders, order_payments]
grain: month
dimensions: [month]
eval_questions:
  - "What was the GMV in each month of 2017?"
  - "Which month had the highest GMV?"
---

## Definition
Gross merchandise value per month: total `payment_value` from `order_payments` per calendar month (by `order_purchase_timestamp`).

## SQL
### main
```sql
SELECT
  TO_CHAR(o.order_purchase_timestamp, 'YYYY-MM') AS order_month,
  SUM(op.payment_value) AS gmv
FROM orders o
LEFT JOIN order_payments op ON op.order_id = o.order_id
GROUP BY order_month
ORDER BY order_month;
```

## Pitfalls
- order_payments has several rows per order (installments, vouchers): never join it to order_items before summing, or payment_value is multiplied by the item count.
- Group by the purchase month (order_purchase_timestamp), not delivery month.
- 2016 and late 2018 months are sparse; say so when the user asks about them.

## Interpretation
Compare adjacent months for trend; the first and last months of the dataset are incomplete.
