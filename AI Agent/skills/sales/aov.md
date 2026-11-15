---
name: aov
domain: sales
title: Average order value
route: analyze
description: "Average order value: GMV divided by the number of paid orders, per month and overall."
triggers:
  - "What is the average order value?"
  - "How much does a typical order cost?"
  - "AOV by month"
  - "Average basket size"
disambiguation: "AOV here is payment_value per order (includes freight). Items per order is items_per_order; average item price is not payment based."
tables: [orders, order_payments]
grain: month
dimensions: [month]
eval_questions:
  - "What is the overall average order value?"
  - "Which month had the highest average order value?"
---

## Definition
Average order value (AOV): total customer payment value divided by the number of orders that have a payment, by purchase month and overall. Includes freight and vouchers, like GMV.

## SQL
### by_month
```sql
SELECT TO_CHAR(o.order_purchase_timestamp, 'YYYY-MM') AS order_month,
  ROUND((SUM(op.payment_value) / COUNT(DISTINCT o.order_id))::numeric, 2) AS aov,
  COUNT(DISTINCT o.order_id) AS orders
FROM orders o
JOIN order_payments op ON op.order_id = o.order_id
GROUP BY order_month
ORDER BY order_month;
```

### overall
```sql
SELECT ROUND((SUM(op.payment_value) / COUNT(DISTINCT op.order_id))::numeric, 2) AS aov,
  COUNT(DISTINCT op.order_id) AS orders
FROM order_payments op;
```

## Pitfalls
- Divide by COUNT(DISTINCT order_id): order_payments has several rows per order, so COUNT(*) would understate AOV.
- Orders without any payment row are excluded (a handful of orders).
- Months at the edges of the dataset (2016, late 2018) have very few orders; AOV is noisy there.

## Interpretation
AOV near 160 BRL with long right tail; compare months, not single orders.
