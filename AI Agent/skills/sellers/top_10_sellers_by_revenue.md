---
name: top_10_sellers_by_revenue
domain: sellers
title: Top 10 sellers by revenue
route: analyze
description: "The ten sellers with the highest total sales value (item price plus freight) across all orders."
triggers:
  - "Who are our top 10 sellers?"
  - "Top sellers by revenue"
  - "Which seller sells the most?"
disambiguation: "Seller revenue uses order_items price + freight_value. Per-month breakdown: seller_tpv_per_month."
tables: [order_items]
grain: seller
dimensions: [seller_id]
eval_questions:
  - "Which seller has the highest total revenue?"
  - "What share of total revenue do the top 10 sellers account for?"
---

## Definition
Sellers ranked by total payment value (TPV) linked via order_items.

## SQL
### main
```sql
SELECT
  oi.seller_id,
  SUM(oi.price + oi.freight_value) AS total_revenue
FROM order_items oi
GROUP BY oi.seller_id
ORDER BY total_revenue DESC
LIMIT 10;
```

## Pitfalls
- Do NOT join order_payments to order_items: payment_value repeats for every item and inflates seller totals.
- The original definition used payment_value and double counted multi-item orders.

## Interpretation
Revenue is concentrated: the top sellers account for a large share.
