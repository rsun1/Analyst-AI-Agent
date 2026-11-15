---
name: seller_tpv_per_month
domain: sellers
title: Seller GMV per month
route: analyze
description: "Sales value (item price plus freight) for each seller per calendar month, by order purchase month."
triggers:
  - "What was each seller's monthly sales?"
  - "Seller TPV per month"
  - "How much did seller X sell in each month?"
disambiguation: "payment_value cannot be attributed to one seller when an order has several sellers, so seller sales use order_items price + freight_value. For the top sellers overall use top_10_sellers_by_revenue."
tables: [orders, order_items]
grain: seller-month
dimensions: [seller_id, month]
eval_questions:
  - "Which seller had the highest sales in November 2017?"
  - "How many sellers had sales in each month of 2017?"
---

## Definition
Total sales (TPV = total payment value) for each seller per month, based on `order_purchase_timestamp` in the `orders` table. One row per seller per month.

## SQL
### main
```sql
SELECT
  oi.seller_id,
  TO_CHAR(o.order_purchase_timestamp, 'YYYY-MM') AS order_month,
  SUM(oi.price + oi.freight_value) AS seller_gmv
FROM orders o
JOIN order_items oi ON oi.order_id = o.order_id
GROUP BY oi.seller_id, order_month
ORDER BY order_month, seller_gmv DESC;
```

## Pitfalls
- Do NOT sum order_payments.payment_value over order_items rows: it is repeated once per item in the order and inflates totals.
- Original definition used payment_value; it was replaced because it double counted multi-item orders.

## Interpretation
Large values concentrate in a few sellers; use top_10_sellers_by_revenue for rankings.
