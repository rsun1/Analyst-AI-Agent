---
name: seller_order_volume_with_lead_status
domain: leads
title: Seller order volume with lead status
route: analyze
description: "For each seller with orders in a period: order count, revenue and whether the seller appears in the closed-leads pipeline."
triggers:
  - "Which active sellers came from the leads pipeline?"
  - "Seller orders and lead status in 2017"
  - "Overlap between active sellers and closed leads"
disambiguation: "Sellers are the bridge between orders and leads."
tables: [orders, order_items, leads_closed]
grain: seller
dimensions: [seller_id, lead_status]
eval_questions:
  - "How many sellers with orders in 2017 are in the closed leads pipeline?"
  - "What is the total revenue of sellers that came from leads in 2017?"
---

## Definition
For each seller who had orders in a given period, show their order count, total revenue, and whether they appear in the leads pipeline (converted or not). Useful for understanding overlap between active sellers and the MQL funnel.

## SQL
### main
```sql
SELECT
  oi.seller_id,
  COUNT(DISTINCT o.order_id) AS order_count,
  SUM(oi.price + oi.freight_value) AS total_revenue,
  CASE WHEN lc.seller_id IS NOT NULL THEN 'in_leads' ELSE 'not_in_leads' END AS lead_status
FROM orders o
JOIN order_items oi ON oi.order_id = o.order_id
LEFT JOIN leads_closed lc ON lc.seller_id = oi.seller_id
WHERE o.order_purchase_timestamp >= '2017-01-01' AND o.order_purchase_timestamp < '2018-01-01'
GROUP BY oi.seller_id, lead_status
ORDER BY total_revenue DESC;
```

## Pitfalls
- Use COUNT(DISTINCT order_id): items repeat orders.
- Revenue uses order_items price + freight; joining order_payments here double counts.
- Group by the CASE alias is fine in Postgres, but HAVING cannot use aliases.

## Interpretation
Compare average revenue of in-leads sellers to the rest.
