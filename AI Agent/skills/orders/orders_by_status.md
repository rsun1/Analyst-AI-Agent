---
name: orders_by_status
domain: orders
title: Orders by status
route: analyze
description: "Count and percentage of orders in each order status."
triggers:
  - "How many orders are in each status?"
  - "Order status breakdown"
  - "How many orders are still processing?"
  - "What share of orders were delivered?"
disambiguation: "All statuses over all time. Cancellation share by month is cancellation_rate."
tables: [orders]
grain: order_status
dimensions: [order_status]
eval_questions:
  - "How many orders have status delivered?"
  - "What percentage of orders are canceled?"
---

## Definition
Number of orders and share of all orders in each order status (delivered, shipped, canceled, unavailable, invoiced, processing, created, approved).

## SQL
### main
```sql
SELECT order_status, COUNT(*) AS order_count,
  ROUND((100.0 * COUNT(*) / SUM(COUNT(*)) OVER ())::numeric, 2) AS pct
FROM orders
GROUP BY order_status
ORDER BY order_count DESC;
```

## Pitfalls
- Valid statuses: delivered, shipped, canceled, unavailable, invoiced, processing, created, approved.
- To count successfully delivered orders also require order_delivered_customer_date IS NOT NULL.

## Interpretation
About 97 percent of orders are delivered; canceled plus unavailable are about 1 percent each.
