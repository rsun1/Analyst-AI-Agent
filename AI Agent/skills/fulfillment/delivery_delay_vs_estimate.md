---
name: delivery_delay_vs_estimate
domain: fulfillment
title: Delivery delay versus estimate
route: analyze
description: "Days between the estimated and the actual delivery date per order (positive means late, negative means early)."
triggers:
  - "How late are deliveries versus the estimate?"
  - "Average delivery delay"
  - "How many orders were delivered late?"
disambiguation: "This is NOT total delivery time. Total time from purchase to delivery is delivered_customer_date minus order_purchase_timestamp."
tables: [orders]
grain: order
dimensions: [month, customer_state]
eval_questions:
  - "What is the average delivery delay versus the estimate, in days?"
  - "How many delivered orders arrived after the estimated date?"
---

## Definition
Days between ESTIMATED delivery date and ACTUAL delivery date (positive = late, negative = early).
NOTE: This is NOT total delivery time from purchase. 
For total delivery time use: order_delivered_customer_date - order_purchase_timestamp

## SQL
### main
```sql
SELECT
  order_id,
  order_estimated_delivery_date,
  order_delivered_customer_date,
  EXTRACT(EPOCH FROM (order_delivered_customer_date - order_estimated_delivery_date)) / 86400.0 AS delay_days
FROM orders
WHERE order_delivered_customer_date IS NOT NULL
  AND order_estimated_delivery_date IS NOT NULL
ORDER BY delay_days DESC;
```

## Pitfalls
- Compute with EXTRACT(EPOCH FROM (actual - estimated)) / 86400.0 on timestamps; never subtract strings.
- Filter to rows where both dates are NOT NULL (undelivered orders have no delivery date).
- Average delay is negative because most orders arrive early; report late share too.

## Interpretation
Negative averages mean early on average; the late tail drives review scores.
