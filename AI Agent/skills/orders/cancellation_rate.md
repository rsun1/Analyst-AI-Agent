---
name: cancellation_rate
domain: orders
title: Cancellation rate
route: analyze
description: "Cancellation rate: share of orders that were canceled, by month and overall."
triggers:
  - "What is the order cancellation rate?"
  - "How many orders were canceled?"
  - "Cancellation rate by month"
  - "Are cancellations increasing?"
disambiguation: "Only status = 'canceled'. 'unavailable' orders (item out of stock) are a different status."
tables: [orders]
grain: month
dimensions: [month]
eval_questions:
  - "What is the overall order cancellation rate?"
  - "Which month had the highest cancellation rate?"
---

## Definition
Share of orders with status canceled, by purchase month and overall. Orders with status unavailable are reported separately and not counted as canceled.

## SQL
### by_month
```sql
SELECT TO_CHAR(order_purchase_timestamp, 'YYYY-MM') AS order_month,
  COUNT(*) AS orders,
  COUNT(*) FILTER (WHERE order_status = 'canceled') AS canceled,
  ROUND((100.0 * COUNT(*) FILTER (WHERE order_status = 'canceled') / COUNT(*))::numeric, 2) AS cancel_pct
FROM orders
GROUP BY order_month
ORDER BY order_month;
```

### overall
```sql
SELECT COUNT(*) AS orders,
  COUNT(*) FILTER (WHERE order_status = 'canceled') AS canceled,
  ROUND((100.0 * COUNT(*) FILTER (WHERE order_status = 'canceled') / COUNT(*))::numeric, 2) AS cancel_pct
FROM orders;
```

## Pitfalls
- Denominator is all orders placed in the period, not only delivered ones.
- Cancellation time is not recorded: months are by purchase date, not cancel date.
- Sparse months (2016, late 2018) give unstable rates.

## Interpretation
Cancellation is under 1 percent overall; individual months can spike.
