---
name: order_approval_lag
domain: orders
title: Order approval lag
route: analyze
description: "Time from order purchase to approval in hours: average and median by month."
triggers:
  - "How long does it take to approve an order?"
  - "Average order approval time"
  - "Order approval lag by month"
  - "How fast are payments approved?"
disambiguation: "Purchase to approval only. Purchase to delivery is delivery_time and approval to carrier handoff is carrier_handoff_time."
tables: [orders]
grain: month
dimensions: [month]
eval_questions:
  - "What is the average time in hours from purchase to approval?"
  - "Which month had the longest median approval time?"
---

## Definition
Hours between order purchase and payment approval (order_approved_at minus order_purchase_timestamp), average and median by purchase month.

## SQL
### main
```sql
SELECT TO_CHAR(order_purchase_timestamp, 'YYYY-MM') AS order_month,
  ROUND(AVG(EXTRACT(EPOCH FROM (order_approved_at - order_purchase_timestamp)) / 3600.0)::numeric, 2) AS avg_approval_hours,
  ROUND((PERCENTILE_CONT(0.5) WITHIN GROUP (
    ORDER BY EXTRACT(EPOCH FROM (order_approved_at - order_purchase_timestamp)) / 3600.0))::numeric, 2) AS median_approval_hours,
  COUNT(*) AS orders
FROM orders
WHERE order_approved_at IS NOT NULL
GROUP BY order_month
ORDER BY order_month;
```

## Pitfalls
- Skip orders with NULL order_approved_at (never approved).
- The average is pulled up by a few very slow approvals; show the median too.
- Compute durations with EXTRACT(EPOCH FROM (a - b)) / 3600.0 on timestamps.

## Interpretation
Median is well under an hour (boleto payments take longer than cards).
