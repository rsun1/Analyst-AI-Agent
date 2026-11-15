---
name: orders_per_month
domain: orders
title: Orders per month
route: analyze
description: "Number of orders placed per calendar month, by order purchase timestamp."
triggers:
  - "How many orders per month?"
  - "Monthly order volume"
  - "Which month had the most orders in 2017?"
disambiguation: "Orders over time, all statuses included. Not for how many orders have a given status (delivered, canceled, shipped...): use orders_by_status."
tables: [orders]
grain: month
dimensions: [month, order_status]
eval_questions:
  - "Which month in 2017 had the highest order volume?"
  - "How many orders were placed in January 2018?"
---

## Definition
Count of orders per calendar month (by `order_purchase_timestamp`).

## SQL
### main
```sql
SELECT
  TO_CHAR(order_purchase_timestamp, 'YYYY-MM') AS order_month,
  COUNT(*) AS order_count
FROM orders
GROUP BY order_month
ORDER BY order_month;
```

## Pitfalls
- Use half-open timestamp ranges (>= start AND < next period start); BETWEEN misses the last day's afternoon.
- Cancelled and unavailable orders are included unless filtered.

## Interpretation
Look for the Black Friday spike (November 2017); the final months of the dataset are incomplete.
